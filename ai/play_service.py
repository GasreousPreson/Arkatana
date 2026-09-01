"""
ai/play_service.py
====================
Play against AI 的后端服务层——api.py 只需要认识这一个文件里的东西，
不需要直接接触 engine_bridge.py / search.py 里的任何类型。

设计要点：
    - AI 的搜索（find_best_move_timed / find_persona_move）是纯 CPU 密集型
      同步代码，没有任何 I/O。在 FastAPI 的 async 事件循环里直接调用会把
      整个进程卡住那么久——这段时间处理不了任何别的请求，包括别的对局的
      心跳、WebSocket 消息。api.py 那边调用 choose_ai_move()/
      choose_persona_move() 时必须用 run_in_threadpool()（Starlette 自带，
      api.py 已经在用）包一层丢进线程池，不能直接 await 或者同步调用。

    - 已知限制：Python 有 GIL，线程池能避免"卡住事件循环"，但没法让多盘
      AI 对局的搜索真正跑满多核——如果同时有好几盘棋都在思考，会互相抢
      CPU 时间片，每一盘实际变慢。2026-08 接入的墙钟时间预算
      （find_best_move_timed/find_persona_move）缓解的是"这一步会不会
      无限期算下去"，不是这个 GIL 限制本身——时间预算是按墙钟算的，
      CPU 被分走了，同样的搜索量自然要花更久才碰到 deadline，深度会
      搜得更浅，但不会导致真正的死锁/无响应。这一步（换成进程池
      ProcessPoolExecutor 让搜索真正并行）先不解决，同样是"真遇到并发
      瓶颈了再切换，现在切换是过度设计"。

    - 输入输出都用网站后端"权威引擎"的类型（Board 对象、pieces.Side、
      (col,row) 坐标元组）——这一层内部转换成 engine_bridge.Position，
      api.py 完全不需要认识 engine_bridge 里的任何东西。
"""

from __future__ import annotations

import random

import engine_bridge as eb
import opening_book
from search import SearchResult, find_best_move_timed, find_persona_move, DEFAULT_TIME_BUDGET_SECONDS

# 难度 -> 搜索深度封顶。2026-08 性能改版之后，实际搜到几层不再是"深度3
# 就一定要等几十秒"这种固定账——find_best_move_timed 会在下面对应的时间
# 预算内做迭代加深，预算不够深就停在浅一点的深度，预算富余就搜到这个封顶
# 为止，见 search.py 顶部关于这次改版的说明。三档先按这个来，以后要加
# 更强档位，加一对 depth/time_budget 配置即可，不需要改调用方的任何代码。
DIFFICULTY_DEPTH = {
    "easy": 1,
    "medium": 2,
    "hard": 3,
}

# 对应的单步思考时间预算（秒）——不是"平均要花这么久"，是"最多愿意等
# 这么久，超过就用已经搜完的那一层"。数值挑得比较保守：这是人类在等的
# 实时交互场景，"hard"给10秒是"宁可偶尔搜不到depth3也不要让人等太久"
# 和"尽量让hard名副其实"之间的权衡，不是从性能基准精确反推出来的，
# 后续如果实际体验偏慢/偏弱，直接改这几个数字就行，不涉及任何其他改动。
DIFFICULTY_TIME_BUDGET_SECONDS = {
    "easy": 2.0,
    "medium": 5.0,
    "hard": 10.0,
}
DEFAULT_DIFFICULTY = "medium"

# AI 人格系统统一用这个时间预算——人格对局的时间控制都很宽裕（最短的
# Sangomanti 也有30分钟基础时间+120秒加秒），不像 Play against AI 那样
# 有真人在对面干等，稍微多给一点预算换更深的搜索、更强的棋力，划算。
# 9个人格目前都是同一个预算，以后如果想让某个人格"想得更久/更快"
# （比如 style_note 里提到的棋风差异延伸到用时习惯），personas.py 加一个
# 字段、这里从 persona 上读就行，不需要改 choose_persona_move 的调用方。
PERSONA_TIME_BUDGET_SECONDS = DEFAULT_TIME_BUDGET_SECONDS + 2.0  # 10秒

DIFFICULTY_LABELS = {
    "easy": "简单",
    "medium": "中等",
    "hard": "困难",
}


def is_valid_difficulty(difficulty: str) -> bool:
    return difficulty in DIFFICULTY_DEPTH


def ai_display_name(difficulty: str) -> str:
    """存进 GAME_PLAYERS 的"玩家名"——直接当成这局对局里 AI 那一方的
    display name 使用（细节见 api.py 里的说明：这样能顺手复用现有的
    走棋权限校验和前端玩家名渲染逻辑，不用为 AI 专门加一整套机制）。"""
    label = DIFFICULTY_LABELS.get(difficulty, difficulty)
    return f"🤖 AI（{label}）"


def _side_to_bridge(side) -> int:
    """side 可以是权威引擎的 pieces.Side 枚举，也兼容直接传 'black'/'white' 字符串。"""
    value = side.value if hasattr(side, "value") else side
    return eb.BLACK if value == "black" else eb.WHITE


def authoritative_board_to_position(board, side_to_move) -> eb.Position:
    """权威引擎的 Board 对象（每格一个棋子对象）-> engine_bridge 的
    Position（扁平数组）。这是 ai/ 目录唯一需要认识 pieces.py 具体类型
    的地方——只在这里做一次转换，转换完之后的搜索过程完全在 engine_bridge
    的世界里跑，不再回头碰权威引擎的对象。"""
    pos = eb.Position()
    for coord in board.occupied_coords():
        piece = board.get(coord)
        piece_type = eb.NOTATION_TO_TYPE[piece.notation]
        side = _side_to_bridge(piece.side)
        pos.place(coord, piece_type, side, has_moved=piece.has_moved, promoted=piece.promoted)
    pos.side_to_move = _side_to_bridge(side_to_move)
    return pos


def choose_ai_move(board, side_to_move, difficulty: str = DEFAULT_DIFFICULTY) -> SearchResult:
    """给定权威引擎的当前局面，搜索一步棋，返回的 SearchResult.best_move
    里的 from_sq/to_sq 直接是 (col,row) 元组，可以原样传给
    game.make_move(from_sq, to_sq) ——两边坐标系统完全一致，不需要再转换。

    这是纯 CPU 密集型同步函数。调用方（api.py）必须用 run_in_threadpool()
    包一层，绝对不能直接在 async 函数里同步调用或者 await 这个函数本身
    （run_in_threadpool 才是"扔进线程池"，直接调用还是会卡住事件循环）。

    2026-08 改版：内部走的是 find_best_move_timed（迭代加深 + 墙钟时间
    预算），不再是固定深度、想搜多久搜多久的 find_best_move——这是"AI
    走棋速度接近0"问题的直接解法，保证这个函数无论局面多复杂都大约在
    DIFFICULTY_TIME_BUDGET_SECONDS[difficulty] 秒内返回。
    """
    depth = DIFFICULTY_DEPTH.get(difficulty, DIFFICULTY_DEPTH[DEFAULT_DIFFICULTY])
    time_budget = DIFFICULTY_TIME_BUDGET_SECONDS.get(difficulty, DEFAULT_TIME_BUDGET_SECONDS)
    pos = authoritative_board_to_position(board, side_to_move)
    side = _side_to_bridge(side_to_move)

    # 第一步棋直接查开局库，不搜索——开局阶段搜索最贵（100+种合法走法）
    # 收益却最低，而且引擎在子力全在家的开局局面里静态评估给不出什么
    # 有效信号，经常选出不合棋理的招。查表是0秒，而且必然是人类验证过
    # 的好棋；按权重随机挑还能保证每盘棋开局都不一样，不会像以前那样
    # 永远只走雁门关。
    if _is_initial_position(pos):
        move, name, notation = opening_book.pick_opening_move(side)
        return SearchResult(move, 0.0, 1, 0.0)

    return find_best_move_timed(pos, side, max_depth=depth, time_budget=time_budget)


def _is_initial_position(pos: eb.Position) -> bool:
    """是不是一步没走的开局局面（用来决定要不要查开局库）。
    直接跟标准初始摆位逐格比对，比数步数可靠——不依赖调用方传步数进来。"""
    initial = eb.Position.initial()
    return pos.types == initial.types and pos.sides == initial.sides


def choose_persona_move(board, side_to_move, persona_key: str,
                         rng: "random.Random | None" = None) -> SearchResult:
    """给"AI 人格"系统用的出招入口（区别于上面 choose_ai_move 那个给
    Play against AI 难度选择器用的）——同样是纯 CPU 密集型同步函数，
    调用方一样必须用 run_in_threadpool() 包一层。

    第一步棋查这个人格自己的开局偏好表（personas.py 里的 opening_only，
    None 就是完整开局库）；之后每一步都用 search.find_persona_move()，
    这个人格的 weights/precision/prefer_aggressive_ties 全部生效。

    ⚠️ 目前还没接深度开局准备树（opening_prep.py 的 PrepNode/BookWalker）——
    Kanderson/Anaxagoras 这些有过"如果对手...那么..."准备的人格暂时只有
    第一步的开局库权重在生效，后续几步已经是正常搜索，不是按人工准备走的
    （personas.py 顶部的说明写了原因：那些密集记谱文本有几处读法拿不准，
    没有贸然编码）。等 DSL 重新录入、opening_prep.py 接上之后，这个函数
    需要跟着改成"先问 BookWalker 有没有准备，没有才退回搜索"，接口已经
    设计好了，到时候只改这一个函数内部，不影响调用方。

    2026-08 改版：find_persona_move 内部现在走的是时间预算版的搜索
    （find_best_move_timed / find_move_distribution_timed，见 search.py
    顶部说明），用 PERSONA_TIME_BUDGET_SECONDS 统一控制"最多愿意为这一步
    想多久"——这是"人格AI基本不走棋"问题的直接解法。
    """
    from personas import get_persona

    persona = get_persona(persona_key)
    pos = authoritative_board_to_position(board, side_to_move)
    side = _side_to_bridge(side_to_move)

    if _is_initial_position(pos):
        move, name, notation = opening_book.pick_opening_move(
            side, rng, only_names=persona.opening_only
        )
        return SearchResult(move, 0.0, 1, 0.0)

    return find_persona_move(
        pos, side, persona.depth, weights=persona.weights,
        precision=persona.precision, prefer_aggressive_ties=persona.prefer_aggressive_ties,
        rng=rng, time_budget=PERSONA_TIME_BUDGET_SECONDS,
    )


if __name__ == "__main__":
    # 冒烟测试：造一个假的"权威引擎 Board"替身（只实现 choose_ai_move 用得到
    # 的接口），确认整条链路（Board -> Position -> 搜索 -> 落子坐标）能跑通。
    # 真正跟权威引擎对拍的交叉验证在 ai/tests/test_engine_bridge.py 里。
    from engine_bridge import Position as _P

    class _FakePiece:
        def __init__(self, notation, side_value, has_moved=False, promoted=False):
            self.notation = notation
            self.side = type("S", (), {"value": side_value})()
            self.has_moved = has_moved
            self.promoted = promoted

    class _FakeBoard:
        def __init__(self, pos: _P):
            self._pos = pos

        def occupied_coords(self):
            return list(self._pos.occupied_coords())

        def get(self, coord):
            idx = eb.sq_index(*coord)
            t = self._pos.types[idx]
            notation = eb.TYPE_TO_NOTATION[t]
            side_value = "black" if self._pos.sides[idx] == eb.BLACK else "white"
            promoted = bool(self._pos.flags[idx] & eb.PROMOTED)
            has_moved = bool(self._pos.flags[idx] & eb.HAS_MOVED)
            return _FakePiece(notation, side_value, has_moved, promoted)

    fake_board = _FakeBoard(_P.initial())
    result = choose_ai_move(fake_board, "black", "easy")
    assert result.best_move is not None
    print("play_service.py 冒烟测试通过 ✅  搜索结果:", result)
