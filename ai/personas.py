"""
ai/personas.py
================
9个AI人格的配置——棋力(depth)、精度(precision)、并列最优时是否偏爱冒险
(prefer_aggressive_ties)、棋风(evaluate权重)、偏好开局、ELO、时间控制。

⚠️ 权重翻译是主观判断，不是从棋谱数据反推出来的：
    "浪漫主义/弃子攻杀"这种文字描述，翻译成"king_safety权重设成多少"
    本身就没有唯一正确答案——这里给的是我读完描述之后的第一版翻译，
    每个数字后面的注释写了翻译依据，但这终究是猜测，不是标定过的结果。
    真正靠谱的调法是等这批人格先用起来、攒够自对弈/实战数据之后，
    拿真实结果去反过来修正这些数字（跟 evaluate.py 权重当初的调法一路）。

⚠️ 深度开局准备（"如果对手...那么..."那种带条件分支的树）目前**没有编码
    进来**：手写的密集记谱文本里有几处我没法确定唯一读法（举例见
    conversation 里贴出的具体分析），贸然按我自己的理解编码风险很大——
    错的开局准备会一直悄悄错下去，没人会发现。这些人格目前先用
    opening_book.py 的"只看第一步"权重（数据来源明确、已经逐条验证过
    合法性），具体是 Kanderson/Anaxagoras；深度树等按提议的 DSL
    重新录入后再接上，接口（PrepNode/BookWalker，见 opening_prep.py）
    已经就绪，不用再改这边的代码。

依赖：ai/evaluate.py、ai/opening_book.py（不依赖网站后端）
"""

from __future__ import annotations

from typing import NamedTuple, Optional

from evaluate import DEFAULT_WEIGHTS, Weights


class Persona(NamedTuple):
    key: str                      # 内部标识，用于 GAME_AI/账号绑定等
    display_name: str
    depth: int
    precision: float              # 1.0 = 几乎总是选最优招；越低越可能选次优招
    prefer_aggressive_ties: bool  # 并列最优时是否偏爱更冒险的那个（见 search.py）
    weights: Weights
    opening_only: Optional[list[str]]  # None = 用完整开局库；列表 = 只从这些开局里按权重抽
    elo: int
    minutes_per_side: int
    increment_seconds: int
    style_note: str = ""          # 人味描述，方便以后做展示/调试


PERSONAS: dict[str, Persona] = {

    "kanderson": Persona(
        key="kanderson", display_name="Kanderson",
        # 2026-09 修改：precision 从0.95改成1.0、prefer_aggressive_ties从True
        # 改成False——用户观察到这套"95%选真正最优解、5%主动挑更冒险的
        # 候选、并列最优时也偏爱冒险"的组合，实战里表现出来根本不是
        # "浪漫主义弃子攻杀"，就是单纯送子。现在改成precision Max的正常
        # 模型，跟 Gasparret 一样是"永远算出来什么就走什么，不主动偏离/
        # 不为了冒险扭曲选择"——Kanderson 的攻击性风格现在完全靠下面这组
        # weights 本身来体现（王城安全看得更轻、更看重空间和节奏），
        # 而不是靠搜索阶段刻意选一个非最优的"看起来更猛"的招法。
        # 这样"敢送子"只会在 weights 真的算出"送子换来的空间/节奏值这个价"
        # 的时候才发生，不会再无缘无故地送。
        depth=3, precision=1.0, prefer_aggressive_ties=False,
        weights=Weights(
            material=1.0,
            king_safety=0.35,   # 默认0.55调低——"以弃子攻杀扬名""喜于激烈对攻"，
                                  # 愿意承担更高的王城风险换取攻势
            mobility=0.032,      # 默认0.02调高——"善于掌控空间优势"
            center=0.09,         # 默认0.06调高——"掌控...开放线"
            tempo=0.18,
        ),
        opening_only=["远古开局", "天穹开局", "激进进马局", "堪德森弃兵", "幻想开局",
                       "安娜格拉斯弃兵", "侧翼弃兵", "雁门关"],
        # 已确认："梦幻开局"="幻想开局"（凤凰走a3/l3），"安娜格拉斯弃兵"=黑方
        # e7/g7（已加进 opening_book.py）。原文给的精确百分比（远古16.6/
        # 天穹16.6/激进进马局16.6/堪德森弃兵11.6/幻想开局11.6/安娜格拉斯弃兵
        # 11.6/侧翼弃兵7.5/雁门关7.5）暂时还是用 opening_book.py 里统一的
        # 权重（1.2/2.0等），没有单独为 Kanderson 定制这8个开局各自的精确
        # 权重——这需要给 pick_opening_move 加一个"人格专属权重覆盖"的参数，
        # 是个小改动，等 Kanderson 深度开局树一起录入的时候顺便做。
        elo=1500, minutes_per_side=120, increment_seconds=90,
        style_note="浪漫主义弃子攻杀，善控空间与开放线，精度Max的正常模型（风格完全靠weights体现），深度开局准备待录入",
    ),

    "anaxagoras": Persona(
        key="anaxagoras", display_name="Anaxagoras",
        depth=3, precision=0.85,   # "精度高"但不到 Kanderson"极高"那一档，
        # 且原文说"其招法往往也不够精确"——给了比 Kanderson 更低的 precision
        prefer_aggressive_ties=True,   # "永远走极度狂野冒险的变化"，跟 Kanderson
        # 同理设成偏爱冒险
        weights=Weights(
            material=1.0,
            king_safety=0.25,    # 比 Kanderson 更低——"棋路比 Kanderson 更为凶险"
            mobility=0.04,
            center=0.10,
            tempo=0.20,
        ),
        opening_only=None,
        # ⚠️ 开局部分完全没编码——原文是"前2~3手都走 b/c/e/g/j/k 这几个文件的
        # 兵"这种"一段时间内的固定模式"，不是"选一个开局"这种一次性决定，
        # 用现在 opening_book.py 的"只看第一步"结构装不下，需要
        # opening_prep.py 的树状结构（已经就绪，见文件顶部说明）；
        # 具体这几手兵怎么排、10%概率走的 Txl8+/Bxd8 弃子线后续怎么接，
        # 都需要按提议的 DSL 重新录入，这里先留空，不代表"不需要开局库"。
        elo=1500, minutes_per_side=45, increment_seconds=30,
        style_note="狂放流，深度开局准备(边翼兵阵+弃子线)待录入",
    ),

    "karspeirsky": Persona(
        key="karspeirsky", display_name="Karspeirsky",
        depth=3, precision=0.9,
        prefer_aggressive_ties=False,   # 原文没提"并列最优时怎么选"，不额外加这个行为
        weights=Weights(
            material=1.0,   # 特意**没有**调低——原文明确说"不会因此走出
            # Txa8+/Bxd8 那种纯送子的垃圾走法"，调低 material 权重正是
            # 造成那个bug的根因之一，这个人格的"不太在意子力"应该体现在
            # 更看重威胁性（mobility/center），而不是真的让它更容易亏子
            king_safety=0.45,
            mobility=0.045,   # 全场最高——"嗜弃子搏出子速度"
            center=0.08,
            tempo=0.22,       # 全场最高——"若某子受胁则果断弃子进棋，喜漏破绽诱敌
                              # 加快出子"，tempo 加分正是"抢先手"的量化
        ),
        opening_only=None,   # 原文没给具体开局列表，用完整开局库
        elo=1500, minutes_per_side=60, increment_seconds=30,
        style_note="速度流，弃子换出子速度",
    ),

    "gasparret": Persona(
        key="gasparret", display_name="Gasparret",
        depth=3, precision=1.0, prefer_aggressive_ties=False,
        weights=DEFAULT_WEIGHTS,   # "无风格棋手"——原文没有任何风格倾向，
        # 直接用未经人格化的默认权重最贴切
        opening_only=None,   # "只要不是违背棋理的开局，他都会下"=完整开局库
        # 2026-09 修复：原来是 minutes_per_side=100, increment_seconds=80——
        # 这两个数字都不在 clock.py 的 ALLOWED_MINUTES_PER_SIDE/
        # ALLOWED_INCREMENT_SECONDS 白名单里（每方分钟数到90之后直接跳到105，
        # 加秒到75之后直接跳到90，100和80都卡在两档中间）。TimeControl 的
        # __post_init__ 发现不合法会直接抛 ValueError——这意味着
        # _create_bot_room("gasparret") 每次都会撞进那个 ValueError 分支，
        # 100%失败、连一次都不会成功，绝不是"轮到它的概率低"这种概率问题。
        # 这正是"Gasparret 不会自动创建对局"的根因：它能加入别的bot建的房间，
        # 是因为加入时用的是房间创建者早就建好、合法的 TimeControl，从来
        # 没用过自己这组非法数值；只有轮到它自己建房时，才会摸到这个从没
        # 校验过的非法配置。改成105+75——都是白名单里离原数值最近的合法档位，
        # 两者之和还是180，尽量不改变原本"较长时间控制"这个设计意图。
        elo=1500, minutes_per_side=105, increment_seconds=75,
        style_note="无风格，随对手应变",
    ),

    "flannery": Persona(
        key="flannery", display_name="Flannery",
        depth=3, precision=0.95,   # 用户确认
        prefer_aggressive_ties=False,   # 跟 Kanderson 不一样：Flannery 的0.05
        # 不精准不是来自冒险偏好（原文没有这个特质），单纯是温度采样的
        # 轻微偏离，所以这里不开 prefer_aggressive_ties
        weights=Weights(
            material=1.0,
            king_safety=0.65,   # 调高——"不求速度，但求细节上的对抗"，偏防守细腻
            mobility=0.012,     # 调低——"厌恶兑子"，不主动追求开放局面
            center=0.03,        # 调低——"喜封闭局面"，不特别抢中心
            tempo=0.10,          # 调低——不求速度
        ),
        opening_only=["幻想开局", "雁门关", "古城进攻", "振枢开局", "鸳鸯跑", "现代起马局"],
        # 原文给的6个开局各16.6%，跟已有开局库里的名字能对上（"梦幻开局"当
        # "幻想开局"处理，"古堡进攻"对应"古城进攻"，"鸳鸯炮"对应"鸳鸯跑"，
        # 这三处是名字对应关系，不是权重歧义，相对有把握）
        elo=1500, minutes_per_side=120, increment_seconds=90,
        style_note="局面复杂型，偏好封闭结构",
    ),

    "avril": Persona(
        key="avril", display_name="Avril",
        depth=3, precision=0.85,
        prefer_aggressive_ties=False,
        weights=Weights(
            material=1.0, king_safety=0.5, mobility=0.028, center=0.075, tempo=0.16,
        ),
        opening_only=["幻想开局", "雁门关", "鬼探头", "艾薇尔变例", "诱惑开局", "边兵开局",
                       "远古开局", "起马局", "现代起马局", "古城进攻"],
        # 已确认：Tk4/Pd2 之前是我判断错了（都合法，见 opening_book.py 的
        # "艾薇尔变例"）。原文是"50%走{梦幻/雁门关/鬼探头/Tk4/诱惑/边兵/
        # 艾薇尔变例Pd2}，50%走主流{远古/起马局/现代起马局/古堡进攻}"这种
        # 两组各50%、组内再分权重的两层结构，跟 Kanderson 一样，眼下先把
        # 两组合并成一个不分组的列表（组间50/50、组内按原比例这种两层权重
        # 也需要前面提到的"人格专属权重覆盖"参数才能精确还原，先记在同一个
        # TODO 里一起做）。
        elo=1500, minutes_per_side=90, increment_seconds=30,
        style_note="创造派",
    ),

    "maggeritta": Persona(
        key="maggeritta", display_name="Maggerita",
        depth=3, precision=1.0, prefer_aggressive_ties=False,
        weights=DEFAULT_WEIGHTS,   # 原文明确说"可以设置成与当前hard-ai一样的模型"
        opening_only=None,
        elo=1500, minutes_per_side=75, increment_seconds=40,
        style_note="局面型，窒息策略——等同当前默认 hard 模型",
    ),

    "ananta": Persona(
        key="ananta", display_name="Ananta",
        depth=3, precision=1.0, prefer_aggressive_ties=False,   # 用户确认精度为1.0
        weights=DEFAULT_WEIGHTS,   # "以纯粹的计算力著称"——没有风格倾向，
        # 用默认权重代表"让客观评估自己说话"，不额外加人格化的偏向
        opening_only=None,
        elo=1500, minutes_per_side=120, increment_seconds=180,
        style_note="计算大师，无风格偏向",
    ),

    "sangomanti": Persona(
        key="sangomanti", display_name="Sangomanti",
        depth=2,   # 原文明确给了，全场最低
        precision=0.5,   # 用户确认
        prefer_aggressive_ties=False,
        weights=Weights(
            material=1.0, king_safety=0.4, mobility=0.03, center=0.09, tempo=0.15,
        ),
        # ⚠️ "棋风怪异，不走寻常路"很难翻译成具体权重数字，这一组纯粹是
        # 让它看起来不像默认模型的临时草案，没有明确的翻译依据，
        # 最需要你审阅这一项
        opening_only=None,
        elo=1500, minutes_per_side=30, increment_seconds=120,
        style_note="鬼魅派，precision待定，weights为粗略草案",
    ),
}


def get_persona(key: str) -> Persona:
    if key not in PERSONAS:
        raise ValueError(f"未知的 AI 人格: {key!r}（可用: {list(PERSONAS)}）")
    return PERSONAS[key]


if __name__ == "__main__":
    import os
    import sys

    import opening_book

    # clock.py 是网站后端"权威引擎"那一层的模块，不在 ai/ 目录里——ai/
    # 平时完全不依赖它，只有这里做"人格时间控制配置是否合法"这项跨层
    # 校验时才需要临时借用，跟 ai/tests/test_engine_bridge.py 借用权威引擎
    # 做交叉验证是同一个思路。只在 __main__ 里加这条 sys.path，不影响
    # play_service.py/api.py 正常 import 这个模块时的行为。
    _REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _REPO_ROOT not in sys.path:
        sys.path.insert(0, _REPO_ROOT)
    from clock import TimeControl

    for key, p in PERSONAS.items():
        print(f"{p.display_name:<12} depth={p.depth} precision={p.precision} "
              f"aggressive_ties={p.prefer_aggressive_ties} elo={p.elo} "
              f"time={p.minutes_per_side}+{p.increment_seconds}")
        print(f"   weights: {p.weights}")

        # 校验人格自己的时间控制真的能建出一个合法的 TimeControl——
        # 2026-09 之前 Gasparret 配的 100+80 两个数字都不在 clock.py 的
        # 白名单档位里，TimeControl() 会直接抛 ValueError，但这个错误只会
        # 在 api.py 的 _create_bot_room 里被悄悄 catch 掉、返回 None，
        # 调度器每一轮都会失败、但不会有任何日志或报错——这正是"Gasparret
        # 不会自动创建对局"这个bug能一直悄悄存在而没人发现的原因。
        # 现在启动时就把这个校验做掉，配置写错了直接在这里炸，不会再
        # 悄无声息地失败几十次调度循环才被人观察出来。
        try:
            TimeControl(p.minutes_per_side, p.increment_seconds)
        except ValueError as e:
            raise AssertionError(
                f"{p.display_name} 的时间控制 {p.minutes_per_side}+{p.increment_seconds} "
                f"不合法（{e}）——这会导致 api.py 的 _create_bot_room 每次都静默失败，"
                f"这个人格永远建不了房，只能加入别人的房间"
            ) from e

        if p.opening_only:
            # 校验人格限定的开局名字都能在 opening_book.py 里找到——
            # 名字打错字的话这里会直接报错，不用等到真的用起来才发现
            book_names = set(opening_book.opening_names())
            missing = [n for n in p.opening_only if n not in book_names]
            assert not missing, f"{p.display_name} 的 opening_only 里有开局库找不到的名字: {missing}"
            print(f"   开局限定: {p.opening_only}（全部在开局库里验证通过）")
        else:
            print("   开局: 完整开局库（不限定）")
        print()

    print(f"personas.py 冒烟测试通过 ✅ 共 {len(PERSONAS)} 个人格（含时间控制合法性校验）")
