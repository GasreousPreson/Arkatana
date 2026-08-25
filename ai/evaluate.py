"""
ai/evaluate.py
===============
局面评估函数：给一个局面打分，正数=黑方(先手)有利，负数=白方有利，
单位大致对齐"一个兵=1.0分"。

设计思路（2026-08 改版，参考 Stockfish 那一套的组织方式）：
    旧版本用的是"王城安全 > 活跃性 > 子力"这种硬性优先级，实战暴露出很严重的
    问题——AI 一旦脱谱或者处于劣势，就会疯狂用弩车/炮塔换一步"看起来在进攻"
    的棋（Txa8+、Bxd4 这类），本质上是拿4分的大子去换1分的兵。

    现在改成 Stockfish 的做法：**子力分是评估的主干**（权重1.0，不缩放），
    其余各项都是在子力这个主干上做小幅修正，各自权重都远小于一个兵：
        - 王城安全 king_safety：被将军/护卫/开放线，量级控制在半个兵上下
        - 子力活跃度 mobility：合法走法数量差，每步只值0.02分左右
        - 中心控制 center_control：越靠近棋盘中央的子越值钱一点点
        - 先手 tempo：轮到谁走，谁白拿一个很小的加分
    这样"送一个炮塔换一个兵"在任何情况下都不可能被这些修正项翻盘——
    4分的差距不是靠零点几分的活跃度/安全分能补回来的。

    ⚠️ 但要注意：光改这里**治不好送子**。实测（改版前，深度3，12个真实中局
    局面）里有 7/12 步棋会白送2分以上的子，而且几乎每一步选的都是吃子——
    根因是"地平线效应"：深度3的最后一层正好是 AI 自己走，它吃了子就到底了，
    对手的反吃发生在搜索深度之外，看不见。真正的解药是 search.py 里的
    静态搜索（quiescence search），评估函数只是配合。两者缺一不可。

依赖：ai/engine_bridge.py（不依赖网站后端）
"""

from __future__ import annotations

from engine_bridge import (
    ARES, BALLISTA, BLACK, CHARIOT, EMPTY, HUSSAR, KNIGHT, MAX_ROW, MIN_ROW,
    NUM_COLS, PAWN, PHOENIX, PROMOTED, ROOK, STRAIGHT_DIRS, DIAGONAL_DIRS,
    SWORDSMAN, THRONE, TURRET, WHITE, Position, count_attackers, find_throne,
    generate_side_moves, index_to_coord, is_in_check, is_valid_coord,
    other_side, sq_index,
)


# ---------------------------------------------------------------------------
# 子力价值（用户给定的数值，单位=一个兵）
# ---------------------------------------------------------------------------

PIECE_VALUES = {
    PAWN: 1.0, SWORDSMAN: 2.7, BALLISTA: 3.2, HUSSAR: 3.5, CHARIOT: 3.6,
    TURRET: 4.0, KNIGHT: 4.1, PHOENIX: 4.7, ARES: 4.8, ROOK: 4.9,
    THRONE: 0.0,   # 王城不计子力分，价值体现在 king_safety
}

PROMOTED_PIECE_VALUES = {PAWN: 1.5, SWORDSMAN: 3.5, CHARIOT: 4.3, TURRET: 4.4}


def piece_value(piece_type: int, promoted: bool) -> float:
    if promoted and piece_type in PROMOTED_PIECE_VALUES:
        return PROMOTED_PIECE_VALUES[piece_type]
    return PIECE_VALUES[piece_type]


# ---------------------------------------------------------------------------
# 权重：子力=1.0 是主干，其余都是小幅修正项
# ---------------------------------------------------------------------------

MATERIAL_WEIGHT = 1.0
KING_SAFETY_WEIGHT = 0.55     # 整体量级压到"半个兵"上下，不再压过子力
MOBILITY_PER_MOVE = 0.02      # 每多一步合法走法值0.02分：50步的巨大差距≈1个兵
CENTER_WEIGHT = 0.06          # 每个子的中心加成上限约0.06分，纯粹是"同等条件下
                              # 优先往中间走"的轻微倾向，不足以驱动任何弃子
TEMPO_BONUS = 0.15            # 轮到谁走谁白拿一点点，避免评估在偶数/奇数深度
                              # 之间来回跳（Stockfish 的 tempo 也是这个作用）


# ---------------------------------------------------------------------------
# 子力分
# ---------------------------------------------------------------------------

def material_score(pos: Position) -> float:
    total = 0.0
    types, sides, flags = pos.types, pos.sides, pos.flags
    for idx in range(len(types)):
        t = types[idx]
        if t == EMPTY:
            continue
        value = piece_value(t, bool(flags[idx] & PROMOTED))
        total += value if sides[idx] == BLACK else -value
    return total


# ---------------------------------------------------------------------------
# 中心控制
# ---------------------------------------------------------------------------

_CENTER_COL = (NUM_COLS - 1) / 2.0          # 5.0
_CENTER_ROW = (MIN_ROW + MAX_ROW) / 2.0      # 6.5
_MAX_CENTER_DIST = _CENTER_COL + (MAX_ROW - _CENTER_ROW)

# 预计算每个格子的"中心度"（0~1，越靠中间越接近1），避免每次评估都重算
_CENTER_TABLE = []
for _idx in range(NUM_COLS * (MAX_ROW - MIN_ROW + 1)):
    _c, _r = index_to_coord(_idx)
    _dist = abs(_c - _CENTER_COL) + abs(_r - _CENTER_ROW)
    _CENTER_TABLE.append(1.0 - (_dist / _MAX_CENTER_DIST))


def center_control_score(pos: Position) -> float:
    """越靠近棋盘中央的棋子稍微值钱一点。王城不参与（它本来就不该往中间跑，
    而且它根本不能移动）；兵也不参与（兵的"该往哪走"由升变排决定，
    不是往中间挤）。"""
    total = 0.0
    types, sides = pos.types, pos.sides
    for idx in range(len(types)):
        t = types[idx]
        if t == EMPTY or t == THRONE or t == PAWN:
            continue
        v = _CENTER_TABLE[idx]
        total += v if sides[idx] == BLACK else -v
    return total


# ---------------------------------------------------------------------------
# 王城安全
# ---------------------------------------------------------------------------

_KING_RING_DIRS = STRAIGHT_DIRS + DIAGONAL_DIRS
_OPEN_LINE_SCAN_DEPTH = 4


def _king_safety_one_side(pos: Position, side: int) -> float:
    throne = find_throne(pos, side)
    if throne is None:
        return 0.0

    score = 0.0
    enemy = other_side(side)

    # 1) 正在被将军。注意这一项的量级：旧版本给到 -4.0（等于一个炮塔！），
    #    直接导致 AI 愿意送掉大子去换对方一步将军。现在压到 -1.2，
    #    "将一步"再也换不来一个大子。真正的杀棋不靠这里加分——
    #    search.py 里杀城/将死本来就是 ±100000 的绝对分，压得住一切。
    if is_in_check(pos, side):
        score -= 1.2
        score -= 0.5 * max(0, count_attackers(pos, side) - 1)

    # 2) 护卫密度：王城周围8格里己方棋子占比
    tx, ty = throne
    shield = 0
    ring_squares = 0
    for dx, dy in _KING_RING_DIRS:
        dest = (tx + dx, ty + dy)
        if not is_valid_coord(*dest):
            continue
        ring_squares += 1
        idx = sq_index(*dest)
        if pos.types[idx] != EMPTY and pos.sides[idx] == side:
            shield += 1
    if ring_squares > 0:
        score += 0.8 * (shield / ring_squares)

    # 3) 开放线路：王城8个方向往外看，最近的那颗子如果是敌方的，按距离扣分
    for dx, dy in _KING_RING_DIRS:
        for dist in range(1, _OPEN_LINE_SCAN_DEPTH + 1):
            dest = (tx + dx * dist, ty + dy * dist)
            if not is_valid_coord(*dest):
                break
            idx = sq_index(*dest)
            if pos.types[idx] == EMPTY:
                continue
            if pos.sides[idx] == enemy:
                score -= 0.35 * (_OPEN_LINE_SCAN_DEPTH + 1 - dist) / _OPEN_LINE_SCAN_DEPTH
            break

    return score


def king_safety_score(pos: Position) -> float:
    return _king_safety_one_side(pos, BLACK) - _king_safety_one_side(pos, WHITE)


# ---------------------------------------------------------------------------
# 活跃度
# ---------------------------------------------------------------------------

def mobility_score(pos: Position) -> float:
    """用伪合法走法数量之差（不过滤送将，那个代价太高，
    活跃度本来就是个粗略信号，不需要精确）。"""
    return float(len(generate_side_moves(pos, BLACK)) - len(generate_side_moves(pos, WHITE)))


# ---------------------------------------------------------------------------
# 综合
# ---------------------------------------------------------------------------

def evaluate(pos: Position, side_to_move: int | None = None) -> float:
    """正数=黑方有利，负数=白方有利。

    side_to_move 用于 tempo 加分。搜索过程中 pos.side_to_move 这个字段
    是不更新的（apply_move 只管搬棋子），所以调用方必须显式把当前行棋方
    传进来才能拿到正确的 tempo——不传就当作不加 tempo，行为退化成
    纯静态评估，不会算错，只是少了这一项。
    """
    score = (
        MATERIAL_WEIGHT * material_score(pos)
        + KING_SAFETY_WEIGHT * king_safety_score(pos)
        + MOBILITY_PER_MOVE * mobility_score(pos)
        + CENTER_WEIGHT * center_control_score(pos)
    )
    if side_to_move is not None:
        score += TEMPO_BONUS if side_to_move == BLACK else -TEMPO_BONUS
    return score


if __name__ == "__main__":
    from engine_bridge import parse_coord

    pos = Position.initial()
    base = evaluate(pos)
    print("开局评估分（不含tempo，双方完全对称，应为0）:", round(base, 6))
    assert abs(base) < 1e-9, "开局是完全对称局面，不含tempo时评估分必须恰好为0"

    with_tempo = evaluate(pos, BLACK)
    assert abs(with_tempo - TEMPO_BONUS) < 1e-9, "黑方走棋应该拿到 tempo 加分"

    # 关键回归测试：拿一个炮塔换一个兵，任何情况下都必须是明确的坏交易——
    # 这正是旧版本评估函数会做的蠢事（王城安全项权重过大导致的）
    p2 = Position.initial()
    p2.clear(parse_coord("a4"))    # 黑方失去一个炮塔(4.0)
    p2.clear(parse_coord("a8"))    # 白方失去一个兵(1.0)
    delta = evaluate(p2) - base
    print(f"用炮塔(4.0)换兵(1.0)的评估变化: {delta:+.2f}（必须明显为负）")
    assert delta < -2.0, f"炮塔换兵必须是明确的坏交易，实际只有 {delta:+.2f}"

    print("evaluate.py 冒烟测试通过 ✅")
