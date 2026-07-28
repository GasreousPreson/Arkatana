"""
pieces.py  (v1.3)
==================
Arkatana（古战棋）— 棋子模块

版本说明（相较 v1.2 的改动）：
    - 按计划，将原先以模块内私有函数形式存在的共享走子逻辑
      （_ranged_moves / _direct_capture_targets / _screen_capture_targets /
       _leap_targets / _sliding_moves，以及方向常量 DIAGONAL_DIRS 等）
      正式迁移到 movement.py，本文件改为直接 import 调用。
    - 所有棋子的走法规则本身【没有任何改动】，只是调用方式变化，
      各棋子类的 pseudo_moves() 方法逻辑与 v1.2 完全一致。

职责范围：
    1. 定义棋子基类 Piece，包含所属阵营、位置、升变状态等通用属性
    2. 定义每种棋子的具体走法规则（作为各子类自己的方法，调用 movement.py 提供的工具函数）
    3. 提供 pseudo_moves(board) 方法：给定当前棋盘，生成该棋子"几何上可行"的走法
       —— 只判断"这么走合不合几何规则"，不判断"走了之后己方王城会不会暴露"
          （那是 rules.py 的职责）

依赖：board.py（坐标系统）、movement.py（走子引擎公共函数）
"""

from __future__ import annotations
from enum import Enum
from typing import Optional

from board import is_valid_coord
from movement import (
    Move,
    DIAGONAL_DIRS,
    STRAIGHT_DIRS,
    EIGHT_DIRS,
    ranged_moves,
    direct_capture_targets,
    screen_capture_targets,
    leap_targets,
    sliding_moves,
)


# ---------------------------------------------------------------------------
# 阵营与方向
# ---------------------------------------------------------------------------

class Side(Enum):
    BLACK = "black"   # 先手，阵营 1~5 排，前进方向为行号增大
    WHITE = "white"    # 后手，阵营 8~12 排，前进方向为行号减小


FORWARD = {
    Side.BLACK: 1,
    Side.WHITE: -1,
}


def promotion_zone(side: Side) -> range:
    """
    升变排数范围，炮/剑士/战车三者共用：
    黑方（先手）7~12 排，白方（后手）1~6 排。
    调用方式：piece.position[1] in promotion_zone(piece.side)
    """
    return range(7, 13) if side == Side.BLACK else range(1, 7)


def pawn_promotion_zone(side: Side) -> range:
    """
    兵专属的升变排数：精确的单一排数（不是范围）。
    黑方兵部署于第5排，向前走3排后恰好到达第8排触发升变；
    白方兵部署于第8排，向前走3排后恰好到达第5排触发升变。
    """
    return range(8, 9) if side == Side.BLACK else range(5, 6)


# ---------------------------------------------------------------------------
# 棋子基类
# ---------------------------------------------------------------------------

class Piece:
    """所有棋子的基类，不直接实例化。"""

    name: str = "piece"
    symbol: str = "?"
    notation: str = "?"   # 记谱缩写，供未来 notation.py 使用

    def __init__(self, side: Side, position: Optional[tuple[int, int]] = None):
        self.side = side
        self.position = position
        self.has_moved = False   # 是否已经走过至少一步
        self.promoted = False    # 是否已升变（兵/炮/剑士专用，其余棋子恒为 False）

    def forward(self) -> int:
        """返回该棋子所属阵营的"前进方向"：黑方 +1（行号增大），白方 -1（行号减小）"""
        return FORWARD[self.side]

    def pseudo_moves(self, board) -> list[Move]:
        """
        生成该棋子在当前棋盘上"几何上可行"的所有走法。
        子类必须重写此方法。基类默认返回空列表（供王城等不可移动棋子直接复用）。
        """
        return []

    def clone(self) -> "Piece":
        """复制一个棋子实例（用于棋盘深拷贝，例如 rules.py 模拟走棋时使用）"""
        new_piece = self.__class__(self.side, self.position)
        new_piece.has_moved = self.has_moved
        new_piece.promoted = self.promoted
        return new_piece

    def __str__(self) -> str:
        prefix = "黑" if self.side == Side.BLACK else "白"
        mark = "+" if self.promoted else ""
        return f"{prefix}{self.symbol}{mark}"

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} {self.side.value} at {self.position}>"


# ---------------------------------------------------------------------------
# 兵 Pawn
# ---------------------------------------------------------------------------

class Pawn(Piece):
    name = "兵"
    symbol = "兵"
    notation = ""

    def pseudo_moves(self, board) -> list[Move]:
        origin = self.position
        fwd = self.forward()
        moves: list[Move] = []

        if self.promoted:
            # 过河兵：正前、斜前(左)、斜前(右)、左、右 共5个方向各一格，可走可吃
            directions = [(0, fwd), (1, fwd), (-1, fwd), (1, 0), (-1, 0)]
            for dx, dy in directions:
                dest = (origin[0] + dx, origin[1] + dy)
                if not is_valid_coord(*dest):
                    continue
                occupant = board.get(dest)
                if occupant is None:
                    moves.append(Move(origin, dest, False))
                elif occupant.side != self.side:
                    moves.append(Move(origin, dest, True))
            return moves

        # 未升级：只能正前方向走/吃
        one_step = (origin[0], origin[1] + fwd)
        if not is_valid_coord(*one_step):
            return moves

        occupant = board.get(one_step)
        if occupant is None:
            moves.append(Move(origin, one_step, False))
            if not self.has_moved:
                two_step = (origin[0], origin[1] + fwd * 2)
                if is_valid_coord(*two_step) and board.is_empty(two_step):
                    moves.append(Move(origin, two_step, False))
        elif occupant.side != self.side:
            moves.append(Move(origin, one_step, True))

        return moves


# ---------------------------------------------------------------------------
# 弩车 Ballista
# ---------------------------------------------------------------------------

class Ballista(Piece):
    name = "弩车"
    symbol = "弩"
    notation = "B"
    MAX_RANGE = 4

    def pseudo_moves(self, board) -> list[Move]:
        origin = self.position
        moves: list[Move] = []

        for dest in ranged_moves(board, origin, DIAGONAL_DIRS, self.MAX_RANGE):
            moves.append(Move(origin, dest, False))

        fwd = self.forward()
        forward_diagonals = [(1, fwd), (-1, fwd)]
        for dest in direct_capture_targets(board, origin, self.side, forward_diagonals, distance=1):
            moves.append(Move(origin, dest, True))

        for dest in screen_capture_targets(board, origin, self.side, DIAGONAL_DIRS, self.MAX_RANGE):
            moves.append(Move(origin, dest, True))

        return moves


# ---------------------------------------------------------------------------
# 炮塔 Turret
# ---------------------------------------------------------------------------

class Turret(Piece):
    name = "炮塔"
    symbol = "炮"
    notation = "T"
    BASE_RANGE = 4
    UPGRADED_RANGE = 5

    # 首步专属：横向（左右，不含竖直）射程临时变成5格
    # 注意：这不是"无阻挡直接吃子"，依然要走隔山打牛（必须有炮架），
    # 只是把炮架的可选范围从4格延伸到5格。
    # 一旦这个炮走过一步（不论是否用了这个横向5格特权），has_moved 变 True，
    # 此特权永久消失，之后横向、竖直都统一按 max_range 处理。
    FIRST_MOVE_HORIZONTAL_RANGE = 5
    HORIZONTAL_DIRS = [(1, 0), (-1, 0)]
    VERTICAL_DIRS = [(0, 1), (0, -1)]

    @property
    def max_range(self) -> int:
        return self.UPGRADED_RANGE if self.promoted else self.BASE_RANGE

    def pseudo_moves(self, board) -> list[Move]:
        origin = self.position
        moves: list[Move] = []

        horizontal_range = self.FIRST_MOVE_HORIZONTAL_RANGE if not self.has_moved else self.max_range
        vertical_range = self.max_range

        # 移动：横向、竖直分开处理，射程可能不同
        for dest in ranged_moves(board, origin, self.HORIZONTAL_DIRS, horizontal_range):
            moves.append(Move(origin, dest, False))
        for dest in ranged_moves(board, origin, self.VERTICAL_DIRS, vertical_range):
            moves.append(Move(origin, dest, False))

        # 直接吃子：正前方一格，不受横向特权影响
        fwd = self.forward()
        for dest in direct_capture_targets(board, origin, self.side, [(0, fwd)], distance=1):
            moves.append(Move(origin, dest, True))

        # 隔山打牛：横向、竖直分开处理，射程可能不同（仍然需要炮架，方案A）
        for dest in screen_capture_targets(board, origin, self.side, self.HORIZONTAL_DIRS, horizontal_range):
            moves.append(Move(origin, dest, True))
        for dest in screen_capture_targets(board, origin, self.side, self.VERTICAL_DIRS, vertical_range):
            moves.append(Move(origin, dest, True))

        return moves


# ---------------------------------------------------------------------------
# 大将 Ares
# ---------------------------------------------------------------------------

class Ares(Piece):
    name = "大将"
    symbol = "将"
    notation = "A"
    MAX_RANGE = 2

    def pseudo_moves(self, board) -> list[Move]:
        origin = self.position
        moves: list[Move] = []

        for dest in ranged_moves(board, origin, EIGHT_DIRS, self.MAX_RANGE):
            moves.append(Move(origin, dest, False))

        for dx, dy in EIGHT_DIRS:
            for dist in range(1, self.MAX_RANGE + 1):
                dest = (origin[0] + dx * dist, origin[1] + dy * dist)
                if not is_valid_coord(*dest):
                    break
                occupant = board.get(dest)
                if occupant is not None and occupant.side != self.side:
                    moves.append(Move(origin, dest, True))

        return moves


# ---------------------------------------------------------------------------
# 轻骑士 Hussar
# ---------------------------------------------------------------------------

_HUSSAR_OFFSETS = [
    (3, 1), (3, -1), (-3, 1), (-3, -1),
    (1, 3), (1, -3), (-1, 3), (-1, -3),
    (3, 0), (-3, 0), (0, 3), (0, -3),
]


class Hussar(Piece):
    name = "轻骑士"
    symbol = "马"
    notation = "H"

    def pseudo_moves(self, board) -> list[Move]:
        origin = self.position
        move_targets, capture_targets = leap_targets(board, origin, self.side, _HUSSAR_OFFSETS)
        moves = [Move(origin, d, False) for d in move_targets]
        moves += [Move(origin, d, True) for d in capture_targets]
        return moves


# ---------------------------------------------------------------------------
# 重骑士 Knight
# ---------------------------------------------------------------------------

_KNIGHT_OFFSETS = [
    (2, 1), (2, -1), (-2, 1), (-2, -1),
    (1, 2), (1, -2), (-1, 2), (-1, -2),
    (3, 1), (3, -1), (-3, 1), (-3, -1),
    (1, 3), (1, -3), (-1, 3), (-1, -3),
]


class Knight(Piece):
    name = "重骑士"
    symbol = "骑"
    notation = "N"

    def pseudo_moves(self, board) -> list[Move]:
        origin = self.position
        move_targets, capture_targets = leap_targets(board, origin, self.side, _KNIGHT_OFFSETS)
        moves = [Move(origin, d, False) for d in move_targets]
        moves += [Move(origin, d, True) for d in capture_targets]
        return moves


# ---------------------------------------------------------------------------
# 攻城塔 Rook
# ---------------------------------------------------------------------------

class Rook(Piece):
    name = "攻城塔"
    symbol = "車"
    notation = "R"   # 沿用国际象棋习惯

    def pseudo_moves(self, board) -> list[Move]:
        origin = self.position
        move_targets, capture_targets = sliding_moves(board, origin, STRAIGHT_DIRS, self.side)
        moves = [Move(origin, d, False) for d in move_targets]
        moves += [Move(origin, d, True) for d in capture_targets]
        return moves


# ---------------------------------------------------------------------------
# 凤凰 Phoenix
# ---------------------------------------------------------------------------

class Phoenix(Piece):
    name = "凤凰"
    symbol = "凤"
    notation = "P"

    def pseudo_moves(self, board) -> list[Move]:
        origin = self.position
        move_targets, capture_targets = sliding_moves(board, origin, DIAGONAL_DIRS, self.side)
        moves = [Move(origin, d, False) for d in move_targets]
        moves += [Move(origin, d, True) for d in capture_targets]
        return moves


# ---------------------------------------------------------------------------
# 剑士 Swordsman
# ---------------------------------------------------------------------------

class Swordsman(Piece):
    name = "剑士"
    symbol = "士"
    notation = "S"

    def pseudo_moves(self, board) -> list[Move]:
        origin = self.position
        fwd = self.forward()
        moves: list[Move] = []

        if self.promoted:
            # 升级后规则一：8方向（米字格）恰好走2格，可越子，可吃子
            for dx, dy in EIGHT_DIRS:
                dest = (origin[0] + dx * 2, origin[1] + dy * 2)
                if not is_valid_coord(*dest):
                    continue
                occupant = board.get(dest)
                if occupant is None:
                    moves.append(Move(origin, dest, False))
                elif occupant.side != self.side:
                    moves.append(Move(origin, dest, True))

            # 升级后规则二：斜线4方向（斜前+斜后）恰好走1格，仅移动，不可吃子
            for dx, dy in DIAGONAL_DIRS:
                dest = (origin[0] + dx, origin[1] + dy)
                if not is_valid_coord(*dest):
                    continue
                if board.is_empty(dest):
                    moves.append(Move(origin, dest, False))

            return moves

        # 未升级：
        # 1) 正前、斜前(左)、斜前(右) 各走2格，可越子，可吃子
        two_step_dirs = [(0, fwd), (1, fwd), (-1, fwd)]
        for dx, dy in two_step_dirs:
            dest = (origin[0] + dx * 2, origin[1] + dy * 2)
            if not is_valid_coord(*dest):
                continue
            occupant = board.get(dest)
            if occupant is None:
                moves.append(Move(origin, dest, False))
            elif occupant.side != self.side:
                moves.append(Move(origin, dest, True))

        # 2) 斜前方一格：仅可移动，不可吃子
        one_step_diag = [(1, fwd), (-1, fwd)]
        for dx, dy in one_step_diag:
            dest = (origin[0] + dx, origin[1] + dy)
            if not is_valid_coord(*dest):
                continue
            if board.is_empty(dest):
                moves.append(Move(origin, dest, False))

        return moves


# ---------------------------------------------------------------------------
# 战车 Chariot
# ---------------------------------------------------------------------------

class Chariot(Piece):
    name = "战车"
    symbol = "輣"
    notation = "C"

    def pseudo_moves(self, board) -> list[Move]:
        origin = self.position
        moves: list[Move] = []
        distances = (2, 3, 4) if self.promoted else (2, 3)

        for dx, dy in STRAIGHT_DIRS:
            for dist in distances:
                dest = (origin[0] + dx * dist, origin[1] + dy * dist)
                if not is_valid_coord(*dest):
                    continue
                occupant = board.get(dest)
                if occupant is None:
                    moves.append(Move(origin, dest, False))
                elif occupant.side != self.side:
                    moves.append(Move(origin, dest, True))

        return moves


# ---------------------------------------------------------------------------
# 王城 Throne
# ---------------------------------------------------------------------------

class Throne(Piece):
    name = "王城"
    symbol = "楚"
    notation = "TH"

    def pseudo_moves(self, board) -> list[Move]:
        return []


# ---------------------------------------------------------------------------
# 棋子注册表（供 layout.py 摆盘时使用）
# ---------------------------------------------------------------------------

PIECE_CLASSES = {
    "pawn": Pawn,
    "ballista": Ballista,
    "turret": Turret,
    "ares": Ares,
    "hussar": Hussar,
    "knight": Knight,
    "rook": Rook,
    "phoenix": Phoenix,
    "swordsman": Swordsman,
    "chariot": Chariot,
    "throne": Throne,
}


# ---------------------------------------------------------------------------
# 简单自检（与 v1.2 相同，用于确认迁移后行为一致）
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    from board import Board, parse_coord

    board = Board()

    ares = Ares(Side.BLACK, parse_coord("f6"))
    board.set(ares.position, ares)
    ares_moves = ares.pseudo_moves(board)
    assert len(ares_moves) == 16, f"大将走法数量异常: {len(ares_moves)}"
    board.remove(ares.position)

    pawn = Pawn(Side.BLACK, parse_coord("d5"))
    board.set(pawn.position, pawn)
    pawn_moves = pawn.pseudo_moves(board)
    assert len(pawn_moves) == 2
    two_step_dest = [m.to_sq for m in pawn_moves if m.to_sq[1] == pawn.position[1] + 2][0]
    assert two_step_dest == parse_coord("d7"), "双步应该落在d7（尚未到达升变排）"
    assert two_step_dest[1] not in pawn_promotion_zone(Side.BLACK), "d7不是兵的升变排，d8才是"
    assert 8 in pawn_promotion_zone(Side.BLACK) and len(list(pawn_promotion_zone(Side.BLACK))) == 1
    board.remove(pawn.position)

    ballista = Ballista(Side.BLACK, parse_coord("a1"))
    screen = Pawn(Side.WHITE, parse_coord("c3"))
    target = Pawn(Side.WHITE, parse_coord("e5"))
    board.set(ballista.position, ballista)
    board.set(screen.position, screen)
    board.set(target.position, target)
    b_moves = ballista.pseudo_moves(board)
    capture_dests = [m.to_sq for m in b_moves if m.is_capture]
    assert parse_coord("e5") in capture_dests
    assert parse_coord("c3") not in capture_dests
    board.remove(ballista.position)
    board.remove(screen.position)
    board.remove(target.position)

    # 炮塔首步横向5格测试：a4 炮架在 c4，目标在 f4（横向距离5，需要炮架）
    turret = Turret(Side.BLACK, parse_coord("a4"))
    screen2 = Pawn(Side.WHITE, parse_coord("c4"))
    target2 = Pawn(Side.WHITE, parse_coord("f4"))
    board.set(turret.position, turret)
    board.set(screen2.position, screen2)
    board.set(target2.position, target2)
    t_moves = turret.pseudo_moves(board)
    t_capture_dests = [m.to_sq for m in t_moves if m.is_capture]
    assert parse_coord("f4") in t_capture_dests, "首步炮塔应能横向5格隔炮架吃到f4"
    # 没有炮架的情况下，即使横向5格内有敌方棋子，也不能吃（方案A：必须要有炮架）
    board.remove(screen2.position)
    t_moves_no_screen = turret.pseudo_moves(board)
    assert parse_coord("f4") not in [m.to_sq for m in t_moves_no_screen if m.is_capture], \
        "没有炮架时，即使在5格射程内也不该吃到"
    # 竖直方向不受首步特权影响，仍然只有4格
    assert all(
        not (m.to_sq[0] == turret.position[0] and abs(m.to_sq[1] - turret.position[1]) == 5)
        for m in t_moves if not m.is_capture
    ), "竖直方向不应该有5格的移动"
    # 走过一步之后，横向5格特权应该消失
    turret.has_moved = True
    board.set(screen2.position, screen2)  # 把炮架放回去
    t_moves_after = turret.pseudo_moves(board)
    assert parse_coord("f4") not in [m.to_sq for m in t_moves_after if m.is_capture], \
        "走过一步后，横向5格特权应该消失"
    board.remove(turret.position)
    board.remove(screen2.position)
    board.remove(target2.position)

    sw = Swordsman(Side.BLACK, parse_coord("f6"))
    sw.promoted = True
    enemy_2away = Pawn(Side.WHITE, parse_coord("f8"))
    enemy_1away_diag = Pawn(Side.WHITE, parse_coord("g7"))
    board.set(sw.position, sw)
    board.set(enemy_2away.position, enemy_2away)
    board.set(enemy_1away_diag.position, enemy_1away_diag)
    sw_moves = sw.pseudo_moves(board)
    assert Move(sw.position, parse_coord("f8"), True) in sw_moves
    assert parse_coord("g7") not in [m.to_sq for m in sw_moves]
    board.remove(sw.position)
    board.remove(enemy_2away.position)
    board.remove(enemy_1away_diag.position)

    # 升级兵测试：5个方向（正前、斜前左、斜前右、左、右）各一格，可走可吃
    promo_pawn = Pawn(Side.BLACK, parse_coord("f6"))
    promo_pawn.promoted = True
    board.set(promo_pawn.position, promo_pawn)
    promo_pawn_moves = promo_pawn.pseudo_moves(board)
    expected_dests = {parse_coord(c) for c in ("f7", "e7", "g7", "e6", "g6")}
    actual_dests = {m.to_sq for m in promo_pawn_moves}
    assert actual_dests == expected_dests, f"升级兵走法应为5个方向，实际: {actual_dests}"
    board.remove(promo_pawn.position)

    # 升级战车测试：射程从2-3格扩展为2-4格，依然不能只走1格
    promo_chariot = Chariot(Side.BLACK, parse_coord("f6"))
    promo_chariot.promoted = True
    board.set(promo_chariot.position, promo_chariot)
    promo_chariot_moves = promo_chariot.pseudo_moves(board)
    assert parse_coord("f10") in [m.to_sq for m in promo_chariot_moves], "升级战车应能走4格"
    assert parse_coord("f7") not in [m.to_sq for m in promo_chariot_moves], "升级战车依然不能只走1格"
    board.remove(promo_chariot.position)

    print("pieces.py v1.3 自检全部通过 ✅（与 movement.py 集成后行为保持一致）")
    print(f"共注册棋子种类: {len(PIECE_CLASSES)} 种")
    for key, cls in PIECE_CLASSES.items():
        print(f"  {key:10s} -> {cls.name}（{cls.symbol}）记谱: {cls.notation}")
