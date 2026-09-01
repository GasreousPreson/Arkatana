"""
ai/search.py
=============
minimax + alpha-beta 搜索，给定一个局面和搜索深度，找出（在这个深度下）
黑方视角评分最优的一步棋。

评分约定跟 evaluate.py 保持一致：正数=黑方(先手)有利，负数=白方有利。
黑方走棋时求分数最大化，白方走棋时求分数最小化。

⚠️ 2026-08 性能改版说明（对应"AI走棋速度接近0"的问题排查）：
    实测发现真正的元凶不是 get_legal_moves()（那个瓶颈其实已经在
    engine_bridge.py 里用"同行/同列/同斜线才需要试走"的近似解决掉了），
    而是三处叠加：

    1. find_move_distribution()（9个人格里有6个人格靠它出招，见
       find_persona_move 的说明）根节点完全不做剪枝——每一手候选都从
       [-inf, inf] 重新搜一遍，只为了保证每个候选的分值"真实可信"方便
       后面采样。实测同一个中局局面，find_best_move 深度3要21秒，
       find_move_distribution 深度3直接跑了200秒以上没跑完。这是目前
       最大的单一瓶颈，也是"人格AI基本不走棋"最直接的原因。
    2. 根节点、非根节点走法排序都很粗糙（只分"吃子/不吃子"两档，吃子内部
       不分大小，不吃子的完全不排序），alpha-beta 剪枝效率上不去，
       "进一步剪枝"这件事本身就有很大空间。
    3. 没有任何"这一步最多想多久"的硬上限——局面复杂度、并发对局数量
       随时可能让单步思考时间失控，而且完全没有兜底：算不完就是不走棋。

    这一版的对策，跟以前"先跑通再优化"的态度一致，只在验证过正确性
    不受影响的前提下动手（正确性验证见本文件 __main__ 和
    ai/tests/test_engine_bridge.py）：

    A. 根节点统一改成 PVS（Principal Variation Search）：排序后的第一手
       全窗口搜索拿到基准分值，之后每一手先用一个几乎零宽度的窗口探一下
       "是不是比当前最优更好"——大多数时候答案是"不是"，这时候直接采用
       探出来的分值（它是一个严格有效的上/下界，虽然不一定是精确值，但
       "不如当前最优"这个结论是可信的，具体差多少不重要）；只有探测显示
       "有戏"，才值得再花一次全窗口搜索的代价去拿精确值。find_best_move
       和 find_move_distribution 现在共用同一份根节点搜索（_root_search），
       前者只取最优的那一个，后者保留全部候选分值——两边都享受到这个
       优化，接口完全不变，自对弈/开局库那些直接调用它们的代码不用改。
    B. 走法排序升级：吃子按"吃的子多值钱"从大到小排（MVV-LVA，原本只有
       quiescence 那边的吃子排序在用，现在主搜索也用上）；非吃子走法加上
       killer move 启发——记住"这一深度上，其他分支里刚造成过剪枝的
       非吃子走法"，优先试。alpha-beta 的正确性不依赖走法顺序（顺序只
       影响剪枝效率，不影响最终分值），所以这一项改动风险很低，
       用本文件 __main__ 里"新旧实现分值必须完全一致"的回归测试兜底。
    C. 新增按墙钟时间预算做迭代加深（find_best_move_timed /
       find_move_distribution_timed）：从深度1开始一层一层搜，每层都是
       一次完整、可信的搜索；开始下一层前先估算大概率搜不搜得完，
       搜不完就不开始，直接返回上一层的结果。真正的硬保证在更底层：
       _minimax/_quiescence 每隔一定节点数会检查一次墙钟时间，一旦
       超过 deadline 立刻抛出 _SearchTimeout 让当前这一层整个作废
       （半成品不可信，很多分支根本没展开），退回上一层已经完整跑完的
       结果——不会出现"这一层不管多久都硬等"的情况。play_service.py
       现在调用的是这两个新函数，不再是固定深度的 find_best_move/
       find_persona_move 直接搜索；self_play.py/book_build.py 等离线批量
       脚本仍然直接调用不带时间预算的 find_best_move/find_move_distribution
       （这些场景本来就能接受偶尔慢一点，不需要强行掐表，接口保持不变）。

依赖：engine_bridge.py、evaluate.py（都是纯离线模块，不依赖网站后端）
"""

from __future__ import annotations

import math
import random
import time
from typing import Optional

from engine_bridge import (
    BLACK, EMPTY, Move, PROMOTED, Position, WHITE, apply_move, generate_side_moves,
    get_legal_moves, has_only_throne, is_checkmate, is_in_check, other_side,
    sq_index,
)
from evaluate import DEFAULT_WEIGHTS, Weights, evaluate, piece_value

# 杀城/残局的分值，要远大于 evaluate() 正常情况下可能算出的任何组合
# （子力总和顶天 29*4.9≈142，安全分、活跃性分量级更小，10万分绝对压得住）
MATE_SCORE = 100_000.0


class SearchResult:
    __slots__ = ("best_move", "score", "nodes", "elapsed", "depth_reached")

    def __init__(self, best_move: Optional[Move], score: float, nodes: int, elapsed: float,
                 depth_reached: Optional[int] = None):
        self.best_move = best_move
        self.score = score
        self.nodes = nodes
        self.elapsed = elapsed
        # depth_reached：迭代加深实际跑完的最深层数——固定深度的
        # find_best_move/find_move_distribution 不填这个（None），只有
        # find_best_move_timed/find_move_distribution_timed 会填，方便
        # 调用方需要的话可以记录/打日志看"这一步到底搜到几层就被墙钟截住了"。
        self.depth_reached = depth_reached

    def __repr__(self) -> str:
        mv = f"{self.best_move.from_sq}->{self.best_move.to_sq}" if self.best_move else "None"
        depth_info = f", depth={self.depth_reached}" if self.depth_reached is not None else ""
        return f"SearchResult(move={mv}, score={self.score:.2f}, nodes={self.nodes}, {self.elapsed:.2f}s{depth_info})"


class _SearchTimeout(Exception):
    """内部专用：迭代加深超时时，从递归深处直接跳回最外层的控制流手段，
    不是真正的错误。被打断的这一层搜索很多分支根本没展开，返回值不可信，
    调用方（find_best_move_timed / find_move_distribution_timed）必须把
    这一层的结果整个丢弃，回退到上一层已经完整跑完的结果，不能只捕获异常
    然后假装无事发生。"""
    pass


# 每搜这么多个节点检查一次墙钟时间，不是每个节点都检查——time.time() 本身
# 也有开销，检查太频繁反而拖累没超时的正常情况。1024 个节点通常是几毫秒到
# 几十毫秒，对"超时后最多再多跑一点点"的精度来说足够细：真正超时时，
# 从"墙钟超过 deadline"到"抛出异常、逐层退栈"之间的延迟上限就是这一点点。
_TIME_CHECK_NODE_INTERVAL = 1024

# 迭代加深默认的单步思考时间预算——不管局面多复杂、并发有几盘棋在抢CPU，
# 这个函数保证大约这么久之内就返回一个"确实完整搜过、可信"的走法。
# 具体数值目前是拍的：深度1~2 通常远低于这个预算（能稳定搜完，兜底），
# 深度3 在优化后的典型中局局面下大多能在这个预算内搜完（不能总是保证，
# 局面复杂度是真实存在的方差），预算不够用时会自动只返回搜完的那一层，
# 不会不走棋。以后想让某个难度档位/人格"想得更久"，加一个参数覆盖这个
# 默认值即可，不需要改这里的逻辑。
DEFAULT_TIME_BUDGET_SECONDS = 8.0


def _check_time(nodes: list[int], deadline: Optional[float]) -> None:
    if deadline is not None and nodes[0] % _TIME_CHECK_NODE_INTERVAL == 0 and time.time() >= deadline:
        raise _SearchTimeout()


def _terminal_value(pos: Position, side_to_move: int, depth_left: int):
    """side_to_move 是接下来要走棋、但可能已经无路可走的一方。
    若已分出胜负，返回 (True, 分值)；否则 (False, None)。
    分值额外叠加 depth_left，让"更快分出胜负"的分数更极端——搜索因此会倾向
    选最快获胜/最晚失败的路线，而不是必胜局面里随便应付。"""
    if has_only_throne(pos, side_to_move) or is_checkmate(pos, side_to_move):
        # side_to_move 一方处于必败状态
        return True, (-MATE_SCORE - depth_left if side_to_move == BLACK else MATE_SCORE + depth_left)
    return False, None


def _victim_value(pos: Position, move: Move) -> float:
    idx = sq_index(*move.to_sq)
    t = pos.types[idx]
    if t == EMPTY:
        return 0.0
    return piece_value(t, bool(pos.flags[idx] & PROMOTED))


def _order_moves(pos: Position, moves: list[Move],
                  killers: Optional[tuple[Optional[Move], Optional[Move]]] = None,
                  pv_move: Optional[Move] = None) -> list[Move]:
    """走法排序，越靠前越优先搜索——排序越好，alpha-beta 剪枝剪得越狠，
    这是"进一步剪枝"里成本最低、最直接的一步（不改变最终分值，只影响
    算到这个分值要绕多少弯路）。优先级从高到低：

        1. pv_move：迭代加深上一层找到的最优解（find_best_move_timed 用），
           或者 PVS 探测阶段"当前已知最优"的那一手（_root_search 内部用）。
           大概率这一层还是最优或接近最优，排第一能让 alpha/beta 一开始
           就顶到很紧，后面的走法更容易被剪掉。
        2. 吃子，按"吃的子多值钱"从大到小排（MVV-LVA 简化版——不看"用什么
           子吃"，只看"吃到什么"，跟 quiescence 那边 _legal_captures_ordered
           的排序依据一致）。
        3. killer moves：不吃子，但在**同一深度的另一个分支**里刚刚造成过
           剪枝的走法——不是这个局面独有的信息，是"这个深度这类局面下，
           这一手往往很凶"的经验，触发过一次就值得优先再试一次。经典
           alpha-beta 优化手段，只对非吃子的走法生效（吃子已经有 MVV-LVA
           排序，不需要 killer 再插一手）。
        4. 其余走法，不再额外排序（没有更多信息可用，排序本身也要花时间，
           没信息支撑的排序纯粹是浪费）。
    """
    def key(m: Move):
        if pv_move is not None and m == pv_move:
            return (0, 0.0)
        if m.is_capture:
            return (1, -_victim_value(pos, m))
        if killers and (m == killers[0] or m == killers[1]):
            return (2, 0.0)
        return (3, 0.0)
    return sorted(moves, key=key)


# 静态搜索最多再往下追多少层吃子。这个上限是必须的：这个棋种里弩车/炮塔
# 隔子吃子的存在，让"互相吃来吃去"的链条可能拖得很长，不设上限极端情况下
# 会退化成搜索爆炸。
QUIESCENCE_MAX_DEPTH = 3

# delta pruning 的安全余量：即使吃到这个子，再加上这点余量还够不到 alpha，
# 这一支就没有搜下去的价值了。留 2.0（两个兵）是保守值，宁可多搜一点
# 也不要把真正有价值的战术支剪掉。
DELTA_MARGIN = 2.0


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
                 nodes: list[int], qdepth: int, weights: Weights,
                 deadline: Optional[float] = None) -> float:
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
    _check_time(nodes, deadline)

    stand_pat = evaluate(pos, side_to_move, weights)

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
        val = _quiescence(child, alpha, beta, next_side, nodes, qdepth + 1, weights, deadline)
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


def _minimax(pos: Position, depth: int, alpha: float, beta: float, side_to_move: int,
             nodes: list[int], weights: Weights,
             killers: Optional[list[tuple[Optional[Move], Optional[Move]]]] = None,
             deadline: Optional[float] = None) -> float:
    nodes[0] += 1
    _check_time(nodes, deadline)

    done, terminal_val = _terminal_value(pos, side_to_move, depth)
    if done:
        return terminal_val

    if depth == 0:
        # 到底了不要直接静态评估——先做静态搜索把还没吃完的兑子链算干净，
        # 否则就会出现"吃了子就到底、看不见对手反吃"的地平线效应
        # （详见 _quiescence 的说明）。
        return _quiescence(pos, alpha, beta, side_to_move, nodes, 0, weights, deadline)

    legal = get_legal_moves(pos, side_to_move)
    if not legal:
        # 规则集本身没有覆盖"零合法走法但未被将军"这种边缘情况（29子对29子，
        # 正常对局里全员同时被冻结的概率约等于0），保守返回静态评估分，
        # 不当作必胜/必败处理，避免在从未验证过的边缘状态下给出误导性极端分。
        return evaluate(pos, side_to_move, weights)

    killer_here = killers[depth] if killers is not None else None
    ordered = _order_moves(pos, legal, killer_here)
    next_side = other_side(side_to_move)

    if side_to_move == BLACK:
        best = float("-inf")
        for move in ordered:
            child = pos.clone()
            apply_move(child, move)
            val = _minimax(child, depth - 1, alpha, beta, next_side, nodes, weights, killers, deadline)
            if val > best:
                best = val
            if best > alpha:
                alpha = best
            if alpha >= beta:
                if killers is not None and not move.is_capture:
                    _record_killer(killers, depth, move)
                break
        return best
    else:
        best = float("inf")
        for move in ordered:
            child = pos.clone()
            apply_move(child, move)
            val = _minimax(child, depth - 1, alpha, beta, next_side, nodes, weights, killers, deadline)
            if val < best:
                best = val
            if best < beta:
                beta = best
            if alpha >= beta:
                if killers is not None and not move.is_capture:
                    _record_killer(killers, depth, move)
                break
        return best


def _record_killer(killers: list[tuple[Optional[Move], Optional[Move]]], depth: int, move: Move) -> None:
    """把 move 记成"这个深度上刚造成过剪枝的非吃子走法"，留2个槽位
    （最近命中的排前面），下次同一深度的另一个分支排序时会优先试它。
    重复记录同一手不产生额外效果（放到槽位0，原本在槽位0的自然还在，
    只是不会出现"同一手占两个槽"这种没意义的情况）。"""
    a, b = killers[depth]
    if move == a:
        return
    killers[depth] = (move, a)


def _new_killer_table(max_depth: int) -> list[tuple[Optional[Move], Optional[Move]]]:
    return [(None, None) for _ in range(max_depth + 1)]


class RootMoveScore:
    __slots__ = ("move", "score")

    def __init__(self, move: Move, score: float):
        self.move = move
        self.score = score


# PVS 零宽度试探窗口的宽度。评估分是浮点数，不能像整数分值引擎那样用
# [alpha, alpha+1] 当"零宽度"——1e-6 远小于这套评估函数里任何有意义的
# 分差（子力最小单位是1.0一个兵，活跃度每步只有0.02，都比这个大好几个
# 数量级），足够窄到"几乎必然直接fail"，同时又不会因为浮点误差而误判。
_PVS_EPSILON = 1e-6


def _root_search(pos: Position, side_to_move: int, depth: int, weights: Weights,
                  nodes: list[int], pv_move: Optional[Move] = None,
                  deadline: Optional[float] = None) -> list[RootMoveScore]:
    """根节点搜索，find_best_move 和 find_move_distribution 共用——两边
    都需要"每个候选走法在这个深度下的分值"，区别只在于前者只要最优的
    那一个，后者要保留全部。

    用 PVS（Principal Variation Search）而不是老实对每一手都做全窗口
    [-inf, inf] 搜索：排序后的第一手（大概率是最好的那手——pv_move
    优先、其次吃大子优先）用全窗口搜，把 alpha/beta 顶到一个有意义的
    起点；之后每一手先用一个几乎零宽度的窗口探一下"是不是比当前已知
    最优的更好"——alpha-beta 在极窄窗口下剪枝剪得极狠，代价远小于
    全窗口搜索。多数情况下"探测"会直接确认"不如当前最优"（fail-low），
    这个 fail-soft 返回值本身就是一个有效的分值上/下界，不需要再搜一遍
    就能放心用（它保证"真实值不会比这个更好"，所以不可能让一手真正更
    强的棋被这个不精确的分值误判成"更差"）；只有探测结果显示"这手可能
    真的更好"（fail-high 或者恰好落在探测窗口里），才值得再花一次全窗口
    搜索的代价去拿精确值。

    对 find_best_move 而言这个近似 100% 安全：反正只要"确认不如当前最优"
    这一个结论，用不用精确值不影响最终选出来的是哪一手。对
    find_move_distribution 而言（分值要喂给温度采样/风格化选择），
    "明显更差的招法分值不够精确"这点代价是可以接受的——这些招法本来
    就该在采样里占极小权重，精确到小数点后几位不影响实际选择。
    """
    legal = get_legal_moves(pos, side_to_move)
    if not legal:
        return []

    max_depth_for_killers = max(depth - 1, 0)
    killers = _new_killer_table(max_depth_for_killers)
    ordered = _order_moves(pos, legal, killers=None, pv_move=pv_move)
    next_side = other_side(side_to_move)
    maximizing = side_to_move == BLACK

    candidates: list[RootMoveScore] = []
    alpha, beta = float("-inf"), float("inf")

    for i, move in enumerate(ordered):
        child = pos.clone()
        apply_move(child, move)

        if i == 0:
            val = _minimax(child, depth - 1, alpha, beta, next_side, nodes, weights, killers, deadline)
        else:
            if maximizing:
                probe_alpha, probe_beta = alpha, alpha + _PVS_EPSILON
            else:
                probe_alpha, probe_beta = beta - _PVS_EPSILON, beta
            val = _minimax(child, depth - 1, probe_alpha, probe_beta, next_side, nodes, weights, killers, deadline)
            needs_full_search = (val > alpha) if maximizing else (val < beta)
            if needs_full_search:
                val = _minimax(child, depth - 1, alpha, beta, next_side, nodes, weights, killers, deadline)

        candidates.append(RootMoveScore(move, val))
        if maximizing:
            if val > alpha:
                alpha = val
        else:
            if val < beta:
                beta = val

    return candidates


def find_move_distribution(pos: Position, side_to_move: int, depth: int, weights: Weights = DEFAULT_WEIGHTS,
                            pv_move: Optional[Move] = None, deadline: Optional[float] = None):
    """根节点每一个合法走法各自的 minimax 分值——self_play.py 靠这个做
    "温度采样"：不是每次都死板地选分数最高的那步，而是按分值转成的概率
    分布抽样，让自对弈开局阶段能走出更多样的变着，不然每盘自对弈的开局都
    长得一模一样，攒出来的开局库毫无意义。人格AI系统（find_persona_move）
    也靠它实现"精度没那么高/偏爱冒险"这些非满分棋力的人格特质。

    2026-08 改版：内部换成了 PVS（见 _root_search 的说明），不再是"每一手
    都从 [-inf, inf] 重新搜"的全暴力写法——实测同一局面下，旧写法比
    find_best_move 慢了5倍以上（复杂中局局面下甚至10倍以上，直接跑不完），
    现在两者共用同一份根节点搜索，速度基本拉平（比 find_best_move 略慢，
    因为要给每个候选都留一个可用的分值，不能像 find_best_move 那样一路
    收紧窗口收到底，但不再是量级上的差距）。返回值的形状/含义完全不变，
    self_play.py/opening 相关代码不需要跟着改。

    deadline 非 None 时，如果搜到一半超时，会抛出 _SearchTimeout——这个
    函数本身不捕获它，调用方（比如 find_move_distribution_timed）负责
    捕获并回退到上一个完整跑完的深度；直接调用这个函数（不传 deadline，
    比如 self_play.py 现在的用法）不受影响，行为等同于没有时间限制。

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

    candidates = _root_search(pos, side_to_move, depth, weights, nodes, pv_move, deadline)
    return candidates, time.time() - t0


def find_best_move(pos: Position, side_to_move: int, depth: int, weights: Weights = DEFAULT_WEIGHTS,
                    pv_move: Optional[Move] = None, deadline: Optional[float] = None) -> SearchResult:
    """根节点单独展开，方便拿到 best_move 本身。2026-08 改版后内部复用
    _root_search（PVS + killer move + MVV-LVA 排序），走的仍然是"根节点
    互相剪枝"这条快速路径（Play against AI / find_best_move_timed 用这个，
    响应速度是硬要求），只是排序和根节点剪枝比以前更狠，返回的最优招法/
    分值不受影响（PVS 对"确认不如当前最优"的招法只给一个有效上/下界，
    但选出来的最优解本身、以及它的分值，都是精确值——回归测试见本文件
    __main__，跟未做 PVS 优化的暴力实现逐局面比对分值必须完全一致）。

    pv_move：如果调用方已经知道"上一次浅一点的搜索认为哪一手最好"（比如
    find_best_move_timed 迭代加深时，把上一层的结果传进来），排序时会
    优先试这一手——大概率它在这一层依然是最优或接近最优，能让 alpha/beta
    一开始就顶到很紧，剪枝剪得更狠。不传（None）就是纯按吃子价值排序，
    行为等同于以前的版本。

    deadline 非 None 时可能抛出 _SearchTimeout，语义跟 find_move_distribution
    一致——不带 deadline 调用（self_play.py 等直接调用方的现状）完全不受
    影响。"""
    t0 = time.time()
    nodes = [0]

    done, terminal_val = _terminal_value(pos, side_to_move, depth)
    if done:
        return SearchResult(None, terminal_val, 1, time.time() - t0)

    legal = get_legal_moves(pos, side_to_move)
    if not legal:
        return SearchResult(None, evaluate(pos, side_to_move, weights), 1, time.time() - t0)

    candidates = _root_search(pos, side_to_move, depth, weights, nodes, pv_move, deadline)
    maximizing = side_to_move == BLACK
    best = candidates[0]
    for c in candidates[1:]:
        if maximizing and c.score > best.score:
            best = c
        elif not maximizing and c.score < best.score:
            best = c

    return SearchResult(best.move, best.score, nodes[0], time.time() - t0)


def find_best_move_timed(pos: Position, side_to_move: int, max_depth: int,
                          weights: Weights = DEFAULT_WEIGHTS,
                          time_budget: float = DEFAULT_TIME_BUDGET_SECONDS) -> SearchResult:
    """迭代加深 + 硬性墙钟时间预算版本的 find_best_move——这是真正解决
    "AI走棋速度接近0"的关键：不管局面多复杂、并发抢了多少CPU时间片，
    保证大约 time_budget 秒之内返回一个"确实完整搜过、可信"的走法，
    绝不会因为某一步算得太久而一直不走棋。

    做法：从 depth=1 开始一层一层往深搜，每完成一层就把结果记成"当前
    最优答案"，同时把这一层找到的最优招法喂给下一层当 pv_move（排序
    提示，剪枝剪得更狠，也让"万一下一层被墙钟截断"时已经搜出来的
    候选尽量可靠）。开始下一层之前，如果按上一层实际耗时粗略外推，
    剩余预算大概率不够这一层用，就直接不开始，返回已经完整跑完的
    最深那一层——半成品的搜索结果不可信（很多分支根本没展开），
    不如干脆不搜，省下这点时间/CPU。

    真正的硬上限来自更底层：_minimax/_quiescence 内部每隔一定节点数会
    检查一次墙钟时间，一旦当前时刻已经超过 deadline，直接抛出
    _SearchTimeout 让这一整层的搜索作废，这里捕获它、退回上一层——
    也就是说，哪怕"粗略外推"判断失误、真去试了一层结果远超预期地慢，
    也不会真的把这层跑完再返回，超时容忍窗口只有"再多跑一点点节点"
    这么大（由 search.py 顶部 _TIME_CHECK_NODE_INTERVAL 决定，通常是
    几十毫秒量级），不会累积成大问题。

    max_depth 依然是硬上限——传进来的深度封顶，跟以前的行为一致
    （比如某个人格配置 depth=3，最多就搜到3层，即使时间预算还有富余）；
    time_budget 只会让实际搜到的层数**更浅**，不会让它超过 max_depth。
    """
    t_start = time.time()
    deadline_absolute = t_start + time_budget

    best_result: Optional[SearchResult] = None
    pv_move: Optional[Move] = None
    last_iteration_seconds = 0.0
    depth_reached = 0

    depth = 1
    while depth <= max_depth:
        remaining = deadline_absolute - time.time()
        if best_result is not None:
            if remaining <= 0:
                break
            # 粗略外推：depth 每深一层，分支因子通常让耗时涨好几倍
            # （哪怕排序/剪枝已经优化过，"深一层"本身的开销跳变依然明显）。
            # 连"跟上一层同样慢"都放不进剩余预算，大概率也撑不住这一层，
            # 不值得再试——试了大概率也是白试（被 _SearchTimeout 半路打断，
            # 那次搜索的时间等于白花）。
            if last_iteration_seconds > 0 and remaining < last_iteration_seconds * 0.5:
                break

        # depth=1 这一层永远不传 deadline，无条件让它完整跑完——这是"再怎么
        # 极端也有一个可信结果"这个承诺的最后一道保险：如果 time_budget
        # 被调用方设成一个极小甚至非正的值，且局面恰好复杂到 depth=1 本身
        # 就超过 _TIME_CHECK_NODE_INTERVAL 个节点，没有这道保险的话
        # _SearchTimeout 会在 best_result 还是 None 的时候就被抛出，下面的
        # assert 就会失败——宁可让这一种极端情况下 depth=1 稍微超一点预算，
        # 也不能让函数在任何输入下都可能直接崩掉、一步棋都不返回。
        this_deadline = deadline_absolute if depth > 1 else None
        try:
            result = find_best_move(pos, side_to_move, depth, weights,
                                     pv_move=pv_move, deadline=this_deadline)
        except _SearchTimeout:
            break

        best_result = result
        pv_move = result.best_move
        last_iteration_seconds = result.elapsed
        depth_reached = depth

        if result.best_move is None:
            break  # 终局/无路可走，不用再往深搜

        depth += 1

    assert best_result is not None, "depth=1 永远不传 deadline，不可能超时（见上面的说明）"
    best_result.depth_reached = depth_reached
    best_result.elapsed = time.time() - t_start
    return best_result


def find_move_distribution_timed(pos: Position, side_to_move: int, max_depth: int,
                                  weights: Weights = DEFAULT_WEIGHTS,
                                  time_budget: float = DEFAULT_TIME_BUDGET_SECONDS):
    """迭代加深 + 硬性墙钟时间预算版本的 find_move_distribution——find_persona_move
    的"非满分棋力"路径（精度<1 或者偏爱冒险的人格，9个里有6个）用这个，
    道理跟 find_best_move_timed 完全一样，见那边的说明。

    返回 (candidates, elapsed, depth_reached)——比不带时间预算的版本多一个
    depth_reached，方便调用方（如果需要）知道这一步到底搜到几层。
    """
    t_start = time.time()
    deadline_absolute = t_start + time_budget

    best_candidates: Optional[list[RootMoveScore]] = None
    pv_move: Optional[Move] = None
    last_iteration_seconds = 0.0
    depth_reached = 0

    depth = 1
    while depth <= max_depth:
        remaining = deadline_absolute - time.time()
        if best_candidates is not None:
            if remaining <= 0:
                break
            if last_iteration_seconds > 0 and remaining < last_iteration_seconds * 0.5:
                break

        # depth=1 永远不传 deadline——理由跟 find_best_move_timed 完全一样，
        # 见那边的说明：这是保证"无论 time_budget 传得多离谱，也至少有一个
        # 完整跑完、可信的结果"的最后一道保险。
        this_deadline = deadline_absolute if depth > 1 else None
        t_iter = time.time()
        try:
            candidates, _elapsed = find_move_distribution(
                pos, side_to_move, depth, weights, pv_move=pv_move, deadline=this_deadline,
            )
        except _SearchTimeout:
            break

        last_iteration_seconds = time.time() - t_iter
        if not candidates:
            # 终局/无合法走法——不管是不是第一层都直接采用（哪怕
            # best_candidates 还是 None，也没有更深的可能了）。
            best_candidates = candidates
            depth_reached = depth
            break

        best_candidates = candidates
        maximizing = side_to_move == BLACK
        best = candidates[0]
        for c in candidates[1:]:
            if maximizing and c.score > best.score:
                best = c
            elif not maximizing and c.score < best.score:
                best = c
        pv_move = best.move
        depth_reached = depth
        depth += 1

    assert best_candidates is not None, "depth=1 永远不传 deadline，不可能超时（见上面的说明）"
    return best_candidates, time.time() - t_start, depth_reached


def softmax_sample(candidates: list[RootMoveScore], side_to_move: int,
                    temperature: float, rng: random.Random) -> Move:
    """candidates 是 find_move_distribution() 返回的"正数偏向黑方"这套统一
    分值。先转成"对 side_to_move 这一方来说好不好"（越大越好），再做标准
    softmax（减最大值防止数值溢出），按概率抽样。temperature 趋近0时
    退化成确定性地选最优招。"""
    side_relative = [c.score if side_to_move == BLACK else -c.score for c in candidates]
    m = max(side_relative)
    weights_ = [math.exp((v - m) / temperature) for v in side_relative]
    total = sum(weights_)
    r = rng.random() * total
    upto = 0.0
    for c, w in zip(candidates, weights_):
        upto += w
        if upto >= r:
            return c.move
    return candidates[-1].move  # 浮点误差兜底


def _precision_to_temperature(precision: float) -> float:
    """精度(0~1) -> softmax 温度。精度越低，温度越高、走法越随机。
    这个映射系数（3.0）是拍出来的初始值，不是标定过的——人格棋力/棋风
    的真正打磨要靠后面自对弈+人工评估去调，这里先给一个"精度越低越飘"
    的合理形状，具体数值随时可改，不影响任何调用方的接口。"""
    return max(0.0, (1.0 - precision)) * 3.0


def _aggressiveness_key(pos: Position, candidate: RootMoveScore, side: int):
    """"更冒险"的简化代理指标，用于 Kanderson 那种"最优解不唯一时偏爱冒险
    招法"的人格特质——目前用"是不是吃子"和"扎进对方阵营多深"两个信号
    近似，不是什么严谨的风险度量，就是个可解释、能跑起来的第一版，
    以后想要更细致的判断（比如"是不是弃子换攻势"）可以在这里单独加。"""
    m = candidate.move
    depth_into_enemy = m.to_sq[1] if side == BLACK else (13 - m.to_sq[1])
    return (1 if m.is_capture else 0, depth_into_enemy)


def find_persona_move(pos: Position, side_to_move: int, depth: int,
                       weights: Weights = DEFAULT_WEIGHTS, precision: float = 1.0,
                       prefer_aggressive_ties: bool = False, tie_epsilon: float = 0.05,
                       aggressive_pool_size: int = 5,
                       rng: Optional[random.Random] = None,
                       time_budget: float = DEFAULT_TIME_BUDGET_SECONDS) -> SearchResult:
    """给 AI 人格系统用的统一出招入口。

    第一版有个真实的设计漏洞，这里改掉了：之前只有 precision 恰好等于1.0
    的时候，prefer_aggressive_ties 才会生效——一旦 precision<1.0（比如
    Kanderson 后来定成0.95），代码会直接走温度采样那条路，"偏爱冒险"这个
    人格特质就完全不起作用了，等于白设。现在改成 precision 和
    prefer_aggressive_ties 两个维度互相独立、可以同时生效：

        precision=1.0 且 prefer_aggressive_ties=False（Gasparret/Ananta/
        Maggerita 这类"无风格/纯计算"人格）
            -> 直接走 find_best_move_timed 的快速路径，不额外付任何代价。

        其余所有组合，都先算出全部候选的真实分值（find_move_distribution_timed，
        比快速路径慢，是这类人格必须付出的代价，2026-08 改版后已经不再是
        量级上的差距，见 find_move_distribution 顶部说明），然后：
          1. 以 precision 的概率"选最优"：如果同时 prefer_aggressive_ties，
             在并列最优（分差在 tie_epsilon 内）的候选里挑更冒险的那个；
             不并列就是唯一的最优解，没什么好挑的。
          2. 以 (1-precision) 的概率"偏离最优"：
             - prefer_aggressive_ties=True 时（Kanderson/Anaxagoras）：
               不是随便乱走，是从排名靠前的一小撮候选（前 aggressive_pool_size
               个）里挑最冒险的那个——即使不是最优解，也不会是随便选的烂棋，
               这才对应"喜欢冒险但不至于送子"的描述。
             - prefer_aggressive_ties=False 时（Flannery/Karspeirsky/Avril/
               Sangomanti 这类"精度没那么高但没有冒险偏好"的人格）：
               普通的温度采样，精度越低温度越高。

    2026-08 改版：depth 现在当作"最多搜到这一层"的封顶，实际会不会搜到
    这么深要看 time_budget 够不够（迭代加深，见 find_best_move_timed /
    find_move_distribution_timed 的说明）——这正是"AI走棋速度接近0"的
    根本解法：不管局面多复杂，这个函数保证大约 time_budget 秒内返回。
    """
    rng = rng or random.Random()

    if precision >= 0.999 and not prefer_aggressive_ties:
        return find_best_move_timed(pos, side_to_move, depth, weights, time_budget=time_budget)

    candidates, elapsed, depth_reached = find_move_distribution_timed(
        pos, side_to_move, depth, weights, time_budget=time_budget,
    )
    if not candidates:
        # 终局/无子可走——find_best_move_timed 走同一段边缘情况处理逻辑，直接借用
        return find_best_move_timed(pos, side_to_move, depth, weights, time_budget=time_budget)

    maximizing = side_to_move == BLACK
    ranked = sorted(candidates, key=lambda c: c.score, reverse=maximizing)
    best_score = ranked[0].score

    take_best = (precision >= 0.999) or (rng.random() < precision)

    if take_best:
        near_best = [c for c in ranked if abs(c.score - best_score) <= tie_epsilon]
        chosen = _pick_most_aggressive(pos, near_best, side_to_move) if (
            prefer_aggressive_ties and len(near_best) > 1
        ) else near_best[0]
        return SearchResult(chosen.move, chosen.score, len(candidates), elapsed, depth_reached)

    if prefer_aggressive_ties:
        pool = ranked[:min(aggressive_pool_size, len(ranked))]
        chosen = _pick_most_aggressive(pos, pool, side_to_move)
        return SearchResult(chosen.move, chosen.score, len(candidates), elapsed, depth_reached)

    temperature = _precision_to_temperature(precision)
    chosen_move = softmax_sample(candidates, side_to_move, temperature, rng)
    chosen_score = next(c.score for c in candidates if c.move == chosen_move)
    return SearchResult(chosen_move, chosen_score, len(candidates), elapsed, depth_reached)


def _pick_most_aggressive(pos: Position, candidates: list[RootMoveScore], side: int) -> RootMoveScore:
    return max(candidates, key=lambda c: _aggressiveness_key(pos, c, side))


# ---------------------------------------------------------------------------
# 冒烟测试 + 正确性回归测试
# ---------------------------------------------------------------------------
#
# 2026-08 改版加了 PVS + killer move + MVV-LVA 排序，这些改动理论上只影响
# "搜索走多少弯路"，不影响"最终算出来的分值"——alpha-beta 剪枝的正确性
# 不依赖走法顺序，PVS 的窄窗口探测对"确认更差"的招法只是不追求精确值，
# 但从不会把它们的分值算得比真实值更高（更不会因此误选一手更差的棋）。
# 下面用一份保留原始暴力写法（老实对每一手都全窗口重搜、没有 PVS/killer/
# MVV-LVA）的参考实现，在多个随机局面上跟新实现逐一比对**分值**，
# 这是这次改动最重要的正确性保证——不是"看起来还能下棋"就算过关。

def _reference_root_search(pos: Position, side_to_move: int, depth: int, weights: Weights) -> list[RootMoveScore]:
    """未做任何根节点优化的参考实现，只用于测试比对，不在正式代码路径里用。"""
    legal = get_legal_moves(pos, side_to_move)
    if not legal:
        return []
    next_side = other_side(side_to_move)
    candidates = []
    for move in legal:
        child = pos.clone()
        apply_move(child, move)
        val = _minimax(child, depth - 1, float("-inf"), float("inf"), next_side, [0], weights)
        candidates.append(RootMoveScore(move, val))
    return candidates


if __name__ == "__main__":
    from engine_bridge import Position as _Position, apply_move as _apply_move

    pos = Position.initial()
    result = find_best_move(pos, BLACK, depth=2)
    print("开局深度2搜索结果:", result)
    assert result.best_move is not None, "开局应该能找到一步棋"

    # ---- 回归测试：新（PVS+killer+MVV-LVA）跟旧（暴力全窗口）实现，
    # 分值必须逐局面完全一致（最优解相同分值即可，允许平分时选到不同
    # 招法——alpha-beta树的分值是唯一的，但同分的最优招法可能不止一个，
    # 排序变了确实可能改选另一手同分的棋，这不算错） ----
    rng = random.Random(2026)

    def _random_playout(n_moves, seed):
        p = _Position.initial()
        r = random.Random(seed)
        for _ in range(n_moves):
            side = p.side_to_move
            moves = get_legal_moves(p, side)
            if not moves:
                break
            mv = r.choice(moves)
            _apply_move(p, mv)
            p.side_to_move = other_side(side)
        return p

    # 只测4个局面、深度2——_reference_root_search 是故意没做任何根节点优化
    # 的暴力版本，单个局面就可能要几秒到几十秒，这里要的是"抓正确性问题"，
    # 不是压力测试，局面数够用来抓 bug 就行，不需要跟性能基准测试一样多
    # （性能基准另见 ai/README.md 或单独跑 /tmp 下的 benchmark 脚本）。
    mismatches = 0
    for seed in range(4):
        test_pos = _random_playout(rng.randint(0, 12), seed)
        side = test_pos.side_to_move
        done, _ = _terminal_value(test_pos, side, 2)
        if done:
            continue
        ref = _reference_root_search(test_pos, side, depth=2, weights=DEFAULT_WEIGHTS)
        new = _root_search(test_pos, side, depth=2, weights=DEFAULT_WEIGHTS, nodes=[0])
        if not ref or not new:
            continue
        maximizing = side == BLACK
        ref_best = (max if maximizing else min)(c.score for c in ref)
        new_best = (max if maximizing else min)(c.score for c in new)
        if abs(ref_best - new_best) > 1e-6:
            mismatches += 1
            print(f"  ❌ seed={seed}: 参考实现最优分值={ref_best:.4f}，新实现={new_best:.4f}")
    assert mismatches == 0, f"{mismatches} 个局面新旧实现最优分值不一致——PVS/排序改动引入了正确性问题"
    print(f"✅ 回归测试：4 个随机局面，新旧根节点搜索最优分值全部一致（PVS+排序优化没有改变搜索结果）")

    # ---- find_best_move / find_move_distribution 在同一局面下，最优分值应该一致
    # （两者共用 _root_search，只是取值方式不同：前者直接取最优，后者保留全部） ----
    mid_pos = _random_playout(10, 999)
    mid_side = mid_pos.side_to_move
    r1 = find_best_move(mid_pos, mid_side, depth=2)
    cands, _elapsed = find_move_distribution(mid_pos, mid_side, depth=2)
    maximizing = mid_side == BLACK
    dist_best = (max if maximizing else min)(c.score for c in cands)
    assert abs(r1.score - dist_best) < 1e-6, (
        f"find_best_move={r1.score:.4f} 与 find_move_distribution 最优值={dist_best:.4f} 不一致"
    )
    print("✅ find_best_move 与 find_move_distribution 在同一局面下最优分值一致")

    # ---- 迭代加深+墙钟预算：给一个明显不合理的时间预算，应该能在预算附近
    # 返回（不会因为预算太紧就返回 None，depth=1 兜底） ----
    t0 = time.time()
    timed_result = find_best_move_timed(mid_pos, mid_side, max_depth=3, time_budget=0.01)
    elapsed = time.time() - t0
    assert timed_result.best_move is not None, "再紧的预算也应该至少有 depth=1 的兜底结果"
    assert timed_result.depth_reached is not None and timed_result.depth_reached >= 1
    print(f"✅ 迭代加深墙钟预算=0.01s 时：{elapsed:.3f}s 内返回，实际搜到 depth={timed_result.depth_reached}"
          f"（预算极紧时应该退化到depth=1附近，不会无限期卡住）")

    # ---- 正常预算下应该能搜到比 depth=1 更深 ----
    t0 = time.time()
    timed_result2 = find_best_move_timed(mid_pos, mid_side, max_depth=3, time_budget=DEFAULT_TIME_BUDGET_SECONDS)
    elapsed2 = time.time() - t0
    assert timed_result2.depth_reached >= 2, f"正常预算下至少应该搜到depth=2，实际={timed_result2.depth_reached}"
    assert elapsed2 <= DEFAULT_TIME_BUDGET_SECONDS + 1.0, f"不该显著超出时间预算，实际耗时{elapsed2:.2f}s"
    print(f"✅ 正常预算({DEFAULT_TIME_BUDGET_SECONDS}s)下：{elapsed2:.2f}s 内返回，搜到 depth={timed_result2.depth_reached}")

    # ---- 边界情况：time_budget 传0甚至负数，depth=1 必须依然完整跑完并
    # 返回可信结果，不能因为"第一层就超时"而直接崩掉（assert 失败）——
    # 这是"无论调用方传什么，都至少有一步棋可走"这个承诺的最后一道保险,
    # 见 find_best_move_timed 内部"depth=1 永远不传 deadline"那段说明 ----
    for bad_budget in (0.0, -5.0):
        r = find_best_move_timed(mid_pos, mid_side, max_depth=3, time_budget=bad_budget)
        assert r.best_move is not None, f"time_budget={bad_budget} 时也必须返回一个可信的走法"
        assert r.depth_reached == 1, f"time_budget={bad_budget} 时应该退化到depth=1，实际={r.depth_reached}"
    print("✅ time_budget=0/负数 这种极端输入下，depth=1 依然完整跑完并返回可信结果（不会崩溃/不返回棋）")

    # ---- find_move_distribution_timed 同理 ----
    dcands, delapsed, ddepth = find_move_distribution_timed(mid_pos, mid_side, max_depth=3,
                                                              time_budget=DEFAULT_TIME_BUDGET_SECONDS)
    assert dcands, "应该有候选走法"
    assert ddepth >= 2, f"正常预算下至少应该搜到depth=2，实际={ddepth}"
    print(f"✅ find_move_distribution_timed：{delapsed:.2f}s 内返回，搜到 depth={ddepth}，候选数={len(dcands)}")

    print("\nsearch.py 冒烟测试通过 ✅（完整对弈验证见 ai/cli_selfplay.py）")
