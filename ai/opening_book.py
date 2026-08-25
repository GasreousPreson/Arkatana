"""
ai/opening_book.py
====================
开局库：把人类总结出来的主流开局录进来，让 AI 在开局阶段**直接查表出招**，
不做搜索。

为什么要这个（两个原因，都很实际）：
    1. 开局阶段搜索最贵、收益最低。11×12 的棋盘开局有 113 种合法走法，
       深度3要搜几十秒，最后还常常选出不合棋理的招（引擎在"子力全在家、
       战术真空"的开局局面里，静态评估本来就给不出什么有效信号）。
       查表出招是 0 秒，而且必然是人类验证过的好棋。
    2. 顺便解决"AI 只会走雁门关"的问题——查表时按权重随机挑，
       每盘棋开局都不一样，自对弈攒出来的开局库才有价值。

**用记谱定义，不用坐标**：库里写的是用户给的记谱（"Hcf3"、"Ne2"），
加载时由引擎自己"生成全部合法走法 -> 算出每一步的记谱 -> 匹配"来反解出
真实走法。这样做的原因很实在：手工推坐标极易出错——第一版我手写坐标时
就把"鬼探头 Ne2"写成了 d1->e2，实际上 e2 是 h1 的重骑士跳过去的
（d1 跳的是 g2），启动校验当场就抓出来了。记谱是用户验证过的事实，
让引擎自己去反解，就没有我手抄出错的余地。

黑白对称：这套开局是以**黑方（先手，底线在第1排）**的视角写的。白方底线在
第12排，整个布局是上下镜像，所以白方把排数镜像过来即可（row -> 13-row）。
用户确认过"白方后手同样可以考虑这些招法"。

依赖：ai/engine_bridge.py、ai/notation_lite.py（都不依赖网站后端）
"""

from __future__ import annotations

import random

import engine_bridge as eb
import notation_lite as nl


# ---------------------------------------------------------------------------
# 开局定义（记谱以黑方先手视角书写）
# ---------------------------------------------------------------------------
# weight: 被挑中的相对概率。注意这**不是**棋力评价，只是"想让自对弈多探索
#         哪些分支"的调节旋钮。雁门关是正着（而且不是唯一正着），所以权重
#         跟其他主流开局一样是 1.2——之前调低纯粹是因为 AI 原本只会走它，
#         但真正解决"只走一种开局"的是开局库本身的随机挑选机制，不需要
#         靠压低某个正着来实现。天穹/远古权重略高只是想多攒一点犀利变化的
#         数据，两个"软招"权重略低同理，都不代表好坏判断。
# gambit: 是否弃子开局（仅作标注，方便以后按类别做对比实验）

OPENINGS = [
    {"name": "远古开局",   "gambit": True,  "weight": 3.0, "moves": ["Hcf3", "Hjf3"]},
    {"name": "天穹开局",   "gambit": True,  "weight": 3.0, "moves": ["g6", "e6"]},
    {"name": "雁门关",     "gambit": False, "weight": 1.2, "moves": ["Ra3", "Rl3"]},
    {"name": "堪德森弃兵", "gambit": True,  "weight": 2.0, "moves": ["c7", "j7"]},
    {"name": "侧翼弃兵",   "gambit": True,  "weight": 2.0, "moves": ["b7", "k7"]},
    {"name": "古城进攻",   "gambit": False, "weight": 1.5, "moves": ["Cb4", "Ck4"]},
    {"name": "起马局",     "gambit": False, "weight": 1.2, "moves": ["Ne3", "Ng3"]},
    {"name": "现代起马局", "gambit": False, "weight": 1.2, "moves": ["Nc3", "Nj3"]},
    {"name": "幻想开局",   "gambit": False, "weight": 1.2, "moves": ["Pa3", "Pl3"]},
    # Kanderson 的准备里提到了"激进进马局 1.Ng4"，之前的18个开局里没有，
    # 补进来（h1马跳g4，镜像j4）。
    {"name": "激进进马局", "gambit": False, "weight": 1.2, "moves": ["Ng4"]},
    {"name": "剑阁开局",   "gambit": False, "weight": 1.2,
     "moves": ["Sc3", "See3", "Sge3", "Sgg3", "Sj3"]},
    {"name": "振枢开局",   "gambit": False, "weight": 1.2, "moves": ["Af3"]},
    {"name": "边兵开局",   "gambit": False, "weight": 1.0, "moves": ["a7", "l7"]},
    {"name": "鸳鸯跑",     "gambit": False, "weight": 1.2, "moves": ["Te4", "Tg4"]},
    {"name": "鬼探头",     "gambit": False, "weight": 1.0, "moves": ["Ne2", "Ng2"]},
    {"name": "诱惑开局",   "gambit": False, "weight": 1.2, "moves": ["Nc4", "Nj4"]},
    # 用户写的是 "Nf2"，但两个重骑士(d1/h1)都能跳到 f2，按记谱规则必须消歧义，
    # 所以这里展开成两条具体变例
    {"name": "城头马",     "gambit": False, "weight": 1.0, "moves": ["Ndf2", "Nhf2"]},
    # 下面两个用户标注为"相对软招"，权重调低但保留——自对弈里偶尔走一走，
    # 才能攒出数据来验证它们到底有多软
    {"name": "弃弩开局",   "gambit": True,  "weight": 0.4, "moves": ["Bxd8", "Bxh8"]},
    {"name": "弃炮开局",   "gambit": True,  "weight": 0.4, "moves": ["Txa8+", "Txl8+"]},
    # 用户确认的两个新开局：
    {"name": "安娜格拉斯弃兵", "gambit": True, "weight": 1.2, "moves": ["e7", "g7"]},
    {"name": "艾薇尔变例", "gambit": False, "weight": 1.2, "moves": ["Tk4", "Pd2"]},
    # 之前我误判 Tk4/Pd2 不合法（l4炮到k4只是横移1格，c1凤凰到d2只是斜移1格，
    # 两个都是完全合法的第一步，之前的判断是我的错，不是原始描述有问题）。
]


def _mirror(sq: tuple[int, int]) -> tuple[int, int]:
    """黑方视角坐标 -> 白方视角坐标（上下镜像）：黑方底线第1排对应白方底线
    第12排，所以 row -> 13-row，列不变。"""
    return (sq[0], 13 - sq[1])


def _notation_index(pos: eb.Position, side: int) -> dict[str, eb.Move]:
    """开局局面下 {记谱字符串: 走法}。记谱由引擎自己生成，跟真实对局里
    走出这一步时记录的记谱完全一致（notation_lite 已经跟网站的 notation.py
    做过450步交叉验证）。"""
    index = {}
    for m in eb.get_legal_moves(pos, side):
        rec = nl.build_move_record(pos, m.from_sq, m.to_sq, False)
        child = pos.clone()
        eb.apply_move(child, m)
        promoted = bool(child.flags[eb.sq_index(*m.to_sq)] & eb.PROMOTED) and not rec.was_already_promoted
        rec = rec._replace(promoted=promoted)
        index[nl.move_notation(rec)] = m
    return index


def _resolve(side: int) -> list[dict]:
    """把记谱解析成真实走法。黑方直接按记谱查；白方先在黑方那边查到坐标，
    再镜像过来（白方自己的记谱排数不一样，不能直接拿黑方的记谱字符串去查）。
    任何一条解析不出来都直接抛错——宁可启动时就炸，也不要等到对局中途
    才发现库里有一步走不通的棋。"""
    pos = eb.Position.initial()
    black_index = _notation_index(pos, eb.BLACK)
    white_legal = {(m.from_sq, m.to_sq): m for m in eb.get_legal_moves(pos, eb.WHITE)}

    resolved = []
    for entry in OPENINGS:
        variations = []
        for notation in entry["moves"]:
            black_move = black_index.get(notation)
            if black_move is None:
                raise ValueError(
                    f"开局库里有一条记谱解析不出来：{entry['name']} {notation!r}"
                    f"（黑方开局局面下没有这步棋，或者记谱写法不对）"
                )
            if side == eb.BLACK:
                move = black_move
            else:
                f, t = _mirror(black_move.from_sq), _mirror(black_move.to_sq)
                move = white_legal.get((f, t))
                if move is None:
                    raise ValueError(
                        f"开局库镜像到白方后不合法：{entry['name']} {notation!r} -> "
                        f"{eb.coord_to_str(*f)}->{eb.coord_to_str(*t)}"
                    )
            rec = nl.build_move_record(pos, move.from_sq, move.to_sq, False)
            child = pos.clone()
            eb.apply_move(child, move)
            promoted = bool(child.flags[eb.sq_index(*move.to_sq)] & eb.PROMOTED) and not rec.was_already_promoted
            rec = rec._replace(promoted=promoted)
            variations.append({"move": move, "notation": nl.move_notation(rec)})
        resolved.append({
            "name": entry["name"], "gambit": entry["gambit"],
            "weight": entry["weight"], "variations": variations,
        })
    return resolved


_CACHE: dict[int, list[dict]] = {}


def get_book(side: int) -> list[dict]:
    if side not in _CACHE:
        _CACHE[side] = _resolve(side)
    return _CACHE[side]


def pick_opening_move(side: int, rng: random.Random | None = None,
                       only_names: list[str] | None = None):
    """按权重随机挑一个开局，再从它的变例里等概率挑一条具体走法。
    返回 (move, opening_name, notation)。

    only_names 可以限定只从指定的几个开局里挑——做"哪个开局胜率更高"的
    对比实验时用得上（比如只跑天穹开局100局，再只跑雁门关100局）。
    """
    rng = rng or random.Random()
    book = get_book(side)
    if only_names:
        book = [b for b in book if b["name"] in only_names]
        if not book:
            raise ValueError(f"没有匹配的开局：{only_names}")

    total = sum(b["weight"] for b in book)
    r = rng.random() * total
    upto = 0.0
    chosen = book[-1]
    for b in book:
        upto += b["weight"]
        if upto >= r:
            chosen = b
            break

    variation = rng.choice(chosen["variations"])
    return variation["move"], chosen["name"], variation["notation"]


def opening_names() -> list[str]:
    return [e["name"] for e in OPENINGS]


if __name__ == "__main__":
    for side, label in ((eb.BLACK, "黑方"), (eb.WHITE, "白方")):
        book = get_book(side)
        total_vars = sum(len(b["variations"]) for b in book)
        print(f"{label}开局库：{len(book)} 个开局，{total_vars} 条具体走法，全部验证合法 ✅")
        for b in book:
            notations = " / ".join(v["notation"] for v in b["variations"])
            tag = "（弃子）" if b["gambit"] else ""
            print(f"   {b['name']}{tag} 权重{b['weight']}: {notations}")
        print()

    rng = random.Random(0)
    counts = {}
    for _ in range(2000):
        _, name, _ = pick_opening_move(eb.BLACK, rng)
        counts[name] = counts.get(name, 0) + 1
    print("按权重随机挑2000次的分布（天穹/远古应明显多于雁门关）:")
    for name, c in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"   {name:<12} {c:>4} 次")
