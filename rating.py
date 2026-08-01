"""
rating.py
=========
Arkatana（古战棋）— Elo 评分系统

职责范围：
    纯粹的评分计算逻辑，不涉及数据库读写（方便独立测试，
    数据库层面的读取/写入交给 db.py 里的封装函数）。

采用标准国际象棋 Elo 公式，并在此基础上加了两层针对本项目的调整：

    1. 新手保护期倍数（按"累计波动额度"判断，不是按局数）：
       新玩家有一个总额度 PROVISIONAL_TOTAL_THRESHOLD（默认320分）。
       每下一局 rated 对局，就用掉"这局在时间控制封顶之后、倍数叠加之前"
       的波动量（绝对值）。倍数从 2.0 开始，随着额度消耗线性降到 1.0，
       额度耗尽后保护期结束，之后恒为 1.0。

       这样设计是为了公平：如果按"局数"算保护期，一直下超快棋的玩家
       因为每局波动本来就小（见下面第2点），保护期"用"的局数虽然一样，
       但总共积累的波动远不如下慢棋的玩家，评分收敛速度不公平地变慢。
       改成按"额度"算之后，不管每局棋的时间控制长短，
       保护期消耗的是同一把"尺子"，快棋玩家自然会用更多局数来消耗同样的额度，
       但最终收敛所需的"总波动量"是公平一致的。

    2. 按时间控制封顶：时间控制越短，单局的"自然"分数波动上限越小；
       每方 10 分钟或以上的时间控制，波动上限固定在 ±20 分，
       更短的时间控制按比例缩小这个上限。
       这个封顶只作用于"标准 Elo 计算出的原始变化量"，
       保护期倍数是在封顶之后再乘上去的——也就是说，
       处于保护期的新玩家，实际单局波动是可以超过 ±20 这个"正常"上限的，
       这是刻意设计（跟 chess.com 早期评分快速收敛的思路一致）。

标准 Elo 公式：
    期望胜率 E_A = 1 / (1 + 10^((R_B - R_A) / 400))
    分数变化 ΔR = K × (实际得分 S - 期望得分 E)
    K = 32（国际通行值）
"""

from __future__ import annotations

K_FACTOR = 32
INITIAL_RATING = 1000
PROVISIONAL_TOTAL_THRESHOLD = 320  # 保护期总"波动额度"，用完即结束


def expected_score(own_rating: float, opponent_rating: float) -> float:
    """标准 Elo 期望胜率公式"""
    return 1 / (1 + 10 ** ((opponent_rating - own_rating) / 400))


def provisional_multiplier(accumulated_progress: float) -> float:
    """
    新手保护期倍数：累计波动额度为 0 时是 2.0，
    随着额度消耗线性降到 1.0，额度达到或超过阈值后恒为 1.0。
    """
    if accumulated_progress >= PROVISIONAL_TOTAL_THRESHOLD:
        return 1.0
    return 2.0 - (accumulated_progress / PROVISIONAL_TOTAL_THRESHOLD)


def rating_change_cap(minutes_per_side: int) -> float:
    """
    按时间控制决定单局"自然"分数波动的封顶值（未叠加保护期倍数之前）：
    每方 10 分钟或以上封顶 ±20 分；更短的时间控制按比例线性缩小。
    """
    if minutes_per_side >= 10:
        return 20.0
    return 20.0 * minutes_per_side / 10


def compute_rating_change(
    own_rating: float,
    opponent_rating: float,
    actual_score: float,
    minutes_per_side: int,
    accumulated_progress: float,
) -> tuple[int, float]:
    """
    综合计算这一局棋应该产生的评分变化量。
    actual_score: 1.0=胜, 0.0=负（本项目目前没有和棋概念，
    保留 0.5 的可能性以防未来规则允许和局）。

    返回 (最终变化量-整数四舍五入, 这局消耗的保护期额度-即封顶后倍数叠加前的绝对值)。
    后者由调用方累加进玩家的保护期进度里。
    """
    expected = expected_score(own_rating, opponent_rating)
    raw_delta = K_FACTOR * (actual_score - expected)

    cap = rating_change_cap(minutes_per_side)
    capped_delta = max(-cap, min(cap, raw_delta))

    multiplier = provisional_multiplier(accumulated_progress)
    final_delta = capped_delta * multiplier

    return round(final_delta), abs(capped_delta)


def apply_game_result(
    black_rating: int,
    white_rating: int,
    black_progress: float,
    white_progress: float,
    winner: str,
    minutes_per_side: int,
) -> tuple[int, int, float, float]:
    """
    计算一局分出胜负的 rated 对局结束后，双方各自的新评分和新的保护期进度。
    winner: "black" 或 "white"
    返回 (黑方新评分, 白方新评分, 黑方新保护期进度, 白方新保护期进度)。
    """
    if winner not in ("black", "white"):
        raise ValueError(f"winner 必须是 'black' 或 'white'，收到: {winner!r}")

    black_score = 1.0 if winner == "black" else 0.0
    white_score = 1.0 if winner == "white" else 0.0

    black_delta, black_used = compute_rating_change(
        black_rating, white_rating, black_score, minutes_per_side, black_progress
    )
    white_delta, white_used = compute_rating_change(
        white_rating, black_rating, white_score, minutes_per_side, white_progress
    )

    new_black_progress = min(PROVISIONAL_TOTAL_THRESHOLD, black_progress + black_used)
    new_white_progress = min(PROVISIONAL_TOTAL_THRESHOLD, white_progress + white_used)

    return (
        black_rating + black_delta,
        white_rating + white_delta,
        new_black_progress,
        new_white_progress,
    )


# ---------------------------------------------------------------------------
# 简单自检
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # 1) 期望胜率
    assert expected_score(1000, 1000) == 0.5
    assert expected_score(1200, 1000) > 0.5
    assert expected_score(1000, 1200) < 0.5

    # 2) 保护期倍数：按额度消耗判断
    assert provisional_multiplier(0) == 2.0
    assert provisional_multiplier(160) == 1.5   # 消耗了一半额度
    assert provisional_multiplier(320) == 1.0
    assert provisional_multiplier(400) == 1.0   # 超过阈值恒为1.0

    # 3) 时间控制封顶
    assert rating_change_cap(10) == 20.0
    assert rating_change_cap(20) == 20.0
    assert rating_change_cap(5) == 10.0
    assert rating_change_cap(1) == 2.0

    # 4) 综合计算：established玩家（额度已耗尽），10分钟时间控制，同分对局黑方赢
    delta, used = compute_rating_change(1000, 1000, 1.0, 10, accumulated_progress=320)
    assert delta == 16, f"established玩家分数变化异常: {delta}"
    assert used == 16.0

    # 5) 全新玩家（额度0），同样场景 -> 16*2.0=32
    delta_new, used_new = compute_rating_change(1000, 1000, 1.0, 10, accumulated_progress=0)
    assert delta_new == 32, f"新手保护期分数变化异常: {delta_new}"
    assert used_new == 16.0, "消耗的额度应该是封顶后、倍数叠加前的原始值"

    # 6) 关键场景：快棋玩家不应该因为局数用光保护期而吃亏
    #    1分钟快棋，封顶只有2分；哪怕连续赢很多局，只要没攒够320的额度，
    #    倍数应该仍然维持在较高的保护期状态（不会像"按局数"设计那样20局后突然掉到1.0）
    progress = 0.0
    for _ in range(20):
        _, used = compute_rating_change(1000, 1000, 1.0, 1, accumulated_progress=progress)
        progress += used
    # 20局1分钟快棋，每局封顶2分，最多消耗40额度，远不到320，保护期应该还没结束
    assert progress < PROVISIONAL_TOTAL_THRESHOLD
    assert provisional_multiplier(progress) > 1.0, "快棋玩家不应该在20局内就被强制结束保护期"

    # 7) apply_game_result：established双方，返回值结构正确
    black_new, white_new, black_prog, white_prog = apply_game_result(
        black_rating=1000, white_rating=1000,
        black_progress=320, white_progress=320,
        winner="black", minutes_per_side=10,
    )
    assert black_new == 1016 and white_new == 984
    assert black_prog == 320 and white_prog == 320  # 已达上限，不会再增加

    # 8) apply_game_result：全新玩家，进度应该正确累加
    black_new2, white_new2, black_prog2, white_prog2 = apply_game_result(
        black_rating=1000, white_rating=1000,
        black_progress=0, white_progress=0,
        winner="black", minutes_per_side=10,
    )
    assert black_new2 == 1032 and white_new2 == 968
    assert black_prog2 == 16.0 and white_prog2 == 16.0

    # 9) 悬殊分差：弱方爆冷获胜
    black_new3, white_new3, _, _ = apply_game_result(
        black_rating=800, white_rating=1400,
        black_progress=320, white_progress=320,
        winner="black", minutes_per_side=10,
    )
    assert black_new3 - 800 > 15
    assert white_new3 - 1400 < -15

    print("rating.py 自检全部通过 ✅")
    print()
    print("示例：established黑方胜(10分钟):", apply_game_result(1000, 1000, 320, 320, "black", 10))
    print("示例：全新玩家黑方胜(10分钟):", apply_game_result(1000, 1000, 0, 0, "black", 10))
    print("示例：800分黑方爆冷战胜1400分白方:", apply_game_result(800, 1400, 320, 320, "black", 10))
