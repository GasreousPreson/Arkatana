"""
ai/book_build.py
==================
把 ai/data/games/*.jsonl 里的自对弈原始棋谱，聚合成两份东西：
    1. ai/data/book.sqlite —— 给以后想用 SQL 查询/统计的场合用
    2. ai/viewer_data.js —— 给本地查看器（ai/explorer.html）用 <script> 标签
       直接加载的数据文件（不用 fetch：file:// 协议下 fetch 本地文件经常被
       浏览器 CORS 挡住，双击打开 HTML 就会看到一片空白，script 标签加载
       没有这个限制，双击即可用，不需要起本地服务器）

每次运行都是全量重建（先清空旧数据，重新扫一遍全部 jsonl），不是增量累加——
这样以后调 PLIES_CAP / MIN_GAMES_THRESHOLD 这些参数随时能重跑，不会有
"改了参数但历史数据没跟着变"的隐患。

开局库不做"殊路同归"合并：key 不是局面哈希，是"从第一步开始、走过的这一串
招法序列本身"——不同顺序走到同一个局面，这里被当成两条独立分支各自统计，
这是刻意的设计（配合 explorer.html "一层一层点进去看分支"的浏览方式），
不是漏掉了什么优化。

用法：
    python3 ai/book_build.py
"""

from __future__ import annotations

import datetime
import glob
import json
import os
import sqlite3
from collections import defaultdict

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(_THIS_DIR, "data")
GAMES_DIR = os.path.join(DATA_DIR, "games")
SQLITE_PATH = os.path.join(DATA_DIR, "book.sqlite")
VIEWER_JS_PATH = os.path.join(_THIS_DIR, "viewer_data.js")

PLIES_CAP = 40           # 开局库只统计前多少半步——再往后基本都是只出现过
                          # 一两次的长尾，统计没意义，还会让库无限膨胀
MIN_GAMES_THRESHOLD = 1  # 一条分支至少要出现几次才收进聚合表——本地查询不用
                          # 担心拖垮服务器，先不筛（=1 等于不筛）；想收紧
                          # 随时改这个常量重跑就行，book_build 本来就是全量重建

SEP = "|"  # 招法序列 key 的分隔符，坐标字符串（比如 "a1"）不会用到这个字符


# ---------------------------------------------------------------------------
# 读取原始对局
# ---------------------------------------------------------------------------

def load_all_games() -> list[dict]:
    games = []
    if not os.path.isdir(GAMES_DIR):
        return games
    for path in sorted(glob.glob(os.path.join(GAMES_DIR, "*.jsonl"))):
        with open(path, encoding="utf-8") as f:
            for line_no, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as e:
                    print(f"  ⚠️ 跳过一行解析失败的记录: {path}:{line_no} ({e})")
                    continue
                record["_source_file"] = os.path.basename(path)
                games.append(record)
    return games


# ---------------------------------------------------------------------------
# 开局树
# ---------------------------------------------------------------------------

def _move_key(m: dict) -> str:
    return f"{m['from']}{m['to']}"


def build_opening_tree(games: list[dict]) -> dict[str, list[dict]]:
    """{prefix_key: [按 games 次数从多到少排序的候选走法列表]}
    prefix_key 用 SEP 拼接"到这一步为止走过的每一步的 move_key"，
    根局面（还没走任何一步）的 prefix_key 是空字符串。"""
    tree: dict[str, dict[str, dict]] = defaultdict(dict)

    for g in games:
        moves = g["moves"][:PLIES_CAP]
        result = g["result"]
        prefix_parts: list[str] = []
        for m in moves:
            prefix_key = SEP.join(prefix_parts)
            mk = _move_key(m)
            node = tree[prefix_key].get(mk)
            if node is None:
                node = {
                    "move": mk, "notation": m["notation"],
                    "from": m["from"], "to": m["to"],
                    "piece_type": m["piece_type"], "captured_type": m["captured_type"],
                    "promoted": m["promoted"],
                    "games": 0, "black_wins": 0, "white_wins": 0, "draws": 0,
                }
                tree[prefix_key][mk] = node
            node["games"] += 1
            if result == "black_wins":
                node["black_wins"] += 1
            elif result == "white_wins":
                node["white_wins"] += 1
            else:
                node["draws"] += 1
            prefix_parts.append(mk)

    pruned: dict[str, list[dict]] = {}
    for prefix_key, moves_dict in tree.items():
        kept = [v for v in moves_dict.values() if v["games"] >= MIN_GAMES_THRESHOLD]
        if not kept:
            continue
        kept.sort(key=lambda v: v["games"], reverse=True)
        pruned[prefix_key] = kept
    return pruned


# ---------------------------------------------------------------------------
# 输出：SQLite
# ---------------------------------------------------------------------------

def write_sqlite(games: list[dict], tree: dict[str, list[dict]]) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    if os.path.exists(SQLITE_PATH):
        os.remove(SQLITE_PATH)
    conn = sqlite3.connect(SQLITE_PATH)
    conn.execute("""
        CREATE TABLE games (
            game_id TEXT PRIMARY KEY, timestamp TEXT, depth INTEGER,
            temperature_plies INTEGER, temperature REAL, opening_tag TEXT,
            result TEXT, ply_count INTEGER, elapsed_seconds REAL,
            source_file TEXT, moves_json TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE opening_stats (
            prefix_key TEXT NOT NULL, move_key TEXT NOT NULL, notation TEXT,
            from_sq TEXT, to_sq TEXT, piece_type TEXT, captured_type TEXT, promoted INTEGER,
            games INTEGER, black_wins INTEGER, white_wins INTEGER, draws INTEGER,
            PRIMARY KEY (prefix_key, move_key)
        )
    """)
    conn.execute("CREATE INDEX idx_opening_prefix ON opening_stats(prefix_key)")

    conn.executemany(
        "INSERT INTO games VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        [
            (g["game_id"], g["timestamp"], g["depth"], g["temperature_plies"], g["temperature"],
             g.get("opening_tag"), g["result"], g["ply_count"], g["elapsed_seconds"],
             g["_source_file"], json.dumps(g["moves"], ensure_ascii=False))
            for g in games
        ],
    )

    rows = []
    for prefix_key, moves in tree.items():
        for m in moves:
            rows.append((
                prefix_key, m["move"], m["notation"], m["from"], m["to"],
                m["piece_type"], m["captured_type"], int(m["promoted"]),
                m["games"], m["black_wins"], m["white_wins"], m["draws"],
            ))
    conn.executemany("INSERT INTO opening_stats VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", rows)

    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# 输出：查看器用的 JS 数据文件
# ---------------------------------------------------------------------------

def write_viewer_js(games: list[dict], tree: dict[str, list[dict]]) -> None:
    game_summaries = [
        {
            "game_id": g["game_id"], "timestamp": g["timestamp"], "depth": g["depth"],
            "opening_tag": g.get("opening_tag"), "result": g["result"],
            "ply_count": g["ply_count"], "moves": g["moves"],
        }
        for g in games
    ]
    game_summaries.sort(key=lambda g: g["timestamp"], reverse=True)

    payload = {
        "games": game_summaries,
        "opening_tree": tree,
        "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "plies_cap": PLIES_CAP,
        "min_games_threshold": MIN_GAMES_THRESHOLD,
    }

    with open(VIEWER_JS_PATH, "w", encoding="utf-8") as f:
        f.write("// 自动生成，不要手改——重新运行 ai/book_build.py 会整个覆盖\n")
        f.write("window.ARKATANA_VIEWER_DATA = ")
        json.dump(payload, f, ensure_ascii=False)
        f.write(";\n")


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

def main():
    games = load_all_games()
    print(f"读到 {len(games)} 局自对弈对局")
    if not games:
        print(f"{GAMES_DIR} 下没有找到任何 .jsonl 文件——先跑\n"
              f"    python3 ai/self_play.py --games 20 --depth 3\n生成一些数据。")
        return

    tree = build_opening_tree(games)
    total_branches = sum(len(v) for v in tree.values())
    print(f"开局树：{len(tree)} 个局面节点，共 {total_branches} 条分支"
          f"（深度上限 {PLIES_CAP} 半步，最低出现次数阈值 {MIN_GAMES_THRESHOLD}）")

    write_sqlite(games, tree)
    print(f"已写入 {SQLITE_PATH}")

    write_viewer_js(games, tree)
    print(f"已写入 {VIEWER_JS_PATH}")
    print("现在可以直接双击打开 ai/explorer.html 查看（不需要起本地服务器）。")


if __name__ == "__main__":
    main()
