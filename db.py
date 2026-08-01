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
import os
import secrets
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import create_engine, Column, Integer, String, DateTime, Text, Float, Boolean, select, or_
from sqlalchemy.orm import sessionmaker, DeclarativeBase

from game import Game
from notation import format_game, board_to_position_string, parse_move_notation
import rating as rating_module
import clock as clock_module

# ---------------------------------------------------------------------------
# 数据库连接
# ---------------------------------------------------------------------------
# 优先使用环境变量 DATABASE_URL（部署上线后指向 PostgreSQL，比如 Neon 提供的连接串）；
# 本地开发没有设置这个环境变量时，自动退回本地 SQLite 文件，行为跟以前完全一样。

DATABASE_URL = os.environ.get("DATABASE_URL")
DB_PATH = os.environ.get("DATABASE_PATH", "arkatana.db")


def _build_engine(sqlite_path: str | None = None):
    """
    构建数据库引擎：
    - 如果环境变量 DATABASE_URL 存在（生产环境接了 PostgreSQL），优先使用它。
      注意 Render/Neon 给出的连接串常以 "postgres://" 开头，
      但 SQLAlchemy 2.0 要求写成 "postgresql://"，这里做了自动兼容处理。
    - 否则退回本地 SQLite 文件（sqlite_path 参数，主要供自检测试传入独立的临时文件，
      不传则用默认的 DB_PATH）。
    """
    if DATABASE_URL:
        url = DATABASE_URL
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql://", 1)
        return create_engine(url, echo=False)
    return create_engine(f"sqlite:///{sqlite_path or DB_PATH}", echo=False)


engine = _build_engine()
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

    # 时间控制：为空表示无时间限制（比如"线下对练"模式）
    time_control_minutes = Column(Integer, nullable=True)
    time_control_increment = Column(Integer, nullable=True)
    black_time_remaining = Column(Float, nullable=True)
    white_time_remaining = Column(Float, nullable=True)

    # 是否是排位对局；rating_applied 防止评分结算被重复触发
    rated = Column(Boolean, default=False, nullable=False)
    rating_applied = Column(Boolean, default=False, nullable=False)


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String, unique=True, nullable=False, index=True)
    password_hash = Column(String, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    rating = Column(Integer, default=rating_module.INITIAL_RATING, nullable=False)
    rated_games_played = Column(Integer, default=0, nullable=False)  # 纯统计展示用，不参与保护期判定
    provisional_progress = Column(Float, default=0.0, nullable=False)  # 保护期"已消耗的波动额度"


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
    rated: Optional[bool] = None,
    session_factory=None,
) -> None:
    """
    把一局棋的当前状态存入数据库；已存在则更新，不存在则新建。
    black_player / white_player / rated 是"可选覆盖"参数：
    - 新建时：直接采用传入的值（可以是 None，代表暂时还没人认领这一方 / 还没确定rated）
    - 更新时：只有传入非 None 的值才会覆盖已有记录，传 None 表示"不改动这个字段"
      （这样日常走棋/悔棋调用不需要每次都带上这些信息，也不会不小心把已有设定清空）

    时间控制和棋钟剩余时间则每次都直接从 game.clock 读取覆盖——这两项
    在对局过程中会自然变化（剩余时间），或者创建时就已经固定不会再变
    （时间控制本身），不需要"保留旧值"的逻辑。
    """
    factory = session_factory or SessionLocal
    move_log_text = format_game(game.move_log)
    position_text = board_to_position_string(game.board, game.current_side)

    tc = game.clock.time_control
    tc_minutes = tc.minutes_per_side if tc is not None else None
    tc_increment = tc.increment_seconds if tc is not None else None
    black_time = game.clock.time_left("black")
    white_time = game.clock.time_left("white")

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
                rated=bool(rated),
                time_control_minutes=tc_minutes,
                time_control_increment=tc_increment,
                black_time_remaining=black_time,
                white_time_remaining=white_time,
            )
            session.add(record)
        else:
            record.result = game.result.value
            record.current_side = game.current_side.value
            record.move_count = len(game.move_log)
            record.move_log_text = move_log_text
            record.position_text = position_text
            record.updated_at = datetime.now(timezone.utc)
            record.time_control_minutes = tc_minutes
            record.time_control_increment = tc_increment
            record.black_time_remaining = black_time
            record.white_time_remaining = white_time
            if black_player is not None:
                record.black_player = black_player
            if white_player is not None:
                record.white_player = white_player
            if rated is not None:
                record.rated = rated

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


def is_rated(game_id: str, session_factory=None) -> bool:
    """查询某局对局是否是 rated 对局；对局不存在则返回 False"""
    factory = session_factory or SessionLocal
    with factory() as session:
        record = session.execute(
            select(GameRecord).where(GameRecord.game_id == game_id)
        ).scalar_one_or_none()
    return bool(record.rated) if record is not None else False


# ---------------------------------------------------------------------------
# 读取 / 重建对局
# ---------------------------------------------------------------------------

def load_game(game_id: str, session_factory=None) -> Optional[Game]:
    """
    从数据库读取一局棋，并回放整局走子记录，重建出一个完整的 Game 对象。
    找不到对应记录则返回 None。

    棋钟处理需要特别说明：回放走子记录的过程是"瞬间"完成的（不是真的等了
    那么久），如果直接让 Game 在回放期间正常计时，会把回放本身的耗时
    （几乎为0）错误地当成"这步棋思考了多久"，导致剩余时间完全不对。
    所以这里的做法是：回放阶段用一个"无时间限制"的棋钟（不受影响），
    回放完成后再用数据库里持久化的真实剩余时间手动重建棋钟状态，
    并从"现在"这一刻开始重新计时。
    """
    factory = session_factory or SessionLocal
    with factory() as session:
        record = session.execute(
            select(GameRecord).where(GameRecord.game_id == game_id)
        ).scalar_one_or_none()

    if record is None:
        return None

    game = Game()  # 回放阶段用默认的无时间限制棋钟
    for line in record.move_log_text.splitlines():
        if "." not in line:
            continue
        _, moves_part = line.split(".", 1)
        for token in moves_part.split():
            from_sq, to_sq = parse_move_notation(token, game.board, game.current_side)
            game.make_move(from_sq, to_sq)

    if record.time_control_minutes is not None:
        tc = clock_module.TimeControl(record.time_control_minutes, record.time_control_increment)
        restored_clock = clock_module.Clock(tc)
        restored_clock.remaining["black"] = (
            record.black_time_remaining if record.black_time_remaining is not None else tc.initial_seconds
        )
        restored_clock.remaining["white"] = (
            record.white_time_remaining if record.white_time_remaining is not None else tc.initial_seconds
        )
        game.clock = restored_clock
        if game.result.value == "ongoing":
            game.clock.start_turn(game.current_side.value)  # 从"现在"这一刻重新开始计时

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
    """列出最近更新过的对局摘要（内部/管理用途；网页首页的"对局历史"用的是 list_user_games）"""
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
                "rated": r.rated,
                "time_control_minutes": r.time_control_minutes,
                "time_control_increment": r.time_control_increment,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "updated_at": r.updated_at.isoformat() if r.updated_at else None,
            }
            for r in records
        ]


def list_user_games(username: str, limit: int = 20, session_factory=None) -> list[dict]:
    """
    列出某个用户自己参与过的对局（不管黑方还是白方），按最近更新排序。
    用于网页首页"Game History"——只显示"我自己下过的棋"，
    不是全站对局流水账（那是以后 database 功能的事）。
    """
    factory = session_factory or SessionLocal
    with factory() as session:
        records = session.execute(
            select(GameRecord)
            .where(or_(GameRecord.black_player == username, GameRecord.white_player == username))
            .order_by(GameRecord.updated_at.desc())
            .limit(limit)
        ).scalars().all()

        return [
            {
                "game_id": r.game_id,
                "result": r.result,
                "current_side": r.current_side,
                "move_count": r.move_count,
                "black_player": r.black_player,
                "white_player": r.white_player,
                "rated": r.rated,
                "time_control_minutes": r.time_control_minutes,
                "time_control_increment": r.time_control_increment,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "updated_at": r.updated_at.isoformat() if r.updated_at else None,
            }
            for r in records
        ]


def list_open_rooms(limit: int = 20, session_factory=None) -> list[dict]:
    """
    列出"大厅"里还在等待开始的房间：对局仍在进行中（result == 'ongoing'）、
    还没有任何人走过一步棋（move_count == 0）、而且不是"黑白双方都已经被
    账号认领"的满员状态。

    这个判定标准同时覆盖两种情况：
    - 匿名创建的房间（黑白双方都没绑定账号）：只要还没人走棋，就一直在大厅里等
    - 账号绑定的房间（一方已认领，等另一方 /join）：同上，且认领方信息用于显示
    一旦有人走了第一步棋，或者账号房间的黑白双方都凑齐了，就从大厅消失。
    """
    factory = session_factory or SessionLocal
    with factory() as session:
        records = session.execute(
            select(GameRecord)
            .where(
                GameRecord.result == "ongoing",
                GameRecord.move_count == 0,
                or_(GameRecord.black_player.is_(None), GameRecord.white_player.is_(None)),
            )
            .order_by(GameRecord.created_at.desc())
            .limit(limit)
        ).scalars().all()

        def _open_side(r):
            if r.black_player is None and r.white_player is None:
                return "either"  # 纯匿名房间，谁都能加入任意一方
            return "white" if r.black_player is not None else "black"

        return [
            {
                "game_id": r.game_id,
                "black_player": r.black_player,
                "white_player": r.white_player,
                "open_side": _open_side(r),
                "rated": r.rated,
                "time_control_minutes": r.time_control_minutes,
                "time_control_increment": r.time_control_increment,
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
# 评分相关函数
# ---------------------------------------------------------------------------

def get_rating(username: str, session_factory=None) -> tuple[int, int]:
    """
    查询某用户当前的评分和已下过的 rated 对局数（纯统计展示用）。
    用户不存在则返回默认初始值（不会报错，方便调用方少写一层判断）。
    """
    factory = session_factory or SessionLocal
    with factory() as session:
        user = session.execute(
            select(User).where(User.username == username)
        ).scalar_one_or_none()
        if user is None:
            return rating_module.INITIAL_RATING, 0
        return user.rating, user.rated_games_played


def apply_rated_game_result(
    black_username: str,
    white_username: str,
    winner: str,
    minutes_per_side: int,
    session_factory=None,
) -> tuple[int, int]:
    """
    一局 rated 对局结束后调用：读取双方当前评分和保护期进度，计算新评分，写回数据库，
    并把各自的 rated_games_played（纯统计用）加一。
    返回 (黑方新评分, 白方新评分)。
    只处理注册用户之间的对局——如果任意一方是匿名（用户名为 None 或找不到），
    这局不会产生评分变化，调用方应该在调用前自行判断是否两边都已登录。
    """
    factory = session_factory or SessionLocal
    with factory() as session:
        black_user = session.execute(
            select(User).where(User.username == black_username)
        ).scalar_one_or_none()
        white_user = session.execute(
            select(User).where(User.username == white_username)
        ).scalar_one_or_none()

        if black_user is None or white_user is None:
            raise ValueError("双方必须都是已注册用户才能计算评分")

        new_black, new_white, new_black_progress, new_white_progress = rating_module.apply_game_result(
            black_rating=black_user.rating,
            white_rating=white_user.rating,
            black_progress=black_user.provisional_progress,
            white_progress=white_user.provisional_progress,
            winner=winner,
            minutes_per_side=minutes_per_side,
        )

        black_user.rating = new_black
        black_user.provisional_progress = new_black_progress
        black_user.rated_games_played += 1
        white_user.rating = new_white
        white_user.provisional_progress = new_white_progress
        white_user.rated_games_played += 1

        session.commit()
        return new_black, new_white


def finalize_rated_game(
    game_id: str,
    winner: str,
    session_factory=None,
) -> Optional[tuple[int, int]]:
    """
    对局分出胜负后调用：如果这局是 rated、双方都是已注册用户、
    时间控制不是"无限制"、而且还没结算过评分，就计算并写入评分变化。

    带防重复触发保护（rating_applied 标记）——因为"惰性检测"机制下，
    同一局对局结束这件事可能会被多个不同的接口调用顺带触发好几次，
    这个函数保证评分变化只会真正生效一次。

    返回 (黑方新评分, 白方新评分)；如果这局不满足结算条件（不是rated、
    有一方匿名、无时间限制、或者已经结算过），返回 None。
    """
    factory = session_factory or SessionLocal
    with factory() as session:
        record = session.execute(
            select(GameRecord).where(GameRecord.game_id == game_id)
        ).scalar_one_or_none()

        if record is None or not record.rated or record.rating_applied:
            return None
        if record.black_player is None or record.white_player is None:
            return None
        if record.time_control_minutes is None:
            return None  # 无时间限制的对局不参与评分结算

        black_username = record.black_player
        white_username = record.white_player
        minutes_per_side = record.time_control_minutes

        record.rating_applied = True
        session.commit()

    return apply_rated_game_result(
        black_username, white_username, winner, minutes_per_side,
        session_factory=session_factory,
    )


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
    game.make_move_str("d5", "d7")   # 冲两步，新规则下这一步尚未升变（d8才是黑兵升变排）
    game.make_move_str("a8", "a7")   # 白兵应一手（避开d列，那里刚被黑方兵占住）
    game.make_move_str("d7", "d8")   # 再走一步，到d8触发升变

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
    original_piece = game.board.get(parse_coord("d8"))
    restored_piece = restored.board.get(parse_coord("d8"))
    assert type(original_piece) is type(restored_piece)
    assert original_piece.promoted == restored_piece.promoted is True

    # 3) 再走一步、更新存档，确认是"更新"而不是"新建重复记录"
    game.make_move_str("k8", "k7")  # 白方随便应一手（示例走法）
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
    save_game("room_anonymous", game7, session_factory=test_session_factory)  # 双方都没绑定账号，还没人走棋

    rooms = list_open_rooms(limit=50, session_factory=test_session_factory)
    room_ids = {r["game_id"] for r in rooms}
    assert "room_black_open" in room_ids
    assert "room_white_open" in room_ids
    assert "room_full" not in room_ids, "双方都已绑定账号的房间应该已经算满员，不该再出现在大厅"
    assert "room_anonymous" in room_ids, "匿名房间只要还没人走棋，也应该出现在大厅里等待加入"

    black_open_entry = next(r for r in rooms if r["game_id"] == "room_black_open")
    assert black_open_entry["open_side"] == "white"
    white_open_entry = next(r for r in rooms if r["game_id"] == "room_white_open")
    assert white_open_entry["open_side"] == "black"
    anon_entry = next(r for r in rooms if r["game_id"] == "room_anonymous")
    assert anon_entry["open_side"] == "either"

    # 一旦走了第一步棋，不管匿名还是账号房间，都应该从大厅消失
    game7.make_move_str("d5", "d6")
    save_game("room_anonymous", game7, session_factory=test_session_factory)
    rooms_after_move = list_open_rooms(limit=50, session_factory=test_session_factory)
    assert "room_anonymous" not in {r["game_id"] for r in rooms_after_move}, \
        "已经开始下棋的房间不应该再出现在大厅"

    # 9) 个人对局历史：只返回这个用户自己参与过的对局
    farkas_games = list_user_games("farkas", session_factory=test_session_factory)
    farkas_game_ids = {g["game_id"] for g in farkas_games}
    assert "room_black_open" in farkas_game_ids
    assert "room_full" in farkas_game_ids
    assert "room_anonymous" not in farkas_game_ids, "farkas没有参与这局匿名对局，不应该出现在他的历史里"
    stranger_games = list_user_games("nobody_registered", session_factory=test_session_factory)
    assert stranger_games == [], "从没下过棋的用户，历史记录应该是空的"

    # 9) 评分系统：新用户初始评分、rated对局后的评分更新
    create_user("player_black", "password123", session_factory=test_session_factory)
    create_user("player_white", "password456", session_factory=test_session_factory)

    initial_rating, initial_games = get_rating("player_black", session_factory=test_session_factory)
    assert initial_rating == rating_module.INITIAL_RATING
    assert initial_games == 0

    new_black, new_white = apply_rated_game_result(
        "player_black", "player_white", winner="black", minutes_per_side=10,
        session_factory=test_session_factory,
    )
    assert new_black == 1032, f"新手保护期黑方胜后评分异常: {new_black}"  # 16*2倍数
    assert new_white == 968, f"新手保护期白方负后评分异常: {new_white}"

    updated_rating, updated_games = get_rating("player_black", session_factory=test_session_factory)
    assert updated_rating == 1032
    assert updated_games == 1, "对局数应该+1"

    # 匿名/未注册用户不应该能计算评分
    try:
        apply_rated_game_result(
            "player_black", "no_such_user", winner="black", minutes_per_side=10,
            session_factory=test_session_factory,
        )
        raise AssertionError("双方有一方未注册时不应该能计算评分")
    except ValueError:
        pass

    # 10) 时间控制的存档与回放：剩余时间应该被正确持久化和还原
    from clock import TimeControl as _TimeControl
    from datetime import datetime as _dt, timedelta as _timedelta, timezone as _tz

    timed_game = Game(_TimeControl(minutes_per_side=10, increment_seconds=5))
    timed_game.start_clocks()
    # 模拟黑方思考了40秒后走棋
    timed_game.clock.turn_started_at = _dt.now(_tz.utc) - _timedelta(seconds=40)
    timed_game.make_move_str("d5", "d6")

    save_game(
        "timed001", timed_game,
        black_player="player_black", white_player="player_white", rated=True,
        session_factory=test_session_factory,
    )

    reloaded = load_game("timed001", session_factory=test_session_factory)
    assert reloaded.clock.time_control is not None
    assert reloaded.clock.time_control.minutes_per_side == 10
    assert reloaded.clock.time_control.increment_seconds == 5
    # 应该用的是持久化的真实剩余时间，而不是回放过程中"瞬间完成"产生的错误值
    expected_black_remaining = timed_game.clock.remaining["black"]
    assert abs(reloaded.clock.remaining["black"] - expected_black_remaining) < 1.0, (
        f"回放重建的剩余时间应接近存档时的真实值，实际: {reloaded.clock.remaining['black']}"
    )
    assert reloaded.clock.active_side == "white", "回放完成后应该正确恢复到白方计时"

    # 11) 评分自动结算：finalize_rated_game 的完整流程 + 防重复触发
    black_rating_before, _ = get_rating("player_black", session_factory=test_session_factory)
    white_rating_before, _ = get_rating("player_white", session_factory=test_session_factory)

    result1 = finalize_rated_game("timed001", winner="black", session_factory=test_session_factory)
    assert result1 is not None, "rated + 双方已注册 + 有时间控制，应该能成功结算"

    black_rating_after, _ = get_rating("player_black", session_factory=test_session_factory)
    white_rating_after, _ = get_rating("player_white", session_factory=test_session_factory)
    assert black_rating_after != black_rating_before, "评分应该已经发生变化"
    assert white_rating_after != white_rating_before

    # 再调用一次，不应该重复结算（防重复触发）
    result2 = finalize_rated_game("timed001", winner="black", session_factory=test_session_factory)
    assert result2 is None, "同一局已经结算过，不应该再次生效"

    black_rating_final, _ = get_rating("player_black", session_factory=test_session_factory)
    assert black_rating_final == black_rating_after, "重复调用不应该让评分再次变化"

    # 12) casual（非rated）对局不应该触发评分结算
    casual_game = Game(_TimeControl(minutes_per_side=10, increment_seconds=5))
    save_game(
        "casual001", casual_game,
        black_player="player_black", white_player="player_white", rated=False,
        session_factory=test_session_factory,
    )
    result3 = finalize_rated_game("casual001", winner="black", session_factory=test_session_factory)
    assert result3 is None, "casual对局不应该触发评分结算"

    print("db.py 自检全部通过 ✅")

    test_engine.dispose()  # 释放连接池，避免 Windows 下文件被占用无法删除
    os.remove(TEST_DB_PATH)
