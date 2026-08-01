"""
clock.py
========
Arkatana（古战棋）— 棋钟系统

职责范围：
    管理一局对局双方的剩余时间，判断是否超时。
    纯逻辑模块，不涉及网络/数据库；依赖真实时间（datetime），
    但所有方法都支持传入一个显式的 now 参数，方便自检测试时模拟时间流逝，
    不需要真的等待。

核心概念：
    - TimeControl：一局棋的时间控制设定（每方基础分钟数 + 每步加秒）
    - Clock：某一局棋的实时棋钟状态，管理双方"剩余时间"，
      并且知道"现在轮到谁思考、这一步已经想了多久"

无时间限制模式：
    TimeControl 为 None 时，Clock 的所有方法都是空操作，
    time_left() 恒返回 None，is_timeout() 恒返回 False——
    用于"线下对练"这类不设时间控制的模式。

使用方式（典型流程）：
    clock = Clock(TimeControl(minutes_per_side=10, increment_seconds=5))
    clock.start_turn("black")          # 黑方开始思考
    ...过了一段时间，黑方走了一步...
    clock.commit_move("black")         # 结算黑方这一步用掉的时间，加上加秒
    clock.start_turn("white")          # 换白方开始思考
"""

from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

# 时间控制的合法取值区间（前端滑杆只能选这些离散档位，后端也据此校验）
# 每方分钟数从1开始（不能是0分钟）；每步加秒数可以是0（比如常见的"10+0""30+0"）
ALLOWED_MINUTES_PER_SIDE: tuple[int, ...] = tuple(
    list(range(1, 21)) + [25, 30, 35, 40, 45, 50, 55, 60, 75, 90, 105, 120, 135, 150, 165, 180]
)
ALLOWED_INCREMENT_SECONDS: tuple[int, ...] = tuple([0]) + ALLOWED_MINUTES_PER_SIDE


@dataclass(frozen=True)
class TimeControl:
    """一局棋的时间控制设定：每方基础分钟数 + 每步加秒"""
    minutes_per_side: int
    increment_seconds: int

    def __post_init__(self) -> None:
        if self.minutes_per_side not in ALLOWED_MINUTES_PER_SIDE:
            raise ValueError(f"不支持的每方分钟数: {self.minutes_per_side}")
        if self.increment_seconds not in ALLOWED_INCREMENT_SECONDS:
            raise ValueError(f"不支持的每步加秒数: {self.increment_seconds}")

    @property
    def initial_seconds(self) -> float:
        return self.minutes_per_side * 60.0

    def __str__(self) -> str:
        return f"{self.minutes_per_side}+{self.increment_seconds}"


class Clock:
    """
    管理一局棋双方的剩余时间。
    time_control 为 None 表示无时间限制（比如"线下对练"模式），
    这种情况下所有方法都是空操作，永远不会超时。
    """

    def __init__(self, time_control: Optional[TimeControl]):
        self.time_control = time_control
        if time_control is not None:
            self.remaining: dict[str, Optional[float]] = {
                "black": time_control.initial_seconds,
                "white": time_control.initial_seconds,
            }
        else:
            self.remaining = {"black": None, "white": None}

        self.turn_started_at: Optional[datetime] = None  # 当前这一步是什么时候开始计时的
        self.active_side: Optional[str] = None  # 当前正在计时的是哪一方（"black"/"white"）

    @property
    def is_unlimited(self) -> bool:
        return self.time_control is None

    def start_turn(self, side: str, now: Optional[datetime] = None) -> None:
        """标记"从现在开始轮到 side 思考"，记录起始时间"""
        if self.is_unlimited:
            return
        self.active_side = side
        self.turn_started_at = now or datetime.now(timezone.utc)

    def elapsed_seconds(self, now: Optional[datetime] = None) -> float:
        """当前这一步已经思考了多久（还没结算进 remaining 里的部分）"""
        if self.is_unlimited or self.turn_started_at is None:
            return 0.0
        now = now or datetime.now(timezone.utc)
        return max(0.0, (now - self.turn_started_at).total_seconds())

    def time_left(self, side: str, now: Optional[datetime] = None) -> Optional[float]:
        """
        查询某一方"此刻"实际剩余的时间：如果正轮到他思考，
        会扣掉这一步已经流逝的部分；无时间限制则返回 None。
        """
        if self.is_unlimited:
            return None
        base = self.remaining[side]
        if side == self.active_side:
            base = base - self.elapsed_seconds(now)
        return max(0.0, base)

    def is_timeout(self, side: str, now: Optional[datetime] = None) -> bool:
        """某一方此刻是否已经超时（剩余时间归零）"""
        left = self.time_left(side, now)
        return left is not None and left <= 0

    def commit_move(self, side: str, now: Optional[datetime] = None) -> None:
        """
        side 刚刚合法地走完一步棋：把这一步实际用掉的思考时间从 remaining 里扣掉，
        再加上这局棋约定的每步加秒。
        调用方需要在这之后自己调用 start_turn() 把棋钟切给对方。
        """
        if self.is_unlimited:
            return
        elapsed = self.elapsed_seconds(now)
        new_remaining = max(0.0, self.remaining[side] - elapsed) + self.time_control.increment_seconds
        self.remaining[side] = new_remaining
        self.turn_started_at = None
        self.active_side = None

    def clone(self) -> "Clock":
        """深拷贝一份棋钟状态，供 Game 的悔棋快照机制使用"""
        new_clock = Clock(self.time_control)
        new_clock.remaining = dict(self.remaining)
        new_clock.turn_started_at = self.turn_started_at
        new_clock.active_side = self.active_side
        return new_clock


# ---------------------------------------------------------------------------
# 简单自检
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    from datetime import timedelta

    T0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

    # 1) 无时间限制模式：所有方法都是空操作
    unlimited = Clock(None)
    unlimited.start_turn("black", now=T0)
    assert unlimited.time_left("black", now=T0 + timedelta(hours=999)) is None
    assert unlimited.is_timeout("black", now=T0 + timedelta(hours=999)) is False

    # 2) 基础计时：10分钟+5秒，黑方思考了30秒后走棋
    tc = TimeControl(minutes_per_side=10, increment_seconds=5)
    clock = Clock(tc)
    assert clock.time_left("black") == 600.0
    assert clock.time_left("white") == 600.0

    clock.start_turn("black", now=T0)
    mid = T0 + timedelta(seconds=30)
    assert clock.time_left("black", now=mid) == 570.0, "思考30秒后应剩余570秒"
    assert clock.time_left("white", now=mid) == 600.0, "没轮到白方思考，白方时间不应该变化"

    clock.commit_move("black", now=mid)
    # 600 - 30(用掉的) + 5(加秒) = 575
    assert clock.remaining["black"] == 575.0, f"结算后黑方剩余时间异常: {clock.remaining['black']}"

    clock.start_turn("white", now=mid)
    later = mid + timedelta(seconds=45)
    assert clock.time_left("white", now=later) == 555.0, "白方思考45秒后应剩余555秒"
    # 黑方这时候没在计时，剩余时间应该维持在刚结算完的575，不会继续流逝
    assert clock.time_left("black", now=later) == 575.0

    # 3) 超时判定
    fast_tc = TimeControl(minutes_per_side=1, increment_seconds=0)
    fast_clock = Clock(fast_tc)
    fast_clock.start_turn("black", now=T0)
    assert fast_clock.is_timeout("black", now=T0 + timedelta(seconds=59)) is False
    assert fast_clock.is_timeout("black", now=T0 + timedelta(seconds=61)) is True
    # 超时后 time_left 不应该是负数，应该封底在0
    assert fast_clock.time_left("black", now=T0 + timedelta(seconds=999)) == 0.0

    # 4) 悔棋场景：clone() 应该完整复制棋钟状态，且互不影响
    tc2 = TimeControl(minutes_per_side=5, increment_seconds=3)
    original = Clock(tc2)
    original.start_turn("black", now=T0)
    snapshot = original.clone()

    # 原始棋钟继续往前走
    original.commit_move("black", now=T0 + timedelta(seconds=10))
    original.start_turn("white", now=T0 + timedelta(seconds=10))

    # 快照应该还停留在"黑方刚开始思考"的那一刻，不受原始棋钟后续变化影响
    assert snapshot.active_side == "black"
    assert snapshot.remaining["black"] == 300.0  # 5分钟=300秒，还没结算过
    assert snapshot.time_left("black", now=T0 + timedelta(seconds=10)) == 290.0

    # 5) 合法取值区间：加秒可以是0（比如常见的"10+0"），分钟数不能是0
    TimeControl(minutes_per_side=10, increment_seconds=0)  # 不应该报错
    TimeControl(minutes_per_side=30, increment_seconds=0)  # 不应该报错
    try:
        TimeControl(minutes_per_side=0, increment_seconds=5)
        raise AssertionError("0分钟不应该是合法的时间控制")
    except ValueError:
        pass
    try:
        TimeControl(minutes_per_side=10, increment_seconds=21)  # 21不在离散档位里（20之后跳到25）
        raise AssertionError("不在合法区间内的加秒数应该报错")
    except ValueError:
        pass

    print("clock.py 自检全部通过 ✅")
    print()
    print(f"示例：{tc} 时间控制，黑方思考30秒后走棋，结算后剩余: {clock.remaining['black']}秒")
