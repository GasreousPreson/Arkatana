"""
ai/notation_lite.py
=====================
跟网站后端 notation.py 逐条对应的记谱格式化——只做"格式化"这一半
（move_notation / compute_disambiguation / format_game），不需要"解析"那一半
（parse_move_notation）：自对弈/查看器这边永远是"引擎自己知道每一步的
起点终点"，不需要像人类棋谱导入那样从文本反推坐标。

规则来源：AnicentChess/notation.py（2026-08-15 读取的版本）。任何一条这里
跟那边对不上，都以 notation.py 为准——这边纯粹是为了让 ai/ 不需要依赖
网站后端代码（engine_bridge.py 的既定原则）而单独实现的一份"跟随"，
不是另一套独立设计。

依赖：ai/engine_bridge.py（不依赖网站后端）
"""

from __future__ import annotations

from typing import NamedTuple, Optional

from engine_bridge import (
    CHARIOT, PAWN, PROMOTED, SWORDSMAN, THRONE, TURRET, TYPE_TO_NOTATION,
    TYPE_NAMES, Position, coord_to_str, get_legal_moves, sq_index,
)


# ---------------------------------------------------------------------------
# 单步记谱记录（跟 game.MoveRecord 字段一一对应，鸭子类型即可，
# 不要求真的是同一个类）
# ---------------------------------------------------------------------------

class MoveRecord(NamedTuple):
    side: int                      # BLACK / WHITE
    piece_type: int                # engine_bridge 的棋子类型编码
    from_sq: tuple[int, int]
    to_sq: tuple[int, int]
    captured_type: Optional[int]   # 被吃棋子的类型编码；没吃子则 None
    promoted: bool                 # 这一步棋本身是否触发了升变
    was_already_promoted: bool     # 走这步棋之前，这颗棋子是不是已经是升变状态
    disambiguation: Optional[str]  # compute_disambiguation() 提前算好的消歧义字符
    is_checkmate: bool             # 这一步棋是否直接将死对方


# ---------------------------------------------------------------------------
# 消歧义（对应 notation.py 的 compute_disambiguation）
# ---------------------------------------------------------------------------

def compute_disambiguation(pos: Position, from_sq: tuple[int, int], piece_type: int,
                            side: int, to_sq: tuple[int, int]) -> Optional[str]:
    """走子前调用：棋盘上是否存在其他"同类型、同阵营"棋子也能合法走到 to_sq。
    跟 notation.py 一样，只统计合法走法（走完不会导致己方王城被将军的走法）。
    没有歧义 -> None；列字母能区分 -> 列字母；列字母也不行 -> 起始排数字。
    """
    rival_positions: set[tuple[int, int]] = set()
    for move in get_legal_moves(pos, side):
        if move.to_sq != to_sq or move.from_sq == from_sq:
            continue
        idx = sq_index(*move.from_sq)
        if pos.types[idx] == piece_type:
            rival_positions.add(move.from_sq)

    if not rival_positions:
        return None

    origin_col_letter = coord_to_str(*from_sq)[0]
    rival_col_letters = {coord_to_str(*p)[0] for p in rival_positions}

    if origin_col_letter not in rival_col_letters:
        return origin_col_letter
    return str(from_sq[1])


def compute_pawn_disambiguation(from_sq: tuple[int, int], to_sq: tuple[int, int],
                                 is_capture: bool, was_already_promoted: bool) -> Optional[str]:
    """兵专属消歧义——跟其他棋子共用的 compute_disambiguation() 是两套独立逻辑
    （逐字对应 notation.py 的 compute_pawn_disambiguation，规则本身见那边的
    详细说明，这里不重复抄一遍注释，只保证行为一致）。"""
    origin_col = coord_to_str(*from_sq)[0]
    dest_col = coord_to_str(*to_sq)[0]

    if was_already_promoted:
        return origin_col if origin_col != dest_col else None
    return origin_col if is_capture else None


# ---------------------------------------------------------------------------
# 走法记谱（对应 notation.py 的 move_notation）
# ---------------------------------------------------------------------------

def move_notation(record: MoveRecord) -> str:
    piece_abbr = TYPE_TO_NOTATION.get(record.piece_type, "?")
    promoted_prefix = "+" if record.was_already_promoted else ""
    disambiguation = record.disambiguation or ""
    capture_marker = "x" if record.captured_type is not None else ""
    destination = coord_to_str(*record.to_sq)
    promote_suffix = "+" if record.promoted else ""
    checkmate_suffix = "#" if record.is_checkmate else ""

    return (
        f"{promoted_prefix}{piece_abbr}{disambiguation}"
        f"{capture_marker}{destination}{promote_suffix}{checkmate_suffix}"
    )


def format_game(move_log: list[MoveRecord]) -> str:
    """对应 notation.py 的 format_game：黑白各一步一行的可读文本。"""
    lines = []
    move_number = 1
    i = 0
    while i < len(move_log):
        black_move = move_log[i]
        white_move = move_log[i + 1] if i + 1 < len(move_log) else None
        line = f"{move_number}. {move_notation(black_move)}"
        if white_move is not None:
            line += f"   {move_notation(white_move)}"
        lines.append(line)
        i += 2
        move_number += 1
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 自对弈落子时一站式生成完整 MoveRecord（供 self_play.py 调用）
# ---------------------------------------------------------------------------

def build_move_record(pos: Position, from_sq: tuple[int, int], to_sq: tuple[int, int],
                       is_checkmate_after: bool) -> MoveRecord:
    """在实际落子**之前**调用（需要落子前的局面来判断消歧义/被吃棋子/
    是否已经升变），返回的 MoveRecord 里 promoted 字段（这步棋是否触发升变）
    需要调用方在真正 apply_move 之后自行判断再补——这里为了不用同时依赖
    "落子前"和"落子后"两份局面，只生成不依赖 promoted 的部分，
    self_play.py 里会在 apply_move 后把 promoted 字段替换成真实值
    （NamedTuple 不可变，用 _replace）。"""
    from_idx = sq_index(*from_sq)
    to_idx = sq_index(*to_sq)
    piece_type = pos.types[from_idx]
    side = pos.sides[from_idx]
    was_already_promoted = bool(pos.flags[from_idx] & PROMOTED)
    captured_type = pos.types[to_idx] if pos.types[to_idx] != 0 else None
    is_capture = captured_type is not None

    if piece_type == PAWN:
        disamb = compute_pawn_disambiguation(from_sq, to_sq, is_capture, was_already_promoted)
    else:
        disamb = compute_disambiguation(pos, from_sq, piece_type, side, to_sq)

    return MoveRecord(
        side=side, piece_type=piece_type, from_sq=from_sq, to_sq=to_sq,
        captured_type=captured_type, promoted=False,
        was_already_promoted=was_already_promoted, disambiguation=disamb,
        is_checkmate=is_checkmate_after,
    )


if __name__ == "__main__":
    from engine_bridge import BLACK, WHITE, parse_coord

    # 复刻 notation.py 自检里的几个关键实例，确认格式完全一致
    pos = Position()
    pos.place(parse_coord("f4"), 8, BLACK)   # PHOENIX=8
    pos.place(parse_coord("j8"), 1, WHITE)   # PAWN=1
    rec = MoveRecord(
        side=BLACK, piece_type=8, from_sq=parse_coord("f4"), to_sq=parse_coord("j8"),
        captured_type=1, promoted=False, was_already_promoted=False,
        disambiguation=None, is_checkmate=True,
    )
    assert move_notation(rec) == "Pxj8#", move_notation(rec)

    rec2 = MoveRecord(
        side=BLACK, piece_type=TURRET, from_sq=parse_coord("a9"), to_sq=parse_coord("f8"),
        captured_type=PAWN, promoted=False, was_already_promoted=True,
        disambiguation=None, is_checkmate=False,
    )
    assert move_notation(rec2) == "+Txf8", move_notation(rec2)

    rec3 = MoveRecord(
        side=BLACK, piece_type=TURRET, from_sq=parse_coord("a9"), to_sq=parse_coord("a12"),
        captured_type=None, promoted=True, was_already_promoted=False,
        disambiguation=None, is_checkmate=True,
    )
    assert move_notation(rec3) == "Ta12+#", move_notation(rec3)

    print("notation_lite.py 冒烟测试通过 ✅")
