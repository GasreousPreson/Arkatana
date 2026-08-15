"""
ai/search.py
=============
minimax + alpha-beta 搜索，给定一个局面和搜索深度，找出（在这个深度下）
黑方视角评分最优的一步棋。

评分约定跟 evaluate.py 保持一致：正数=黑方(先手)有利，负数=白方有利。
黑方走棋时求分数最大化，白方走棋时求分数最小化。

性能提示（先跑通再优化，第2步不追求极致速度）：
    每个搜索节点都调用 engine_bridge.get_legal_moves()，它内部对每个候选
    走法都要 clone+apply+is_in_check 来判断"走完会不会送将"——这是当前最大的
    性能瓶颈。先用命令行脚本验证棋力和基本可用的速度，真影响到 Play against
    AI 的响应时间/自对弈吞吐量了，再考虑用"增量维护是否被将军"之类的办法
    优化，不在这一步做。

依赖：engine_bridge.py、evaluate.py（都是纯离线模块，不依赖网站后端）
"""

from __future__ import annotations

import time
from typing import Optional

from engine_bridge import (
    BLACK, Move, Position, apply_move, get_legal_moves, has_only_throne,
    is_checkmate, other_side,
)
from evaluate import evaluate

# 杀城/残局的分值，要远大于 evaluate() 正常情况下可能算出的任何组合
# （子力总和顶天 29*4.9≈142，安全分、活跃性分量级更小，10万分绝对压得住）
MATE_SCORE = 100_000.0


class SearchResult:
    __slots__ = ("best_move", "score", "nodes", "elapsed")

    def __init__(self, best_move: Optional[Move], score: float, nodes: int, elapsed: float):
        self.best_move = best_move
        self.score = score
        self.nodes = nodes
        self.elapsed = elapsed

    def __repr__(self) -> str:
        mv = f"{self.best_move.from_sq}->{self.best_move.to_sq}" if self.best_move else "None"
        return f"SearchResult(move={mv}, score={self.score:.2f}, nodes={self.nodes}, {self.elapsed:.2f}s)"


def _terminal_value(pos: Position, side_to_move: int, depth_left: int):
    """side_to_move 是接下来要走棋、但可能已经无路可走的一方。
    若已分出胜负，返回 (True, 分值)；否则 (False, None)。
    分值额外叠加 depth_left，让"更快分出胜负"的分数更极端——搜索因此会倾向
    选最快获胜/最晚失败的路线，而不是必胜局面里随便应付。"""
    if has_only_throne(pos, side_to_move) or is_checkmate(pos, side_to_move):
        # side_to_move 一方处于必败状态
        return True, (-MATE_SCORE - depth_left if side_to_move == BLACK else MATE_SCORE + depth_left)
    return False, None


def _order_moves(moves: list[Move]) -> list[Move]:
    """吃子优先——让 alpha-beta 更快遇到强走法，剪枝剪得更狠。
    第2步先用这个简单规则，以后可以升级成 MVV-LVA 或历史启发表。"""
    return sorted(moves, key=lambda m: 0 if m.is_capture else 1)


def _minimax(pos: Position, depth: int, alpha: float, beta: float, side_to_move: int, nodes: list[int]) -> float:
    nodes[0] += 1

    done, terminal_val = _terminal_value(pos, side_to_move, depth)
    if done:
        return terminal_val

    if depth == 0:
        return evaluate(pos)

    legal = get_legal_moves(pos, side_to_move)
    if not legal:
        # 规则集本身没有覆盖"零合法走法但未被将军"这种边缘情况（29子对29子，
        # 正常对局里全员同时被冻结的概率约等于0），保守返回静态评估分，
        # 不当作必胜/必败处理，避免在从未验证过的边缘状态下给出误导性极端分。
        return evaluate(pos)

    next_side = other_side(side_to_move)
    if side_to_move == BLACK:
        best = float("-inf")
        for move in _order_moves(legal):
            child = pos.clone()
            apply_move(child, move)
            val = _minimax(child, depth - 1, alpha, beta, next_side, nodes)
            if val > best:
                best = val
            if best > alpha:
                alpha = best
            if alpha >= beta:
                break
        return best
    else:
        best = float("inf")
        for move in _order_moves(legal):
            child = pos.clone()
            apply_move(child, move)
            val = _minimax(child, depth - 1, alpha, beta, next_side, nodes)
            if val < best:
                best = val
            if best < beta:
                beta = best
            if alpha >= beta:
                break
        return best


def find_best_move(pos: Position, side_to_move: int, depth: int) -> SearchResult:
    """根节点单独展开（而不是直接调用 _minimax 再反查），方便拿到 best_move 本身，
    也方便未来加迭代加深/根节点专属的走法排序策略。"""
    t0 = time.time()
    nodes = [0]

    done, terminal_val = _terminal_value(pos, side_to_move, depth)
    if done:
        return SearchResult(None, terminal_val, 1, time.time() - t0)

    legal = get_legal_moves(pos, side_to_move)
    if not legal:
        return SearchResult(None, evaluate(pos), 1, time.time() - t0)

    next_side = other_side(side_to_move)
    maximizing = side_to_move == BLACK
    best_move: Optional[Move] = None
    best_val = float("-inf") if maximizing else float("inf")
    alpha, beta = float("-inf"), float("inf")

    for move in _order_moves(legal):
        child = pos.clone()
        apply_move(child, move)
        val = _minimax(child, depth - 1, alpha, beta, next_side, nodes)
        if maximizing and (best_move is None or val > best_val):
            best_val, best_move = val, move
            alpha = max(alpha, val)
        elif not maximizing and (best_move is None or val < best_val):
            best_val, best_move = val, move
            beta = min(beta, val)

    return SearchResult(best_move, best_val, nodes[0], time.time() - t0)


if __name__ == "__main__":
    pos = Position.initial()
    result = find_best_move(pos, BLACK, depth=2)
    print("开局深度2搜索结果:", result)
    assert result.best_move is not None, "开局应该能找到一步棋"
    print("search.py 冒烟测试通过 ✅（完整对弈验证见 ai/cli_selfplay.py）")
