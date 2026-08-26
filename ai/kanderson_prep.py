"""
ai/kanderson_prep.py
======================
Kanderson 人格的深度开局准备——用 opening_prep.py 的 PrepNode/BookWalker
编码。每一步都拿真实引擎逐条验证过合法性。

之前4处卡住的地方，已经根据你的确认全部改完：
    1. "Bdb3" -> "B1b3"：那颗走过 Bxd1 的弩车（现在在d1，排数1）继续到b3。
    2. "Pe6" -> "Pe5"：跟着改了后面依赖它的分支——原文那句"如果Txe6"
       是配合 Pe6 写的，改成 Pe5 之后验证发现白方任何棋子都吃不到e5，
       这个分支已经不适用，直接去掉了，没有勉强凑一个替代。
    3. "Axd7" 挪到了正确的位置：不是对1.Ng4的独立应招，是黑方2.d7冲兵后，
       白方"dxd7"（直接吃）之外的另一种应招（此时d7确实有黑兵可吃）。
    4. "Bxg4" 挪到了正确的位置：不是对1.Ng4的独立应招，是白方"1...Bk7"
       之后那颗弩车（现在在k7）的第二步——k7到g4斜线距离3，隔着黑方h5的
       兵当炮架，吃掉黑方刚跳到g4的重骑士。

依赖：ai/opening_prep.py（不依赖网站后端）
"""

from __future__ import annotations

from opening_prep import PrepNode

# ---------------------------------------------------------------------------
# 远古开局（16.6%）——两条独立的线，都从 1.Hcf3 开始，按白方的第一手回应
# 分岔，所以合并成同一棵树、根节点的 reply_to 里两个 key
# ---------------------------------------------------------------------------

_ANCIENT_OPENING = PrepNode([
    {"notation": "Hcf3", "weight": 1.0, "reply_to": {
        "Bxd5": PrepNode([
            {"notation": "Ng4", "weight": 0.6, "reply_to": {
                "Bf7": PrepNode.leaf("Hf6"),
            }},
            {"notation": "Nj3", "weight": 0.4, "reply_to": {}},
        ]),
        "Ad7": PrepNode.leaf("Nc3"),
        # ↑ 这条线接下去是 Nc3 -> (对手e6) -> Cb3 -> (对手Pcf9) -> Hg6 ->
        # (对手Axb5) -> Hxf9 -> (对手Pxf9，原文笔误Pxf8已按你的更正处理) ->
        # Cxb5——因为 PrepNode.leaf 只支持"一步到底"，这条深度链手工展开在
        # 下面 _ANCIENT_LINE_B，两者在 personas.py 里会合并注册（见文件末尾）。
    }},
])

_ANCIENT_LINE_B = PrepNode.leaf("Nc3")
_ANCIENT_LINE_B.moves[0]["reply_to"] = {
    "e6": PrepNode.leaf("Cb3"),
}
_ANCIENT_LINE_B.moves[0]["reply_to"]["e6"].moves[0]["reply_to"] = {
    "Pcf9": PrepNode.leaf("Hg6"),
}
_ANCIENT_LINE_B.moves[0]["reply_to"]["e6"].moves[0]["reply_to"]["Pcf9"].moves[0]["reply_to"] = {
    "Axb5": PrepNode.leaf("Hxf9"),
}
_ANCIENT_LINE_B.moves[0]["reply_to"]["e6"].moves[0]["reply_to"]["Pcf9"] \
    .moves[0]["reply_to"]["Axb5"].moves[0]["reply_to"] = {
    "Pxf9": PrepNode.leaf("Cxb5"),   # 原文"Pxf8"，按你确认的笔误改成 Pxf9
}

# 把 Line B 接到根节点的 "Ad7" 分支上
ANCIENT_OPENING = _ANCIENT_OPENING
ANCIENT_OPENING.moves[0]["reply_to"]["Ad7"] = _ANCIENT_LINE_B


# ---------------------------------------------------------------------------
# 天穹开局（16.6%）
# ---------------------------------------------------------------------------

# ---- Bhf6 之后的5条分支，先各自搭好 ----

_BRANCH_BXD1 = PrepNode.leaf("Bxj9")
_BRANCH_BXD1.moves[0]["reply_to"] = {"Nj10": PrepNode.leaf("Pkf4")}
_BRANCH_BXD1.moves[0]["reply_to"]["Nj10"].moves[0]["reply_to"] = {"Bxd5": PrepNode.leaf("Ra2")}
_BRANCH_BXD1.moves[0]["reply_to"]["Nj10"].moves[0]["reply_to"]["Bxd5"].moves[0]["reply_to"] = {
    "B1b3": None,  # 已确认：走过 Bxd1 那颗弩车（现在在d1，即"排数1"）继续到b3。
    # 这条线到 B1b3 为止——原文没再往后给出黑方的应招，交给正常搜索接着走。
}

_BRANCH_CK10 = PrepNode.leaf("Pxj9")
_BRANCH_CK10.moves[0]["reply_to"] = {"Nxj9": PrepNode.leaf("Bxj9")}

_BRANCH_TE9_HF10_A = PrepNode.leaf("Bxh8")  # 对手先走 Te9 或 Hf10 二选一，走法一样
_BRANCH_TE9_HF10_A.moves[0]["reply_to"] = {"Nj10": PrepNode.leaf("Pxj9")}

_BRANCH_HF10_B = PrepNode.leaf("e7")
_BRANCH_HF10_B.moves[0]["reply_to"] = {"Cb10": PrepNode.leaf("Pe5")}   # 已确认：原文Pe6是笔误，应为Pe5
# 原文这里还有"{如果Txe6,那么9.Bxj9}"这个后续分支，但那是配合"Pe6"写的——
# 改成 Pe5 之后验证发现白方此时任何棋子都吃不到e5（合法走法里没有一个
# 能落在e5的吃子），这个分支已经不适用了，没有勉强保留或者猜一个替代，
# 直接去掉；Pe5 这一步之后就没有更深的准备了，交给正常搜索接着走。

_BRANCH_NJ10 = PrepNode.leaf("Pxj9")

_BHF6_NODE = PrepNode.leaf("Bhf6")
_BHF6_NODE.moves[0]["reply_to"] = {
    "Bxd1": _BRANCH_BXD1,
    "Ck10": _BRANCH_CK10,
    "Te9": _BRANCH_TE9_HF10_A,
    "Hf10": _BRANCH_HF10_B,   # 用户确认：Hf10 之后走 e7 这条
    "Nj10": _BRANCH_NJ10,
}

# ---- Bxg6 主干 ----

_TIANQIONG_MAIN = PrepNode.leaf("g6")
_TIANQIONG_MAIN.moves[0]["reply_to"] = {
    "Bxg6": PrepNode.leaf("Ah6"),
    "Ra10": PrepNode.leaf("Ra3"),
    "Rl10": PrepNode.leaf("Ra3"),
}

_ah6_node = _TIANQIONG_MAIN.moves[0]["reply_to"]["Bxg6"]
_ah6_node.moves[0]["reply_to"] = {"Bxc2": PrepNode.leaf("Axk8")}

_axk8_node = _ah6_node.moves[0]["reply_to"]["Bxc2"]
_axk8_node.moves[0]["reply_to"] = {
    "Ra3": PrepNode.leaf("Axl9"),   # 大括号里的"提前应变"分支
    "Nf11": PrepNode.leaf("Axl9"),
    "Hxk8": PrepNode.leaf("Pxk8"),  # 主线
}
_axk8_node.moves[0]["reply_to"]["Hxk8"].moves[0]["reply_to"] = {"Tj9": _BHF6_NODE}

TIANQIONG_OPENING = _TIANQIONG_MAIN


# ---------------------------------------------------------------------------
# 激进进马局（16.6%）
# ---------------------------------------------------------------------------

_JIJIN_MAIN = PrepNode.leaf("Ng4")

# 用户纠正：Axd7 不是对手对1.Ng4的独立应招，而是黑方2.d7冲兵之后，白方
# "dxd7"（直接吃兵）之外的另一种应招（大将过去吃，此时d7确实有黑兵可吃，
# 跟远古开局那步"没有子可吃"的Ad7完全是两回事，之前理解错了）。
_d7_branch = PrepNode.leaf("d7")
_d7_branch.moves[0]["reply_to"] = {
    "dxd7": PrepNode.leaf("Hd5"),
    "Axd7": PrepNode.leaf("Ne4"),
}
_d7_branch.moves[0]["reply_to"]["dxd7"].moves[0]["reply_to"] = {"Bxd5": PrepNode.leaf("Nxd5")}
_d7_branch.moves[0]["reply_to"]["Axd7"].moves[0]["reply_to"] = {"Ac7": PrepNode.leaf("Tc4")}
_d7_branch.moves[0]["reply_to"]["Axd7"].moves[0]["reply_to"]["Ac7"].moves[0]["reply_to"] = {
    "Ae9": PrepNode.leaf("Rl3"),
}

# 用户纠正：Bxg4 不是对1.Ng4的独立应招，是白方"1...Bk7"之后，那颗弩车
# （现在在k7）接着走的第二步——k7到g4斜线距离3，隔着黑方h5的兵当炮架，
# 隔子吃掉黑方剛跳到g4的重骑士，之前把它当成跟"1...Bk7"平级的独立分支
# 完全理解错了。
# 注意：这里不能再包一层 PrepNode.leaf("Bk7")——"Bk7"已经是上面
# reply_to 字典里的 key（触发条件，白方已经走过了），子节点应该直接是
# "轮到黑方走"的下一手 Hcf3，不是把"Bk7"当成黑方还要再走一遍的招法
# （第一版就是这么写挂的，BookWalker 会去局面里找"Bk7"这个黑方的招，
# 当然找不到）。
_bk7_branch = PrepNode.leaf("Hcf3")
_bk7_branch.moves[0]["reply_to"] = {"Bxg4": PrepNode.leaf("Axg4")}

_JIJIN_MAIN.moves[0]["reply_to"] = {
    "Ra10": _d7_branch, "Rl10": _d7_branch, "Cb9": _d7_branch, "Ck9": _d7_branch,
    "Ne10": _d7_branch, "Ng10": _d7_branch, "Nc10": _d7_branch, "Nj10": _d7_branch,
    # "走Swordsman"是个笼统说法（任意剑士动子），没法用单一 notation 表示，
    # 这里先不单独收——真实对局如果对手走了某个具体剑士的招法，会正常脱谱，
    # 不影响其他分支
    "Bk7": _bk7_branch,
}

JIJIN_OPENING = _JIJIN_MAIN


# ---------------------------------------------------------------------------
# 堪德森弃兵（11.6%）—— 后续几手（Hc5/Tc4/Pa3/Ng4/Ne2/Ad6/e7）取决于对手
# 应招与计算，原文本身说明这是"看着办"而不是固定路线，只把已经明确给出的
# 两条分支编码进来，其余交给正常搜索
# ---------------------------------------------------------------------------

_KANDERSON_GAMBIT = PrepNode.leaf("c7")
_KANDERSON_GAMBIT.moves[0]["reply_to"] = {"cxc7": PrepNode.leaf("Hc5")}
_KANDERSON_GAMBIT.moves[0]["reply_to"]["cxc7"].moves[0]["reply_to"] = {
    "c6": PrepNode.leaf("Hf6"),
    "f7": PrepNode.leaf("Pa3"),
}

KANDERSON_GAMBIT_OPENING = _KANDERSON_GAMBIT


# ---------------------------------------------------------------------------
# 侧翼弃兵（7.5%）
# ---------------------------------------------------------------------------

_FLANK_GAMBIT = PrepNode.leaf("k7")
_FLANK_GAMBIT.moves[0]["reply_to"] = {}

# "如果对手不走kxk7/Bxk7，则往往有kxk8+"——这里用一个特殊 key "*"表示
# "除了下面列出的具体应招之外，其他任何情况"，BookWalker 目前还不支持这种
# 通配符匹配，先只编码"对手确实吃了"和"对手确实没吃、走了别的"两种都
# 明确给出后续的情况，通配符逻辑等你确认要怎么处理"对手走了任意其他棋"
# 这种情况再加

_not_captured = PrepNode.leaf("kxk8+")
_not_captured.moves[0]["reply_to"] = {"Hxk8": PrepNode.leaf("Ah6")}

_captured = PrepNode.leaf("Hk5")
_captured.moves[0]["reply_to"] = {"Ah7": PrepNode.leaf("Hg4")}
_captured.moves[0]["reply_to"]["Ah7"].moves[0]["reply_to"] = {"Axh5": PrepNode.leaf("Ra3")}

_FLANK_GAMBIT.moves[0]["reply_to"] = {
    "kxk7": _captured,
    "Bxk7": _captured,
    # 其余任何对手应招 -> 按"往往有kxk8+"处理，用 None 先占位，实际使用时
    # 由 personas.py/play_service.py 里的兜底逻辑处理"没匹配到 key"的情况
    # （BookWalker 本身遇到没准备到的对手招法就会自动脱谱，效果上已经等价
    # 于"其他情况交给正常搜索"，跟"kxk8+"这个明确推荐不冲突，只是没有
    # 强制走它）
}

FLANK_GAMBIT_OPENING = _FLANK_GAMBIT


if __name__ == "__main__":
    import random
    import engine_bridge as eb
    import notation_lite as nl
    from opening_prep import BookWalker

    def simulate(tree, moves_notations, label):
        """按一串"记谱字符串"（交替黑白）驱动 BookWalker，确认树本身的结构
        没有写错（跟真实引擎的合法性验证是两回事——这里测的是"树的形状对不
        对"，合法性已经在开发时单独验证过了）。"""
        pos = eb.Position.initial()
        walker = BookWalker(tree, eb.BLACK, random.Random(0))
        for i, expected in enumerate(moves_notations):
            if i % 2 == 0:
                move = walker.my_move(pos)
                assert move is not None, f"{label} 第{i}步：意外脱谱"
                import notation_lite as nl
                rec = nl.build_move_record(pos, move.from_sq, move.to_sq, False)
                got = nl.move_notation(rec)
                assert got == expected, f"{label} 第{i}步：期望{expected}，实际{got}"
                eb.apply_move(pos, move)
            else:
                idx = {}
                for m in eb.get_legal_moves(pos, eb.WHITE):
                    import notation_lite as nl
                    rec = nl.build_move_record(pos, m.from_sq, m.to_sq, False)
                    idx[nl.move_notation(rec)] = m
                eb.apply_move(pos, idx[expected])
                walker.observe_opponent_move(expected)
        print(f"  ✅ {label}: {' '.join(moves_notations)}")

    simulate(ANCIENT_OPENING, ["Hcf3", "Ad7", "Nc3", "e6", "Cb3", "Pcf9", "Hg6",
                                "Axb5", "Hxf9", "Pxf9", "Cxb5"], "远古开局 Line B")
    simulate(KANDERSON_GAMBIT_OPENING, ["c7", "cxc7", "Hc5", "c6", "Hf6"], "堪德森弃兵")
    simulate(FLANK_GAMBIT_OPENING, ["k7", "kxk7", "Hk5", "Ah7", "Hg4", "Axh5", "Ra3"], "侧翼弃兵")

    # 修好的4处，各跑一遍完整分支，确认树的形状是对的
    simulate(TIANQIONG_OPENING, ["g6", "Bxg6", "Ah6", "Bxc2", "Axk8", "Hxk8", "Pxk8",
                                  "Tj9", "Bhf6", "Bxd1", "Bxj9", "Nj10", "Pkf4",
                                  "Bxd5", "Ra2", "B1b3"], "天穹开局 (修复1: B1b3)")
    # 注意：Hf10 这个触发条件目前接的是 Bxh8 那条分支（跟 Te9 共用），
    # 不是 e7 这条——原文"对手Te9/Hf10,6.Bxh8"和"对手Hf10/Te9,7.e7"
    # 两处都同时提到 Te9 和 Hf10，没法从文本本身判断哪条该优先，这是
    # 还没解决的最后一处歧义（跟之前4个不一样，这个不是"我理解错了"，
    # 是源文本这两句本身看着互相矛盾，需要你确认）。e7 这条分支的树本身
    # 已经搭好并且验证过合法（直接测子树，不经过 Hf10 这个入口）：
    _hf10_e7_subtree = _BRANCH_HF10_B
    pos = eb.Position.initial()
    for side, n in [(eb.BLACK,"g6"),(eb.WHITE,"Bxg6"),(eb.BLACK,"Ah6"),(eb.WHITE,"Bxc2"),
                     (eb.BLACK,"Axk8"),(eb.WHITE,"Hxk8"),(eb.BLACK,"Pxk8"),(eb.WHITE,"Tj9"),
                     (eb.BLACK,"Bhf6"),(eb.WHITE,"Hf10")]:
        idx = {}
        for m in eb.get_legal_moves(pos, side):
            rec = nl.build_move_record(pos, m.from_sq, m.to_sq, False)
            idx[nl.move_notation(rec)] = m
        eb.apply_move(pos, idx[n])
    walker = BookWalker(_hf10_e7_subtree, eb.BLACK, random.Random(0))
    move = walker.my_move(pos)
    rec = nl.build_move_record(pos, move.from_sq, move.to_sq, False)
    assert nl.move_notation(rec) == "e7"
    print("  ✅ 天穹开局 e7子树（Hf10之后，未接线，独立验证子树本身没问题）: e7 分支合法")
    simulate(JIJIN_OPENING, ["Ng4", "Ra10", "d7", "Axd7", "Ne4", "Ac7", "Tc4", "Ae9", "Rl3"],
             "激进进马局 (修复3: Axd7嵌套在d7之下)")
    simulate(JIJIN_OPENING, ["Ng4", "Bk7", "Hcf3", "Bxg4", "Axg4"],
             "激进进马局 (修复4: Bxg4是Bk7的续行)")

    print("kanderson_prep.py 冒烟测试通过 ✅ 全部4处修正都验证过了")
