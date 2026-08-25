"""
ai/self_play.py
=================
自我对弈：用 search.py 的引擎左右互搏，生成对局数据存进
ai/data/games/*.jsonl（本地专用，不进版本库，见 ai/.gitignore）。

开局多样性怎么来的：前 --temperature-plies 步用"温度采样"——不是每次都
死板地选分数最高的那步，而是把每个候选走法的分值转成概率分布，按概率抽样
（温度越高越随机，温度趋近0就退化成"直接选最优"）。这一段用的是
search.find_move_distribution()，能拿到每个候选走法各自的真实分值，
但因为放弃了根节点剪枝，比平时下棋用的 find_best_move() 慢不少
（同一开局局面实测：深度3下 find_best_move ≈18~30秒，
find_move_distribution ≈54秒）——这个代价只有温度采样阶段才付，
过了 --temperature-plies 之后自动切回快速的 find_best_move()，
不会让整盘棋都用慢的那条路径。

对局怎么算结束：
    - 杀城 / 将死 -> 分出胜负
    - 三次重复 -> 判和（真正的三次重复判定，不是近似）
    - 超过 MAX_PLIES 步还没结束 -> 判和（纯安全阀，正常对局不会碰到这个上限）

每盘棋结束立刻写一行 JSONL 并 flush——不是攒够一批再一次性写文件，
中途 Ctrl+C 或者进程被杀掉，已经下完的那些盘不会丢。

用法：
    python3 ai/self_play.py --games 20 --depth 3
    python3 ai/self_play.py --games 20 --depth 3 --temperature-plies 12 --temperature 0.8
    python3 ai/self_play.py --games 5 --depth 3 --opening a1a3,b1b3 --opening-tag "test-opening"

依赖：engine_bridge.py / search.py / evaluate.py / notation_lite.py（全部离线，
不依赖网站后端，可以脱离网站单独长时间跑）
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import time
import uuid
from datetime import datetime, timezone

from engine_bridge import (
    BLACK, WHITE, Move, PROMOTED, Position, TYPE_NAMES, apply_move, coord_to_str,
    get_legal_moves, has_only_throne, is_checkmate, is_threefold_repetition,
    other_side, parse_coord, position_signature, sq_index,
)
from search import find_best_move, find_move_distribution
import notation_lite as nl
import opening_book

MAX_PLIES = 300  # 安全阀——正常对局远远用不到，纯粹防止极端情况下无限下下去
DEFAULT_OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "games")

SIDE_NAME = {BLACK: "black", WHITE: "white"}


# ---------------------------------------------------------------------------
# 温度采样
# ---------------------------------------------------------------------------

def softmax_sample(candidates, side_to_move: int, temperature: float, rng: random.Random) -> Move:
    """candidates: list[RootMoveScore]（来自 find_move_distribution，分值是
    "正数偏向黑方"这套统一约定）。先转换成"对 side_to_move 这一方来说好不好"
    （越大越好），再做标准的 softmax（减最大值防止数值溢出），按概率抽样。
    """
    side_relative = [c.score if side_to_move == BLACK else -c.score for c in candidates]
    m = max(side_relative)
    weights = [math.exp((s - m) / temperature) for s in side_relative]
    total = sum(weights)
    r = rng.random() * total
    upto = 0.0
    for c, w in zip(candidates, weights):
        upto += w
        if upto >= r:
            return c.move
    return candidates[-1].move  # 浮点误差兜底，理论上走不到这里


# ---------------------------------------------------------------------------
# 单局自对弈
# ---------------------------------------------------------------------------

def _validate_opening_move(pos: Position, side: int, from_sq, to_sq) -> Move:
    """强制开局变例用：确认 (from_sq, to_sq) 在当前局面下真的是一步合法走法，
    顺手拿到引擎自己生成的、is_capture 字段正确的 Move 对象（而不是自己拼一个，
    自己拼的话 is_capture 标错不影响 apply_move 本身，但会影响后面消歧义/
    JSONL 记录里 captured_type 之外的一致性，不如直接复用引擎生成的）。"""
    for m in get_legal_moves(pos, side):
        if m.from_sq == from_sq and m.to_sq == to_sq:
            return m
    raise ValueError(
        f"强制开局变例里的一步棋不合法: {coord_to_str(*from_sq)}->{coord_to_str(*to_sq)}"
        f"（第 {SIDE_NAME[side]} 方，当前局面下走不通）"
    )


def play_one_game(depth: int, temperature_plies: int, temperature: float,
                   rng: random.Random, opening_moves=None, use_book: bool = True,
                   book_only=None):
    """跑一整盘自对弈。opening_moves 是可选的 [(from_sq, to_sq), ...] 强制前缀
    （用坐标元组，不是记谱文本——校验放在 _validate_opening_move 里）。

    返回 (move_records, result, ply_count)：
        move_records: list[notation_lite.MoveRecord]
        result: "black_wins" / "white_wins" / "draw_repetition" /
                "draw_movecap" / "draw_no_moves"（极端边缘情况，见 search.py
                里"零合法走法但未被将军"的说明，正常不会出现）
    """
    pos = Position.initial()
    side = BLACK
    move_records: list[nl.MoveRecord] = []
    history = [position_signature(pos, side)]
    opening_moves = opening_moves or []
    opening_names_used: list[str] = []

    for ply in range(MAX_PLIES):
        if has_only_throne(pos, side) or is_checkmate(pos, side):
            return move_records, ("white_wins" if side == BLACK else "black_wins"), ply, (opening_names_used[0] if opening_names_used else None)

        if is_threefold_repetition(history):
            return move_records, "draw_repetition", ply, (opening_names_used[0] if opening_names_used else None)

        # -- 选一步棋 --------------------------------------------------
        if ply < len(opening_moves):
            forced_from, forced_to = opening_moves[ply]
            move = _validate_opening_move(pos, side, forced_from, forced_to)
        elif ply < 2 and use_book:
            # 双方的第一步都从开局库里按权重随机挑——这是开局多样性的
            # 主要来源（比温度采样有效得多：温度采样只是在引擎自己的
            # 偏好附近抖动，开局库是直接换一条人类验证过的路线）。
            move, book_name, _ = opening_book.pick_opening_move(side, rng, only_names=book_only)
            if ply == 0:
                opening_names_used.append(book_name)
        elif ply < temperature_plies:
            candidates, _ = find_move_distribution(pos, side, depth)
            if not candidates:
                return move_records, "draw_no_moves", ply, (opening_names_used[0] if opening_names_used else None)
            move = softmax_sample(candidates, side, temperature, rng)
        else:
            result = find_best_move(pos, side, depth)
            if result.best_move is None:
                return move_records, "draw_no_moves", ply, (opening_names_used[0] if opening_names_used else None)
            move = result.best_move

        # -- 落子前先把消歧义/被吃棋子这些字段算好（需要落子前的局面）--------
        partial_record = nl.build_move_record(pos, move.from_sq, move.to_sq, is_checkmate_after=False)

        apply_move(pos, move)
        side = other_side(side)
        history.append(position_signature(pos, side))

        is_mate_after = is_checkmate(pos, side)
        promoted_now = bool(pos.flags[sq_index(*move.to_sq)] & PROMOTED) and not partial_record.was_already_promoted
        record = partial_record._replace(promoted=promoted_now, is_checkmate=is_mate_after)
        move_records.append(record)

    return move_records, "draw_movecap", MAX_PLIES, (opening_names_used[0] if opening_names_used else None)


# ---------------------------------------------------------------------------
# 批量跑 + 存档
# ---------------------------------------------------------------------------

def _move_to_jsonable(record: nl.MoveRecord) -> dict:
    return {
        "side": SIDE_NAME[record.side],
        "piece_type": TYPE_NAMES.get(record.piece_type, "?"),
        "from": coord_to_str(*record.from_sq),
        "to": coord_to_str(*record.to_sq),
        "captured_type": TYPE_NAMES.get(record.captured_type) if record.captured_type is not None else None,
        "promoted": record.promoted,
        "was_already_promoted": record.was_already_promoted,
        "disambiguation": record.disambiguation,
        "is_checkmate": record.is_checkmate,
        "notation": nl.move_notation(record),
    }


def run_batch(num_games: int, depth: int, temperature_plies: int, temperature: float,
              out_dir: str = DEFAULT_OUT_DIR, opening_moves=None, opening_tag=None,
              seed: int | None = None, use_book: bool = True, book_only=None) -> str:
    os.makedirs(out_dir, exist_ok=True)
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out_path = os.path.join(out_dir, f"{date_str}.jsonl")

    rng = random.Random(seed)
    results_tally: dict[str, int] = {}
    t_batch_start = time.time()

    print(f"自对弈开始：{num_games} 局，深度={depth}，温度采样前{temperature_plies}步"
          f"（温度={temperature}），输出到 {out_path}")
    if opening_moves:
        print(f"  强制开局变例（{opening_tag or '未命名'}）：{len(opening_moves)} 步")

    with open(out_path, "a", encoding="utf-8") as f:
        for game_idx in range(1, num_games + 1):
            t0 = time.time()
            try:
                move_records, result, ply_count, book_name = play_one_game(
                    depth, temperature_plies, temperature, rng, opening_moves,
                    use_book=use_book, book_only=book_only,
                )
            except KeyboardInterrupt:
                print(f"\n手动中断——已完成的 {game_idx - 1} 局已经写进 {out_path}，"
                      f"这一局没下完的不会保存。")
                raise

            elapsed = time.time() - t0
            record = {
                "game_id": uuid.uuid4().hex[:12],
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "depth": depth,
                "temperature_plies": temperature_plies,
                "temperature": temperature,
                "opening_tag": opening_tag or book_name,
                "result": result,
                "ply_count": ply_count,
                "elapsed_seconds": round(elapsed, 1),
                "moves": [_move_to_jsonable(r) for r in move_records],
            }
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
            f.flush()

            results_tally[result] = results_tally.get(result, 0) + 1
            print(f"  第{game_idx:>3}/{num_games}局: {result:<16} {ply_count:>3}步  "
                  f"用时{elapsed:>6.1f}s  {book_name or ''}  (累计 {time.time() - t_batch_start:>7.1f}s)")

    print(f"\n批次完成：{num_games} 局，总用时 {time.time() - t_batch_start:.1f}s")
    print("结果统计:", results_tally)
    print(f"已保存到: {out_path}")
    return out_path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_opening_arg(text: str | None):
    """--opening 参数格式：\"a1a3,b1b3\"（逗号分隔，每一项是"起点终点"拼接，
    比如 a1a3 = 从 a1 走到 a3）。跟记谱文本不同，这里故意用最直白的坐标拼接，
    不需要考虑消歧义解析，出错也容易一眼看出是哪一步写错了。"""
    if not text:
        return None
    moves = []
    for chunk in text.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        # 坐标可能是1~2位数字（比如 a1 到 a12），从中间切开需要找列字母之后
        # 第一个数字的位置，两段各自再拆一次"字母+数字"
        mid = None
        digit_run = 0
        for i, ch in enumerate(chunk):
            if ch.isdigit():
                digit_run += 1
                if digit_run >= 1 and i + 1 < len(chunk) and chunk[i + 1].isalpha():
                    mid = i + 1
                    break
        if mid is None:
            raise ValueError(f"无法解析开局坐标片段: {chunk!r}（期望格式如 'a1a3'）")
        moves.append((parse_coord(chunk[:mid]), parse_coord(chunk[mid:])))
    return moves


def main():
    parser = argparse.ArgumentParser(description="Arkatana AI 自我对弈")
    parser.add_argument("--games", type=int, default=20)
    parser.add_argument("--depth", type=int, default=3)
    parser.add_argument("--temperature-plies", type=int, default=16,
                         help="前多少步用温度采样制造开局多样性（默认16=双方各8步）")
    parser.add_argument("--temperature", type=float, default=1.0)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--out-dir", type=str, default=DEFAULT_OUT_DIR)
    parser.add_argument("--opening", type=str, default=None,
                         help="强制开局变例，如 'a1a3,b1b3'（逗号分隔的坐标对）")
    parser.add_argument("--opening-tag", type=str, default=None,
                         help="给这批强制开局变例起个名字，方便以后按开局分组统计胜率")
    parser.add_argument("--no-book", action="store_true",
                         help="不使用开局库（默认使用：双方第一步都从18个主流开局里按权重随机挑）")
    parser.add_argument("--book-only", type=str, default=None,
                         help="只用指定开局，逗号分隔，如 '天穹开局,雁门关'——做开局胜率对比实验时用")
    args = parser.parse_args()

    opening_moves = _parse_opening_arg(args.opening)

    book_only = [s.strip() for s in args.book_only.split(",")] if args.book_only else None

    run_batch(
        num_games=args.games, depth=args.depth,
        temperature_plies=args.temperature_plies, temperature=args.temperature,
        out_dir=args.out_dir, opening_moves=opening_moves, opening_tag=args.opening_tag,
        seed=args.seed, use_book=not args.no_book, book_only=book_only,
    )


if __name__ == "__main__":
    main()
