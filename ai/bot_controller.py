"""
ai/bot_controller.py
======================
AI 自主建房/轮转/超时/全暂停——这一层只做"决策"，不碰数据库/WebSocket，
所以能在完全脱离网站后端的情况下单独测试（api.py 那边只需要：读一遍当前
状态 -> 调这里的纯函数拿到"这一轮该做什么" -> 真去执行数据库/广播这些
有副作用的操作）。这样分层的原因很直接：调度器这种"每隔几秒跑一次、
状态会持续演变"的逻辑最容易出的错就是"某个边界条件没考虑到"，
纯函数可以把这些边界条件一条条写成单元测试跑很多遍，不用真的连着
数据库和真实时间反复试。

已确认的规则：
    - lobby 里同时最多 MAX_PENDING_ROOMS 个"AI已建但没人加入"的空房间
    - AI 按固定顺序轮流建房
    - 每个空房间超过 ROOM_TIMEOUT_SECONDS（5分钟）还没人加入，自动关闭，
      轮到下一个 AI 建房
    - 只要有一盘"AI 参与、真人已经加入"的对局正在进行，其他所有 AI 的
      建房动作全部暂停，直到那盘结束

⚠️ 还没做的：AI 主动加入别的 AI 空房间（构成 AI vs AI 表演赛给人观战）。
    这是用户最初设想里的一部分，但目前只确认了"5分钟超时+全暂停"这两条，
    没有确认"超时之后是直接关房，还是改成让另一个AI加入"、"多久出现一次
    AI vs AI"这些具体参数——现在的实现严格按已确认的规则来（超时就关房，
    轮到下一个AI建新房），AI-vs-AI 观战是下一层要单独设计的功能，不是
    忘了做，是故意先不猜。

依赖：无（纯 Python，不依赖 ai/ 目录其他文件，也不依赖网站后端）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


MAX_PENDING_ROOMS = 3
ROOM_TIMEOUT_SECONDS = 5 * 60


@dataclass
class BotRoom:
    game_id: str
    creator_key: str      # personas.py 里的人格 key，比如 "kanderson"
    created_at: float      # unix 时间戳
    joined: bool = False   # 有没有人（真人）加入了


@dataclass
class SchedulerState:
    """调度器需要记住的全部状态。api.py 那边负责在每次 tick 之间把这份状态
    持久化下来（可以就是几个模块级全局变量，不需要专门建数据库表——
    这些状态丢了大不了重新走一轮轮转，不是需要长期保留的数据）。"""
    pending_rooms: list[BotRoom] = field(default_factory=list)
    live_bot_game_ids: set[str] = field(default_factory=set)  # 正在进行、
    # 有真人已经加入的bot对局——只要这个集合非空，就是"全暂停"状态
    next_bot_index: int = 0


@dataclass
class SchedulerActions:
    """一次 tick 该执行的动作，全部是"建议"，真正的数据库/WebSocket 操作
    由 api.py 去做，做完之后再把结果同步回 SchedulerState。"""
    close_room_ids: list[str] = field(default_factory=list)
    create_bot_keys: list[str] = field(default_factory=list)


def decide_actions(state: SchedulerState, now: float, bot_order: list[str],
                    max_pending: int = MAX_PENDING_ROOMS,
                    timeout_seconds: float = ROOM_TIMEOUT_SECONDS) -> SchedulerActions:
    """核心决策函数——传入当前状态和"现在几点"，返回这一轮该做的事。
    不修改 state 本身（除了下面单独说明的 next_bot_index 推进，那个是
    "预支"轮转指针，方便调用方知道"如果真的建了这几个房，下一次该从谁
    开始"，但 api.py 应该把 create_bot_keys 实际建房成功之后的最终指针
    位置写回 state，不能假设这里返回的一定会全部成功执行）。
    """
    actions = SchedulerActions()

    if not bot_order:
        return actions

    if state.live_bot_game_ids:
        # 全暂停：有真人已经在跟某个 bot 下棋，这段时间不再建新房，
        # 也不清超时——已有的空房间维持原样，等真人那盘结束了再继续算超时。
        return actions

    for room in state.pending_rooms:
        if not room.joined and (now - room.created_at) >= timeout_seconds:
            actions.close_room_ids.append(room.game_id)

    still_pending_count = sum(
        1 for r in state.pending_rooms if r.game_id not in actions.close_room_ids
    )

    idx = state.next_bot_index
    while still_pending_count + len(actions.create_bot_keys) < max_pending:
        actions.create_bot_keys.append(bot_order[idx % len(bot_order)])
        idx += 1

    return actions


def apply_actions(state: SchedulerState, actions: SchedulerActions,
                   created_game_ids: dict[str, str], now: float) -> None:
    """actions 真正执行完之后调用，把结果写回 state。
    created_game_ids：{bot_key: 新建成功的 game_id}——如果某个 bot 建房失败
    （比如数据库写入出错），不要把它传进来，跳过的那个 bot 下一轮还会
    排在轮转最前面再试一次，不会被跳过。
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


def mark_room_joined(state: SchedulerState, game_id: str) -> None:
    """真人加入了某个 bot 建的空房间时调用——从"待加入"名单移到"进行中"，
    并触发全暂停。"""
    for room in state.pending_rooms:
        if room.game_id == game_id:
            room.joined = True
            state.pending_rooms.remove(room)
            break
    state.live_bot_game_ids.add(game_id)


def mark_game_ended(state: SchedulerState, game_id: str) -> None:
    """一盘 bot 参与的对局结束时调用（不管是谁参与的）——解除全暂停
    （如果没有别的 live bot game 了的话）。"""
    state.live_bot_game_ids.discard(game_id)


if __name__ == "__main__":
    bots = ["kanderson", "anaxagoras", "karspeirsky"]

    # ---- 测试1：从空状态开始，应该一次性建满 MAX_PENDING_ROOMS 个房间 ----
    s = SchedulerState()
    a = decide_actions(s, now=0.0, bot_order=bots, max_pending=3)
    assert a.create_bot_keys == ["kanderson", "anaxagoras", "karspeirsky"], a.create_bot_keys
    assert a.close_room_ids == []
    apply_actions(s, a, created_game_ids={"kanderson": "g1", "anaxagoras": "g2", "karspeirsky": "g3"}, now=0.0)
    assert len(s.pending_rooms) == 3
    assert s.next_bot_index == 3
    print("✅ 测试1：初始状态一次建满3个房间")

    # ---- 测试2：还没超时，不应该有任何动作 ----
    a2 = decide_actions(s, now=60.0, bot_order=bots, max_pending=3)
    assert a2.create_bot_keys == [] and a2.close_room_ids == []
    print("✅ 测试2：未超时时不建不关")

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

    # ---- 测试4：真人加入 g2，触发全暂停——即使 g3 也超时了，也不应该有任何新动作 ----
    mark_room_joined(s, "g2")
    assert "g2" in s.live_bot_game_ids
    assert len(s.pending_rooms) == 2  # g2 从待加入列表移出去了
    a4 = decide_actions(s, now=99999.0, bot_order=bots, max_pending=3)
    assert a4.close_room_ids == [] and a4.create_bot_keys == [], "全暂停期间不应该有任何动作"
    print("✅ 测试4：真人加入后全暂停，其余房间不再计超时也不补位")

    # ---- 测试5：那盘结束，恢复调度，该超时的（g3, g4）都应该被清掉并补位 ----
    mark_game_ended(s, "g2")
    assert not s.live_bot_game_ids
    a5 = decide_actions(s, now=99999.0, bot_order=bots, max_pending=3)
    assert set(a5.close_room_ids) == {"g3", "g4"}
    assert len(a5.create_bot_keys) == 3
    print("✅ 测试5：全暂停解除后恢复正常调度")

    print("\nbot_controller.py 冒烟测试通过 ✅")
