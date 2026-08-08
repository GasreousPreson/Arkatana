"""
notation.py
===========
Arkatana（古战棋）— 记谱模块

职责范围：
    1. 走法记谱（正式版：仿照国际象棋代数记谱法，只在必要时才消歧义）
       结构：[升变前缀+?]{棋子字母}[消歧义?]{吃子标记x?}{终点坐标}[升变+?][将死#?]
       例："Pxj8#"（凤凰吃子将死）、"+Txf8"（已升变的炮吃子）、
           "Ta12+#"（炮走到a12，触发升变同时将死）、"Hcf3"（消歧义：还有另一个轻骑士也能到f3）

       - move_notation(record)        单步 MoveRecord -> 记谱字符串
       - parse_move_notation(text, board, side)
            记谱字符串 + 当时的棋盘状态 -> (起点坐标, 终点坐标)
            （新格式经常省略起点，需要结合棋盘反推，所以这个函数比单纯的字符串
             解析更"重"，调用时必须传入这步棋即将被走出来之前的棋盘和行棋方）
       - format_game(move_log)        整局走子记录 -> 类似PGN的可读文本
       - compute_disambiguation(board, piece, to_sq)
            走子前调用：判断是否有其他同类型同阵营棋子也能走到 to_sq，
            需要的话返回消歧义字符（列字母优先，列字母也无法区分则用排数字）

    2. 局面记谱（类似FEN，把某一瞬间的完整棋盘状态压缩成一行字符串）：
       每格用 "|" 分隔，格式：{阵营字符}{棋子缩写}{升变标记+}{是否已走过*}
       空格用 "."；每排(rank)之间用 "/" 分隔，从第12排到第1排；
       末尾加一个空格 + 当前该谁走('b'/'w')
       例：'.|.|.|bBA|.|bAR|.../... b'

       - board_to_position_string(board, side_to_move) -> str
       - position_string_to_board(position_str) -> (Board, Side)

依赖：board.py（坐标转换）、pieces.py（棋子类注册表、Side）、rules.py（合法走法过滤，
      供 compute_disambiguation 判断"歧义候选"时排除会导致送将的假歧义）
      注意：notation.py 不 import game.py（只在自身的自检代码里局部 import），
      但 game.py 现在会 import notation.py（用来在走棋时调用 compute_disambiguation）。
"""

from __future__ import annotations
import re

from board import Board, parse_coord, coord_to_str, MIN_ROW, MAX_ROW, NUM_COLS
from pieces import Side, PIECE_CLASSES, Pawn
from rules import get_legal_moves


# ---------------------------------------------------------------------------
# 棋子缩写 <-> 棋子类 的双向映射
# ---------------------------------------------------------------------------

NOTATION_TO_CLASS = {cls.notation: cls for cls in PIECE_CLASSES.values()}
NOTATION_BY_CLASSNAME = {cls.__name__: cls.notation for cls in PIECE_CLASSES.values()}

# 按缩写长度从长到短排序，解析记谱字符串时优先匹配更长的前缀，
# 避免类似 "R" 和 未来可能新增的 "RE" 之类前缀互相冲突的问题
_SORTED_NOTATION_CODES = sorted(NOTATION_TO_CLASS.keys(), key=len, reverse=True)


# ---------------------------------------------------------------------------
# 消歧义计算（走子前调用）
# ---------------------------------------------------------------------------

def compute_disambiguation(board: Board, piece, to_sq: tuple[int, int]) -> str | None:
    """
    在实际执行走法之前调用：判断棋盘上是否存在其他"同类型、同阵营"的棋子
    也能合法走到 to_sq 这个目标格。

    没有歧义 -> 返回 None
    有歧义，且棋子们的起始列字母互不相同 -> 返回起始列字母（例如 "d"）
    列字母也无法区分（几个棋子在同一列）-> 返回起始排数字（例如 "5"）

    只统计"合法走法"（不会导致己方王城被将军的走法），
    跟真实国际象棋的消歧义规则一致——如果对方棋子理论上能走到那格，
    但那样走会送将，就不构成真正的歧义。
    """
    legal_moves = get_legal_moves(board, piece.side)
    rival_positions: set[tuple[int, int]] = set()

    for move in legal_moves:
        if move.to_sq != to_sq or move.from_sq == piece.position:
            continue
        mover = board.get(move.from_sq)
        if mover is not None and type(mover) is type(piece):
            rival_positions.add(move.from_sq)

    if not rival_positions:
        return None

    origin_col_letter = coord_to_str(*piece.position)[0]
    rival_col_letters = {coord_to_str(*p)[0] for p in rival_positions}

    if origin_col_letter not in rival_col_letters:
        return origin_col_letter

    # 列字母无法区分（有别的同类棋子也在同一列），改用排数字消歧义
    return str(piece.position[1])


def compute_pawn_disambiguation(
    from_sq: tuple[int, int], to_sq: tuple[int, int],
    is_capture: bool, was_already_promoted: bool,
) -> str | None:
    """
    兵专属的消歧义规则——跟其他棋子共用的 compute_disambiguation() 是两套
    独立逻辑，不看"场上是否真的存在别的兵也能到同一格"，只看这一步棋本身：

    - 未升变兵：只要是吃子，永远带上出发列字母（不管是否真的存在歧义，
      跟真实国际象棋 "exd5" 的写法习惯一致）；非吃子的移动不带列字母。
    - 已升变兵：只要出发列跟到达列不一样（也就是这一步斜走或横移了）就带上
      出发列字母，一样（直着走）就不带。

    origin_col == dest_col 时省略字母，即使当时场上其实有别的兵也能走到
    这一格（例如三个兵分别从斜两侧、正前方汇聚同一格）——这种情况下，
    "没带字母的那个"就代表"正前方直走过来的那个"，这本身就是消歧义的一部分。
    """
    origin_col = coord_to_str(*from_sq)[0]
    dest_col = coord_to_str(*to_sq)[0]

    if was_already_promoted:
        return origin_col if origin_col != dest_col else None
    return origin_col if is_capture else None


# ---------------------------------------------------------------------------
# 走法记谱
# ---------------------------------------------------------------------------

def move_notation(record) -> str:
    """
    把一条 MoveRecord（来自 game.py）格式化成正式记谱字符串。

    结构：[升变前缀?]{棋子字母}[消歧义?]{吃子标记?}{终点坐标}[升变后缀?][将死后缀?]

    - 升变前缀 "+"：这个棋子在走这步棋之前就已经是升变状态（例如 "+Sge5"）
    - 消歧义：record.disambiguation 不为 None 时，原样插入棋子字母之后
      （由 compute_disambiguation() 提前算好，是列字母或排数字）
    - 吃子标记 "x"：record.captured_type 不为空则加上
    - 升变后缀 "+"：这步棋本身触发了升变（例如 "Seg7+"）
    - 将死后缀 "#"：这步棋直接将死对方（不记录普通将军，只记将死）
      升变和将死同时发生时，先升变后将死："+" 在前，"#" 在后（例如 "Ta12+#"）

    record 需要具备 piece_type / captured_type / to_sq / promoted /
    was_already_promoted / disambiguation / is_checkmate 字段
    （鸭子类型，不强制要求是 game.MoveRecord 类型本身，方便未来扩展）。
    """
    piece_abbr = NOTATION_BY_CLASSNAME.get(record.piece_type, "?")
    promoted_prefix = "+" if record.was_already_promoted else ""
    disambiguation = record.disambiguation or ""
    capture_marker = "x" if record.captured_type else ""
    destination = coord_to_str(*record.to_sq)
    promote_suffix = "+" if record.promoted else ""
    checkmate_suffix = "#" if record.is_checkmate else ""

    return (
        f"{promoted_prefix}{piece_abbr}{disambiguation}"
        f"{capture_marker}{destination}{promote_suffix}{checkmate_suffix}"
    )


# 终点坐标固定出现在字符串最后：一个列字母 + 1~2位排数。
# 用 search 从字符串里找这个模式最靠右的一次出现，而不是假设"棋子字母/吃子标记
# 去掉之后剩下的就正好是终点"——消歧义字母和吃子标记 "x" 谁先谁后有好几种组合
# （纯棋子字母后接终点、消歧义字母后接终点、消歧义字母+x+终点……），
# 与其穷举顺序，不如直接"从后往前"先把终点坐标锁定，剩下的前缀里再拆吃子标记
# 和消歧义字母，顺序问题就不存在了。
# 列字母范围要跟 board.COLUMNS 保持一致（a~h, j~l，跳过 i）。
_DESTINATION_AT_END = re.compile(r"([a-hj-l][0-9]{1,2})$")


def parse_move_notation(
    notation: str, board: Board, side: Side
) -> tuple[tuple[int, int], tuple[int, int]]:
    """
    结合当前棋盘状态，把一条正式记谱字符串解析回 (起点坐标, 终点坐标)。

    之所以需要棋盘：新格式的记谱经常省略起点坐标（只有出现歧义时才标一个
    列字母或排数字），必须结合"棋盘上到底是谁能走到这一格"才能确定真正的起点，
    单看记谱文本本身是不够的（这跟真实国际象棋 SAN 记谱解析是同一个道理）。

    board / side 应该是"这步棋即将被走出来之前"的棋盘状态和行棋方
    （回放整局棋谱时，随着每一步的执行同步更新，而不是固定用某个历史时刻的棋盘）。
    """
    text = notation

    # 去掉将死后缀 "#"，再去掉升变后缀 "+"（这个在最后面，跟开头的"已升变前缀+"是两回事）
    if text.endswith("#"):
        text = text[:-1]
    if text.endswith("+"):
        text = text[:-1]

    # 去掉"这个棋子本来就已经升变"的前缀 "+"（在最前面）；记下是否有这个前缀，
    # 兵的消歧义解析需要用到（见下方特殊处理）
    was_already_promoted = text.startswith("+")
    if was_already_promoted:
        text = text[1:]

    # 匹配棋子字母（兵没有字母，remainder 就是整个剩余字符串）
    piece_code = ""
    for code in _SORTED_NOTATION_CODES:
        if code and text.startswith(code):
            piece_code = code
            break
    piece_cls = NOTATION_TO_CLASS.get(piece_code)
    if piece_cls is None:
        raise ValueError(f"无法识别的记谱前缀: {notation!r}")

    remainder = text[len(piece_code):]

    # 先从末尾锁定终点坐标，剩下的 prefix 里才去拆"消歧义字母"和"吃子标记 x"
    # ——不再假设两者的先后顺序，避免"dxd8"这类"消歧义字母紧贴在x前面"的写法
    # 因为顺序假设错误而解析失败。
    dest_match = _DESTINATION_AT_END.search(remainder)
    if not dest_match:
        raise ValueError(f"无法解析目的地: {notation!r}（剩余部分: {remainder!r}）")
    destination_text = dest_match.group(1)
    prefix = remainder[:dest_match.start()]

    is_capture = prefix.endswith("x")
    if is_capture:
        prefix = prefix[:-1]
    disambig = prefix or None

    to_sq = parse_coord(destination_text)

    # 在"合法走法"范围内找出所有同阵营、同类型、能走到 to_sq 的候选起点，
    # 跟 compute_disambiguation() 用的是同一套判定标准，确保生成/解析互相对应
    candidates: set[tuple[int, int]] = set()
    for move in get_legal_moves(board, side):
        if move.to_sq != to_sq:
            continue
        mover = board.get(move.from_sq)
        if mover is not None and type(mover) is piece_cls:
            candidates.add(move.from_sq)

    if disambig is not None:
        if disambig.isalpha():
            candidates = {c for c in candidates if coord_to_str(*c)[0] == disambig}
        else:
            candidates = {c for c in candidates if str(c[1]) == disambig}
    elif piece_cls is Pawn and was_already_promoted:
        # 兵专属规则：已升变兵省略消歧义字母，意味着"出发列跟到达列相同"
        # （也就是直着走过来的那个），不能套用其他棋子"没写字母=候选只有一个"
        # 的假设——这里同一格可能真的有好几个兵能到，但没带字母的必然是
        # 那个"列不变"的，用这个额外条件筛选。
        dest_col = coord_to_str(*to_sq)[0]
        candidates = {c for c in candidates if coord_to_str(*c)[0] == dest_col}

    if len(candidates) != 1:
        readable = [coord_to_str(*c) for c in candidates]
        raise ValueError(
            f"无法从棋谱 {notation!r} 唯一确定起点，候选坐标: {readable}"
        )

    return next(iter(candidates)), to_sq


def format_game(move_log: list) -> str:
    """
    把整局走子记录格式化成类似 PGN 的可读文本：
        1. BAa1-h4   Pf8-f6
        2. ...
    黑方先手，每回合黑白各一步一行。
    """
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
# 局面记谱（类似 FEN）
# ---------------------------------------------------------------------------

def board_to_position_string(board: Board, side_to_move: Side) -> str:
    """
    把当前棋盘状态压缩成一行字符串，包含每个棋子的位置、阵营、
    升变状态(+)、是否已走过(*，用于兵的双步资格判断)，以及当前该谁走。
    """
    rows = []
    for row in range(MAX_ROW, MIN_ROW - 1, -1):
        cells = []
        for col in range(NUM_COLS):
            piece = board.get((col, row))
            if piece is None:
                cells.append(".")
            else:
                side_char = "b" if piece.side == Side.BLACK else "w"
                promo_mark = "+" if piece.promoted else ""
                moved_mark = "*" if piece.has_moved else ""
                cells.append(f"{side_char}{piece.notation}{promo_mark}{moved_mark}")
        rows.append("|".join(cells))

    board_part = "/".join(rows)
    turn_char = "b" if side_to_move == Side.BLACK else "w"
    return f"{board_part} {turn_char}"


def position_string_to_board(position_str: str) -> tuple[Board, Side]:
    """board_to_position_string() 的逆操作：字符串 -> (Board, 当前行棋方)"""
    board_part, turn_char = position_str.rsplit(" ", 1)
    row_strs = board_part.split("/")

    board = Board()
    for i, row_str in enumerate(row_strs):
        row = MAX_ROW - i
        cells = row_str.split("|")
        for col, cell in enumerate(cells):
            if cell == ".":
                continue

            side_char = cell[0]
            rest = cell[1:]

            moved = rest.endswith("*")
            if moved:
                rest = rest[:-1]
            promoted = rest.endswith("+")
            if promoted:
                rest = rest[:-1]
            notation = rest

            piece_cls = NOTATION_TO_CLASS.get(notation)
            if piece_cls is None:
                raise ValueError(f"未知棋子记谱符号: {notation!r}（格子内容: {cell!r}）")

            side = Side.BLACK if side_char == "b" else Side.WHITE
            piece = piece_cls(side, (col, row))
            piece.promoted = promoted
            piece.has_moved = moved
            board.set((col, row), piece)

    side_to_move = Side.BLACK if turn_char == "b" else Side.WHITE
    return board, side_to_move


# ---------------------------------------------------------------------------
# 简单自检
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    from game import Game, MoveRecord
    from layout import setup_initial_board

    # 1) 单步记谱格式化：用规格里已确认过的实例逐一验证
    #    "凤凰吃j8无解 -> Pxj8#"
    checkmate_capture = MoveRecord(
        side=Side.BLACK, piece_type="Phoenix",
        from_sq=parse_coord("f4"), to_sq=parse_coord("j8"),
        captured_type="Pawn", promoted=False,
        was_already_promoted=False, disambiguation=None, is_checkmate=True,
    )
    assert move_notation(checkmate_capture) == "Pxj8#"

    #    "升级炮吃f8 -> +Txf8"（炮本身走这步棋之前就已经升变过了）
    already_promoted_capture = MoveRecord(
        side=Side.BLACK, piece_type="Turret",
        from_sq=parse_coord("a9"), to_sq=parse_coord("f8"),
        captured_type="Pawn", promoted=False,
        was_already_promoted=True, disambiguation=None, is_checkmate=False,
    )
    assert move_notation(already_promoted_capture) == "+Txf8"

    #    "炮到a12绝杀 -> Ta12+#"（这步棋触发升变，同时将死，+ 在前 # 在后）
    promote_and_mate = MoveRecord(
        side=Side.BLACK, piece_type="Turret",
        from_sq=parse_coord("a9"), to_sq=parse_coord("a12"),
        captured_type=None, promoted=True,
        was_already_promoted=False, disambiguation=None, is_checkmate=True,
    )
    assert move_notation(promote_and_mate) == "Ta12+#"

    #    "f6的剑士走到h8...绝杀 -> Sh8+#"
    swordsman_mate = MoveRecord(
        side=Side.BLACK, piece_type="Swordsman",
        from_sq=parse_coord("f6"), to_sq=parse_coord("h8"),
        captured_type=None, promoted=True,
        was_already_promoted=False, disambiguation=None, is_checkmate=True,
    )
    assert move_notation(swordsman_mate) == "Sh8+#"

    #    消歧义实例："Hcf3"（c2轻骑士走到f3，i2轻骑士也能到，需要标列字母）
    disambiguated = MoveRecord(
        side=Side.BLACK, piece_type="Hussar",
        from_sq=parse_coord("c2"), to_sq=parse_coord("f3"),
        captured_type=None, promoted=False,
        was_already_promoted=False, disambiguation="c", is_checkmate=False,
    )
    assert move_notation(disambiguated) == "Hcf3"

    promo_record = MoveRecord(
        side=Side.BLACK, piece_type="Pawn",
        from_sq=parse_coord("d7"), to_sq=parse_coord("d8"),
        captured_type="Pawn", promoted=True,
        was_already_promoted=False, disambiguation="d", is_checkmate=False,
    )
    promo_text = move_notation(promo_record)
    assert promo_text == "dxd8+", f"未升变兵吃子应带出发列字母: {promo_text}"

    # 2) 整局记谱格式化
    game_text = format_game([checkmate_capture, disambiguated])
    assert game_text == "1. Pxj8#   Hcf3", f"整局记谱格式异常: {game_text!r}"

    # 2b) parse_move_notation 往返测试：无歧义 + 有歧义两种情况，结合真实棋盘反推起点
    from layout import setup_initial_board as _setup_initial_board

    replay_board = _setup_initial_board()
    # 无歧义：黑方兵 d5 走一步到 d6，记谱应该就是 "d6"
    d5_pawn_move_text = "d6"
    from_sq, to_sq = parse_move_notation(d5_pawn_move_text, replay_board, Side.BLACK)
    assert from_sq == parse_coord("d5") and to_sq == parse_coord("d6")

    # 有歧义：黑方 c2/j2 轻骑士都能走到 f3，"Hcf3" 应该解析回 c2，"Hjf3" 应该解析回 i2
    from_sq_c, to_sq_c = parse_move_notation("Hcf3", replay_board, Side.BLACK)
    assert from_sq_c == parse_coord("c2") and to_sq_c == parse_coord("f3")
    from_sq_i, to_sq_i = parse_move_notation("Hjf3", replay_board, Side.BLACK)
    assert from_sq_i == parse_coord("j2") and to_sq_i == parse_coord("f3")

    # 3) 局面记谱往返：初始局面
    board = setup_initial_board()
    pos_str = board_to_position_string(board, Side.BLACK)
    restored_board, restored_side = position_string_to_board(pos_str)
    assert restored_side == Side.BLACK
    original_count = sum(1 for _ in board.occupied_coords())
    restored_count = sum(1 for _ in restored_board.occupied_coords())
    assert original_count == restored_count == 58
    for coord in board.occupied_coords():
        orig_piece = board.get(coord)
        new_piece = restored_board.get(coord)
        assert new_piece is not None
        assert type(new_piece) is type(orig_piece)
        assert new_piece.side == orig_piece.side

    # 4) 局面记谱往返：包含升变 + 已走过状态的实战局面
    game = Game()
    game.make_move_str("d5", "d7")   # 冲两步，新规则下这一步尚未到升变排
    game.make_move_str("a8", "a7")   # 白方随便应一手，避开 d 线
    game.make_move_str("d7", "d8")   # d8 才是黑兵的升变排，这一步触发升变
    pos_str2 = board_to_position_string(game.board, game.current_side)
    restored_board2, restored_side2 = position_string_to_board(pos_str2)
    d8 = parse_coord("d8")
    restored_pawn = restored_board2.get(d8)
    assert restored_pawn.promoted is True, "局面记谱应保留升变状态"
    assert restored_pawn.has_moved is True, "局面记谱应保留是否已走过的状态"
    assert restored_side2 == Side.WHITE

    # 5) 消歧义计算：列字母消歧义（复刻用户给的例子：c2/j2 轻骑士都能走到 f3）
    from board import Board
    from pieces import Hussar, Knight

    board5 = Board()
    hussar_c2 = Hussar(Side.BLACK, parse_coord("c2"))
    hussar_j2 = Hussar(Side.BLACK, parse_coord("j2"))
    board5.set(hussar_c2.position, hussar_c2)
    board5.set(hussar_j2.position, hussar_j2)
    f3 = parse_coord("f3")
    assert compute_disambiguation(board5, hussar_c2, f3) == "c"
    assert compute_disambiguation(board5, hussar_j2, f3) == "j"

    # 6) 消歧义计算：列字母无法区分时改用排数字（两个重骑士同在 d 列，行不同）
    board6 = Board()
    knight_d1 = Knight(Side.BLACK, parse_coord("d1"))
    knight_d7 = Knight(Side.BLACK, parse_coord("d7"))
    board6.set(knight_d1.position, knight_d1)
    board6.set(knight_d7.position, knight_d7)
    e4 = parse_coord("e4")
    assert compute_disambiguation(board6, knight_d1, e4) == "1"
    assert compute_disambiguation(board6, knight_d7, e4) == "7"

    # 7) 无歧义的情况：只有一个棋子能到达目标格
    board7 = Board()
    lone_hussar = Hussar(Side.BLACK, parse_coord("c2"))
    board7.set(lone_hussar.position, lone_hussar)
    assert compute_disambiguation(board7, lone_hussar, f3) is None

    # 5) 兵专属记谱规则（compute_pawn_disambiguation）——跟其他棋子是两套独立逻辑
    from game import Game as _Game
    from board import parse_coord as _pc, coord_to_str as _cts
    from pieces import Pawn as _Pawn

    def _fresh_board_with_pawns(coords):
        g = _Game()
        g.board = type(g.board)()
        for sq in coords:
            p = _Pawn(Side.BLACK, _pc(sq))
            p.promoted = True
            g.board.set(_pc(sq), p)
        g.current_side = Side.BLACK
        return g

    # 5a) 未升变兵：非吃子移动不带列字母
    g = _Game()
    r = g.make_move(_pc("e5"), _pc("e6"))
    assert move_notation(r) == "e6", move_notation(r)

    # 5b) 未升变兵：吃子永远带列字母（未升变兵只能正前方吃，列字母技术上总是
    #     跟到达列相同，但这是按要求保留的"冗余但一致"写法，不是bug）
    g = _Game()
    g.board = type(g.board)()
    black_pawn = _Pawn(Side.BLACK, _pc("d5"))
    white_pawn = _Pawn(Side.WHITE, _pc("d6"))
    g.board.set(_pc("d5"), black_pawn)
    g.board.set(_pc("d6"), white_pawn)
    g.current_side = Side.BLACK
    rec = g.make_move(_pc("d5"), _pc("d6"))
    text = move_notation(rec)
    assert text == "dxd6", f"未升变兵吃子应带出发列字母: {text}"

    # 5c) 已升变兵：三个兵分别斜/直/斜汇聚同一格（用户举的第一组例子）
    for start, expect in [("c9", "+cd10"), ("d9", "+d10"), ("e9", "+ed10")]:
        gg = _fresh_board_with_pawns(["c9", "d9", "e9"])
        rec = gg.make_move(_pc(start), _pc("d10"))
        got = move_notation(rec)
        assert got == expect, f"{start}->d10 期望{expect}，实际{got}"

    # 5d) 已升变兵：两侧横移 + 中间直走汇聚同一格（用户举的第二组例子）
    for start, expect in [("c10", "+cd10"), ("e10", "+ed10"), ("d9", "+d10")]:
        gg = _fresh_board_with_pawns(["c10", "e10", "d9"])
        rec = gg.make_move(_pc(start), _pc("d10"))
        got = move_notation(rec)
        assert got == expect, f"{start}->d10 期望{expect}，实际{got}"

    # 5e) 解析器往返：省略字母的 "+d10" 必须能从三个候选里正确挑出"直走"的那个，
    #     不能套用其他棋子"没写字母=候选只有一个"的通用假设
    gg = _fresh_board_with_pawns(["c10", "e10", "d9"])
    from_sq, to_sq = parse_move_notation("+d10", gg.board, Side.BLACK)
    assert _cts(*from_sq) == "d9", _cts(*from_sq)
    from_sq, to_sq = parse_move_notation("+cd10", gg.board, Side.BLACK)
    assert _cts(*from_sq) == "c10", _cts(*from_sq)

    # 6) 解析器回归测试：消歧义字母 + 吃子标记同时出现（曾经的 bug 来源）
    #    不管是哪种棋子，"字母紧贴在 x 前面"这种写法都必须能正确解析
    g6 = _Game()
    g6.board = type(g6.board)()
    bp6 = _Pawn(Side.BLACK, _pc("d7"))
    wp6 = _Pawn(Side.WHITE, _pc("d8"))
    g6.board.set(_pc("d7"), bp6)
    g6.board.set(_pc("d8"), wp6)
    g6.current_side = Side.BLACK
    from_sq, to_sq = parse_move_notation("dxd8+", g6.board, Side.BLACK)
    assert _cts(*from_sq) == "d7" and _cts(*to_sq) == "d8", \
        f"消歧义字母+吃子标记组合解析失败: {_cts(*from_sq)} -> {_cts(*to_sq)}"

    # 7) 终点坐标正则要跟当前坐标系（a~h, j~l，跳过 i）保持一致，
    #    之前坐标系从 abcdefghijk 改成 abcdefghjkl 时这里曾经漏改
    assert _DESTINATION_AT_END.search("l4"), "l 是合法列，应该能匹配"
    assert not _DESTINATION_AT_END.search("i4"), "i 已经不是合法列，不应该匹配"
    assert _DESTINATION_AT_END.search("k12").group(1) == "k12"

    print("notation.py 自检全部通过 ✅")
    print()
    print("示例 - 单步记谱:", move_notation(checkmate_capture), "|", promo_text)
    print("示例 - 整局记谱:")
    print(game_text)
    print()
    print("示例 - 初始局面记谱字符串（截断显示前80字符）:")
    print(pos_str[:80] + "...")
