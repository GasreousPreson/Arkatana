"""
main.py
=======
Arkatana（古战棋）— 命令行测试入口

用途：
    在网页前端做出来之前，提供一个最简单的命令行交互界面，
    让你能够手动走棋、查看合法走法、悔棋、认输，
    脱离自检代码，真正"玩"一局，方便发现规则问题。

用法：
    python3 main.py             进入交互模式
    python3 main.py --selftest  跑一次非交互式冒烟测试（供开发时快速验证用）

交互指令：
    d5 d7           走一步棋（起点 终点，中间用空格或 - 隔开都行）
    moves d5        查看 d5 这个棋子当前的合法走法
    board           重新打印当前棋盘
    undo            悔棋
    resign          当前行棋方认输
    help            查看帮助
    quit / exit     退出程序
"""

from __future__ import annotations
import sys

from board import parse_coord
from pieces import Side
from game import Game, MoveRecord, IllegalMoveError, GameOverError


WELCOME_TEXT = """
========================================
   Arkatana《古战棋》 命令行测试台
========================================
黑方（先手）在下方，白方（后手）在上方。
输入 help 查看指令说明。
"""

HELP_TEXT = """
可用指令：
  d5 d7          走一步棋（也支持 d5-d7 这种写法）
  moves d5       查看 d5 这个棋子当前的合法走法
  board          重新打印当前棋盘
  undo           悔棋
  resign         当前行棋方认输
  help           查看本帮助
  quit / exit    退出程序
"""


def format_move(move) -> str:
    """把一个 Move 对象格式化成 'd5-d7' 或 'd5xe6'（吃子用 x）的可读字符串"""
    from board import coord_to_str
    sep = "x" if move.is_capture else "-"
    return f"{coord_to_str(*move.from_sq)}{sep}{coord_to_str(*move.to_sq)}"


def format_record(record: MoveRecord, side_name: str) -> str:
    from board import coord_to_str
    desc = f"{side_name} {record.piece_type} {coord_to_str(*record.from_sq)} → {coord_to_str(*record.to_sq)}"
    if record.captured_type:
        desc += f"，吃掉 {record.captured_type}"
    if record.promoted:
        desc += "，升变！"
    return desc


def side_name(side: Side) -> str:
    return "黑方" if side == Side.BLACK else "白方"


# ---------------------------------------------------------------------------
# 交互主循环
# ---------------------------------------------------------------------------

def main() -> None:
    game = Game()
    print(WELCOME_TEXT)
    print(game)

    while True:
        if game.is_over():
            print(f"\n🏁 对局结束！结果: {game.result.value}")
            again = input("再来一局？(y/n): ").strip().lower()
            if again == "y":
                game = Game()
                print(game)
                continue
            print("再见！")
            break

        prompt = f"\n[{side_name(game.current_side)}] 请输入指令 (help 查看帮助): "
        try:
            raw = input(prompt).strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再见！")
            break

        if not raw:
            continue

        parts = raw.replace("-", " ").split()
        cmd = parts[0].lower()

        if cmd in ("quit", "exit", "q"):
            print("再见！")
            break

        elif cmd == "help":
            print(HELP_TEXT)

        elif cmd == "board":
            print(game)

        elif cmd == "undo":
            try:
                game.undo()
                print("已悔棋。")
                print(game)
            except IllegalMoveError as e:
                print(f"无法悔棋: {e}")

        elif cmd == "resign":
            resigning_side = side_name(game.current_side)
            game.resign(game.current_side)
            print(f"{resigning_side}认输。")

        elif cmd == "moves":
            if len(parts) != 2:
                print("用法: moves <坐标>，例如 moves d5")
                continue
            try:
                coord = parse_coord(parts[1])
            except ValueError as e:
                print(f"坐标错误: {e}")
                continue
            legal = game.legal_moves_from(coord)
            if not legal:
                print("该格没有合法走法（可能没有棋子、不是你的棋子，或者这个棋子被将军钉住了）。")
            else:
                print("合法走法: " + ", ".join(format_move(m) for m in legal))

        else:
            move_args = parts[1:] if cmd == "move" else parts
            if len(move_args) != 2:
                print("无法识别指令，输入 help 查看帮助。")
                continue
            try:
                from_coord = parse_coord(move_args[0])
                to_coord = parse_coord(move_args[1])
            except ValueError as e:
                print(f"坐标错误: {e}")
                continue

            mover = game.current_side
            try:
                record = game.make_move(from_coord, to_coord)
                print(format_record(record, side_name(mover)))
                print(game)
            except IllegalMoveError as e:
                print(f"非法走法: {e}")
            except GameOverError as e:
                print(str(e))


# ---------------------------------------------------------------------------
# 非交互式冒烟测试（python3 main.py --selftest）
# ---------------------------------------------------------------------------

def _smoke_test() -> None:
    game = Game()
    print("[冒烟测试] 初始局面")
    print(game)

    record = game.make_move_str("d5", "d7")
    print(f"[冒烟测试] {format_record(record, '黑方')}")
    assert record.promoted is False, "新规则下d7不是黑兵的升变排，这一步不该升变"
    print(game)

    game.make_move_str("a8", "a7")  # 白方随便应一手，避开 d 线
    record2 = game.make_move_str("d7", "d8")
    print(f"[冒烟测试] {format_record(record2, '黑方')}")
    assert record2.promoted is True, "d8 才是黑兵的升变排"

    game.undo()
    print("[冒烟测试] 悔棋后局面（应还原到 d7→d8 这一步之前）")
    print(game)
    assert game.current_side == Side.BLACK
    restored = game.board.get(parse_coord("d7"))
    assert restored is not None and restored.promoted is False

    legal = game.legal_moves_from(parse_coord("d7"))
    assert len(legal) > 0
    print(f"[冒烟测试] d7 合法走法: {', '.join(format_move(m) for m in legal)}")

    print("\nmain.py 冒烟测试全部通过 ✅")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _smoke_test()
    else:
        main()
