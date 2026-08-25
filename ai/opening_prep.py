"""
ai/opening_prep.py
====================
带条件分支的开局准备（"如果对手走了X，我就走Y"这种）——跟 opening_book.py
不是一回事：opening_book.py 只管"第一步走哪个开局"（自对弈用，需要的是
多样性，不是深度准备）；这里管的是"顺着一个开局往后，遇到对手的具体应招，
我预先想好怎么接"，是 AI 人格系统专用的、可以有很多层的准备树。

树的结构：一个 PrepNode 代表"轮到我方走的一个局面"，里面是一组按权重
加权的候选招法，每个候选招法后面可以再挂一个"对手应招 -> 我方下一手"
的字典（opponent_replies），没挂就表示"这条线到这里为止，后面交给引擎
自己想"。

用法（AI 实际下棋时）：
    walker = BookWalker(persona_prep_tree, side)
    move = walker.my_move(pos, rng)      # 拿到这一手要走的书本着法，
                                          # 也可能是 None（已经脱谱，交给搜索）
    walker.observe_opponent_move(opp_move_notation)   # 对手走完之后调用，
                                          # 走到子节点，或者发现对手走了
                                          # 准备之外的招 -> 自动脱谱

依赖：ai/engine_bridge.py、ai/notation_lite.py（不依赖网站后端）
"""

from __future__ import annotations

import random
from typing import Optional

import engine_bridge as eb
import notation_lite as nl


class PrepNode:
    """一层"轮到我方走"的准备。moves 是 [{"notation": str, "weight": float,
    "reply_to": {对手记谱: PrepNode 或 None}}, ...]。

    reply_to 里某个对手应招映射到 None，表示"预料到对手可能这么走，
    但这条线到此为止，后面交给引擎自己算"——跟"对手走了没预料到的招"是
    两种不同的情况：前者是主动决定不再往下准备，后者是真正的脱谱，
    调用方需要分得清（BookWalker 用 in_book 属性表示这个区别）。
    """

    def __init__(self, moves: list[dict]):
        self.moves = moves

    @staticmethod
    def leaf(notation: str) -> "PrepNode":
        """只有一手准备、后面没有再往下的分支——常见于"目前先只想到这一步"
        的情况，比 PrepNode([{"notation":..,"weight":1.0,"reply_to":{}}])
        写起来更省事。"""
        return PrepNode([{"notation": notation, "weight": 1.0, "reply_to": {}}])


class BookWalker:
    """在一棵 PrepNode 树里按实际对局往前走的状态机。in_book 为 False 之后
    永远保持 False（脱谱了就不会凭空回到书里）。"""

    def __init__(self, root: Optional[PrepNode], side: int, rng: Optional[random.Random] = None):
        self._node = root
        self._side = side
        self._rng = rng or random.Random()
        self.in_book = root is not None

    def my_move(self, pos: eb.Position) -> Optional[eb.Move]:
        """按权重从当前节点的候选里抽一手；脱谱了或者这个人格根本没有
        准备树就返回 None（调用方应该退回正常搜索）。"""
        if not self.in_book or self._node is None:
            return None
        index = _notation_index(pos, self._side)
        total = sum(m["weight"] for m in self._node.moves)
        r = self._rng.random() * total
        upto = 0.0
        chosen = self._node.moves[-1]
        for m in self._node.moves:
            upto += m["weight"]
            if upto >= r:
                chosen = m
                break
        move = index.get(chosen["notation"])
        if move is None:
            raise ValueError(
                f"准备树里有一步在当前局面下不合法: {chosen['notation']!r}"
            )
        self._pending_reply_map = chosen["reply_to"]
        return move

    def observe_opponent_move(self, opponent_notation: str) -> None:
        """对手走完之后调用。如果这一步在准备范围内，走到对应的子节点
        （子节点是 None 也没关系，表示"这条线的准备到此为止"，下一次
        my_move 会因为 self._node is None 而自动返回 None）；
        如果对手走了没准备到的招，标记为脱谱。"""
        if not self.in_book:
            return
        if not hasattr(self, "_pending_reply_map"):
            self.in_book = False
            return
        if opponent_notation in self._pending_reply_map:
            self._node = self._pending_reply_map[opponent_notation]
            self.in_book = self._node is not None
        else:
            self.in_book = False


def _notation_index(pos: eb.Position, side: int) -> dict[str, eb.Move]:
    index = {}
    for m in eb.get_legal_moves(pos, side):
        rec = nl.build_move_record(pos, m.from_sq, m.to_sq, False)
        child = pos.clone()
        eb.apply_move(child, m)
        promoted = bool(child.flags[eb.sq_index(*m.to_sq)] & eb.PROMOTED) and not rec.was_already_promoted
        rec = rec._replace(promoted=promoted)
        index[nl.move_notation(rec)] = m
    return index


if __name__ == "__main__":
    # 冒烟测试：一棵两层的玩具准备树（不是真实开局理论，只用来验证机制本身），
    # 黑方 1.Hcf3，如果白方回 Ra10 就走 2.Ne3，其余情况脱谱交给引擎。
    tree = PrepNode([
        {"notation": "Hcf3", "weight": 1.0, "reply_to": {
            "Ra10": PrepNode.leaf("Ne3"),
        }},
    ])

    pos = eb.Position.initial()
    walker = BookWalker(tree, eb.BLACK, random.Random(0))

    move1 = walker.my_move(pos)
    assert move1 is not None
    rec1 = nl.build_move_record(pos, move1.from_sq, move1.to_sq, False)
    assert nl.move_notation(rec1) == "Hcf3", nl.move_notation(rec1)
    eb.apply_move(pos, move1)
    print("黑方按准备走出:", nl.move_notation(rec1))

    # 白方按"准备内"的应招走 Ra10
    white_move = None
    for m in eb.get_legal_moves(pos, eb.WHITE):
        rec = nl.build_move_record(pos, m.from_sq, m.to_sq, False)
        if nl.move_notation(rec) == "Ra10":
            white_move = m
            break
    assert white_move is not None
    eb.apply_move(pos, white_move)
    walker.observe_opponent_move("Ra10")
    assert walker.in_book, "对手走的是准备范围内的招，应该还在书里"

    move2 = walker.my_move(pos)
    rec2 = nl.build_move_record(pos, move2.from_sq, move2.to_sq, False)
    assert nl.move_notation(rec2) == "Ne3", nl.move_notation(rec2)
    print("对手 Ra10 之后，按准备走出:", nl.move_notation(rec2))

    # 再往后已经没有准备了，应该正确脱谱（第2层节点的 reply_to 是空字典）
    walker.observe_opponent_move("随便什么")
    print("再往后自动脱谱:", not walker.in_book)

    # 分支2：白方走了准备之外的招，应该立刻脱谱
    walker2 = BookWalker(tree, eb.BLACK, random.Random(0))
    pos2 = eb.Position.initial()
    m = walker2.my_move(pos2)
    eb.apply_move(pos2, m)
    walker2.observe_opponent_move("完全没准备到的招")
    print("对手脱离准备范围时立刻脱谱:", not walker2.in_book)

    print("opening_prep.py 冒烟测试通过 ✅")
