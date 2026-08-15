"""
ai/evaluate.py
===============
局面评估函数：给一个局面打一个分数，正数代表黑方（先手）有利，负数代表白方
有利，单位大致对齐"一个兵的价值=1.0分"，方便直觉理解。

三块独立打分，加权求和：
    1. 子力分 material_score      —— 双方棋子价值表之差
    2. 王城安全分 king_safety_score —— 被将军/护卫棋子/开放线路
    3. 活跃性分 mobility_score     —— 双方"几何上可行"的走法数量之差

按你的要求，子力分只是三项之一，不能让它自动占主导——王城安全 > 活跃性 >
子力，具体权重（KING_SAFETY_WEIGHT / MOBILITY_WEIGHT）现在是我按经验拍的
初始值，第5步会用 tune.py 拿自对弈的真实胜负结果去反推更准的权重，
所以这里的常量往后大概率会变，不用现在就纠结到底准不准。

依赖：ai/engine_bridge.py（不依赖网站后端，纯离线可跑）
"""

from __future__ import annotations

from engine_bridge import (
    ARES, BALLISTA, BLACK, CHARIOT, HUSSAR, KNIGHT, PAWN, PHOENIX, ROOK,
    STRAIGHT_DIRS, DIAGONAL_DIRS, SWORDSMAN, THRONE, TURRET, WHITE,
    Position, count_attackers, find_throne, generate_side_moves, is_in_check,
    is_valid_coord, other_side, sq_index,
)


# ---------------------------------------------------------------------------
# 子力价值表（你给的数值，单位=1个兵）
# ---------------------------------------------------------------------------

PIECE_VALUES = {
    PAWN: 1.0,
    SWORDSMAN: 2.7,
    BALLISTA: 3.2,
    HUSSAR: 3.5,
    CHARIOT: 3.6,
    TURRET: 4.0,
    KNIGHT: 4.1,
    PHOENIX: 4.7,
    ARES: 4.8,
    ROOK: 4.9,
    THRONE: 0.0,   # 王城不计子力分——它的价值体现在 king_safety_score，
                   # 而且它不会被真正吃掉（杀城判定会提前结束对局）。
}

# 升变后价值有变化的四种棋子（其余棋子不受 promoted 影响）
PROMOTED_PIECE_VALUES = {
    PAWN: 1.5,
    SWORDSMAN: 3.5,
    CHARIOT: 4.3,
    TURRET: 4.4,
}


def piece_value(piece_type: int, promoted: bool) -> float:
    if promoted and piece_type in PROMOTED_PIECE_VALUES:
        return PROMOTED_PIECE_VALUES[piece_type]
    return PIECE_VALUES[piece_type]


# ---------------------------------------------------------------------------
# 三项权重（第5步 texel tuning 会替换成学出来的值，这里先手写一版合理初始值）
# ---------------------------------------------------------------------------

MATERIAL_WEIGHT = 1.0
MOBILITY_WEIGHT = 0.04     # 每多1步"几何上可行"的走法 = 0.04分，
                           # 100步差距 ≈ 4分，量级上跟丢一个大子相当
KING_SAFETY_WEIGHT = 1.0   # king_safety_score 自身的量级已经调到跟丢子相当，
                           # 这里不再额外放大


# ---------------------------------------------------------------------------
# 子力分
# ---------------------------------------------------------------------------

def material_score(pos: Position) -> float:
    """返回 (黑方子力总值 - 白方子力总值)。"""
    total = 0.0
    for idx in range(len(pos.types)):
        t = pos.types[idx]
        if t == 0:  # EMPTY
            continue
        value = piece_value(t, bool(pos.flags[idx] & 2))  # 2 == PROMOTED flag
        total += value if pos.sides[idx] == BLACK else -value
    return total


# ---------------------------------------------------------------------------
# 王城安全分
# ---------------------------------------------------------------------------

_KING_RING_DIRS = STRAIGHT_DIRS + DIAGONAL_DIRS  # 王城周围8个相邻格

# 检查开放线路时往外看几格：太近了看不出"这条线是不是空的"，
# 太远了每步都算代价又太高，4格是射程最长的几个棋子（弩/炮/大将等）
# 常见有效距离，够用又不至于太慢。
_OPEN_LINE_SCAN_DEPTH = 4


def _king_safety_one_side(pos: Position, side: int) -> float:
    throne = find_throne(pos, side)
    if throne is None:
        # 正常局面不会发生（王城被杀城的瞬间对局就结束了，不会真的从棋盘消失），
        # 但评估函数要对"万一发生"这种异常输入保持健壮，给个中性分而不是崩溃。
        return 0.0

    score = 0.0
    enemy = other_side(side)

    # 1) 是否正被将军——这是最直接、最重的信号
    if is_in_check(pos, side):
        score -= 4.0
        # 被攻击的棋子数量（"双将"比"单将"更危险，即便还没到杀城的地步）
        score -= 1.5 * max(0, count_attackers(pos, side) - 1)

    # 2) 护卫密度：王城周围8格，己方棋子占的比例越高越安全
    tx, ty = throne
    shield = 0
    ring_squares = 0
    for dx, dy in _KING_RING_DIRS:
        dest = (tx + dx, ty + dy)
        if not is_valid_coord(*dest):
            continue
        ring_squares += 1
        idx = sq_index(*dest)
        if pos.types[idx] != 0 and pos.sides[idx] == side:
            shield += 1
    if ring_squares > 0:
        score += 1.2 * (shield / ring_squares)

    # 3) 开放线路：从王城出发8个方向，往外看几格，
    #    如果这条线上最近的棋子是敌方棋子（没有己方棋子先挡住），
    #    按距离给递减的扣分——离得越近威胁越大。
    for dx, dy in _KING_RING_DIRS:
        for dist in range(1, _OPEN_LINE_SCAN_DEPTH + 1):
            dest = (tx + dx * dist, ty + dy * dist)
            if not is_valid_coord(*dest):
                break
            idx = sq_index(*dest)
            if pos.types[idx] == 0:
                continue
            if pos.sides[idx] == enemy:
                score -= 0.5 * (_OPEN_LINE_SCAN_DEPTH + 1 - dist) / _OPEN_LINE_SCAN_DEPTH
            break  # 不管敌我，这条线上第一个挡住的棋子决定了这条线的评价

    return score


def king_safety_score(pos: Position) -> float:
    """返回 (黑方王城安全分 - 白方王城安全分)。"""
    return _king_safety_one_side(pos, BLACK) - _king_safety_one_side(pos, WHITE)


# ---------------------------------------------------------------------------
# 活跃性分（用伪合法走法数量，不做"是否送将"的过滤——那个代价太高，
# 而且活跃性本来就只是个粗略的"这些棋子有多少种可能性"信号，不需要精确）
# ---------------------------------------------------------------------------

def mobility_score(pos: Position) -> float:
    black_moves = len(generate_side_moves(pos, BLACK))
    white_moves = len(generate_side_moves(pos, WHITE))
    return float(black_moves - white_moves)


# ---------------------------------------------------------------------------
# 综合评估
# ---------------------------------------------------------------------------

def evaluate(pos: Position) -> float:
    """正数=黑方(先手)有利，负数=白方有利。"""
    return (
        MATERIAL_WEIGHT * material_score(pos)
        + KING_SAFETY_WEIGHT * king_safety_score(pos)
        + MOBILITY_WEIGHT * mobility_score(pos)
    )


if __name__ == "__main__":
    pos = Position.initial()
    print("开局评估分（理论上应接近0，双方完全对称）:", evaluate(pos))
    assert abs(evaluate(pos)) < 1e-9, "开局是完全对称局面，评估分必须恰好为0"

    # 简单单元检查：黑方多一个兵，评估分应该明显偏向黑方
    pos2 = pos.clone()
    # 拿掉白方一个兵（h8）
    from engine_bridge import parse_coord
    pos2.clear(parse_coord("h8"))
    assert evaluate(pos2) > 0.5, "白方少一个兵，评估分应该明显偏向黑方"

    print("evaluate.py 冒烟测试通过 ✅")
