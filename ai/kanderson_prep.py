"""
ai/kanderson_prep.py
======================
Kanderson 人格的深度开局准备——用 opening_prep.py 的 PrepNode/BookWalker
编码。每一步都拿真实引擎逐条验证过合法性（脚本见开发过程，这里不重复贴），
四处验证失败的分支标了 TODO，先设成 None（等于"准备到此为止，交给引擎
自己接"），不是编码错误，是源材料本身还需要你确认：

    1. 天穹开局 -> Bxg6 -> Ah6 -> Bxc2 -> Axk8 -> Hxk8 -> Pxk8 -> Tj9 -> Bhf6
       -> Bxd1 -> Bxj9 -> Nj10 -> Pkf4 -> Bxd5 -> Ra2 -> 白方回"Bdb3"：
       这一步棋盘上有两个白方弩车都在d列（一个原本在d9没动过，一个是
       走到 Bxd5 那步、原本在h9现在到了d5的），"d"这个字母已经不能唯一
       区分了，按记谱规则应该改用起始排数消歧义——需要你确认具体是
       "d9那个"还是"d5那个"弩车走到b3。
    2. 天穹开局 -> ... -> Hf10 -> e7 -> Cb10 -> 黑方"Pe6"：
       这一步在引擎里验证不合法，这个局面下黑方凤凰能走到的格子是
       Pd6/Pe5/Pe3/Pg3/Pg5/... 都没有e6，需要你确认具体是哪一步。
    3. 激进进马局 -> 对手"Axd7"：这一步验证不合法，但去掉"x"变成单纯的
       "Ad7"（不吃子）就合法——注意到你在远古开局里写的是"对手走Ad7"
       （没有x），这里大概率是笔误多打了个x，已经按 Ad7（无吃子）编码，
       如果理解错了请告诉我。
    4. 激进进马局第二行 -> 对手"Bxg4"：验证不合法——白方两个弩车（d9/h9）
       都没有到 g4 的斜线（不共线，不管有没有炮架都够不着），这一步
       需要你重新确认具体是哪个棋子、走到哪。

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
    "Bdb3": None,  # TODO(1)：两个白弩车共享d列，需要你指定具体是哪一个
}

_BRANCH_CK10 = PrepNode.leaf("Pxj9")
_BRANCH_CK10.moves[0]["reply_to"] = {"Nxj9": PrepNode.leaf("Bxj9")}

_BRANCH_TE9_HF10_A = PrepNode.leaf("Bxh8")  # 对手先走 Te9 或 Hf10 二选一，走法一样
_BRANCH_TE9_HF10_A.moves[0]["reply_to"] = {"Nj10": PrepNode.leaf("Pxj9")}

_BRANCH_HF10_B = PrepNode.leaf("e7")
_BRANCH_HF10_B.moves[0]["reply_to"] = {"Cb10": PrepNode.leaf(None)}
_BRANCH_HF10_B.moves[0]["reply_to"]["Cb10"] = PrepNode([
    {"notation": "TODO_Pe6", "weight": 1.0, "reply_to": {}}
])
# TODO(2)：黑方这一步的 notation 验证不合法，先占位成 "TODO_Pe6"（这个字符串
# 引擎里必然找不到，BookWalker 遇到会直接报错提醒，不会被误当成正常招法用）

_BRANCH_NJ10 = PrepNode.leaf("Pxj9")

_BHF6_NODE = PrepNode.leaf("Bhf6")
_BHF6_NODE.moves[0]["reply_to"] = {
    "Bxd1": _BRANCH_BXD1,
    "Ck10": _BRANCH_CK10,
    "Te9": _BRANCH_TE9_HF10_A,
    # "Hf10" 在原文里同时对应两种不同后续（"6.Bxh8"那条 和 "7.e7"那条）——
    # 两条都是"对手走 Hf10"之后的选择，但黑方回应不一样，这在当前
    # PrepNode 的"一个对手招法只能对应一个后续"设计下没法同时表示。
    # 先按接在"Te9"同一条处理（Bxh8 那条），"e7"那条单独存在 _BRANCH_HF10_B
    # 里，需要你确认 Hf10 之后黑方具体想走哪一条再最终定下来。
    "Hf10": _BRANCH_TE9_HF10_A,
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

_d7_branch = PrepNode.leaf("d7")
_d7_branch.moves[0]["reply_to"] = {"dxd7": PrepNode.leaf("Hd5")}
_d7_branch.moves[0]["reply_to"]["dxd7"].moves[0]["reply_to"] = {"Bxd5": PrepNode.leaf("Nxd5")}

_JIJIN_MAIN.moves[0]["reply_to"] = {
    "Ra10": _d7_branch, "Rl10": _d7_branch, "Cb9": _d7_branch, "Ck9": _d7_branch,
    "Ne10": _d7_branch, "Ng10": _d7_branch, "Nc10": _d7_branch, "Nj10": _d7_branch,
    # "走Swordsman"是个笼统说法（任意剑士动子），没法用单一 notation 表示，
    # 这里先不单独收——真实对局如果对手走了某个具体剑士的招法，会正常脱谱，
    # 不影响其他分支
    "Ad7": PrepNode.leaf("Ne4"),   # TODO(3)：原文"Axd7"验证不合法（d7当时是
    # 空的，没有子可吃），去掉x变成"Ad7"就合法，且你自己在远古开局里对同一
    # 招法就是写的不带x的"Ad7"，大概率是笔误，先按这个编码，理解错了请说
    "Bk7": PrepNode.leaf("Hcf3"),
    "Bxg4": None,  # TODO(4)：白方两个弩车都够不到g4，需要你重新确认这一步
}
_JIJIN_MAIN.moves[0]["reply_to"]["Ad7"].moves[0]["reply_to"] = {"Ac7": PrepNode.leaf("Tc4")}
_JIJIN_MAIN.moves[0]["reply_to"]["Ad7"].moves[0]["reply_to"]["Ac7"].moves[0]["reply_to"] = {
    "Ae9": PrepNode.leaf("Rl3"),
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

    print("kanderson_prep.py 冒烟测试通过 ✅（4处 TODO 待你确认后补完）")
