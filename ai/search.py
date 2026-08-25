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
    BLACK, EMPTY, Move, PROMOTED, Position, apply_move, generate_side_moves,
    get_legal_moves, has_only_throne, is_checkmate, is_in_check, other_side,
    sq_index,
)
from evaluate import evaluate, piece_value

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


# 静态搜索最多再往下追多少层吃子。这个上限是必须的：这个棋种里弩车/炮塔
# 隔子吃子的存在，让"互相吃来吃去"的链条可能拖得很长，不设上限极端情况下
# 会退化成搜索爆炸。
QUIESCENCE_MAX_DEPTH = 3

# delta pruning 的安全余量：即使吃到这个子，再加上这点余量还够不到 alpha，
# 这一支就没有搜下去的价值了。留 2.0（两个兵）是保守值，宁可多搜一点
# 也不要把真正有价值的战术支剪掉。
DELTA_MARGIN = 2.0


def _victim_value(pos: Position, move: Move) -> float:
    idx = sq_index(*move.to_sq)
    t = pos.types[idx]
    if t == EMPTY:
        return 0.0
    return piece_value(t, bool(pos.flags[idx] & PROMOTED))


def _legal_captures_ordered(pos: Position, side: int) -> list[tuple[Move, float]]:
    """静态搜索专用的吃子走法生成，按"被吃的子有多值钱"从大到小排序
    （MVV-LVA 的简化版：先吃大子）。

    不能直接用 get_legal_moves()——实测它要 3.05 毫秒/次（它对**每一个**候选
    走法都要 clone+apply+is_in_check 验一遍合法性），而静态搜索每个节点都要
    调一次，直接把搜索拖垮（第一版就是这么写的，深度2直接跑到超时）。

    改成两步走：先用便宜的 generate_side_moves()（0.14毫秒/次）拿到伪合法
    走法、筛出吃子，再**只对这些吃子**逐个验一次"走完自己王城会不会被将军"。
    吃子走法通常只占全部走法的一小部分，这样省下的是绝大部分验证开销，
    而结果跟 get_legal_moves 筛吃子完全一致，不牺牲任何正确性。

    先吃大子的排序很关键：alpha-beta 越早遇到强走法，剪枝就剪得越狠，
    能直接砍掉大量后续搜索。
    """
    scored = []
    for m in generate_side_moves(pos, side):
        if not m.is_capture:
            continue
        child = pos.clone()
        apply_move(child, m)
        if is_in_check(child, side):
            continue
        scored.append((m, _victim_value(pos, m)))
    scored.sort(key=lambda pair: pair[1], reverse=True)
    return scored


def _quiescence(pos: Position, alpha: float, beta: float, side_to_move: int,
                 nodes: list[int], qdepth: int) -> float:
    """静态搜索（quiescence search）——固定深度搜索最致命的毛病是"地平线效应"：
    深度3的最后一层正好轮到自己走，于是引擎兴高采烈地吃掉一个有保护的子，
    到底了、该评估了，对手的反吃发生在深度之外**根本看不见**，
    评估函数只看到"我刚吃了一个子"，于是给出虚高的分数。
    实测改版前深度3有 7/12 步是这种白送2分以上的棋。

    解法：到达深度0之后不要立刻评估，而是继续**只搜吃子**，一直搜到
    局面"安静"下来（没有吃子可走）为止，再做静态评估。这样"吃了子之后
    会被反吃"这件事一定会在静态搜索里被算出来。

    stand-pat（原地不动的分）是关键一环：轮到我走时我并没有义务非吃不可，
    所以先拿当前局面的静态评估当作保底分，只有当某个吃子能比保底分更好时
    才去走它——这样才不会把"被迫吃子"的假设强加给引擎。
    """
    nodes[0] += 1

    stand_pat = evaluate(pos, side_to_move)

    if qdepth >= QUIESCENCE_MAX_DEPTH:
        return stand_pat

    maximizing = side_to_move == BLACK
    if maximizing:
        if stand_pat >= beta:
            return stand_pat
        if stand_pat > alpha:
            alpha = stand_pat
    else:
        if stand_pat <= alpha:
            return stand_pat
        if stand_pat < beta:
            beta = stand_pat

    captures = _legal_captures_ordered(pos, side_to_move)
    if not captures:
        return stand_pat

    next_side = other_side(side_to_move)
    best = stand_pat

    for move, victim in captures:
        # delta pruning：吃到这个子能带来的最大收益（对方少了 victim 分）
        # 再加上安全余量都够不着 alpha/beta，这一支就不用搜了。
        # 因为上面已经按"被吃的子从大到小"排过序，一旦这里剪枝成立，
        # 后面的吃子只会更不值钱，可以直接整体 break 掉。
        if maximizing:
            if stand_pat + victim + DELTA_MARGIN <= alpha:
                break
        else:
            if stand_pat - victim - DELTA_MARGIN >= beta:
                break

        child = pos.clone()
        apply_move(child, move)
        val = _quiescence(child, alpha, beta, next_side, nodes, qdepth + 1)
        if maximizing:
            if val > best:
                best = val
            if best > alpha:
                alpha = best
            if alpha >= beta:
                break
        else:
            if val < best:
                best = val
            if best < beta:
                beta = best
            if alpha >= beta:
                break

    return best


def _minimax(pos: Position, depth: int, alpha: float, beta: float, side_to_move: int, nodes: list[int]) -> float:
    nodes[0] += 1

    done, terminal_val = _terminal_value(pos, side_to_move, depth)
    if done:
        return terminal_val

    if depth == 0:
        # 到底了不要直接静态评估——先做静态搜索把还没吃完的兑子链算干净，
        # 否则就会出现"吃了子就到底、看不见对手反吃"的地平线效应
        # （详见 _quiescence 的说明）。
        return _quiescence(pos, alpha, beta, side_to_move, nodes, 0)

    legal = get_legal_moves(pos, side_to_move)
    if not legal:
        # 规则集本身没有覆盖"零合法走法但未被将军"这种边缘情况（29子对29子，
        # 正常对局里全员同时被冻结的概率约等于0），保守返回静态评估分，
        # 不当作必胜/必败处理，避免在从未验证过的边缘状态下给出误导性极端分。
        return evaluate(pos, side_to_move)

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


class RootMoveScore:
    __slots__ = ("move", "score")

    def __init__(self, move: Move, score: float):
        self.move = move
        self.score = score


def find_move_distribution(pos: Position, side_to_move: int, depth: int):
    """根节点每一个合法走法各自的 minimax 分值——self_play.py 靠这个做
    "温度采样"：不是每次都死板地选分数最高的那步，而是按分值转成的概率
    分布抽样，让自对弈开局阶段能走出更多样的变着，不然每盘自对弈的开局都
    长得一模一样，攒出来的开局库毫无意义。

    注意：这个函数**不会**被 find_best_move()（Play against AI 用的那个）
    调用，是完全独立的一条路径——原因很直接：要让每个候选走法的分数都
    真实可信、能拿来采样，根节点这一层就不能用"兄弟走法之间互相剪枝"
    （那样分数低的走法会被剪枝提前砍断，根本没搜完，分值不可信）。
    这意味着 find_move_distribution 比 find_best_move 慢不少（实测同一
    局面下慢了5倍多）——这个代价只有自对弈这种后台批量任务能接受，
    绝对不能让 Play against AI 走这条路径，会直接把响应时间拖垮。
    （之前有一版失手把 find_best_move 也改成调用这个函数，实测深度2从
    0.82秒变成5.28秒，已经改回来了——这里特意写清楚，避免以后重蹈覆辙。）

    返回 (candidates, elapsed)，candidates 是 list[RootMoveScore]；
    没有合法走法（终局）时返回 ([], elapsed)。

    分值约定跟 evaluate()/find_best_move() 一致：正数偏向黑方，负数偏向白方——
    self_play.py 自己负责按 side_to_move 转换成"对当前这一方来说好不好"再采样，
    这里不做任何"对哪一方有利"的转换，跟其他函数保持同一套约定，减少心智负担。
    """
    t0 = time.time()
    nodes = [0]

    done, _ = _terminal_value(pos, side_to_move, depth)
    if done:
        return [], time.time() - t0

    legal = get_legal_moves(pos, side_to_move)
    if not legal:
        return [], time.time() - t0

    next_side = other_side(side_to_move)
    candidates: list[RootMoveScore] = []

    for move in _order_moves(legal):
        child = pos.clone()
        apply_move(child, move)
        val = _minimax(child, depth - 1, float("-inf"), float("inf"), next_side, nodes)
        candidates.append(RootMoveScore(move, val))

    return candidates, time.time() - t0


def find_best_move(pos: Position, side_to_move: int, depth: int) -> SearchResult:
    """根节点单独展开（而不是直接调用 _minimax 再反查），方便拿到 best_move 本身，
    也方便未来加迭代加深/根节点专属的走法排序策略。

    走的是根节点互相剪枝的快速路径（Play against AI 用这个，响应速度是硬要求），
    不要改成调用 find_move_distribution()——那个为了保证每个候选分值可信，
    故意不做根节点剪枝，会明显更慢（见 find_move_distribution 的说明）。
    需要"所有候选走法各自的真实分值"时才用 find_move_distribution（目前只有
    self_play.py 的温度采样在用）。"""
    t0 = time.time()
    nodes = [0]

    done, terminal_val = _terminal_value(pos, side_to_move, depth)
    if done:
        return SearchResult(None, terminal_val, 1, time.time() - t0)

    legal = get_legal_moves(pos, side_to_move)
    if not legal:
        return SearchResult(None, evaluate(pos, side_to_move), 1, time.time() - t0)

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
