"""
ai/bot_controller.py
======================
AI 自主建房/轮转/超时/AI互相配对——这一层只做"决策"，不碰数据库/WebSocket，
所以能在完全脱离网站后端的情况下单独测试（api.py 那边只需要：读一遍当前
状态 -> 调这里的纯函数拿到"这一轮该做什么" -> 真去执行数据库/广播这些
有副作用的操作）。这样分层的原因很直接：调度器这种"每隔几秒跑一次、
状态会持续演变"的逻辑最容易出的错就是"某个边界条件没考虑到"，
纯函数可以把这些边界条件一条条写成单元测试跑很多遍，不用真的连着
数据库和真实时间反复试。

已确认的规则（v2，替换掉 v1 的"全暂停"设计）：
    - lobby 里同时最多 MAX_PENDING_ROOMS 个"AI已建但没人加入"的空房间
    - AI 按固定顺序轮流建房
    - 每个空房间超过 ROOM_TIMEOUT_SECONDS（5分钟）还没人加入，自动关闭，
      轮到下一个 AI 建房
    - 真人加入 bot 建的空房间、或者真人在跟某个 AI 对局，**都不会**让
      其余 bot 的建房/超时节奏受任何影响——这两件事完全独立。
    - 新增：AI 会主动加入别的 AI 建的空房间，凑成一场 AI vs AI 表演赛
      供人观战。同一时刻整个网站最多只有一场这样的表演赛在进行——
      用 active_ai_vs_ai_game_id 记录"当前这一场"，非 None 时不再撮合
      新的一场，直到这场结束（调用方在对局结束时调用 mark_game_ended）
      才会解除占用，下一轮调度才可能撮合下一场。
    - 这个"同时最多一场"的限制**只**约束"AI 主动加入 AI 房间"这一个
      动作，不影响：真人随时可以加入任何一个 bot 空房间（哪怕此时
      已经有一场 AI vs AI 表演赛在进行）；bot 建房/关房的轮转节奏也
      完全不受影响。

依赖：无（纯 Python，不依赖 ai/ 目录其他文件，也不依赖网站后端）
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Optional


MAX_PENDING_ROOMS = 3
ROOM_TIMEOUT_SECONDS = 5 * 60


@dataclass
class BotRoom:
    game_id: str
    creator_key: str      # personas.py 里的人格 key，比如 "kanderson"
    created_at: float      # unix 时间戳
    joined: bool = False   # 有没有人（真人或者另一个bot）加入了


@dataclass
class SchedulerState:
    """调度器需要记住的全部状态。api.py 那边负责在每次 tick 之间把这份状态
    持久化下来（可以就是几个模块级全局变量，不需要专门建数据库表——
    这些状态丢了大不了重新走一轮轮转，不是需要长期保留的数据）。"""
    pending_rooms: list[BotRoom] = field(default_factory=list)
    next_bot_index: int = 0
    active_ai_vs_ai_game_id: Optional[str] = None  # 当前唯一一场"AI主动
    # 加入AI房间"凑成的表演赛，非None时暂停撮合新的一场（但不影响建房/
    # 真人加入）。对局结束时调用方必须调用 mark_game_ended 来解除占用。


@dataclass
class SchedulerActions:
    """一次 tick 该执行的动作，全部是"建议"，真正的数据库/WebSocket 操作
    由 api.py 去做，做完之后再把结果同步回 SchedulerState。"""
    close_room_ids: list[str] = field(default_factory=list)
    create_bot_keys: list[str] = field(default_factory=list)
    join: Optional[tuple[str, str]] = None  # (game_id, joiner_bot_key)——
    # 建议让 joiner_bot_key 这个人格去加入 game_id 这个空房间，凑成一场
    # AI vs AI 表演赛。None 表示这一轮不撮合（可能是没有合适的空房、
    # 没有空闲的候选人格，或者已经有一场表演赛在进行中）。


def decide_actions(state: SchedulerState, now: float, bot_order: list[str],
                    max_pending: int = MAX_PENDING_ROOMS,
                    timeout_seconds: float = ROOM_TIMEOUT_SECONDS,
                    join_eligible_bots: Optional[list[str]] = None,
                    rng: Optional[random.Random] = None) -> SchedulerActions:
    """核心决策函数——传入当前状态和"现在几点"，返回这一轮该做的事。
    不修改 state 本身（除了下面单独说明的 next_bot_index 推进，那个是
    "预支"轮转指针，方便调用方知道"如果真的建了这几个房，下一次该从谁
    开始"，但 api.py 应该把 create_bot_keys 实际建房成功之后的最终指针
    位置写回 state，不能假设这里返回的一定会全部成功执行）。

    join_eligible_bots：允许被撮合去"加入别的AI房间"的人格 key 列表——
    调用方应该把"当前已经在某局对局里（不管对手是真人还是bot）"的人格
    排除在外再传进来，避免同一个人格账号同时被拉进两盘棋。默认（None）
    等同于 bot_order 本身（不做排除，主要方便单元测试）。这个参数只影响
    "撮合AI互相加入"这一步，跟建房轮转（bot_order）互不影响——一个人格
    正在下棋不妨碍它继续按轮转顺序建下一个空房间（建房那一刻还没有对手，
    不构成"重复下棋"）。
    """
    actions = SchedulerActions()

    if not bot_order:
        return actions

    rng = rng or random.Random()

    # 1) 超时关房——不再看"是否有真人在跟某个bot下棋"，任何时候都按超时
    #    规则正常处理，跟真人活动完全解耦。
    for room in state.pending_rooms:
        if not room.joined and (now - room.created_at) >= timeout_seconds:
            actions.close_room_ids.append(room.game_id)

    still_pending = [r for r in state.pending_rooms if r.game_id not in actions.close_room_ids]
    still_pending_count = len(still_pending)

    # 2) 补建到 max_pending 个
    idx = state.next_bot_index
    while still_pending_count + len(actions.create_bot_keys) < max_pending:
        actions.create_bot_keys.append(bot_order[idx % len(bot_order)])
        idx += 1

    # 3) 撮合一场 AI vs AI 表演赛——同一时刻最多一场。只从"这一轮开始之前
    #    就已经存在、这一轮也没被判超时关掉"的空房间里选，不从这一轮刚
    #    建议要建的新房间里选（新房间这一刻还没真正建出来，也让"建房"和
    #    "被另一个AI加入"在同一瞬间发生显得很怪）。
    if state.active_ai_vs_ai_game_id is None and still_pending:
        pool = join_eligible_bots if join_eligible_bots is not None else bot_order
        rooms_to_try = list(still_pending)
        rng.shuffle(rooms_to_try)  # 哪个空房间被凑成表演赛不固定，
        # 避免总是同一个位置（比如轮转指针刚好建出来的那个）被优待。
        for room in rooms_to_try:
            candidates = [b for b in pool if b != room.creator_key]
            if candidates:
                actions.join = (room.game_id, rng.choice(candidates))
                break

    return actions


def apply_actions(state: SchedulerState, actions: SchedulerActions,
                   created_game_ids: dict[str, str], now: float,
                   joined_game_id: Optional[str] = None) -> None:
    """actions 真正执行完之后调用，把结果写回 state。
    created_game_ids：{bot_key: 新建成功的 game_id}——如果某个 bot 建房失败
    （比如数据库写入出错），不要把它传进来，跳过的那个 bot 下一轮还会
    排在轮转最前面再试一次，不会被跳过。
    joined_game_id：actions.join 建议的这次"AI加入AI房间"如果真的执行
    成功了，把那个 game_id 传进来；失败了（比如race输给了一个真人，或者
    调用方发现候选人格账号有问题）就传 None——跟 created_game_ids 的
    "只传成功的"是同一个原则。
    """
    close_set = set(actions.close_room_ids)
    state.pending_rooms = [r for r in state.pending_rooms if r.game_id not in close_set]

    for bot_key, game_id in created_game_ids.items():
        state.pending_rooms.append(BotRoom(game_id=game_id, creator_key=bot_key, created_at=now))

    if actions.create_bot_keys:
        # 轮转指针推进到"这一批建房尝试过的下一个"，不管每一个是不是都成功——
        # 失败的那个不会被跳过：它下一轮仍然排在 bot_order 里同样的位置，
        # 只是指针已经往前走了，会在下一整轮轮到它。
        # （更简单也更符合直觉的做法：按 create_bot_keys 的长度直接推进。）
        state.next_bot_index += len(actions.create_bot_keys)

    if joined_game_id is not None:
        state.pending_rooms = [r for r in state.pending_rooms if r.game_id != joined_game_id]
        state.active_ai_vs_ai_game_id = joined_game_id


def mark_room_joined(state: SchedulerState, game_id: str) -> None:
    """有人（真人，或者走 apply_actions(joined_game_id=...) 之外的其他
    路径）加入了某个 bot 建的空房间时调用——只是从"待加入"名单里记账
    移除，不再触发任何全局暂停（v1 里的"全暂停"设计已经取消：真人的
    对局进行与否，不影响 bot 后续建房/AI互相配对的节奏）。"""
    for room in state.pending_rooms:
        if room.game_id == game_id:
            state.pending_rooms.remove(room)
            break


def mark_game_ended(state: SchedulerState, game_id: str) -> None:
    """一盘 bot 参与的对局结束时调用（不管是谁参与的，人机对局结束也可以
    无脑调用，跟这里无关的 game_id 会被安全忽略）——如果这局正是当前
    "唯一在跑的AI vs AI表演赛"，解除占用，下一轮调度就能重新撮合新的
    一场。调用方必须保证"对局结束"的每一条路径都会调用到这里（包括
    棋钟超时判负这种不是靠正常走棋触发结束的情况），否则一旦漏调，
    active_ai_vs_ai_game_id 会永远卡住，后续再也撮合不出新的表演赛。"""
    if state.active_ai_vs_ai_game_id == game_id:
        state.active_ai_vs_ai_game_id = None


if __name__ == "__main__":
    bots = ["kanderson", "anaxagoras", "karspeirsky"]

    # ---- 测试1：从空状态开始，应该一次性建满 MAX_PENDING_ROOMS 个房间 ----
    s = SchedulerState()
    a = decide_actions(s, now=0.0, bot_order=bots, max_pending=3)
    assert a.create_bot_keys == ["kanderson", "anaxagoras", "karspeirsky"], a.create_bot_keys
    assert a.close_room_ids == []
    assert a.join is None, "还没有任何已存在的空房间，不该建议撮合"
    apply_actions(s, a, created_game_ids={"kanderson": "g1", "anaxagoras": "g2", "karspeirsky": "g3"}, now=0.0)
    assert len(s.pending_rooms) == 3
    assert s.next_bot_index == 3
    print("✅ 测试1：初始状态一次建满3个房间，且不会撮合刚建议要建的新房")

    # ---- 测试2：还没超时，不建不关，但应该建议撮合一场 AI vs AI ----
    a2 = decide_actions(s, now=60.0, bot_order=bots, max_pending=3, rng=random.Random(42))
    assert a2.create_bot_keys == [] and a2.close_room_ids == []
    assert a2.join is not None, "已经有3个空房间、没有表演赛在进行，应该建议撮合一场"
    join_game_id, joiner = a2.join
    assert join_game_id in {"g1", "g2", "g3"}
    creator_of_room = next(r.creator_key for r in s.pending_rooms if r.game_id == join_game_id)
    assert joiner != creator_of_room, "不能建议一个bot加入自己建的房间"
    print("✅ 测试2：未超时时不建不关，但会建议撮合AI vs AI（且不会撮合bot加入自己的房间）")

    # ---- 测试2b：只有g1单独超时（模拟g2/g3是之后才建的，创建时间错开）----
    s.pending_rooms[1].created_at = 250.0  # g2 250秒时才建的
    s.pending_rooms[2].created_at = 250.0  # g3 同上
    a2b = decide_actions(s, now=301.0, bot_order=bots, max_pending=3)
    assert a2b.close_room_ids == ["g1"], a2b.close_room_ids  # 只有g1超过300秒
    print("✅ 测试2b：只有真正超时的房间被关（创建时间错开的房间不受影响）")

    # ---- 测试3：第一个房间超时（5分钟），应该关掉它，轮到下一个bot（轮转到kanderson，
    # 因为 next_bot_index=3，3%3=0=kanderson）建新房 ----
    a3 = decide_actions(s, now=301.0, bot_order=bots, max_pending=3)
    assert a3.close_room_ids == ["g1"], a3.close_room_ids
    assert a3.create_bot_keys == ["kanderson"], a3.create_bot_keys
    apply_actions(s, a3, created_game_ids={"kanderson": "g4"}, now=301.0)
    assert {r.game_id for r in s.pending_rooms} == {"g2", "g3", "g4"}
    print("✅ 测试3：超时关房 + 轮转补位")

    # ---- 测试4：真人加入 g2——只是记账移除，不应该影响任何后续的建房/超时/撮合节奏 ----
    mark_room_joined(s, "g2")
    assert {r.game_id for r in s.pending_rooms} == {"g3", "g4"}
    a4 = decide_actions(s, now=301.0, bot_order=bots, max_pending=3)
    assert a4.create_bot_keys == ["anaxagoras"], a4.create_bot_keys  # 照常补位到3个，不受真人加入影响
    assert a4.close_room_ids == []
    print("✅ 测试4：真人加入后【不再全暂停】——其余房间正常计超时/补位")

    # ---- 测试5：AI 真的加入了另一个AI的房间（g3），撮合成功——之后不应该再建议撮合新的一场，
    # 直到这场结束 ----
    apply_actions(s, SchedulerActions(join=("g3", "kanderson")), created_game_ids={}, now=301.0,
                  joined_game_id="g3")
    assert s.active_ai_vs_ai_game_id == "g3"
    assert {r.game_id for r in s.pending_rooms} == {"g4"}, "g3 应该从待加入列表移除"
    a5 = decide_actions(s, now=500.0, bot_order=bots, max_pending=3)
    assert a5.join is None, "已经有一场表演赛在进行，不应该再撮合新的一场"
    assert a5.create_bot_keys, "撮合无关，建房补位应该照常进行"
    print("✅ 测试5：AI互相加入成功后，同一时刻不会撮合第二场，但建房/超时不受影响")

    # ---- 测试6：那场表演赛结束，解除占用，之后又能重新撮合 ----
    mark_game_ended(s, "g3")
    assert s.active_ai_vs_ai_game_id is None
    a6 = decide_actions(s, now=500.0, bot_order=bots, max_pending=3)
    assert a6.join is not None, "表演赛结束后，应该能重新撮合下一场"
    print("✅ 测试6：表演赛结束后解除占用，恢复撮合")

    # ---- 测试7：join_eligible_bots 排除掉正忙的人格——如果只剩一个空闲人格且它正是
    # 房间创建者本人，则不应该撮合（找不到候选加入者）----
    s2 = SchedulerState(pending_rooms=[BotRoom(game_id="gx", creator_key="kanderson", created_at=0.0)])
    a7 = decide_actions(s2, now=10.0, bot_order=bots, max_pending=1,
                        join_eligible_bots=["kanderson"])  # 唯一空闲的就是创建者自己
    assert a7.join is None, "候选加入者只剩房间创建者本人时，不该撮合"
    print("✅ 测试7：join_eligible_bots 排除正忙的人格后，找不到候选人时不会强行撮合")

    print("\nbot_controller.py 冒烟测试通过 ✅")
