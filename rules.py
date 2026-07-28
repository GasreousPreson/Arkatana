"""
rules.py
========
Arkatana（古战棋）— 全局规则裁判模块

职责范围：
    1. 杀城判定（对应传统象棋的"将死"）：
       - is_in_check          王城当前是否处于被攻击状态
       - count_attackers      当前有几个敌方棋子正在攻击王城（用于判断双将）
       - is_checkmate         王城是否已陷入"无法化解"的死局
    2. 合法走法过滤：
       - get_legal_moves      在 movement.py 生成的"伪合法走法"基础上，
                               排除那些走完之后会让己方王城陷入被攻击状态的走法
                               （沿用类似传统棋类"不能送将"的惯例，详见文末说明）
    3. 残局判负：
       - has_only_throne      某一方是否只剩王城（子力全灭）
    4. 重复走子规则：
       - position_signature   生成当前局面的可哈希签名（用于记录历史局面）
       - is_threefold_repetition  给定局面历史，判断是否已出现三次重复
       （注：重复走子的历史记录本身由 game.py 维护，本模块只提供纯函数判断逻辑）
    5. 综合判定：
       - evaluate_game_state  综合杀城、残局两类负局条件，给出当前对局结果

依赖：board.py、pieces.py（Side、Throne 等）、movement.py（走法生成引擎）
被依赖：game.py（对局流程会在每步棋后调用本模块判断胜负、过滤合法走法）
"""

from __future__ import annotations
from collections import Counter
from enum import Enum

from board import Board
from pieces import Side, Throne
from movement import Move, generate_side_moves, is_square_attacked


# ---------------------------------------------------------------------------
# 阵营工具
# ---------------------------------------------------------------------------

def other_side(side: Side) -> Side:
    return Side.WHITE if side == Side.BLACK else Side.BLACK


def find_throne(board: Board, side: Side) -> tuple[int, int] | None:
    """找到某一方王城所在坐标；正常情况下必定存在（王城不会被真正吃掉，
    游戏会在"杀城"判定成立的瞬间结束，不会走到王城真的从棋盘上消失）。"""
    for coord in board.occupied_coords():
        piece = board.get(coord)
        if piece.side == side and isinstance(piece, Throne):
            return coord
    return None


# ---------------------------------------------------------------------------
# 棋盘克隆与试走（供合法性检验使用，不影响真实对局状态）
# ---------------------------------------------------------------------------

def clone_board(board: Board) -> Board:
    """
    深拷贝棋盘：不仅拷贝格子结构，也用 piece.clone() 逐个克隆棋子实例，
    确保在克隆棋盘上试走不会污染原棋盘上的棋子对象。
    """
    new_board = Board()
    for coord in board.occupied_coords():
        piece = board.get(coord)
        cloned = piece.clone()
        cloned.position = coord
        new_board.set(coord, cloned)
    return new_board


def apply_move(board: Board, move: Move):
    """
    在给定棋盘上原地执行一步走法：搬运棋子、更新其 position/has_moved。
    通常配合 clone_board() 先克隆再执行，用于"试走"而不影响真实棋盘。
    返回被吃掉的棋子（如果没有吃子则为 None）。
    """
    piece = board.get(move.from_sq)
    captured = board.move_piece(move.from_sq, move.to_sq)
    piece.position = move.to_sq
    piece.has_moved = True
    return captured


# ---------------------------------------------------------------------------
# 杀城判定
# ---------------------------------------------------------------------------

def is_in_check(board: Board, side: Side) -> bool:
    """某一方的王城当前是否处于被攻击状态"""
    throne_pos = find_throne(board, side)
    if throne_pos is None:
        return False
    return is_square_attacked(board, throne_pos, other_side(side))


def count_attackers(board: Board, side: Side) -> int:
    """当前有多少个敌方棋子正在攻击某一方的王城（用于判断"双将"）"""
    throne_pos = find_throne(board, side)
    if throne_pos is None:
        return 0
    attacker_side = other_side(side)
    count = 0
    for coord in board.occupied_coords():
        piece = board.get(coord)
        if piece.side != attacker_side:
            continue
        for move in piece.pseudo_moves(board):
            if move.is_capture and move.to_sq == throne_pos:
                count += 1
                break  # 同一个棋子只算一次攻击来源
    return count


def get_legal_moves(board: Board, side: Side) -> list[Move]:
    """
    在 movement.py 生成的伪合法走法基础上，过滤掉那些走完之后
    会让己方王城处于被攻击状态的走法（无论这个状态本身是否可解——
    只要走完那一刻王城被攻击，这步棋本身就不允许）。
    """
    legal = []
    for move in generate_side_moves(board, side):
        trial = clone_board(board)
        apply_move(trial, move)
        if not is_in_check(trial, side):
            legal.append(move)
    return legal


def is_checkmate(board: Board, side: Side) -> bool:
    """
    杀城判定：
    - 若同时有 >=2 个敌方棋子攻击王城，直接判定"双将即杀"，不再检验是否有解
    - 若恰好 1 个棋子攻击王城，检验是否存在任何一步棋能解除攻击；
      不存在则判死
    - 若无攻击，则不成立
    """
    attackers = count_attackers(board, side)
    if attackers >= 2:
        return True  # 双将即杀
    if attackers == 1:
        return len(get_legal_moves(board, side)) == 0
    return False


# ---------------------------------------------------------------------------
# 残局判负
# ---------------------------------------------------------------------------

def has_only_throne(board: Board, side: Side) -> bool:
    """某一方是否子力全灭，只剩王城"""
    pieces = [board.get(c) for c in board.occupied_coords() if board.get(c).side == side]
    return len(pieces) == 1 and isinstance(pieces[0], Throne)


# ---------------------------------------------------------------------------
# 重复走子规则（历史记录由 game.py 维护，这里只提供纯函数判断）
# ---------------------------------------------------------------------------

def position_signature(board: Board, side_to_move: Side):
    """
    生成当前局面的可哈希签名，用于重复走子检测。
    包含棋盘上每个棋子的坐标、类型、阵营、升变状态，以及当前轮到谁走。
    """
    pieces_repr = tuple(sorted(
        (coord, board.get(coord).__class__.__name__, board.get(coord).side.value, board.get(coord).promoted)
        for coord in board.occupied_coords()
    ))
    return (pieces_repr, side_to_move.value)


def is_threefold_repetition(position_history: list) -> bool:
    """给定局面签名历史列表，判断是否有任意一个局面已经出现过三次或以上"""
    counts = Counter(position_history)
    return any(c >= 3 for c in counts.values())


# 规则明确规定：重复走子判负的结果固定偏向白方（后手），不是平局
REPETITION_WINNER = Side.WHITE


# ---------------------------------------------------------------------------
# 综合判定
# ---------------------------------------------------------------------------

class GameResult(Enum):
    ONGOING = "ongoing"
    BLACK_WINS = "black_wins"
    WHITE_WINS = "white_wins"


def _winner_result(losing_side: Side) -> GameResult:
    return GameResult.WHITE_WINS if losing_side == Side.BLACK else GameResult.BLACK_WINS


def evaluate_game_state(board: Board, side_to_move: Side) -> GameResult:
    """
    综合杀城、残局两类判负条件，评估当前该 side_to_move 走棋方是否已经败北。
    重复走子规则不在此处判断（需要历史记录，由 game.py 结合 is_threefold_repetition 处理）。
    """
    if has_only_throne(board, side_to_move):
        return _winner_result(side_to_move)
    if is_checkmate(board, side_to_move):
        return _winner_result(side_to_move)
    return GameResult.ONGOING


# ---------------------------------------------------------------------------
# 简单自检
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    from board import parse_coord
    from pieces import Rook, Phoenix, Pawn, Hussar

    # -- 测试1：简单将军（无阻挡直线攻击）
    board = Board()
    throne = Throne(Side.BLACK, parse_coord("f1"))
    rook = Rook(Side.WHITE, parse_coord("f5"))
    board.set(throne.position, throne)
    board.set(rook.position, rook)
    assert is_in_check(board, Side.BLACK) is True
    assert count_attackers(board, Side.BLACK) == 1
    assert is_in_check(board, Side.WHITE) is False

    # -- 测试2：杀城（唯一攻击者 + 无解）
    board2 = Board()
    throne2 = Throne(Side.BLACK, parse_coord("f1"))
    rook2 = Rook(Side.WHITE, parse_coord("f2"))       # 贴脸攻击，无法阻挡
    far_pawn = Pawn(Side.BLACK, parse_coord("a5"))    # 远处棋子，救不了场
    board2.set(throne2.position, throne2)
    board2.set(rook2.position, rook2)
    board2.set(far_pawn.position, far_pawn)
    assert is_checkmate(board2, Side.BLACK) is True
    assert has_only_throne(board2, Side.BLACK) is False  # 还有一个兵，纯粹是杀城导致的判负

    # -- 测试3：双将即杀
    board3 = Board()
    throne3 = Throne(Side.BLACK, parse_coord("f1"))
    rook3 = Rook(Side.WHITE, parse_coord("f2"))
    phoenix3 = Phoenix(Side.WHITE, parse_coord("d3"))  # 与 rook3 同时攻击 f1
    board3.set(throne3.position, throne3)
    board3.set(rook3.position, rook3)
    board3.set(phoenix3.position, phoenix3)
    assert count_attackers(board3, Side.BLACK) == 2
    assert is_checkmate(board3, Side.BLACK) is True

    # -- 测试4：合法走法过滤（"钉住"效应：一动就送将）
    board4 = Board()
    throne4 = Throne(Side.BLACK, parse_coord("f1"))
    rook4 = Rook(Side.WHITE, parse_coord("f5"))
    hussar4 = Hussar(Side.BLACK, parse_coord("f3"))    # 挡在攻击线上
    other_piece = Pawn(Side.BLACK, parse_coord("a5"))  # 无关棋子，走法应不受影响
    board4.set(throne4.position, throne4)
    board4.set(rook4.position, rook4)
    board4.set(hussar4.position, hussar4)
    board4.set(other_piece.position, other_piece)
    assert is_in_check(board4, Side.BLACK) is False  # 目前被挡住，尚未被将军
    legal4 = get_legal_moves(board4, Side.BLACK)
    hussar_legal_moves = [m for m in legal4 if m.from_sq == hussar4.position]
    assert len(hussar_legal_moves) == 0, "被钉住的轻骑士不应该有任何合法走法"
    pawn_legal_moves = [m for m in legal4 if m.from_sq == other_piece.position]
    assert len(pawn_legal_moves) > 0, "无关棋子的合法走法不应被误伤"

    # -- 测试5：残局判负
    board5 = Board()
    throne5 = Throne(Side.BLACK, parse_coord("f1"))
    board5.set(throne5.position, throne5)
    assert has_only_throne(board5, Side.BLACK) is True
    assert evaluate_game_state(board5, Side.BLACK) == GameResult.WHITE_WINS

    # -- 测试6：重复走子判定
    sig_a = ("posA", Side.BLACK.value)
    sig_b = ("posB", Side.WHITE.value)
    history = [sig_a, sig_b, sig_a, sig_b, sig_a]  # sig_a 出现3次
    assert is_threefold_repetition(history) is True
    assert is_threefold_repetition([sig_a, sig_b, sig_a]) is False

    print("rules.py 自检全部通过 ✅")
