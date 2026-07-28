"""
db.py
=====
Arkatana（古战棋）— 数据持久化模块

职责范围：
    1. 定义数据库表结构：
       - games 表：对局记录
       - users 表：账号信息（用户名、加密后的密码）
    2. 提供保存/读取对局的函数：
       - init_db()          建表（第一次运行时调用）
       - save_game(...)     把一局棋的当前状态存入数据库（新建或更新）
       - load_game(...)     从数据库读取一局棋，并"回放"重建成一个完整的 Game 对象
       - list_recent_games  列出最近的对局摘要（供以后"对局历史"页面使用）
    3. 提供账号相关函数：
       - create_user(...)        注册新用户（用户名重复则返回 None）
       - verify_user_login(...)  校验用户名+密码是否匹配
       - get_user_by_username    按用户名查询用户

设计说明：
    - 对局部分：目前只存"当前状态"，不是逐步存每一步棋的独立记录；
      具体做法是把整局走子记录（notation.format_game 生成的文本）
      和当前局面（notation.board_to_position_string 生成的文本）存成两个文本字段。
      load_game() 读取时是从头"回放"整局走子记录重建 Game 对象，
      而不是直接反序列化局面。
    - 账号部分：密码不会明文存储。使用 PBKDF2-HMAC-SHA256 加盐哈希
      （Python 标准库自带，不需要额外安装 bcrypt/passlib 之类的包），
      存储格式为 "盐值$哈希值"，校验时用同样的盐值重新计算一遍哈希做比较。
    - black_player / white_player 两个字段目前还没跟 users 表关联，
      下一步接入登录状态后，会在创建/加入对局时把当前登录用户名填进去。

依赖：game.py（Game）、notation.py（记谱与局面序列化）
被依赖：未来的 api.py（在每次走棋/悔棋/认输后调用 save_game 落盘；
        注册/登录接口调用账号相关函数）
"""

from __future__ import annotations
import hashlib
import secrets
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import create_engine, Column, Integer, String, DateTime, Text, select, or_, and_
from sqlalchemy.orm import sessionmaker, DeclarativeBase

from game import Game
from notation import format_game, board_to_position_string, parse_move_notation


# ---------------------------------------------------------------------------
# 数据库连接（默认使用项目目录下的 arkatana.db 文件）
# ---------------------------------------------------------------------------

DB_PATH = "arkatana.db"


def _build_engine(db_path: str):
    return create_engine(f"sqlite:///{db_path}", echo=False)


engine = _build_engine(DB_PATH)
SessionLocal = sessionmaker(bind=engine)


# ---------------------------------------------------------------------------
# 表结构定义
# ---------------------------------------------------------------------------

class Base(DeclarativeBase):
    pass


class GameRecord(Base):
    __tablename__ = "games"

    id = Column(Integer, primary_key=True, autoincrement=True)
    game_id = Column(String, unique=True, nullable=False, index=True)

    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    result = Column(String, nullable=False, default="ongoing")
    current_side = Column(String, nullable=False, default="black")
    move_count = Column(Integer, default=0)

    move_log_text = Column(Text, default="")     # format_game() 输出，整局记谱文本
    position_text = Column(Text, nullable=True)  # board_to_position_string() 输出，当前局面

    # 预留字段：账号系统接入后，这里存对应用户的标识
    black_player = Column(String, nullable=True)
    white_player = Column(String, nullable=True)


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String, unique=True, nullable=False, index=True)
    password_hash = Column(String, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


# ---------------------------------------------------------------------------
# 建表
# ---------------------------------------------------------------------------

def init_db(target_engine=None) -> None:
    """建表（如果表已存在则不会重复创建）"""
    Base.metadata.create_all(target_engine or engine)


# ---------------------------------------------------------------------------
# 保存对局
# ---------------------------------------------------------------------------

def save_game(
    game_id: str,
    game: Game,
    black_player: Optional[str] = None,
    white_player: Optional[str] = None,
    session_factory=None,
) -> None:
    """
    把一局棋的当前状态存入数据库；已存在则更新，不存在则新建。
    black_player / white_player 是可选的：
    - 新建时：直接采用传入的值（可以是 None，代表暂时还没人认领这一方）
    - 更新时：只有传入非 None 的值才会覆盖已有记录，传 None 表示"不改动这个字段"
      （这样日常走棋/悔棋调用不需要每次都带上玩家信息，也不会不小心把已绑定的玩家清空）
    """
    factory = session_factory or SessionLocal
    move_log_text = format_game(game.move_log)
    position_text = board_to_position_string(game.board, game.current_side)

    with factory() as session:
        record = session.execute(
            select(GameRecord).where(GameRecord.game_id == game_id)
        ).scalar_one_or_none()

        if record is None:
            record = GameRecord(
                game_id=game_id,
                result=game.result.value,
                current_side=game.current_side.value,
                move_count=len(game.move_log),
                move_log_text=move_log_text,
                position_text=position_text,
                black_player=black_player,
                white_player=white_player,
            )
            session.add(record)
        else:
            record.result = game.result.value
            record.current_side = game.current_side.value
            record.move_count = len(game.move_log)
            record.move_log_text = move_log_text
            record.position_text = position_text
            record.updated_at = datetime.now(timezone.utc)
            if black_player is not None:
                record.black_player = black_player
            if white_player is not None:
                record.white_player = white_player

        session.commit()


def get_players(game_id: str, session_factory=None) -> tuple[Optional[str], Optional[str]]:
    """查询某局对局的黑白双方玩家用户名；对局不存在则返回 (None, None)"""
    factory = session_factory or SessionLocal
    with factory() as session:
        record = session.execute(
            select(GameRecord).where(GameRecord.game_id == game_id)
        ).scalar_one_or_none()
    if record is None:
        return None, None
    return record.black_player, record.white_player


# ---------------------------------------------------------------------------
# 读取 / 重建对局
# ---------------------------------------------------------------------------

def load_game(game_id: str, session_factory=None) -> Optional[Game]:
    """
    从数据库读取一局棋，并回放整局走子记录，重建出一个完整的 Game 对象。
    找不到对应记录则返回 None。
    """
    factory = session_factory or SessionLocal
    with factory() as session:
        record = session.execute(
            select(GameRecord).where(GameRecord.game_id == game_id)
        ).scalar_one_or_none()

    if record is None:
        return None

    game = Game()
    for line in record.move_log_text.splitlines():
        if "." not in line:
            continue
        _, moves_part = line.split(".", 1)
        for token in moves_part.split():
            from_sq, to_sq = parse_move_notation(token, game.board, game.current_side)
            game.make_move(from_sq, to_sq)

    return game


def replay_steps(game_id: str, session_factory=None) -> list[dict]:
    """
    逐步回放整局棋谱，返回每一步棋之后的"记谱文本 + 局面记谱字符串"列表，
    供复盘功能使用（前端可以据此一步步展示棋盘是怎么走到当前状态的）。
    对局不存在或者还没有任何走子记录，返回空列表。
    """
    factory = session_factory or SessionLocal
    with factory() as session:
        record = session.execute(
            select(GameRecord).where(GameRecord.game_id == game_id)
        ).scalar_one_or_none()

    if record is None:
        return []

    game = Game()
    steps: list[dict] = []
    move_number = 0

    for line in record.move_log_text.splitlines():
        if "." not in line:
            continue
        _, moves_part = line.split(".", 1)
        for token in moves_part.split():
            side_before = game.current_side
            from_sq, to_sq = parse_move_notation(token, game.board, game.current_side)
            game.make_move(from_sq, to_sq)
            move_number += 1
            steps.append({
                "move_number": move_number,
                "side": side_before.value,
                "notation": token,
                "position": board_to_position_string(game.board, game.current_side),
            })

    return steps


def game_exists(game_id: str, session_factory=None) -> bool:
    """查询数据库中是否存在某个对局ID的记录"""
    factory = session_factory or SessionLocal
    with factory() as session:
        record = session.execute(
            select(GameRecord).where(GameRecord.game_id == game_id)
        ).scalar_one_or_none()
    return record is not None


def list_recent_games(limit: int = 20, session_factory=None) -> list[dict]:
    """列出最近更新过的对局摘要（供以后"对局历史"页面使用）"""
    factory = session_factory or SessionLocal
    with factory() as session:
        records = session.execute(
            select(GameRecord).order_by(GameRecord.updated_at.desc()).limit(limit)
        ).scalars().all()

        return [
            {
                "game_id": r.game_id,
                "result": r.result,
                "current_side": r.current_side,
                "move_count": r.move_count,
                "black_player": r.black_player,
                "white_player": r.white_player,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "updated_at": r.updated_at.isoformat() if r.updated_at else None,
            }
            for r in records
        ]


def list_open_rooms(limit: int = 20, session_factory=None) -> list[dict]:
    """
    列出"大厅"里还在等待对手的开放房间：
    对局仍在进行中（result == 'ongoing'），且黑白双方恰好只有一方绑定了账号
    （另一方还空着，等别人加入）。两方都没绑定账号的纯匿名对局不算"房间"，
    不会出现在大厅列表里。
    """
    factory = session_factory or SessionLocal
    with factory() as session:
        records = session.execute(
            select(GameRecord)
            .where(
                GameRecord.result == "ongoing",
                or_(
                    and_(GameRecord.black_player.isnot(None), GameRecord.white_player.is_(None)),
                    and_(GameRecord.black_player.is_(None), GameRecord.white_player.isnot(None)),
                ),
            )
            .order_by(GameRecord.created_at.desc())
            .limit(limit)
        ).scalars().all()

        return [
            {
                "game_id": r.game_id,
                "black_player": r.black_player,
                "white_player": r.white_player,
                "open_side": "white" if r.black_player is not None else "black",
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in records
        ]


# ---------------------------------------------------------------------------
# 密码哈希工具（PBKDF2-HMAC-SHA256 加盐哈希，Python 标准库自带）
# ---------------------------------------------------------------------------

_HASH_ITERATIONS = 200_000


def _hash_password(password: str, salt: Optional[str] = None) -> str:
    """
    对密码进行加盐哈希，返回 "盐值$哈希值" 格式的字符串。
    如果没有提供 salt，会随机生成一个新的（用于注册时）；
    如果提供了 salt，会用这个盐值重新计算（用于登录校验时）。
    """
    if salt is None:
        salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("utf-8"), _HASH_ITERATIONS
    )
    return f"{salt}${digest.hex()}"


def _verify_password(password: str, stored_hash: str) -> bool:
    """用同样的盐值重新计算一遍哈希，跟存储的哈希值做安全比较"""
    salt = stored_hash.split("$", 1)[0]
    recomputed = _hash_password(password, salt)
    return secrets.compare_digest(recomputed, stored_hash)


# ---------------------------------------------------------------------------
# 账号相关函数
# ---------------------------------------------------------------------------

def create_user(username: str, password: str, session_factory=None) -> Optional[User]:
    """
    注册新用户。用户名已存在则返回 None（注册失败），
    成功则返回新建的 User 对象。
    """
    factory = session_factory or SessionLocal
    with factory() as session:
        existing = session.execute(
            select(User).where(User.username == username)
        ).scalar_one_or_none()
        if existing is not None:
            return None

        user = User(username=username, password_hash=_hash_password(password))
        session.add(user)
        session.commit()
        session.refresh(user)
        return user


def verify_user_login(username: str, password: str, session_factory=None) -> bool:
    """校验用户名+密码是否匹配；用户不存在也返回 False（不额外区分，避免泄露用户名是否存在）"""
    factory = session_factory or SessionLocal
    with factory() as session:
        user = session.execute(
            select(User).where(User.username == username)
        ).scalar_one_or_none()
        if user is None:
            return False
        return _verify_password(password, user.password_hash)


def get_user_by_username(username: str, session_factory=None) -> Optional[User]:
    """按用户名查询用户；不存在返回 None"""
    factory = session_factory or SessionLocal
    with factory() as session:
        return session.execute(
            select(User).where(User.username == username)
        ).scalar_one_or_none()


# ---------------------------------------------------------------------------
# 简单自检（使用独立的临时数据库文件，不污染正式的 arkatana.db）
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import os

    TEST_DB_PATH = "test_arkatana.db"
    if os.path.exists(TEST_DB_PATH):
        os.remove(TEST_DB_PATH)

    test_engine = _build_engine(TEST_DB_PATH)
    test_session_factory = sessionmaker(bind=test_engine)
    init_db(test_engine)

    # 1) 新建一局棋，走几步，存进数据库
    game = Game()
    game.make_move_str("d5", "d7")   # 冲两步升变
    game.make_move_str("a8", "a7")   # 白兵应一手（避开d列，那里刚被黑方升变兵占住）

    save_game("test001", game, session_factory=test_session_factory)
    assert game_exists("test001", session_factory=test_session_factory) is True
    assert game_exists("no_such_id", session_factory=test_session_factory) is False

    # 2) 读取并重建，应该和原对局状态完全一致
    restored = load_game("test001", session_factory=test_session_factory)
    assert restored is not None
    assert restored.current_side == game.current_side
    assert restored.result == game.result
    assert len(restored.move_log) == len(game.move_log)

    from board import parse_coord
    original_piece = game.board.get(parse_coord("d7"))
    restored_piece = restored.board.get(parse_coord("d7"))
    assert type(original_piece) is type(restored_piece)
    assert original_piece.promoted == restored_piece.promoted is True

    # 3) 再走一步、更新存档，确认是"更新"而不是"新建重复记录"
    game.make_move_str("e5", "e6")  # 黑方兵再走一手（示例走法）
    save_game("test001", game, session_factory=test_session_factory)
    with test_session_factory() as session:
        count = session.execute(
            select(GameRecord).where(GameRecord.game_id == "test001")
        ).scalars().all()
        assert len(count) == 1, "同一个 game_id 存两次应该是更新，不是新增一行"

    # 4) 列表功能
    game2 = Game()
    save_game("test002", game2, session_factory=test_session_factory)
    recent = list_recent_games(limit=10, session_factory=test_session_factory)
    ids = {r["game_id"] for r in recent}
    assert {"test001", "test002"} <= ids

    # 5) 账号系统：注册、重复用户名、登录校验
    user = create_user("farkas", "correct-horse-battery", session_factory=test_session_factory)
    assert user is not None
    assert user.username == "farkas"
    assert user.password_hash != "correct-horse-battery"  # 确认没有明文存储

    duplicate = create_user("farkas", "another-password", session_factory=test_session_factory)
    assert duplicate is None, "用户名重复应该注册失败"

    assert verify_user_login("farkas", "correct-horse-battery", session_factory=test_session_factory) is True
    assert verify_user_login("farkas", "wrong-password", session_factory=test_session_factory) is False
    assert verify_user_login("no_such_user", "whatever", session_factory=test_session_factory) is False

    fetched = get_user_by_username("farkas", session_factory=test_session_factory)
    assert fetched is not None and fetched.username == "farkas"
    assert get_user_by_username("no_such_user", session_factory=test_session_factory) is None

    # 6) 用户名区分大小写（"Farkas" 和 "farkas" 是两个独立账号）
    other_user = create_user("Farkas", "another-password-456", session_factory=test_session_factory)
    assert other_user is not None, "不同大小写应该视为不同的用户名，允许注册"
    assert verify_user_login("Farkas", "another-password-456", session_factory=test_session_factory) is True
    assert verify_user_login("farkas", "another-password-456", session_factory=test_session_factory) is False, \
        "大小写不同应该是完全独立的账号，密码不能混用"

    # 7) 对局绑定玩家：save_game 的 black_player/white_player 参数
    game3 = Game()
    save_game("test003", game3, black_player="farkas", session_factory=test_session_factory)
    black_p, white_p = get_players("test003", session_factory=test_session_factory)
    assert black_p == "farkas" and white_p is None

    # 日常保存（不传玩家参数）不应该把已绑定的玩家清空
    game3.make_move_str("d5", "d7")
    save_game("test003", game3, session_factory=test_session_factory)
    black_p2, white_p2 = get_players("test003", session_factory=test_session_factory)
    assert black_p2 == "farkas", "不传玩家参数时，已绑定的玩家不应该被清空"

    # 之后指定 white_player，应该只更新 white，不影响已有的 black
    save_game("test003", game3, white_player="someone_else", session_factory=test_session_factory)
    black_p3, white_p3 = get_players("test003", session_factory=test_session_factory)
    assert black_p3 == "farkas" and white_p3 == "someone_else"

    # 8) 大厅开放房间查询
    game4 = Game()
    save_game("room_black_open", game4, black_player="farkas", white_player=None, session_factory=test_session_factory)
    game5 = Game()
    save_game("room_white_open", game5, black_player=None, white_player="Farkas", session_factory=test_session_factory)
    game6 = Game()
    save_game("room_full", game6, black_player="farkas", white_player="Farkas", session_factory=test_session_factory)
    game7 = Game()
    save_game("room_anonymous", game7, session_factory=test_session_factory)  # 双方都没绑定账号

    rooms = list_open_rooms(limit=50, session_factory=test_session_factory)
    room_ids = {r["game_id"] for r in rooms}
    assert "room_black_open" in room_ids
    assert "room_white_open" in room_ids
    assert "room_full" not in room_ids, "双方都已绑定的对局不应该出现在大厅里"
    assert "room_anonymous" not in room_ids, "纯匿名对局不应该出现在大厅里"

    black_open_entry = next(r for r in rooms if r["game_id"] == "room_black_open")
    assert black_open_entry["open_side"] == "white"
    white_open_entry = next(r for r in rooms if r["game_id"] == "room_white_open")
    assert white_open_entry["open_side"] == "black"

    print("db.py 自检全部通过 ✅")

    test_engine.dispose()  # 释放连接池，避免 Windows 下文件被占用无法删除
    os.remove(TEST_DB_PATH)
