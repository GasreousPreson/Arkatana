"""
game.py
=======
Arkatana（古战棋）— 对局生命周期管理模块

职责范围：
    1. 管理一局棋的完整状态：当前棋盘、当前该谁走、走子历史、对局结果、棋钟
    2. 提供对外核心接口（未来 FastAPI 直接调用这一层，不需要碰
       board/pieces/movement/rules 的细节）：
       - make_move(from, to)       执行一步棋（含合法性校验、升变判定、
                                     轮次切换、胜负判断、重复走子检测、棋钟结算）
       - legal_moves_from(coord)   查询某个棋子当前的合法走法
       - undo()                    悔棋（整局面快照还原，包含棋钟状态）
       - resign(side)              认输
       - start_clocks()            开始计时（由调用方决定"什么时候算对局正式开始"）
       - check_timeout()           检查当前行棋方是否超时，超时则直接判负
    3. 自动处理升变：兵/炮/剑士/战车每步棋后检查是否到达指定排数，是则升变
    4. 自动处理重复走子判定：维护局面签名历史，三次重复触发白方获胜
    5. 棋钟：time_control 为 None 表示无时间限制（例如"线下对练"模式）

依赖：board.py、pieces.py、layout.py、rules.py、notation.py、clock.py
被依赖：main.py（命令行测试）、db.py（存档/复盘）、以及 FastAPI 接口层
"""

from __future__ import annotations
from typing import NamedTuple, Optional

from board import Board, parse_coord, coord_to_str
from pieces import Side, Pawn, Turret, Swordsman, Chariot, promotion_zone, pawn_promotion_zone
from layout import setup_initial_board
from rules import (
    get_legal_moves,
    apply_move,
    clone_board,
    evaluate_game_state,
    GameResult,
    is_threefold_repetition,
    position_signature,
    other_side,
    is_checkmate,
    has_only_throne,
    EndReason,
)
from notation import compute_disambiguation, compute_pawn_disambiguation
from clock import Clock, TimeControl


# ---------------------------------------------------------------------------
# 异常类型
# ---------------------------------------------------------------------------

class IllegalMoveError(Exception):
    """走法不合法（不在当前行棋方的合法走法列表中）"""


class GameOverError(Exception):
    """对局已经结束，不能再走棋"""


# ---------------------------------------------------------------------------
# 走子记录（用于走子历史 / 记谱系统）
# ---------------------------------------------------------------------------

class MoveRecord(NamedTuple):
    side: Side
    piece_type: str
    from_sq: tuple[int, int]
    to_sq: tuple[int, int]
    captured_type: Optional[str]
    promoted: bool                          # 这步棋本身是否触发了升变
    was_already_promoted: bool = False      # 走这步棋之前，这个棋子是否已经处于升变状态
    disambiguation: Optional[str] = None    # 记谱消歧义字符（列字母或排数字），无歧义则为 None
    is_checkmate: bool = False              # 这步棋走完后，对方是否被将死


# ---------------------------------------------------------------------------
# Game 类：对局生命周期管理
# ---------------------------------------------------------------------------

class Game:
    def __init__(self, time_control: Optional[TimeControl] = None):
        self.board: Board = setup_initial_board()
        self.current_side: Side = Side.BLACK  # 黑方先手
        self.result: GameResult = GameResult.ONGOING
        # 对局是怎么结束的（对局进行中为 None）——决定结果文案怎么写
        self.end_reason: EndReason | None = None
        self.move_log: list[MoveRecord] = []

        # 棋钟：time_control 为 None 表示无时间限制（比如"线下对练"模式）。
        # 注意：这里只是创建棋钟，并不会自动开始计时——
        # 什么时候算"对局正式开始、可以开始计时"由调用方决定（调用 start_clocks()）。
        self.clock = Clock(time_control)

        # 局面签名历史，用于三次重复走子判定；初始局面也计入一次
        self._position_history: list = [position_signature(self.board, self.current_side)]

        # 悔棋用的整局面快照栈：每步棋执行前压入一份快照
        self._undo_stack: list[tuple] = []

    # -- 查询接口 -----------------------------------------------------------

    def legal_moves(self):
        """当前行棋方全部合法走法；对局已结束则返回空列表"""
        if self.result != GameResult.ONGOING:
            return []
        return get_legal_moves(self.board, self.current_side)

    def legal_moves_from(self, coord: tuple[int, int]):
        """指定坐标上棋子的合法走法"""
        return [m for m in self.legal_moves() if m.from_sq == coord]

    def is_over(self) -> bool:
        return self.result != GameResult.ONGOING

    def start_clocks(self) -> None:
        """开始计时：标记当前行棋方（通常是对局刚开始时的黑方）开始思考"""
        self.clock.start_turn(self.current_side.value)

    def check_timeout(self) -> bool:
        """
        检查当前行棋方是否已经超时；如果超时，直接结束对局（对方判胜），返回 True。
        这是"惰性"检测——不会主动倒计时触发，而是在每次跟这局对局交互时
        （查询状态、尝试走棋等）顺便检查一次。对局已经结束则不做任何事。
        """
        if self.result != GameResult.ONGOING:
            return False
        if self.clock.is_timeout(self.current_side.value):
            loser = self.current_side
            winner = other_side(loser)
            self.result = GameResult.WHITE_WINS if winner == Side.WHITE else GameResult.BLACK_WINS
            self.end_reason = EndReason.TIMEDOUT
            return True
        return False

    # -- 核心操作：走棋 -----------------------------------------------------

    def make_move(self, from_sq: tuple[int, int], to_sq: tuple[int, int]) -> MoveRecord:
        """
        执行一步棋。会先校验该走法是否在当前行棋方的合法走法列表中，
        然后处理吃子、升变判定、轮次切换、局面历史记录、胜负判断。
        走法不合法则抛出 IllegalMoveError；对局已结束则抛出 GameOverError。
        """
        if self.result != GameResult.ONGOING:
            raise GameOverError("对局已结束，无法继续走棋")

        if self.check_timeout():
            raise GameOverError("对局已结束，无法继续走棋")

        move = self._find_legal_move(from_sq, to_sq)
        if move is None:
            raise IllegalMoveError(
                f"{coord_to_str(*from_sq)} -> {coord_to_str(*to_sq)} 不是合法走法"
            )

        # 走棋前压入快照，供悔棋使用
        self._undo_stack.append(self._snapshot())

        mover_side = self.current_side
        moving_piece = self.board.get(from_sq)
        was_already_promoted = moving_piece.promoted
        if isinstance(moving_piece, Pawn):
            # 兵的记谱规则跟其他棋子是两套独立逻辑，见 compute_pawn_disambiguation
            is_capture_now = self.board.get(to_sq) is not None
            disambiguation = compute_pawn_disambiguation(
                from_sq, to_sq, is_capture_now, was_already_promoted
            )
        else:
            disambiguation = compute_disambiguation(self.board, moving_piece, to_sq)

        captured = apply_move(self.board, move)
        piece = self.board.get(to_sq)
        promoted_now = self._maybe_promote(piece)

        # 结算这一步用掉的思考时间（加上加秒），再把棋钟切给即将行棋的一方
        self.clock.commit_move(mover_side.value)

        # 切换行棋方，再判断新的行棋方是否被将死（供记谱的 "#" 标记使用）
        self.current_side = other_side(mover_side)
        self.clock.start_turn(self.current_side.value)
        checkmate_now = is_checkmate(self.board, self.current_side)

        record = MoveRecord(
            side=mover_side,
            piece_type=type(piece).__name__,
            from_sq=from_sq,
            to_sq=to_sq,
            captured_type=(type(captured).__name__ if captured is not None else None),
            promoted=promoted_now,
            was_already_promoted=was_already_promoted,
            disambiguation=disambiguation,
            is_checkmate=checkmate_now,
        )
        self.move_log.append(record)

        # 记录新局面签名（签名中包含"接下来轮到谁走"，与 rules.py 的约定一致）
        self._position_history.append(position_signature(self.board, self.current_side))

        self._update_result()
        return record

    def make_move_str(self, from_str: str, to_str: str) -> MoveRecord:
        """便捷方法：直接用 'd5' -> 'd7' 这种字符串坐标走棋（方便命令行测试）"""
        return self.make_move(parse_coord(from_str), parse_coord(to_str))

    # -- 悔棋 / 认输 ----------------------------------------------------------

    def undo(self) -> None:
        """悔棋：还原到上一步棋之前的整局面快照（包含棋钟状态）"""
        if not self._undo_stack:
            raise IllegalMoveError("没有可悔的棋")
        board, side, result, pos_history, clock, end_reason = self._undo_stack.pop()
        self.board = board
        self.current_side = side
        self.result = result
        self.end_reason = end_reason
        self._position_history = pos_history
        self.clock = clock
        if self.move_log:
            self.move_log.pop()

    def resign(self, side: Side) -> None:
        """认输：指定一方直接判负；对局已结束则不做任何事"""
        if self.result != GameResult.ONGOING:
            return
        winner = other_side(side)
        self.result = GameResult.WHITE_WINS if winner == Side.WHITE else GameResult.BLACK_WINS
        self.end_reason = EndReason.RESIGNED

    # -- 内部工具 -----------------------------------------------------------

    def _find_legal_move(self, from_sq, to_sq):
        for move in self.legal_moves():
            if move.from_sq == from_sq and move.to_sq == to_sq:
                return move
        return None

    def _maybe_promote(self, piece) -> bool:
        """兵/炮/剑士/战车到达指定排数后自动升变（强制触发，无法拒绝），返回本步是否触发了升变"""
        if piece.promoted:
            return False

        if isinstance(piece, Pawn):
            zone = pawn_promotion_zone(piece.side)
        elif isinstance(piece, (Turret, Swordsman, Chariot)):
            zone = promotion_zone(piece.side)
        else:
            return False

        if piece.position[1] in zone:
            piece.promoted = True
            return True
        return False

    def _update_result(self) -> None:
        """走完一步棋后，依次检查重复走子、杀城/残局，更新 self.result 与 end_reason"""
        if is_threefold_repetition(self._position_history):
            # 规则明确规定：三次重复走子固定判白方（后手）获胜，不是平局
            self.result = GameResult.WHITE_WINS
            self.end_reason = EndReason.STALEMATE
            return

        self.result = evaluate_game_state(self.board, self.current_side)
        if self.result == GameResult.ONGOING:
            self.end_reason = None
        elif has_only_throne(self.board, self.current_side):
            # 只剩王城、无子可动 —— 困毙
            self.end_reason = EndReason.STALEMATE
        else:
            self.end_reason = EndReason.CHECKMATE

    def _snapshot(self):
        return (
            clone_board(self.board),
            self.current_side,
            self.result,
            list(self._position_history),
            self.clock.clone(),
            self.end_reason,
        )

    def __str__(self) -> str:
        turn = "黑方" if self.current_side == Side.BLACK else "白方"
        status = f"当前行棋方: {turn} | 状态: {self.result.value}"
        if not self.clock.is_unlimited:
            black_left = self.clock.time_left("black")
            white_left = self.clock.time_left("white")
            status += f" | 黑方剩余: {black_left:.0f}秒 | 白方剩余: {white_left:.0f}秒"
        return f"{status}\n{self.board}"


# ---------------------------------------------------------------------------
# 简单自检
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    from pieces import Pawn

    game = Game()

    # 1) 初始状态检查
    assert game.current_side == Side.BLACK
    assert game.result == GameResult.ONGOING
    assert sum(1 for _ in game.board.occupied_coords()) == 58
    assert len(game.legal_moves()) > 0

    # 2) 走棋+升变测试：黑兵 d5 冲两步到 d7（新规则下这一步不升变，d8 才是黑兵的升变排）
    d5, d7, d8 = parse_coord("d5"), parse_coord("d7"), parse_coord("d8")
    assert game.legal_moves_from(d5), "d5 的兵应该有合法走法"
    record = game.make_move(d5, d7)
    assert record.promoted is False, "d7 不是黑兵的升变排，这一步不应该升变"
    assert game.current_side == Side.WHITE, "走完一步后应切换到白方"

    game.make_move(parse_coord("a8"), parse_coord("a7"))  # 白方随便应一手，避开 d 线

    assert game.current_side == Side.BLACK
    record2 = game.make_move(d7, d8)
    assert record2.promoted is True, "d8 是黑兵的升变排，这一步应该升变"
    assert record2.captured_type == "Pawn", "d8 原本有白兵，这一步应该吃掉它"
    moved_piece = game.board.get(d8)
    assert isinstance(moved_piece, Pawn) and moved_piece.promoted is True

    # 3) 非法走法应抛出异常
    try:
        game.make_move(parse_coord("a1"), parse_coord("a1"))
        raise AssertionError("非法走法竟然没有报错")
    except IllegalMoveError:
        pass

    # 4) 悔棋应完整还原（包括升变状态、以及被吃掉的白兵）
    game.undo()
    assert game.current_side == Side.BLACK
    restored_piece = game.board.get(d7)
    assert isinstance(restored_piece, Pawn) and restored_piece.promoted is False
    restored_white_pawn = game.board.get(d8)
    assert isinstance(restored_white_pawn, Pawn) and restored_white_pawn.side == Side.WHITE, \
        "悔棋应该把被吃掉的白兵还原回d8"
    assert len(game.move_log) == 2

    # 5) 认输
    game2 = Game()
    game2.resign(Side.BLACK)
    assert game2.result == GameResult.WHITE_WINS
    assert game2.is_over() is True
    try:
        game2.make_move(parse_coord("a5"), parse_coord("a7"))
        raise AssertionError("对局已结束，走棋竟然没有报错")
    except GameOverError:
        pass

    # 6) 消歧义字段的集成测试：开局黑方 c2/j2 两个轻骑士都能走到 f3
    game3 = Game()
    hussar_move = game3.make_move_str("c2", "f3")
    assert hussar_move.disambiguation == "c", (
        f"c2 轻骑士走到 f3 应该标注消歧义列字母 'c'（因为 j2 也能到），实际: {hussar_move.disambiguation!r}"
    )
    assert hussar_move.was_already_promoted is False
    assert hussar_move.is_checkmate is False

    # 7) 棋钟集成测试：正常对局的计时与结算
    from datetime import datetime, timedelta, timezone
    from clock import TimeControl

    timed_game = Game(TimeControl(minutes_per_side=10, increment_seconds=5))
    assert timed_game.clock.time_left("black") == 600.0
    timed_game.start_clocks()
    assert timed_game.clock.active_side == "black"

    # 手动把"这一步开始思考的时间"往前拨30秒，模拟黑方思考了30秒后走棋
    timed_game.clock.turn_started_at = datetime.now(timezone.utc) - timedelta(seconds=30)
    timed_game.make_move_str("d5", "d6")
    # 600 - 30(用掉) + 5(加秒) ≈ 575，允许有几秒的执行耗时误差
    assert 570 <= timed_game.clock.remaining["black"] <= 576
    assert timed_game.clock.active_side == "white", "走完棋后应该切给白方计时"

    # 8) 超时判负测试
    timeout_game = Game(TimeControl(minutes_per_side=1, increment_seconds=0))
    timeout_game.start_clocks()
    # 把黑方"开始思考"的时间拨到2分钟前，远超1分钟的时间控制，制造超时
    timeout_game.clock.turn_started_at = datetime.now(timezone.utc) - timedelta(minutes=2)
    assert timeout_game.check_timeout() is True
    assert timeout_game.result == GameResult.WHITE_WINS, "黑方超时应该判白方胜"

    try:
        timeout_game.make_move_str("d5", "d6")
        raise AssertionError("对局已经因超时结束，不应该还能走棋")
    except GameOverError:
        pass

    # 9) 悔棋应该同时还原棋钟状态
    undo_game = Game(TimeControl(minutes_per_side=10, increment_seconds=5))
    undo_game.start_clocks()
    remaining_before = undo_game.clock.remaining["black"]
    undo_game.make_move_str("d5", "d6")
    assert undo_game.clock.remaining["black"] != remaining_before, "走完一步棋钟应该已经变化"
    undo_game.undo()
    assert undo_game.clock.remaining["black"] == remaining_before, "悔棋应该把棋钟也还原回去"
    assert undo_game.clock.active_side == "black", "悔棋后应该重新轮到黑方计时"

    print("game.py 自检全部通过 ✅")
    print()
    print(Game())  # 打印一局新对局的初始状态，直观确认
