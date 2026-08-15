"""
ai/engine_bridge.py
====================
Arkatana（古战棋）— AI 专用轻量规则引擎

为什么需要这个文件（而不是直接 import 网站后端的 board.py/pieces.py）：
    网站后端那套引擎是"每个棋子一个 Python 对象、每个格子存对象引用"的写法，
    设计目标是"代码清晰、职责分离"，服务几十毫秒一次的 HTTP/WebSocket 请求完全够用。
    但 minimax 搜索一秒钟要生成、试走、撤销几十万次局面，Python 的对象属性查找
    和方法调用本身就有明显开销，这种规模下会成为瓶颈。

    所以这里换一套表示：整个棋盘拍平成三个定长数组（types/sides/flags），
    棋子=数组里的一个整数，不再是对象；局面克隆=切片复制数组，不再是逐个
    clone() 棋子对象。规则逻辑本身（每种棋子怎么走、怎么判杀城、怎么判升变）
    跟后端代码逐条对应，不做任何"顺手改进"——任何规则改动都必须先改网站后端，
    这边跟着同步改，两边规则来源必须永远只有一个。

    正确性由 ai/tests/test_engine_bridge.py 保证：随机对局 + 随机局面，
    逐步对比这里的走法生成结果和后端权威引擎是否完全一致。跑通之前，
    上面的地基不算稳，后面第2步开始写搜索之前必须先跑绿。

依赖：不依赖网站后端任何模块（自成一体，因此可以脱离网站单独跑批量自对弈）。
"""

from __future__ import annotations

import random
from typing import NamedTuple, Optional


# ---------------------------------------------------------------------------
# 坐标系统（对应 board.py）
# ---------------------------------------------------------------------------

COLUMNS = "abcdefghjkl"          # 11 列，跳过字母 i（与后端 board.py 完全一致）
NUM_COLS = len(COLUMNS)          # 11
MIN_ROW = 1
MAX_ROW = 12
NUM_ROWS = MAX_ROW - MIN_ROW + 1  # 12
NUM_SQUARES = NUM_COLS * NUM_ROWS  # 132


def col_to_index(col: str) -> int:
    col = col.lower()
    if col not in COLUMNS:
        raise ValueError(f"非法列坐标: {col!r}")
    return COLUMNS.index(col)


def index_to_col(index: int) -> str:
    if not (0 <= index < NUM_COLS):
        raise ValueError(f"列索引越界: {index}")
    return COLUMNS[index]


def is_valid_coord(col: int, row: int) -> bool:
    return 0 <= col < NUM_COLS and MIN_ROW <= row <= MAX_ROW


def parse_coord(text: str) -> tuple[int, int]:
    """外部记谱 -> (col_index, row)，例："d4" -> (3, 4)"""
    text = text.strip().lower()
    if len(text) < 2 or not text[1:].isdigit():
        raise ValueError(f"非法坐标格式: {text!r}")
    col = col_to_index(text[0])
    row = int(text[1:])
    if not is_valid_coord(col, row):
        raise ValueError(f"坐标越界: {text!r}")
    return col, row


def coord_to_str(col: int, row: int) -> str:
    if not is_valid_coord(col, row):
        raise ValueError(f"坐标越界: col={col}, row={row}")
    return f"{index_to_col(col)}{row}"


def sq_index(col: int, row: int) -> int:
    """(col,row) -> 扁平数组下标 0~131。row 优先，方便按排整段切片调试打印。"""
    return (row - MIN_ROW) * NUM_COLS + col


def index_to_coord(idx: int) -> tuple[int, int]:
    row = MIN_ROW + idx // NUM_COLS
    col = idx % NUM_COLS
    return col, row


# ---------------------------------------------------------------------------
# 棋子类型编码（对应 pieces.py 的 notation）
# ---------------------------------------------------------------------------

EMPTY = 0
PAWN = 1
BALLISTA = 2
TURRET = 3
ARES = 4
HUSSAR = 5
KNIGHT = 6
ROOK = 7
PHOENIX = 8
SWORDSMAN = 9
CHARIOT = 10
THRONE = 11

# 记谱缩写 <-> 类型编码，跟后端 pieces.py 的 notation 字段逐一对应
NOTATION_TO_TYPE = {
    "": PAWN, "B": BALLISTA, "T": TURRET, "A": ARES, "H": HUSSAR,
    "N": KNIGHT, "R": ROOK, "P": PHOENIX, "S": SWORDSMAN, "C": CHARIOT,
    "TH": THRONE,
}
TYPE_TO_NOTATION = {v: k for k, v in NOTATION_TO_TYPE.items()}

TYPE_NAMES = {
    PAWN: "兵", BALLISTA: "弩车", TURRET: "炮塔", ARES: "大将", HUSSAR: "轻骑士",
    KNIGHT: "重骑士", ROOK: "攻城塔", PHOENIX: "凤凰", SWORDSMAN: "剑士",
    CHARIOT: "战车", THRONE: "王城",
}

# 会升变的棋子类型（兵单独用 pawn_promotion_row，其余三种共用 promotion_zone）
ZONE_PROMOTABLE_TYPES = {TURRET, SWORDSMAN, CHARIOT}


# ---------------------------------------------------------------------------
# 阵营
# ---------------------------------------------------------------------------

BLACK = 0
WHITE = 1
FORWARD = {BLACK: 1, WHITE: -1}


def other_side(side: int) -> int:
    return WHITE if side == BLACK else BLACK


def promotion_zone_contains(side: int, row: int) -> bool:
    """炮/剑士/战车共用的升变排数范围：黑方7~12排，白方1~6排。"""
    return (7 <= row <= 12) if side == BLACK else (1 <= row <= 6)


def pawn_promotion_row(side: int) -> int:
    """兵专属升变排：黑方第8排，白方第5排（单一排数，不是范围）。"""
    return 8 if side == BLACK else 5


# ---------------------------------------------------------------------------
# 每格状态标志位
# ---------------------------------------------------------------------------

HAS_MOVED = 1
PROMOTED = 2


# ---------------------------------------------------------------------------
# 走法表示（字段顺序、命名跟后端 movement.Move 完全一致，
# 两边生成的 NamedTuple 可以直接互相 == 比较，交叉验证不需要额外转换）
# ---------------------------------------------------------------------------

class Move(NamedTuple):
    from_sq: tuple[int, int]
    to_sq: tuple[int, int]
    is_capture: bool


# ---------------------------------------------------------------------------
# 局面容器：三个定长数组，不再是"格子->对象"的字典
# ---------------------------------------------------------------------------

class Position:
    __slots__ = ("types", "sides", "flags", "side_to_move")

    def __init__(self) -> None:
        self.types = [EMPTY] * NUM_SQUARES
        self.sides = [BLACK] * NUM_SQUARES
        self.flags = [0] * NUM_SQUARES
        self.side_to_move = BLACK

    # -- 基础读写（坐标一律用 (col,row) 元组，跟后端保持同一套"外部接口"）--------

    def get_type(self, coord: tuple[int, int]) -> int:
        return self.types[sq_index(*coord)]

    def get_side(self, coord: tuple[int, int]) -> int:
        return self.sides[sq_index(*coord)]

    def get_flags(self, coord: tuple[int, int]) -> int:
        return self.flags[sq_index(*coord)]

    def is_empty(self, coord: tuple[int, int]) -> bool:
        return self.types[sq_index(*coord)] == EMPTY

    def has_moved(self, coord: tuple[int, int]) -> bool:
        return bool(self.flags[sq_index(*coord)] & HAS_MOVED)

    def is_promoted(self, coord: tuple[int, int]) -> bool:
        return bool(self.flags[sq_index(*coord)] & PROMOTED)

    def place(self, coord: tuple[int, int], piece_type: int, side: int,
              has_moved: bool = False, promoted: bool = False) -> None:
        idx = sq_index(*coord)
        self.types[idx] = piece_type
        self.sides[idx] = side
        self.flags[idx] = (HAS_MOVED if has_moved else 0) | (PROMOTED if promoted else 0)

    def clear(self, coord: tuple[int, int]) -> None:
        idx = sq_index(*coord)
        self.types[idx] = EMPTY
        self.sides[idx] = BLACK
        self.flags[idx] = 0

    def occupied_coords(self):
        for idx in range(NUM_SQUARES):
            if self.types[idx] != EMPTY:
                yield index_to_coord(idx)

    def clone(self) -> "Position":
        """浅拷贝三个数组即可——不再需要像后端那样逐个 clone() 棋子对象。"""
        p = Position.__new__(Position)
        p.types = self.types[:]
        p.sides = self.sides[:]
        p.flags = self.flags[:]
        p.side_to_move = self.side_to_move
        return p

    # -- 初始摆位（对应 layout.py）-------------------------------------------

    _BLACK_LAYOUT = {
        THRONE: ["f1"],
        PAWN: [f"{c}5" for c in COLUMNS],
        ARES: ["f4"],
        BALLISTA: ["d4", "h4"],
        TURRET: ["a4", "l4"],
        ROOK: ["a1", "l1"],
        CHARIOT: ["b1", "k1"],
        PHOENIX: ["c1", "j1"],
        KNIGHT: ["d1", "h1"],
        SWORDSMAN: ["e1", "g1"],
        HUSSAR: ["c2", "j2"],
    }
    _WHITE_LAYOUT = {
        THRONE: ["f12"],
        PAWN: [f"{c}8" for c in COLUMNS],
        ARES: ["f9"],
        BALLISTA: ["d9", "h9"],
        TURRET: ["a9", "l9"],
        ROOK: ["a12", "l12"],
        CHARIOT: ["b12", "k12"],
        PHOENIX: ["c12", "j12"],
        KNIGHT: ["d12", "h12"],
        SWORDSMAN: ["e12", "g12"],
        HUSSAR: ["c11", "j11"],
    }

    @classmethod
    def initial(cls) -> "Position":
        pos = cls()
        for side, layout in ((BLACK, cls._BLACK_LAYOUT), (WHITE, cls._WHITE_LAYOUT)):
            for piece_type, coord_strs in layout.items():
                for s in coord_strs:
                    pos.place(parse_coord(s), piece_type, side)
        pos.side_to_move = BLACK
        return pos

    # -- 与前端棋盘 JSON 互转（learn-lessons.js / game.html 用的那套格式）------
    # 前端形状： {"d4": {"notation": "B", "side": "black", "promoted": False}, ...}

    def to_frontend_board(self) -> dict:
        board = {}
        for coord in self.occupied_coords():
            idx = sq_index(*coord)
            t = self.types[idx]
            entry = {
                "notation": TYPE_TO_NOTATION[t],
                "side": "black" if self.sides[idx] == BLACK else "white",
            }
            if self.flags[idx] & PROMOTED:
                entry["promoted"] = True
            board[coord_to_str(*coord)] = entry
        return board

    @classmethod
    def from_frontend_board(cls, board: dict, side_to_move: str = "black") -> "Position":
        pos = cls()
        for sq_str, entry in board.items():
            if entry.get("notation") == "STAR":
                continue  # 星星是 Learn 模块专用的教学标记，不是真实对局棋子
            coord = parse_coord(sq_str)
            piece_type = NOTATION_TO_TYPE[entry["notation"]]
            side = BLACK if entry.get("side") == "black" else WHITE
            pos.place(
                coord, piece_type, side,
                has_moved=bool(entry.get("hasMoved", True)),
                promoted=bool(entry.get("promoted", False)),
            )
        pos.side_to_move = BLACK if side_to_move == "black" else WHITE
        return pos


# ---------------------------------------------------------------------------
# 方向常量（对应 movement.py）
# ---------------------------------------------------------------------------

DIAGONAL_DIRS = [(1, 1), (1, -1), (-1, 1), (-1, -1)]
STRAIGHT_DIRS = [(0, 1), (0, -1), (1, 0), (-1, 0)]
EIGHT_DIRS = DIAGONAL_DIRS + STRAIGHT_DIRS

_HUSSAR_OFFSETS = [
    (3, 1), (3, -1), (-3, 1), (-3, -1),
    (1, 3), (1, -3), (-1, 3), (-1, -3),
    (3, 0), (-3, 0), (0, 3), (0, -3),
]
_KNIGHT_OFFSETS = [
    (2, 1), (2, -1), (-2, 1), (-2, -1),
    (1, 2), (1, -2), (-1, 2), (-1, -2),
    (3, 1), (3, -1), (-3, 1), (-3, -1),
    (1, 3), (1, -3), (-1, 3), (-1, -3),
]


# ---------------------------------------------------------------------------
# 走法生成工具函数（逐条对应 movement.py 的同名函数，语义必须逐字一致）
# ---------------------------------------------------------------------------

def ranged_moves(pos: Position, origin, directions, max_range) -> list[tuple[int, int]]:
    """自由跳跃式移动：忽略路径阻挡，只能落空格。"""
    moves = []
    ox, oy = origin
    for dx, dy in directions:
        for dist in range(1, max_range + 1):
            dest = (ox + dx * dist, oy + dy * dist)
            if not is_valid_coord(*dest):
                break
            if pos.is_empty(dest):
                moves.append(dest)
    return moves


def direct_capture_targets(pos: Position, origin, side, directions, distance=1) -> list[tuple[int, int]]:
    """固定方向、固定距离的直接吃子。"""
    targets = []
    ox, oy = origin
    for dx, dy in directions:
        dest = (ox + dx * distance, oy + dy * distance)
        if not is_valid_coord(*dest):
            continue
        idx = sq_index(*dest)
        if pos.types[idx] != EMPTY and pos.sides[idx] != side:
            targets.append(dest)
    return targets


def screen_capture_targets(pos: Position, origin, side, directions, max_range) -> list[tuple[int, int]]:
    """隔山打牛：第一个遇到的棋子当炮架（不分敌我，本身不能被吃），
    炮架之后射程内的任意敌方棋子都可以被吃（可以跳过己方棋子继续找）。"""
    targets = []
    ox, oy = origin
    for dx, dy in directions:
        screen_found = False
        for dist in range(1, max_range + 1):
            dest = (ox + dx * dist, oy + dy * dist)
            if not is_valid_coord(*dest):
                break
            idx = sq_index(*dest)
            if pos.types[idx] == EMPTY:
                continue
            if not screen_found:
                screen_found = True
                continue
            if pos.sides[idx] != side:
                targets.append(dest)
    return targets


def leap_targets(pos: Position, origin, side, offsets) -> tuple[list, list]:
    """固定偏移跳跃（轻骑/重骑），完全不受路径阻挡影响。"""
    move_targets, capture_targets = [], []
    ox, oy = origin
    for dx, dy in offsets:
        dest = (ox + dx, oy + dy)
        if not is_valid_coord(*dest):
            continue
        idx = sq_index(*dest)
        if pos.types[idx] == EMPTY:
            move_targets.append(dest)
        elif pos.sides[idx] != side:
            capture_targets.append(dest)
    return move_targets, capture_targets


def sliding_moves(pos: Position, origin, directions, side, max_range=None) -> tuple[list, list]:
    """传统车/象式滑动：遇子即止（可吃该子，不能越子）。"""
    move_targets, capture_targets = [], []
    ox, oy = origin
    for dx, dy in directions:
        dist = 0
        while True:
            dist += 1
            if max_range is not None and dist > max_range:
                break
            dest = (ox + dx * dist, oy + dy * dist)
            if not is_valid_coord(*dest):
                break
            idx = sq_index(*dest)
            if pos.types[idx] == EMPTY:
                move_targets.append(dest)
            else:
                if pos.sides[idx] != side:
                    capture_targets.append(dest)
                break
    return move_targets, capture_targets


# ---------------------------------------------------------------------------
# 各棋子走法生成（逐条对应 pieces.py 各个类的 pseudo_moves）
# ---------------------------------------------------------------------------

def _pawn_moves(pos, origin, side, flags) -> list[Move]:
    fwd = FORWARD[side]
    moves: list[Move] = []
    ox, oy = origin

    if flags & PROMOTED:
        for dx, dy in [(0, fwd), (1, fwd), (-1, fwd), (1, 0), (-1, 0)]:
            dest = (ox + dx, oy + dy)
            if not is_valid_coord(*dest):
                continue
            idx = sq_index(*dest)
            if pos.types[idx] == EMPTY:
                moves.append(Move(origin, dest, False))
            elif pos.sides[idx] != side:
                moves.append(Move(origin, dest, True))
        return moves

    one_step = (ox, oy + fwd)
    if not is_valid_coord(*one_step):
        return moves
    idx1 = sq_index(*one_step)
    if pos.types[idx1] == EMPTY:
        moves.append(Move(origin, one_step, False))
        if not (flags & HAS_MOVED):
            two_step = (ox, oy + fwd * 2)
            if is_valid_coord(*two_step) and pos.is_empty(two_step):
                moves.append(Move(origin, two_step, False))
    elif pos.sides[idx1] != side:
        moves.append(Move(origin, one_step, True))
    return moves


def _ballista_moves(pos, origin, side, flags) -> list[Move]:
    moves = [Move(origin, d, False) for d in ranged_moves(pos, origin, DIAGONAL_DIRS, 4)]
    fwd = FORWARD[side]
    fdiag = [(1, fwd), (-1, fwd)]
    moves += [Move(origin, d, True) for d in direct_capture_targets(pos, origin, side, fdiag, 1)]
    moves += [Move(origin, d, True) for d in screen_capture_targets(pos, origin, side, DIAGONAL_DIRS, 4)]
    return moves


_TURRET_H_DIRS = [(1, 0), (-1, 0)]
_TURRET_V_DIRS = [(0, 1), (0, -1)]
_TURRET_BASE_RANGE = 4
_TURRET_UPGRADED_RANGE = 5
_TURRET_FIRST_MOVE_H_RANGE = 5


def _turret_moves(pos, origin, side, flags) -> list[Move]:
    promoted = bool(flags & PROMOTED)
    has_moved = bool(flags & HAS_MOVED)
    max_range = _TURRET_UPGRADED_RANGE if promoted else _TURRET_BASE_RANGE
    h_range = _TURRET_FIRST_MOVE_H_RANGE if not has_moved else max_range
    v_range = max_range

    moves = [Move(origin, d, False) for d in ranged_moves(pos, origin, _TURRET_H_DIRS, h_range)]
    moves += [Move(origin, d, False) for d in ranged_moves(pos, origin, _TURRET_V_DIRS, v_range)]

    fwd = FORWARD[side]
    moves += [Move(origin, d, True) for d in direct_capture_targets(pos, origin, side, [(0, fwd)], 1)]

    moves += [Move(origin, d, True) for d in screen_capture_targets(pos, origin, side, _TURRET_H_DIRS, h_range)]
    moves += [Move(origin, d, True) for d in screen_capture_targets(pos, origin, side, _TURRET_V_DIRS, v_range)]
    return moves


def _ares_moves(pos, origin, side, flags) -> list[Move]:
    """大将：米字格2格，可越子移动/吃子（不需要炮架，只要射程内是敌方就能吃）。"""
    max_range = 2
    moves = [Move(origin, d, False) for d in ranged_moves(pos, origin, EIGHT_DIRS, max_range)]
    ox, oy = origin
    for dx, dy in EIGHT_DIRS:
        for dist in range(1, max_range + 1):
            dest = (ox + dx * dist, oy + dy * dist)
            if not is_valid_coord(*dest):
                break
            idx = sq_index(*dest)
            if pos.types[idx] != EMPTY and pos.sides[idx] != side:
                moves.append(Move(origin, dest, True))
    return moves


def _hussar_moves(pos, origin, side, flags) -> list[Move]:
    mv, cap = leap_targets(pos, origin, side, _HUSSAR_OFFSETS)
    return [Move(origin, d, False) for d in mv] + [Move(origin, d, True) for d in cap]


def _knight_moves(pos, origin, side, flags) -> list[Move]:
    mv, cap = leap_targets(pos, origin, side, _KNIGHT_OFFSETS)
    return [Move(origin, d, False) for d in mv] + [Move(origin, d, True) for d in cap]


def _rook_moves(pos, origin, side, flags) -> list[Move]:
    mv, cap = sliding_moves(pos, origin, STRAIGHT_DIRS, side)
    return [Move(origin, d, False) for d in mv] + [Move(origin, d, True) for d in cap]


def _phoenix_moves(pos, origin, side, flags) -> list[Move]:
    mv, cap = sliding_moves(pos, origin, DIAGONAL_DIRS, side)
    return [Move(origin, d, False) for d in mv] + [Move(origin, d, True) for d in cap]


def _swordsman_moves(pos, origin, side, flags) -> list[Move]:
    fwd = FORWARD[side]
    ox, oy = origin
    moves: list[Move] = []

    if flags & PROMOTED:
        for dx, dy in EIGHT_DIRS:
            dest = (ox + dx * 2, oy + dy * 2)
            if not is_valid_coord(*dest):
                continue
            idx = sq_index(*dest)
            if pos.types[idx] == EMPTY:
                moves.append(Move(origin, dest, False))
            elif pos.sides[idx] != side:
                moves.append(Move(origin, dest, True))
        for dx, dy in DIAGONAL_DIRS:
            dest = (ox + dx, oy + dy)
            if is_valid_coord(*dest) and pos.is_empty(dest):
                moves.append(Move(origin, dest, False))
        return moves

    for dx, dy in [(0, fwd), (1, fwd), (-1, fwd)]:
        dest = (ox + dx * 2, oy + dy * 2)
        if not is_valid_coord(*dest):
            continue
        idx = sq_index(*dest)
        if pos.types[idx] == EMPTY:
            moves.append(Move(origin, dest, False))
        elif pos.sides[idx] != side:
            moves.append(Move(origin, dest, True))

    for dx, dy in [(1, fwd), (-1, fwd)]:
        dest = (ox + dx, oy + dy)
        if is_valid_coord(*dest) and pos.is_empty(dest):
            moves.append(Move(origin, dest, False))
    return moves


def _chariot_moves(pos, origin, side, flags) -> list[Move]:
    distances = (2, 3, 4) if (flags & PROMOTED) else (2, 3)
    moves = []
    ox, oy = origin
    for dx, dy in STRAIGHT_DIRS:
        for dist in distances:
            dest = (ox + dx * dist, oy + dy * dist)
            if not is_valid_coord(*dest):
                continue
            idx = sq_index(*dest)
            if pos.types[idx] == EMPTY:
                moves.append(Move(origin, dest, False))
            elif pos.sides[idx] != side:
                moves.append(Move(origin, dest, True))
    return moves


def _throne_moves(pos, origin, side, flags) -> list[Move]:
    return []


_MOVE_GENERATORS = {
    PAWN: _pawn_moves, BALLISTA: _ballista_moves, TURRET: _turret_moves,
    ARES: _ares_moves, HUSSAR: _hussar_moves, KNIGHT: _knight_moves,
    ROOK: _rook_moves, PHOENIX: _phoenix_moves, SWORDSMAN: _swordsman_moves,
    CHARIOT: _chariot_moves, THRONE: _throne_moves,
}


def pseudo_moves(pos: Position, origin: tuple[int, int]) -> list[Move]:
    idx = sq_index(*origin)
    t = pos.types[idx]
    if t == EMPTY:
        return []
    return _MOVE_GENERATORS[t](pos, origin, pos.sides[idx], pos.flags[idx])


def generate_side_moves(pos: Position, side: int) -> list[Move]:
    moves: list[Move] = []
    for coord in pos.occupied_coords():
        if pos.sides[sq_index(*coord)] == side:
            moves.extend(pseudo_moves(pos, coord))
    return moves


def is_square_attacked(pos: Position, coord: tuple[int, int], by_side: int) -> bool:
    for src in pos.occupied_coords():
        idx = sq_index(*src)
        if pos.sides[idx] != by_side:
            continue
        for mv in pseudo_moves(pos, src):
            if mv.is_capture and mv.to_sq == coord:
                return True
    return False


# ---------------------------------------------------------------------------
# 局面规则（对应 rules.py + game.py 的升变部分）
# ---------------------------------------------------------------------------

def find_throne(pos: Position, side: int) -> Optional[tuple[int, int]]:
    idx = side  # placeholder to keep lints quiet; real lookup below
    for coord in pos.occupied_coords():
        i = sq_index(*coord)
        if pos.types[i] == THRONE and pos.sides[i] == side:
            return coord
    return None


def _bare_apply_move(pos: Position, move: Move) -> Optional[tuple[int, int, int]]:
    """只搬棋子、置 has_moved，不处理升变——对应 rules.apply_move()。
    专给 get_legal_moves() 的"试走看会不会被将军"用，跟后端行为逐字对应。
    返回被吃掉棋子的 (type, side, flags)，没吃子则 None。"""
    fi, ti = sq_index(*move.from_sq), sq_index(*move.to_sq)
    captured = None
    if pos.types[ti] != EMPTY:
        captured = (pos.types[ti], pos.sides[ti], pos.flags[ti])
    pos.types[ti] = pos.types[fi]
    pos.sides[ti] = pos.sides[fi]
    pos.flags[ti] = pos.flags[fi] | HAS_MOVED
    pos.types[fi] = EMPTY
    pos.sides[fi] = BLACK
    pos.flags[fi] = 0
    return captured


def apply_move(pos: Position, move: Move) -> Optional[tuple[int, int, int]]:
    """搬棋子 + 自动升变——对应 game.Game.make_move() 里 apply_move + _maybe_promote
    这一整套组合效果（AI 搜索/自对弈只关心"走完一步之后局面长什么样"，
    不需要棋钟/记谱/悔棋历史那些跟胜负判断无关的簿记）。"""
    captured = _bare_apply_move(pos, move)
    ti = sq_index(*move.to_sq)
    t = pos.types[ti]
    if not (pos.flags[ti] & PROMOTED):
        promote = False
        if t == PAWN:
            promote = move.to_sq[1] == pawn_promotion_row(pos.sides[ti])
        elif t in ZONE_PROMOTABLE_TYPES:
            promote = promotion_zone_contains(pos.sides[ti], move.to_sq[1])
        if promote:
            pos.flags[ti] |= PROMOTED
    return captured


def is_in_check(pos: Position, side: int) -> bool:
    throne = find_throne(pos, side)
    if throne is None:
        return False
    return is_square_attacked(pos, throne, other_side(side))


def count_attackers(pos: Position, side: int) -> int:
    throne = find_throne(pos, side)
    if throne is None:
        return 0
    attacker_side = other_side(side)
    count = 0
    for src in pos.occupied_coords():
        if pos.sides[sq_index(*src)] != attacker_side:
            continue
        for mv in pseudo_moves(pos, src):
            if mv.is_capture and mv.to_sq == throne:
                count += 1
                break
    return count


def get_legal_moves(pos: Position, side: int) -> list[Move]:
    legal = []
    for move in generate_side_moves(pos, side):
        trial = pos.clone()
        _bare_apply_move(trial, move)
        if not is_in_check(trial, side):
            legal.append(move)
    return legal


def is_checkmate(pos: Position, side: int) -> bool:
    attackers = count_attackers(pos, side)
    if attackers >= 2:
        return True
    if attackers == 1:
        return len(get_legal_moves(pos, side)) == 0
    return False


def has_only_throne(pos: Position, side: int) -> bool:
    count = 0
    only_throne = True
    for coord in pos.occupied_coords():
        idx = sq_index(*coord)
        if pos.sides[idx] != side:
            continue
        count += 1
        if pos.types[idx] != THRONE:
            only_throne = False
    return count == 1 and only_throne


# ---------------------------------------------------------------------------
# 局面签名 / Zobrist 哈希（重复走子判定 + 未来开局库按局面查询用）
# ---------------------------------------------------------------------------

def position_signature(pos: Position, side_to_move: int):
    """跟 rules.position_signature 等价的可哈希签名：每个棋子的坐标/类型/阵营/
    升变状态 + 轮到谁走。用于精确的三次重复判定（不能用容易碰撞的近似哈希）。"""
    pieces_repr = tuple(sorted(
        (coord, pos.types[sq_index(*coord)], pos.sides[sq_index(*coord)],
         bool(pos.flags[sq_index(*coord)] & PROMOTED))
        for coord in pos.occupied_coords()
    ))
    return (pieces_repr, side_to_move)


def is_threefold_repetition(position_history: list) -> bool:
    counts: dict = {}
    for sig in position_history:
        counts[sig] = counts.get(sig, 0) + 1
        if counts[sig] >= 3:
            return True
    return False


# Zobrist：给开局库用的"局面 -> 64位整数"哈希，允许碰撞（key 冲突极小概率事件，
# 真正判重复走子必须用上面的 position_signature，不能用这个近似哈希）。
_rng = random.Random(20260815)  # 固定种子，保证每次生成的表完全一样，不会因为
                                 # 训练机重启就导致旧对局库的 hash 全部对不上
_ZOBRIST_PIECE = [
    [[_rng.getrandbits(64) for _ in range(2)] for _ in range(len(TYPE_TO_NOTATION) + 1)]
    for _ in range(NUM_SQUARES)
]  # [square][piece_type][side]
_ZOBRIST_PROMOTED = [_rng.getrandbits(64) for _ in range(NUM_SQUARES)]
_ZOBRIST_SIDE_TO_MOVE = _rng.getrandbits(64)


def zobrist_hash(pos: Position) -> int:
    h = 0
    for idx in range(NUM_SQUARES):
        t = pos.types[idx]
        if t == EMPTY:
            continue
        h ^= _ZOBRIST_PIECE[idx][t][pos.sides[idx]]
        if pos.flags[idx] & PROMOTED:
            h ^= _ZOBRIST_PROMOTED[idx]
    if pos.side_to_move == WHITE:
        h ^= _ZOBRIST_SIDE_TO_MOVE
    return h


# ---------------------------------------------------------------------------
# 冒烟测试（真正的交叉验证在 ai/tests/test_engine_bridge.py，这里只confirm
# 基本行为没有低级错误，方便单独运行本文件时快速自检）
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    pos = Position.initial()
    assert sum(1 for _ in pos.occupied_coords()) == 58, "初始棋子总数应为58"
    black_moves = generate_side_moves(pos, BLACK)
    assert len(black_moves) > 0
    legal = get_legal_moves(pos, BLACK)
    assert len(legal) == len(black_moves), "开局第一步不应有任何走法被将军过滤掉"

    fb = pos.to_frontend_board()
    assert fb["f1"] == {"notation": "TH", "side": "black"}
    pos2 = Position.from_frontend_board(fb, "black")
    assert pos2.to_frontend_board() == fb, "前端 JSON 往返转换应该完全一致"

    h1 = zobrist_hash(pos)
    h2 = zobrist_hash(Position.initial())
    assert h1 == h2, "同一局面两次生成的 zobrist hash 应该相同"

    print("engine_bridge.py 冒烟测试通过 ✅（完整交叉验证见 ai/tests/test_engine_bridge.py）")
