"""
movement.py
===========
Arkatana（古战棋）— 走子引擎模块

职责范围：
    1. 提供公共方向常量：DIAGONAL_DIRS（斜线4方向）、STRAIGHT_DIRS（直线4方向）、
       EIGHT_DIRS（米字格8方向）—— 原先定义在 pieces.py 中，现正式迁移至此。
    2. 提供公共走法生成工具函数（原先以私有函数形式散落在 pieces.py 中）：
       - ranged_moves          自由跳跃式移动（不受阻挡，只能落空格）
       - direct_capture_targets 固定方向固定距离的直接吃子
       - screen_capture_targets "隔山打牛"式吃子（弩车、炮塔共用）
       - leap_targets          固定偏移量跳跃走法（轻骑士、重骑士等）
       - sliding_moves         传统滑动走法，遇子即止（攻城塔、凤凰）
    3. 提供 Move 数据结构（一步候选走法的通用表示）
    4. 提供更高层的聚合工具：
       - generate_side_moves   汇总某一方全部棋子当前"几何上可行"的走法
       - is_square_attacked    判断某格是否处于某一方的攻击范围内（供 rules.py 判杀城用）

注意：
    - 本模块只依赖 board.py，不 import pieces.py，避免循环引用。
      generate_side_moves() / is_square_attacked() 通过鸭子类型调用
      piece.side 和 piece.pseudo_moves(board)，不需要知道 Piece 的具体类定义。
    - 这里生成的都是"伪合法走法"（pseudo-legal moves）：只判断几何规则，
      不判断走了之后己方王城会不会因此暴露 —— 那部分过滤逻辑属于 rules.py。

依赖：board.py（坐标系统与几何工具）
被依赖：pieces.py（各棋子类调用这里的公共函数生成自己的走法）
        rules.py / game.py（后续会调用聚合函数做合法性过滤、杀城判断等）
"""

from __future__ import annotations
from typing import NamedTuple

from board import is_valid_coord


# ---------------------------------------------------------------------------
# 公共方向常量
# ---------------------------------------------------------------------------

DIAGONAL_DIRS = [(1, 1), (1, -1), (-1, 1), (-1, -1)]
STRAIGHT_DIRS = [(0, 1), (0, -1), (1, 0), (-1, 0)]
EIGHT_DIRS = DIAGONAL_DIRS + STRAIGHT_DIRS


# ---------------------------------------------------------------------------
# 走法表示
# ---------------------------------------------------------------------------

class Move(NamedTuple):
    """一步候选走法。升变判定不在此处标记，交给 game.py 在走子后统一检查排数。"""
    from_sq: tuple[int, int]
    to_sq: tuple[int, int]
    is_capture: bool


# ---------------------------------------------------------------------------
# 走法生成工具函数
# ---------------------------------------------------------------------------

def ranged_moves(board, origin, directions, max_range) -> list[tuple[int, int]]:
    """
    自由跳跃式移动：在给定方向集合、射程范围内，忽略路径上的所有阻挡，
    只能落在空格上。返回可到达的空格坐标列表。
    用于：弩车、炮塔、大将、战车等"可越子移动"的棋子。
    """
    moves = []
    for dx, dy in directions:
        for dist in range(1, max_range + 1):
            dest = (origin[0] + dx * dist, origin[1] + dy * dist)
            if not is_valid_coord(*dest):
                break
            if board.is_empty(dest):
                moves.append(dest)
    return moves


def direct_capture_targets(board, origin, piece_side, directions, distance=1) -> list[tuple[int, int]]:
    """
    直接吃子：固定方向、固定距离，格子上必须正好站着敌方棋子。
    用于：弩车的斜前方吃子、炮塔的正前方吃子、兵的正前方吃子。
    """
    targets = []
    for dx, dy in directions:
        dest = (origin[0] + dx * distance, origin[1] + dy * distance)
        if not is_valid_coord(*dest):
            continue
        occupant = board.get(dest)
        if occupant is not None and occupant.side != piece_side:
            targets.append(dest)
    return targets


def screen_capture_targets(board, origin, piece_side, directions, max_range) -> list[tuple[int, int]]:
    """
    "隔山打牛"式吃子（弩车、炮塔共用）：
    沿给定方向扫描，路径上遇到的第一个棋子作为"炮架"（screen），
    炮架本身不能被吃；炮架之后（同一方向、仍在射程内）的任意一个棋子，
    无论中间还隔了多少棋子，只要是敌方棋子，都可以被吃掉。
    """
    targets = []
    for dx, dy in directions:
        screen_found = False
        for dist in range(1, max_range + 1):
            dest = (origin[0] + dx * dist, origin[1] + dy * dist)
            if not is_valid_coord(*dest):
                break
            occupant = board.get(dest)
            if occupant is None:
                continue
            if not screen_found:
                screen_found = True
                continue
            if occupant.side != piece_side:
                targets.append(dest)
    return targets


def leap_targets(board, origin, piece_side, offsets) -> tuple[list, list]:
    """
    固定偏移量的跳跃式走法（完全不受路径阻挡影响，只看目标格）。
    用于：轻骑士、重骑士等"目/日"字走法的棋子。
    返回 (可移动的空格列表, 可吃子的目标格列表)。
    """
    move_targets = []
    capture_targets = []
    for dx, dy in offsets:
        dest = (origin[0] + dx, origin[1] + dy)
        if not is_valid_coord(*dest):
            continue
        occupant = board.get(dest)
        if occupant is None:
            move_targets.append(dest)
        elif occupant.side != piece_side:
            capture_targets.append(dest)
    return move_targets, capture_targets


def sliding_moves(board, origin, directions, piece_side, max_range=None) -> tuple[list, list]:
    """
    传统"车/象"式滑动走法：沿方向直线前进，遇到棋子即止步（可吃该子，不能越子）。
    用于：攻城塔（Rook）、凤凰（Phoenix）。
    max_range 为 None 表示不限距离。
    返回 (可移动的空格列表, 可吃子的目标格列表)。
    """
    move_targets = []
    capture_targets = []
    for dx, dy in directions:
        dist = 0
        while True:
            dist += 1
            if max_range is not None and dist > max_range:
                break
            dest = (origin[0] + dx * dist, origin[1] + dy * dist)
            if not is_valid_coord(*dest):
                break
            occupant = board.get(dest)
            if occupant is None:
                move_targets.append(dest)
            else:
                if occupant.side != piece_side:
                    capture_targets.append(dest)
                break
    return move_targets, capture_targets


# ---------------------------------------------------------------------------
# 聚合工具（供 rules.py / game.py 使用）
# ---------------------------------------------------------------------------

def generate_side_moves(board, side) -> list[Move]:
    """
    遍历整个棋盘，汇总某一方所有棋子当前"几何上可行"的走法（伪合法走法）。
    依赖每个棋子自身的 pseudo_moves(board) 方法（鸭子类型，不需要 import pieces.py）。
    """
    moves: list[Move] = []
    for coord in board.occupied_coords():
        piece = board.get(coord)
        if piece.side == side:
            moves.extend(piece.pseudo_moves(board))
    return moves


def moves_from(board, coord) -> list[Move]:
    """获取指定坐标上棋子当前"几何上可行"的走法；若该格为空，返回空列表。"""
    piece = board.get(coord)
    if piece is None:
        return []
    return piece.pseudo_moves(board)


def is_square_attacked(board, coord, by_side) -> bool:
    """
    判断 by_side 一方是否有棋子能在下一步吃到 coord 这个格子
    （即该格当前是否处于 by_side 的攻击范围内）。
    这是纯几何层面的判断，将作为 rules.py 判定"杀城"的基础工具。
    """
    for source_coord in board.occupied_coords():
        piece = board.get(source_coord)
        if piece.side != by_side:
            continue
        for move in piece.pseudo_moves(board):
            if move.is_capture and move.to_sq == coord:
                return True
    return False


# ---------------------------------------------------------------------------
# 简单自检
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    from board import Board, parse_coord

    # 用一个极简的鸭子类型棋子桩（不依赖 pieces.py），保持 movement.py 独立可测
    class _StubPiece:
        def __init__(self, side, position, move_fn):
            self.side = side
            self.position = position
            self._move_fn = move_fn

        def pseudo_moves(self, board):
            return self._move_fn(self, board)

    BLACK, WHITE = "black", "white"

    # -- 测试 ranged_moves：空棋盘上，中心点斜线射程3，应有 4方向 x 3距离 = 12个空格
    board = Board()
    origin = parse_coord("f6")
    dests = ranged_moves(board, origin, DIAGONAL_DIRS, 3)
    assert len(dests) == 12, f"ranged_moves 数量异常: {len(dests)}"

    # -- 测试 screen_capture_targets：炮架 + 目标
    board = Board()
    screen = _StubPiece(WHITE, parse_coord("d4"), lambda s, b: [])
    target = _StubPiece(WHITE, parse_coord("f4"), lambda s, b: [])
    board.set(screen.position, screen)
    board.set(target.position, target)
    origin = parse_coord("a4")
    hits = screen_capture_targets(board, origin, BLACK, STRAIGHT_DIRS, 5)
    assert parse_coord("f4") in hits
    assert parse_coord("d4") not in hits

    # -- 测试 generate_side_moves 与 is_square_attacked
    board = Board()
    attacker = _StubPiece(
        BLACK, parse_coord("a1"),
        lambda s, b: [Move(s.position, parse_coord("a4"), True)]
    )
    victim = _StubPiece(WHITE, parse_coord("a4"), lambda s, b: [])
    board.set(attacker.position, attacker)
    board.set(victim.position, victim)

    black_moves = generate_side_moves(board, BLACK)
    assert len(black_moves) == 1
    assert is_square_attacked(board, parse_coord("a4"), BLACK) is True
    assert is_square_attacked(board, parse_coord("k12"), BLACK) is False

    print("movement.py 自检全部通过 ✅")
