"""
layout.py
=========
Arkatana（古战棋）— 开局摆位模块

职责范围：
    1. 硬编码黑方（先手）与白方（后手）的完整开局摆位
    2. 提供 setup_initial_board()：生成一个摆好开局的 Board 实例
    3. 提供镜像坐标工具函数，仅用于自检阶段交叉验证"黑白布局是否严格对称"

数据来源说明：
    - 黑方布局摘自规则文本中"各棋子走法"部分自带的部署坐标
      （原文以黑方为参照，散落在每种棋子的说明里）
    - 白方布局摘自"附加布局规则"里单独列出的完整白方阵型
    - 两者独立硬编码（不是靠镜像公式互相生成），
      是为了保留"若未来双方布局出现刻意不对称"的可能性；
      镜像关系目前只作为自检工具，用来验证两份数据是否吻合。

依赖：board.py（坐标转换）、pieces.py（棋子类、Side 枚举）
"""

from __future__ import annotations

from board import Board, parse_coord, coord_to_str, COLUMNS
from pieces import (
    Side, Pawn, Ballista, Turret, Ares, Hussar, Rook,
    Phoenix, Knight, Swordsman, Chariot, Throne,
)


# ---------------------------------------------------------------------------
# 黑方（先手）开局布局 —— 摘自各棋子走法说明中的部署坐标
# ---------------------------------------------------------------------------

BLACK_LAYOUT: dict[type, list[str]] = {
    Throne:    ["f1"],
    Pawn:      [f"{c}5" for c in COLUMNS],          # 第5排，11列全部部署
    Ares:      ["f4"],
    Ballista:  ["d4", "h4"],
    Turret:    ["a4", "l4"],
    Rook:      ["a1", "l1"],
    Chariot:   ["b1", "k1"],
    Phoenix:   ["c1", "j1"],
    Knight:    ["d1", "h1"],
    Swordsman: ["e1", "g1"],
    Hussar:    ["c2", "j2"],
}


# ---------------------------------------------------------------------------
# 白方（后手）开局布局 —— 摘自"附加布局规则"中的完整白方阵型
# ---------------------------------------------------------------------------

WHITE_LAYOUT: dict[type, list[str]] = {
    Throne:    ["f12"],
    Pawn:      [f"{c}8" for c in COLUMNS],          # 第8排，11列全部部署
    Ares:      ["f9"],
    Ballista:  ["d9", "h9"],
    Turret:    ["a9", "l9"],
    Rook:      ["a12", "l12"],
    Chariot:   ["b12", "k12"],
    Phoenix:   ["c12", "j12"],
    Knight:    ["d12", "h12"],
    Swordsman: ["e12", "g12"],
    Hussar:    ["c11", "j11"],
}


# ---------------------------------------------------------------------------
# 镜像工具（仅用于自检交叉验证黑白布局的对称性）
# ---------------------------------------------------------------------------

def mirror_row(row: int) -> int:
    """行号镜像：黑方 row <-> 白方 13-row（例如 1<->12, 2<->11, 4<->9, 5<->8）"""
    return 13 - row


def mirror_coord_str(coord_str: str) -> str:
    """坐标字符串镜像，列不变，行按 mirror_row 转换。例：'d4' -> 'd9'"""
    col, row = parse_coord(coord_str)
    return coord_to_str(col, mirror_row(row))


# ---------------------------------------------------------------------------
# 摆盘主函数
# ---------------------------------------------------------------------------

def setup_initial_board() -> Board:
    """生成一个摆好 Arkatana 开局的 Board 实例"""
    board = Board()

    for side, layout in ((Side.BLACK, BLACK_LAYOUT), (Side.WHITE, WHITE_LAYOUT)):
        for piece_cls, coord_strs in layout.items():
            for coord_str in coord_strs:
                coord = parse_coord(coord_str)
                piece = piece_cls(side, coord)
                board.set(coord, piece)

    return board


# ---------------------------------------------------------------------------
# 简单自检
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    board = setup_initial_board()

    # 1) 总棋子数校验：兵11 + 大将1 + (弩/炮/轻/重/塔/凤/战/剑 各2)*8 + 王城1 = 29，双方共58
    EXPECTED_COUNTS = {
        Pawn: 11, Ares: 1, Throne: 1,
        Ballista: 2, Turret: 2, Hussar: 2, Knight: 2,
        Rook: 2, Phoenix: 2, Swordsman: 2, Chariot: 2,
    }
    for side, layout in ((Side.BLACK, BLACK_LAYOUT), (Side.WHITE, WHITE_LAYOUT)):
        for piece_cls, expected in EXPECTED_COUNTS.items():
            actual = len(layout[piece_cls])
            assert actual == expected, (
                f"{side.value} 阵营 {piece_cls.__name__} 数量异常: "
                f"期望 {expected}，实际 {actual}"
            )

    total_pieces = sum(len(v) for v in BLACK_LAYOUT.values()) + sum(len(v) for v in WHITE_LAYOUT.values())
    assert total_pieces == 58, f"总棋子数异常: {total_pieces}（应为58）"

    # 2) 对称性交叉验证：黑方每个坐标镜像后，应恰好等于白方对应棋子的坐标集合
    for piece_cls in BLACK_LAYOUT:
        black_coords = set(BLACK_LAYOUT[piece_cls])
        mirrored = {mirror_coord_str(c) for c in black_coords}
        white_coords = set(WHITE_LAYOUT[piece_cls])
        assert mirrored == white_coords, (
            f"{piece_cls.__name__} 黑白布局不对称！\n"
            f"  黑方镜像后应为: {sorted(mirrored)}\n"
            f"  白方实际布局为: {sorted(white_coords)}"
        )

    # 3) 棋盘上棋子总数应与摆位数据一致
    board_piece_count = sum(1 for _ in board.occupied_coords())
    assert board_piece_count == 58, f"棋盘上棋子数异常: {board_piece_count}"

    # 4) 王城不可移动、不可被吃 —— 确认双方王城各就各位
    assert isinstance(board.get(parse_coord("f1")), Throne)
    assert isinstance(board.get(parse_coord("f12")), Throne)

    print("layout.py 自检全部通过 ✅")
    print(f"棋盘总棋子数: {board_piece_count}（黑白各 29）\n")
    print(board)
