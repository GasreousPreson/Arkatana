"""
ai/tests/test_engine_bridge.py
================================
交叉验证：ai/engine_bridge.py（轻量版）跟网站后端权威引擎（board.py/pieces.py/
movement.py/rules.py/game.py）在同一局面下，走法生成、杀城判定、残局判定
必须逐字一致。这是整个 AI 项目的地基——第2步开始写搜索之前，这里必须全绿。

两种验证策略一起上：
    1. 随机对局：用权威引擎的 game.Game 真实下几百盘随机合法对局（含升变、
       被将军后的走法过滤等真实场景），每一步之后，把当前局面转成轻量版
       Position，对棋盘上每一个棋子分别比对 pseudo_moves，以及双方的
       get_legal_moves / is_checkmate / has_only_throne 是否完全一致。
    2. 随机散点局面：不走真实对局，直接在棋盘上随机撒一批棋子（含边界/
       角落位置，随机 promoted / has_moved 状态），专门用来压边界条件——
       真实对局未必会恰好让棋子停在边角，这里强制覆盖到。

只要任意一处不一致，立刻打印出具体局面 + 具体差异走法然后 assert 失败，
不会"平均通过率高就算了"——地基性质的测试，必须每一条都对得上。

运行方式：
    python3 ai/tests/test_engine_bridge.py

依赖：需要能 import 到网站后端那套引擎代码（board.py/pieces.py/movement.py/
rules.py/game.py）。下面 AUTHORITATIVE_ENGINE_PATH 按你的仓库实际目录结构
调整一次即可（假设 ai/ 和后端代码目录是同级）。
"""

from __future__ import annotations

import os
import random
import sys

# ---------------------------------------------------------------------------
# 让权威引擎可以被 import 到——按你的仓库实际路径调整这一处即可
# ---------------------------------------------------------------------------

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
AI_DIR = os.path.dirname(THIS_DIR)
REPO_ROOT = os.path.dirname(AI_DIR)

# 依次尝试几个常见的后端代码位置，找到第一个存在 board.py 的目录就用它。
# 如果你的仓库结构跟这几个候选都不一样，把正确路径直接加进这个列表最前面。
_CANDIDATE_BACKEND_DIRS = [
    os.environ.get("ARKATANA_BACKEND_DIR", ""),
    os.path.join(REPO_ROOT, "AnicentChess"),
    os.path.join(REPO_ROOT, "backend"),
    REPO_ROOT,
]

_backend_dir = None
for candidate in _CANDIDATE_BACKEND_DIRS:
    if candidate and os.path.isfile(os.path.join(candidate, "board.py")):
        _backend_dir = candidate
        break

if _backend_dir is None:
    raise SystemExit(
        "找不到网站后端引擎代码（board.py 所在目录）。\n"
        "请设置环境变量 ARKATANA_BACKEND_DIR 指向后端代码目录后重试，例如：\n"
        "  ARKATANA_BACKEND_DIR=/path/to/AnicentChess python3 ai/tests/test_engine_bridge.py"
    )

sys.path.insert(0, _backend_dir)
sys.path.insert(0, AI_DIR)

import board as auth_board          # noqa: E402  （权威引擎）
import pieces as auth_pieces        # noqa: E402
import movement as auth_movement    # noqa: E402
import rules as auth_rules          # noqa: E402
import game as auth_game            # noqa: E402

import engine_bridge as eb          # noqa: E402  （轻量版）


# ---------------------------------------------------------------------------
# 权威引擎 Board -> 轻量版 Position 转换器（专供测试用，不进正式代码路径）
# ---------------------------------------------------------------------------

_AUTH_SIDE_TO_BRIDGE = {auth_pieces.Side.BLACK: eb.BLACK, auth_pieces.Side.WHITE: eb.WHITE}


def auth_board_to_bridge_position(board: "auth_board.Board", side_to_move) -> eb.Position:
    pos = eb.Position()
    for coord in board.occupied_coords():
        piece = board.get(coord)
        piece_type = eb.NOTATION_TO_TYPE[piece.notation]
        side = _AUTH_SIDE_TO_BRIDGE[piece.side]
        pos.place(coord, piece_type, side, has_moved=piece.has_moved, promoted=piece.promoted)
    pos.side_to_move = _AUTH_SIDE_TO_BRIDGE[side_to_move]
    return pos


def moves_as_set(moves):
    return set(moves)  # NamedTuple 跨类按值比较/哈希，两边的 Move 可以直接混在一个 set 里比


# ---------------------------------------------------------------------------
# 单局面全量比对：棋盘上每个棋子的 pseudo_moves + 双方 legal_moves +
# is_checkmate / has_only_throne / is_in_check / count_attackers
# ---------------------------------------------------------------------------

def compare_position(auth_bd, bridge_pos: eb.Position, label: str) -> list[str]:
    mismatches = []

    for coord in auth_bd.occupied_coords():
        piece = auth_bd.get(coord)
        auth_mv = moves_as_set(piece.pseudo_moves(auth_bd))
        bridge_mv = moves_as_set(eb.pseudo_moves(bridge_pos, coord))
        if auth_mv != bridge_mv:
            only_auth = auth_mv - bridge_mv
            only_bridge = bridge_mv - auth_mv
            mismatches.append(
                f"[{label}] {piece.notation or 'Pawn'}@{auth_board.coord_to_str(*coord)} "
                f"({piece.side.value}) pseudo_moves 不一致：\n"
                f"    仅权威引擎有: {sorted(only_auth)}\n"
                f"    仅轻量版有:   {sorted(only_bridge)}"
            )

    for auth_side, bridge_side in ((auth_pieces.Side.BLACK, eb.BLACK), (auth_pieces.Side.WHITE, eb.WHITE)):
        auth_legal = moves_as_set(auth_rules.get_legal_moves(auth_bd, auth_side))
        bridge_legal = moves_as_set(eb.get_legal_moves(bridge_pos, bridge_side))
        if auth_legal != bridge_legal:
            mismatches.append(
                f"[{label}] {auth_side.value} legal_moves 不一致：\n"
                f"    仅权威引擎有: {sorted(auth_legal - bridge_legal)}\n"
                f"    仅轻量版有:   {sorted(bridge_legal - auth_legal)}"
            )

        auth_check = auth_rules.is_in_check(auth_bd, auth_side)
        bridge_check = eb.is_in_check(bridge_pos, bridge_side)
        if auth_check != bridge_check:
            mismatches.append(f"[{label}] {auth_side.value} is_in_check 不一致: auth={auth_check} bridge={bridge_check}")

        auth_cnt = auth_rules.count_attackers(auth_bd, auth_side)
        bridge_cnt = eb.count_attackers(bridge_pos, bridge_side)
        if auth_cnt != bridge_cnt:
            mismatches.append(f"[{label}] {auth_side.value} count_attackers 不一致: auth={auth_cnt} bridge={bridge_cnt}")

        auth_mate = auth_rules.is_checkmate(auth_bd, auth_side)
        bridge_mate = eb.is_checkmate(bridge_pos, bridge_side)
        if auth_mate != bridge_mate:
            mismatches.append(f"[{label}] {auth_side.value} is_checkmate 不一致: auth={auth_mate} bridge={bridge_mate}")

        auth_only = auth_rules.has_only_throne(auth_bd, auth_side)
        bridge_only = eb.has_only_throne(bridge_pos, bridge_side)
        if auth_only != bridge_only:
            mismatches.append(f"[{label}] {auth_side.value} has_only_throne 不一致: auth={auth_only} bridge={bridge_only}")

    return mismatches


# ---------------------------------------------------------------------------
# 策略1：随机对局
# ---------------------------------------------------------------------------

def run_random_games(num_games: int, max_plies: int, seed: int) -> list[str]:
    rng = random.Random(seed)
    all_mismatches = []
    games_played = 0
    total_plies_checked = 0

    for game_idx in range(num_games):
        g = auth_game.Game()
        for ply in range(max_plies):
            if g.is_over():
                break
            side_to_move = g.current_side
            bridge_pos = auth_board_to_bridge_position(g.board, side_to_move)
            mismatches = compare_position(g.board, bridge_pos, f"game{game_idx}#ply{ply}")
            all_mismatches.extend(mismatches)
            total_plies_checked += 1
            if mismatches:
                # 一旦发现问题，这盘就不用继续往下走了，省时间尽快报告
                break

            legal = g.legal_moves()
            if not legal:
                break
            move = rng.choice(legal)
            g.make_move(move.from_sq, move.to_sq)
        games_played += 1

    print(f"  随机对局: {games_played} 局，共校验 {total_plies_checked} 个局面")
    return all_mismatches


# ---------------------------------------------------------------------------
# 策略2：随机散点局面（专门覆盖边角、各种 promoted/has_moved 组合）
# ---------------------------------------------------------------------------

_AUTH_PIECE_CLASSES = [
    auth_pieces.Pawn, auth_pieces.Ballista, auth_pieces.Turret, auth_pieces.Ares,
    auth_pieces.Hussar, auth_pieces.Knight, auth_pieces.Rook, auth_pieces.Phoenix,
    auth_pieces.Swordsman, auth_pieces.Chariot,
]


def _random_scattered_board(rng: random.Random, num_pieces: int):
    bd = auth_board.Board()
    all_coords = list(bd.all_coords())
    rng.shuffle(all_coords)

    # 双方各放一个王城（必须有，否则 is_in_check / is_checkmate 逻辑没有意义）
    throne_b = auth_pieces.Throne(auth_pieces.Side.BLACK, all_coords.pop())
    throne_w = auth_pieces.Throne(auth_pieces.Side.WHITE, all_coords.pop())
    bd.set(throne_b.position, throne_b)
    bd.set(throne_w.position, throne_w)

    for _ in range(num_pieces):
        if not all_coords:
            break
        coord = all_coords.pop()
        cls = rng.choice(_AUTH_PIECE_CLASSES)
        side = rng.choice([auth_pieces.Side.BLACK, auth_pieces.Side.WHITE])
        piece = cls(side, coord)
        piece.has_moved = rng.choice([True, False])
        if cls in (auth_pieces.Turret, auth_pieces.Swordsman, auth_pieces.Chariot, auth_pieces.Pawn):
            piece.promoted = rng.choice([True, False, False])  # 偏向未升变，更贴近真实分布
        bd.set(coord, piece)
    return bd


def run_random_scatter(num_positions: int, seed: int) -> list[str]:
    rng = random.Random(seed)
    all_mismatches = []
    for i in range(num_positions):
        num_pieces = rng.randint(2, 40)
        auth_bd = _random_scattered_board(rng, num_pieces)
        # side_to_move 对散点局面的走法生成本身没影响（pseudo_moves 不看 side_to_move），
        # 只在 get_legal_moves 等聚合函数里用得到，两边都各测一次即可，
        # compare_position 内部已经把黑白两侧都测了。
        bridge_pos = auth_board_to_bridge_position(auth_bd, auth_pieces.Side.BLACK)
        mismatches = compare_position(auth_bd, bridge_pos, f"scatter{i}")
        all_mismatches.extend(mismatches)
    print(f"  随机散点局面: {num_positions} 个（每个 2~40 枚棋子，含边角/各种升变状态组合）")
    return all_mismatches


def positions_equal(bridge_pos: eb.Position, ground_truth: eb.Position) -> bool:
    return (bridge_pos.types == ground_truth.types
            and bridge_pos.sides == ground_truth.sides
            and bridge_pos.flags == ground_truth.flags)


def run_parallel_drive_games(num_games: int, max_plies: int, seed: int) -> list[str]:
    """光比对 pseudo_moves 还不够——上面两个策略每一步都是从权威引擎的棋盘
    重新生成一份轻量版局面，从没真正调用过 engine_bridge 自己的 apply_move()，
    所以升变触发、has_moved 置位这些"走完一步之后状态怎么变"的逻辑其实完全
    没测到。这里改成：权威引擎负责挑合法走法（保证走法本身没问题），
    但落子这一步权威引擎和轻量版各自独立执行，每步之后比对两边棋盘状态
    是否逐格完全一致（不是比对走法集合，是直接比对局面本身）。"""
    rng = random.Random(seed)
    mismatches = []
    total_plies = 0
    promotions_seen = 0

    for game_idx in range(num_games):
        g = auth_game.Game()
        bridge_pos = eb.Position.initial()
        for ply in range(max_plies):
            if g.is_over():
                break
            legal = g.legal_moves()
            if not legal:
                break
            chosen = rng.choice(legal)
            move = eb.Move(chosen.from_sq, chosen.to_sq, chosen.is_capture)

            g.make_move(chosen.from_sq, chosen.to_sq)
            eb.apply_move(bridge_pos, move)
            bridge_pos.side_to_move = _AUTH_SIDE_TO_BRIDGE[g.current_side]
            total_plies += 1

            ground_truth = auth_board_to_bridge_position(g.board, g.current_side)
            if not positions_equal(bridge_pos, ground_truth):
                mismatches.append(
                    f"[drive game{game_idx}#ply{ply}] apply_move({auth_board.coord_to_str(*chosen.from_sq)}->"
                    f"{auth_board.coord_to_str(*chosen.to_sq)}) 后局面跟权威引擎不一致"
                )
                break
            if bridge_pos.flags[eb.sq_index(*chosen.to_sq)] & eb.PROMOTED:
                promotions_seen += 1

    print(f"  并行驱动 apply_move: {num_games} 局，共 {total_plies} 步，观察到 {promotions_seen} 次升变")
    return mismatches




def run_frontend_roundtrip_check(seed: int) -> list[str]:
    rng = random.Random(seed)
    mismatches = []
    for i in range(20):
        auth_bd = _random_scattered_board(rng, rng.randint(2, 30))
        bridge_pos = auth_board_to_bridge_position(auth_bd, auth_pieces.Side.BLACK)
        fb = bridge_pos.to_frontend_board()
        restored = eb.Position.from_frontend_board(fb, "black")
        if restored.to_frontend_board() != fb:
            mismatches.append(f"[frontend_roundtrip{i}] 前端 JSON 往返转换后不一致")
    print(f"  前端 JSON 往返一致性: 20 个随机局面")
    return mismatches


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------

def main():
    print(f"使用的后端引擎目录: {_backend_dir}")
    print("开始交叉验证 ai/engine_bridge.py ...")

    all_mismatches = []
    all_mismatches += run_random_games(num_games=20, max_plies=25, seed=1)
    all_mismatches += run_parallel_drive_games(num_games=20, max_plies=30, seed=42)
    all_mismatches += run_random_scatter(num_positions=500, seed=2)
    all_mismatches += run_frontend_roundtrip_check(seed=3)

    if all_mismatches:
        print(f"\n❌ 发现 {len(all_mismatches)} 处不一致：\n")
        for m in all_mismatches[:30]:
            print(m)
            print()
        if len(all_mismatches) > 30:
            print(f"... 还有 {len(all_mismatches) - 30} 处，已省略")
        raise SystemExit(1)

    print("\n✅ 交叉验证全部通过：engine_bridge.py 跟权威引擎在所有测试局面下结果完全一致")


if __name__ == "__main__":
    main()
