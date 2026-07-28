"""
board.py
========
Arkatana（古战棋）— 棋盘底层模块

职责范围（只做这些事）：
    1. 定义坐标系统：列 a~k（共11列），行 1~12（共12行）
    2. 提供坐标与内部索引之间的互相转换
    3. 提供棋盘容器 Board，负责存放棋子、读写格子内容
    4. 提供基础几何判断：是否越界、是否同行/同列/同斜线等

不做的事（留给后续模块）：
    - 不知道"棋子怎么走"（交给 pieces.py）
    - 不知道"这步棋合不合法"（交给 rules.py / movement.py）
    - 不做开局摆位（交给 layout.py）

坐标约定：
    - 外部记谱格式：字母+数字，例如 "d4"、"k12"（棋盘上常见的写法）
    - 内部索引格式：(col_index, row_index)，均为从 0 开始的整数
      例如 "a1" -> (0, 1)，"k12" -> (10, 12)
      注意：行号沿用棋规原始编号 1~12（不做 0 基转换），
      只有"列"做了 0 基索引，这样便于换算的同时也保留了直觉上的行号。
"""

from __future__ import annotations
from typing import Optional, Iterator


# ---------------------------------------------------------------------------
# 基础常量
# ---------------------------------------------------------------------------

COLUMNS = "abcdefghijk"   # 11 列，a 在最左，k 在最右
NUM_COLS = len(COLUMNS)   # 11
MIN_ROW = 1
MAX_ROW = 12
NUM_ROWS = MAX_ROW - MIN_ROW + 1  # 12

# 双方阵营分界（仅作参考常量，具体规则判断以 rules.py 为准）
BLACK_HOME_ROWS = range(1, 6)    # 黑方（先手）阵营：1~5 排
WHITE_HOME_ROWS = range(8, 13)   # 白方（后手）阵营：8~12 排
NEUTRAL_ROWS = (6, 7)            # 中间空出的 2 格地带


# ---------------------------------------------------------------------------
# 坐标转换
# ---------------------------------------------------------------------------

def col_to_index(col: str) -> int:
    """列字母 -> 0 基索引。 'a' -> 0, 'k' -> 10"""
    col = col.lower()
    if col not in COLUMNS:
        raise ValueError(f"非法列坐标: {col!r}，合法范围为 a~k")
    return COLUMNS.index(col)


def index_to_col(index: int) -> str:
    """0 基索引 -> 列字母。 0 -> 'a', 10 -> 'k'"""
    if not (0 <= index < NUM_COLS):
        raise ValueError(f"列索引越界: {index}，合法范围为 0~{NUM_COLS - 1}")
    return COLUMNS[index]


def is_valid_row(row: int) -> bool:
    return MIN_ROW <= row <= MAX_ROW


def is_valid_coord(col: int, row: int) -> bool:
    """col 为 0 基索引，row 为原始行号 1~12"""
    return 0 <= col < NUM_COLS and is_valid_row(row)


def parse_coord(text: str) -> tuple[int, int]:
    """
    外部记谱 -> 内部坐标 (col_index, row)
    例："d4" -> (3, 4)，"k12" -> (10, 12)
    """
    text = text.strip().lower()
    if len(text) < 2:
        raise ValueError(f"非法坐标格式: {text!r}")

    col_letter = text[0]
    row_part = text[1:]

    if not row_part.isdigit():
        raise ValueError(f"非法坐标格式: {text!r}")

    col = col_to_index(col_letter)
    row = int(row_part)

    if not is_valid_coord(col, row):
        raise ValueError(f"坐标越界: {text!r}")

    return col, row


def coord_to_str(col: int, row: int) -> str:
    """内部坐标 -> 外部记谱字符串。例：(3, 4) -> 'd4'"""
    if not is_valid_coord(col, row):
        raise ValueError(f"坐标越界: col={col}, row={row}")
    return f"{index_to_col(col)}{row}"


# ---------------------------------------------------------------------------
# 几何关系判断（纯坐标层面，不关心棋子）
# ---------------------------------------------------------------------------

def same_col(a: tuple[int, int], b: tuple[int, int]) -> bool:
    return a[0] == b[0]


def same_row(a: tuple[int, int], b: tuple[int, int]) -> bool:
    return a[1] == b[1]


def same_diagonal(a: tuple[int, int], b: tuple[int, int]) -> bool:
    """判断两点是否在同一条斜线上（正斜或反斜均可）"""
    return abs(a[0] - b[0]) == abs(a[1] - b[1])


def is_straight_line(a: tuple[int, int], b: tuple[int, int]) -> bool:
    """是否同行或同列（用于战车、炮等直线棋子）"""
    return same_row(a, b) or same_col(a, b)


def chebyshev_distance(a: tuple[int, int], b: tuple[int, int]) -> int:
    """棋盘上的“米字格”距离（横、竖、斜均按1格计），用于大将等走法判断"""
    return max(abs(a[0] - b[0]), abs(a[1] - b[1]))


def direction_between(a: tuple[int, int], b: tuple[int, int]) -> tuple[int, int]:
    """
    返回从 a 指向 b 的单位方向向量 (dx, dy)，例如 (1, 0) 表示向右。
    仅在 a、b 处于直线或斜线关系时有意义，调用前应自行判断。
    若 a == b，返回 (0, 0)。
    """
    dx = b[0] - a[0]
    dy = b[1] - a[1]
    sign = lambda n: (n > 0) - (n < 0)
    return sign(dx), sign(dy)


def squares_between(a: tuple[int, int], b: tuple[int, int]) -> list[tuple[int, int]]:
    """
    返回 a 到 b 之间（不含两端）沿直线/斜线经过的所有格子坐标。
    若 a、b 既不同行、不同列、也不同斜线，返回空列表（表示无意义）。
    """
    if a == b:
        return []
    if not (is_straight_line(a, b) or same_diagonal(a, b)):
        return []

    dx, dy = direction_between(a, b)
    squares = []
    cur = (a[0] + dx, a[1] + dy)
    while cur != b:
        squares.append(cur)
        cur = (cur[0] + dx, cur[1] + dy)
    return squares


# ---------------------------------------------------------------------------
# 棋盘容器
# ---------------------------------------------------------------------------

class Board:
    """
    棋盘容器：只负责存放和读取"某格子上有什么"，
    不判断走法是否合法，也不理解棋子的具体规则。
    格子内容可以是任意对象（后续会是 pieces.py 中定义的棋子实例），
    空格用 None 表示。
    """

    def __init__(self) -> None:
        # 用字典存储比二维列表更直观，key 为 (col_index, row)
        self._grid: dict[tuple[int, int], Optional[object]] = {}
        for col in range(NUM_COLS):
            for row in range(MIN_ROW, MAX_ROW + 1):
                self._grid[(col, row)] = None

    # -- 基础读写 -----------------------------------------------------------

    def get(self, coord: tuple[int, int]) -> Optional[object]:
        """获取某格子上的内容，越界则抛异常"""
        self._check_bounds(coord)
        return self._grid[coord]

    def set(self, coord: tuple[int, int], piece: Optional[object]) -> None:
        """设置某格子上的内容（放置棋子或清空）"""
        self._check_bounds(coord)
        self._grid[coord] = piece

    def remove(self, coord: tuple[int, int]) -> Optional[object]:
        """移除某格子上的棋子并返回该棋子"""
        piece = self.get(coord)
        self._grid[coord] = None
        return piece

    def is_empty(self, coord: tuple[int, int]) -> bool:
        return self.get(coord) is None

    def move_piece(self, src: tuple[int, int], dst: tuple[int, int]) -> Optional[object]:
        """
        将棋子从 src 移动到 dst（不做合法性检查，只做搬运）。
        若 dst 原本有棋子（被吃），返回被吃掉的棋子；否则返回 None。
        """
        piece = self.remove(src)
        captured = self.get(dst)
        self.set(dst, piece)
        return captured

    # -- 遍历 -----------------------------------------------------------

    def all_coords(self) -> Iterator[tuple[int, int]]:
        """遍历棋盘上所有合法坐标"""
        for row in range(MIN_ROW, MAX_ROW + 1):
            for col in range(NUM_COLS):
                yield (col, row)

    def occupied_coords(self) -> Iterator[tuple[int, int]]:
        """遍历所有非空格子的坐标"""
        for coord in self.all_coords():
            if not self.is_empty(coord):
                yield coord

    # -- 工具方法 -----------------------------------------------------------

    def _check_bounds(self, coord: tuple[int, int]) -> None:
        if not is_valid_coord(*coord):
            raise ValueError(f"坐标越界: {coord}")

    def copy(self) -> "Board":
        """深拷贝棋盘（浅拷贝格子内的棋子引用，棋子对象本身若需要深拷贝，
        应在 pieces.py 中的棋子类自行实现 __deepcopy__ 或 clone 方法）"""
        new_board = Board()
        new_board._grid = dict(self._grid)
        return new_board

    def __str__(self) -> str:
        """
        以文字形式打印棋盘，便于命令行阶段调试。
        每个格子若为空显示 '.'，若有棋子则显示其 repr()（后续棋子类可自定义显示符号）。
        从第12排（顶端/白方底线）打印到第1排（底端/黑方底线），
        符合"黑方在下、白方在上"的视觉直觉。
        """
        lines = []
        header = "   " + " ".join(c.upper() for c in COLUMNS)
        lines.append(header)
        for row in range(MAX_ROW, MIN_ROW - 1, -1):
            row_cells = []
            for col in range(NUM_COLS):
                piece = self._grid[(col, row)]
                symbol = "." if piece is None else str(piece)
                row_cells.append(symbol.rjust(1))
            lines.append(f"{row:>2} " + " ".join(row_cells))
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# 简单自检（直接运行本文件时执行，便于快速验证坐标系统是否正确）
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # 坐标转换测试
    assert parse_coord("d4") == (3, 4)
    assert parse_coord("k12") == (10, 12)
    assert coord_to_str(3, 4) == "d4"
    assert coord_to_str(10, 12) == "k12"

    # 几何关系测试
    assert same_diagonal((3, 4), (6, 7)) is True
    assert same_diagonal((3, 4), (6, 8)) is False
    assert is_straight_line((3, 4), (3, 9)) is True
    assert chebyshev_distance((3, 4), (5, 6)) == 2
    assert squares_between((0, 1), (0, 4)) == [(0, 2), (0, 3)]
    assert squares_between((3, 4), (6, 7)) == [(4, 5), (5, 6)]

    # 棋盘容器测试
    b = Board()
    d4 = parse_coord("d4")
    h4 = parse_coord("h4")
    b.set(d4, "弩")
    b.set(h4, "弩")
    assert b.get(d4) == "弩"
    assert b.is_empty(parse_coord("a1"))

    print("board.py 自检全部通过 ✅\n")
    print(b)
