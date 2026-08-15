"""
ai/play_service.py
====================
Play against AI 的后端服务层——api.py 只需要认识这一个文件里的东西，
不需要直接接触 engine_bridge.py / search.py 里的任何类型。

设计要点：
    - AI 的搜索（find_best_move）是纯 CPU 密集型同步代码，没有任何 I/O。
      在 FastAPI 的 async 事件循环里直接调用会把整个进程卡住那么久——
      这段时间处理不了任何别的请求，包括别的对局的心跳、WebSocket 消息。
      api.py 那边调用 choose_ai_move() 时必须用 run_in_threadpool()
      （Starlette 自带，api.py 已经在用）包一层丢进线程池，不能直接 await
      或者同步调用。

    - 已知限制：Python 有 GIL，线程池能避免"卡住事件循环"，但没法让多盘
      AI 对局的搜索真正跑满多核——如果同时有好几盘棋都在用 hard 难度
      思考，会互相抢 CPU 时间片，每一盘实际变慢。这一步先不解决
      （解决办法是换成进程池 ProcessPoolExecutor，但那样每次搜索都要
      重新序列化局面、启动成本更高，只有真遇到并发瓶颈了再切换，
      现在切换是过度设计）。

    - 输入输出都用网站后端"权威引擎"的类型（Board 对象、pieces.Side、
      (col,row) 坐标元组）——这一层内部转换成 engine_bridge.Position，
      api.py 完全不需要认识 engine_bridge 里的任何东西。
"""

from __future__ import annotations

import engine_bridge as eb
from search import SearchResult, find_best_move

# 难度 -> 搜索深度。深度2目前稳定在1秒以内，深度3中局约7秒（开局最慢约
# 30秒）——具体数字见 ai/README.md 的性能记录。三档先按这个来，
# 以后要加更强档位（比如超时预算+迭代加深）再扩展这张表，
# 不需要改调用方的任何代码。
DIFFICULTY_DEPTH = {
    "easy": 1,
    "medium": 2,
    "hard": 3,
}
DEFAULT_DIFFICULTY = "medium"

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
    """
    depth = DIFFICULTY_DEPTH.get(difficulty, DIFFICULTY_DEPTH[DEFAULT_DIFFICULTY])
    pos = authoritative_board_to_position(board, side_to_move)
    side = _side_to_bridge(side_to_move)
    return find_best_move(pos, side, depth)


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
