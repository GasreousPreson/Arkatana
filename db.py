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
from datetime import datetime, timezone, timedelta
from typing import Optional

from sqlalchemy import create_engine, Column, Integer, String, DateTime, Text, Float, Boolean, UniqueConstraint, select, or_, func
from sqlalchemy.orm import sessionmaker, DeclarativeBase

from game import Game
from board import parse_coord
from notation import format_game, board_to_position_string, parse_move_notation, move_notation
import rating as rating_module
import clock as clock_module

# ---------------------------------------------------------------------------
# 数据库连接
# ---------------------------------------------------------------------------
# 优先使用环境变量 DATABASE_URL（部署上线后指向 PostgreSQL，比如 Neon 提供的连接串）；
# 本地开发没有设置这个环境变量时，自动退回本地 SQLite 文件，行为跟以前完全一样。

DATABASE_URL = os.environ.get("DATABASE_URL")
DB_PATH = os.environ.get("DATABASE_PATH", "arkatana.db")

# Render 会自动给每个服务设一个 RENDER=true 的环境变量，专门就是给代码用来判断
# "我是不是跑在 Render 上"的。
#
# 这里用它做一道安全阀：Render 的免费实例本地磁盘是"临时的"——每次重新部署/
# 容器重建，本地文件就没了。如果 DATABASE_URL 因为任何原因（环境变量被清空、
# 忘记配置、拼错名字……）没有生效，代码会不声不响地退回到本地 SQLite 文件，
# 那份数据在下一次部署就会被冲掉，而且全程不会有任何报错——这正是之前账号
# 数据丢失的真正原因。
#
# 所以现在的规则是：**只要检测到是在 Render 上跑、但没配置 DATABASE_URL，
# 直接拒绝启动**，而不是"能凑合跑就先跑着"。宁可眼前部署失败、你一眼就看出
# 问题所在，也不要让它悄悄用临时存储把数据存丢。
IS_RENDER = os.environ.get("RENDER") == "true"
if IS_RENDER and not DATABASE_URL:
    raise RuntimeError(
        "检测到正运行在 Render 上，但环境变量 DATABASE_URL 没有配置！\n"
        "如果就这样启动，所有数据都会存在 Render 的临时本地磁盘里，\n"
        "下一次部署或重启就会全部丢失（之前的账号丢失就是这个原因）。\n"
        "请先去 Render 服务的 Environment 页面确认 DATABASE_URL 这一项还在、\n"
        "值也对（应该是 Neon 提供的 PostgreSQL 连接串），再重新部署。"
    )


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

# 启动时把"这次到底用的是哪个数据库"直接打进日志，一眼就能看出来，
# 不用等到数据丢了才去猜——Render 的 Logs 页面里应该能看到这一行。
if DATABASE_URL:
    print(f"[db.py] 使用 PostgreSQL（数据会持久保存，不受重启/重新部署影响）")
else:
    print(f"[db.py] ⚠️ 使用本地 SQLite 文件：{DB_PATH}（如果这是在 Render 上看到这条，说明数据不会持久保存，请检查 DATABASE_URL 配置）")


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

    # 棋钟是否已经真正开始走（对局是否已经"正式开始"）。
    # 之所以需要单独存这个字段，而不是靠 black_player/white_player 判断：
    # 匿名对局的"访客认领"完全是内存里的临时状态，从来不会写进
    # black_player/white_player 这两个字段——大厅查询只认数据库里的东西，
    # 所以必须把"是否已经开始"这件事也持久化，大厅才能正确感知到
    # "匿名对局其实已经凑齐两人了，该从大厅消失了"。
    clock_started = Column(Boolean, default=False, nullable=False)

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


class OAuthAccount(Base):
    """
    第三方登录账号关联表：一个 User 可以关联多个第三方账号
    （比如同一个人既用 Google 登录过，又用 QQ 登录过，理论上可以都指向同一个 User，
    不过目前的实现是"每种第三方账号第一次登录时都会新建一个独立 User"，
    账号合并/关联管理是更进一步的功能，这里先只处理"查找/创建"）。
    """
    __tablename__ = "oauth_accounts"
    __table_args__ = (
        UniqueConstraint("provider", "provider_user_id", name="uq_oauth_provider_account"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=False, index=True)
    provider = Column(String, nullable=False)          # "google" / "facebook" / "qq"
    provider_user_id = Column(String, nullable=False)  # 第三方平台给的用户唯一ID
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class OpeningNode(Base):
    """
    开局库节点：整个开局库是**一棵树**，不是一条条互相独立的棋谱。

    每个节点代表"在某个局面下走出的某一步棋"，节点的子节点就是这步棋之后
    所有已经录入过的后续变化。比如"天穹弃兵"往下分出"天琴反弃兵"和
    "格雷夫反弃兵"，在树里就是同一个节点的两个子节点——共享前面的走法，
    不需要把公共部分重复录入两遍。

    - 根节点（parent_id 为 None）代表初始局面，它没有走法，只有局面。
    - name 只挂在"这条线开始有名字"的那个节点上，不需要每步都填。
    - sort_order 决定同一层的兄弟节点在变例列表里的显示顺序，
      数字小的排前面——用来把主流变例放到上面。
    - position_text 是"走完这步之后"的局面，冗余存一份是为了浏览时
      不用每次都从根节点重放一遍，换取查询速度。
    """
    __tablename__ = "opening_nodes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    parent_id = Column(Integer, nullable=True, index=True)  # None 表示根节点（初始局面）

    # 走法信息（根节点全部为空）
    move_notation = Column(String, nullable=True)   # 正式记谱，比如 "Hcf3"
    from_sq = Column(String, nullable=True)
    to_sq = Column(String, nullable=True)
    moved_side = Column(String, nullable=True)      # 走这步的是哪一方："black"/"white"

    position_text = Column(Text, nullable=False)    # 走完这步之后的局面（board_to_position_string）

    name = Column(String, nullable=True)            # 变例名称，可空（比如"天穹弃兵"）
    comment = Column(Text, nullable=True)           # 备注：评估、Σ标记、说明文字等
    sort_order = Column(Integer, default=0, nullable=False)

    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


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
    clock_started = (not game.clock.is_unlimited) and (game.clock.active_side is not None)

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
                clock_started=clock_started,
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
            record.clock_started = clock_started
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


def delete_game(game_id: str, session_factory=None) -> bool:
    """
    彻底删除一局对局记录（用于"取消房间"——一局还没正式开始、
    连一步棋都没走过的对局，取消了就没有保留价值）。
    返回是否真的删除了（对局不存在则返回 False）。
    """
    factory = session_factory or SessionLocal
    with factory() as session:
        record = session.execute(
            select(GameRecord).where(GameRecord.game_id == game_id)
        ).scalar_one_or_none()
        if record is None:
            return False
        session.delete(record)
        session.commit()
        return True


def _game_summary(r: "GameRecord") -> dict:
    """把一条 GameRecord 转成对局摘要字典——列表类接口共用同一份字段。"""
    return {
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


def list_recent_games(limit: int = 20, session_factory=None) -> list[dict]:
    """列出最近更新过的对局摘要（内部/管理用途；网页首页的"对局历史"用的是 list_user_games）"""
    factory = session_factory or SessionLocal
    with factory() as session:
        records = session.execute(
            select(GameRecord).order_by(GameRecord.updated_at.desc()).limit(limit)
        ).scalars().all()
        return [_game_summary(r) for r in records]


def list_user_games(username: str, limit: int = 20, session_factory=None) -> list[dict]:
    """
    列出某个用户自己参与过的对局（不管黑方还是白方），按最近更新排序。
    用于网页首页"Game History"——只显示"我自己下过的棋"，
    不是全站对局流水账（那是 database 功能的事，见 search_games）。
    """
    factory = session_factory or SessionLocal
    with factory() as session:
        records = session.execute(
            select(GameRecord)
            .where(or_(GameRecord.black_player == username, GameRecord.white_player == username))
            .order_by(GameRecord.updated_at.desc())
            .limit(limit)
        ).scalars().all()
        return [_game_summary(r) for r in records]


def search_games(
    player: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = 20,
    offset: int = 0,
    session_factory=None,
) -> dict:
    """
    搜索全站对局库（"database" 功能用这个，不同于 list_user_games 只查自己）。

    - player: 按棋手用户名做子串匹配（不分大小写），黑方或白方命中都算；
      留空则不限定棋手。
    - date_from / date_to: 按对局"最后更新时间"过滤，格式 "YYYY-MM-DD"
      （闭区间：date_from 当天 00:00 到 date_to 当天 24:00 都算在内）；
      留空则不限定这一头。
    - 只收录**已经结束**的对局（result != "ongoing"）——数据库是给人回顾
      "已经发生过的对局"用的，进行中的对局不适合被任意搜索/围观到。
    - 返回 {"games": [...], "total": 匹配总数}，total 用于前端做分页
      （因为 limit/offset 只是这一页，需要知道总共有多少条才能画分页控件）。
    """
    factory = session_factory or SessionLocal
    with factory() as session:
        conditions = [GameRecord.result != "ongoing"]

        if player:
            pattern = f"%{player}%"
            conditions.append(or_(
                GameRecord.black_player.ilike(pattern),
                GameRecord.white_player.ilike(pattern),
            ))

        if date_from:
            start = datetime.fromisoformat(date_from)
            conditions.append(GameRecord.updated_at >= start)

        if date_to:
            # 加一天再用"小于"，让 date_to 当天全天都算在范围内
            end = datetime.fromisoformat(date_to) + timedelta(days=1)
            conditions.append(GameRecord.updated_at < end)

        total = session.execute(
            select(func.count()).select_from(GameRecord).where(*conditions)
        ).scalar_one()

        records = session.execute(
            select(GameRecord)
            .where(*conditions)
            .order_by(GameRecord.updated_at.desc())
            .limit(limit)
            .offset(offset)
        ).scalars().all()

        return {"games": [_game_summary(r) for r in records], "total": total}


def list_open_rooms(limit: int = 20, session_factory=None) -> list[dict]:
    """
    列出"大厅"里还在等待开始的房间：对局仍在进行中（result == 'ongoing'）、
    棋钟还没真正开始走（clock_started == False）、还没有任何人走过一步棋
    （move_count == 0，用于兜底覆盖无时间限制对局——那种对局的
    clock_started 永远是 False，靠 move_count 判断"是否已经开始"），
    而且不是"黑白双方都已经被账号认领"的满员状态。

    这个判定标准同时覆盖两种情况：
    - 匿名创建的房间：访客认领本身不会写进数据库，但"棋钟开始走了"这件事会——
      不管是通过 /join 还是 /start 触发，只要棋钟真的开始走，就说明凑齐两人了，
      从大厅消失。
    - 账号绑定的房间（一方已认领，等另一方 /join）：同上。
    """
    factory = session_factory or SessionLocal
    with factory() as session:
        records = session.execute(
            select(GameRecord)
            .where(
                GameRecord.result == "ongoing",
                GameRecord.move_count == 0,
                GameRecord.clock_started.is_(False),
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
# 第三方登录相关函数
# ---------------------------------------------------------------------------

def _generate_unique_username(base: str, session) -> str:
    """
    根据建议的用户名（比如邮箱前缀）生成一个不重复的用户名：
    如果 base 本身没被占用就直接用；被占用了就依次尝试 base1, base2, base3...
    """
    base = (base or "player").strip() or "player"
    candidate = base
    suffix = 0
    while session.execute(
        select(User).where(User.username == candidate)
    ).scalar_one_or_none() is not None:
        suffix += 1
        candidate = f"{base}{suffix}"
    return candidate


def find_user_by_oauth(provider: str, provider_user_id: str, session_factory=None) -> Optional[User]:
    """查询某个第三方账号是否已经关联过本站用户；没有则返回 None"""
    factory = session_factory or SessionLocal
    with factory() as session:
        link = session.execute(
            select(OAuthAccount).where(
                OAuthAccount.provider == provider,
                OAuthAccount.provider_user_id == provider_user_id,
            )
        ).scalar_one_or_none()
        if link is None:
            return None
        return session.execute(
            select(User).where(User.id == link.user_id)
        ).scalar_one_or_none()


def create_oauth_user(
    provider: str, provider_user_id: str, suggested_username: str, session_factory=None
) -> User:
    """
    为一个第三方账号新建一个本站用户（第一次用这个第三方账号登录时调用）。
    这个用户没有可用的密码——存的是一段随机哈希，任何人都不可能靠猜密码登进去，
    这类账号只能通过对应的第三方登录方式进入。
    """
    factory = session_factory or SessionLocal
    with factory() as session:
        username = _generate_unique_username(suggested_username, session)
        placeholder_password_hash = _hash_password(secrets.token_hex(32))
        user = User(username=username, password_hash=placeholder_password_hash)
        session.add(user)
        session.flush()  # 先拿到 user.id，还没提交事务

        link = OAuthAccount(user_id=user.id, provider=provider, provider_user_id=provider_user_id)
        session.add(link)
        session.commit()
        session.refresh(user)
        return user


def get_or_create_oauth_user(
    provider: str, provider_user_id: str, suggested_username: str, session_factory=None
) -> User:
    """
    第三方登录的统一入口：账号已经关联过就直接返回对应用户，
    没有的话自动新建一个（用户名从 suggested_username 派生，重复了会自动加数字）。
    """
    existing = find_user_by_oauth(provider, provider_user_id, session_factory=session_factory)
    if existing is not None:
        return existing
    return create_oauth_user(provider, provider_user_id, suggested_username, session_factory=session_factory)


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
# 开局库（树状变例结构）
# ---------------------------------------------------------------------------

def _replay_to_node(node_id: Optional[int], session) -> Game:
    """
    从根节点一路重放到指定节点，返回那个局面对应的 Game 对象。
    node_id 为 None 或根节点时，直接返回一局全新的初始对局。

    为什么要重放而不是直接读 position_text：因为要在这个局面上继续走棋
    （校验合法性、生成记谱），需要一个完整可用的 Game 对象，
    而 position_text 只是局面快照，不含走子历史，没法直接拿来续走。
    """
    # 先从目标节点往上收集到根的路径
    chain: list[OpeningNode] = []
    current_id = node_id
    while current_id is not None:
        node = session.execute(
            select(OpeningNode).where(OpeningNode.id == current_id)
        ).scalar_one_or_none()
        if node is None:
            break
        chain.append(node)
        current_id = node.parent_id
    chain.reverse()  # 变成从根到目标的顺序

    game = Game()
    for node in chain:
        if node.from_sq is None or node.to_sq is None:
            continue  # 根节点没有走法，跳过
        game.make_move(parse_coord(node.from_sq), parse_coord(node.to_sq))
    return game


def ensure_opening_root(session_factory=None) -> int:
    """
    确保开局库的根节点（初始局面）存在，返回它的 id。
    根节点是整棵树的入口，第一次调用时自动创建。
    """
    factory = session_factory or SessionLocal
    with factory() as session:
        root = session.execute(
            select(OpeningNode).where(OpeningNode.parent_id.is_(None))
        ).scalars().first()
        if root is not None:
            return root.id

        fresh = Game()
        root = OpeningNode(
            parent_id=None,
            move_notation=None,
            from_sq=None,
            to_sq=None,
            moved_side=None,
            position_text=board_to_position_string(fresh.board, fresh.current_side),
            name="初始局面",
            sort_order=0,
        )
        session.add(root)
        session.commit()
        return root.id


def _node_to_dict(node: OpeningNode) -> dict:
    return {
        "id": node.id,
        "parent_id": node.parent_id,
        "move_notation": node.move_notation,
        "from_sq": node.from_sq,
        "to_sq": node.to_sq,
        "moved_side": node.moved_side,
        "name": node.name,
        "comment": node.comment,
        "sort_order": node.sort_order,
    }


def get_opening_node(node_id: int, session_factory=None) -> Optional[dict]:
    """读取单个开局节点的信息；不存在返回 None"""
    factory = session_factory or SessionLocal
    with factory() as session:
        node = session.execute(
            select(OpeningNode).where(OpeningNode.id == node_id)
        ).scalar_one_or_none()
        return _node_to_dict(node) if node is not None else None


def list_opening_children(node_id: int, session_factory=None) -> list[dict]:
    """
    列出某个节点下已经录入的所有后续走法（也就是变例列表窗口要显示的内容），
    按 sort_order 升序排列，同序时按创建时间排。
    每条会附带 has_children，方便前端显示"这条线下面还有更多变化"。
    """
    factory = session_factory or SessionLocal
    with factory() as session:
        children = session.execute(
            select(OpeningNode)
            .where(OpeningNode.parent_id == node_id)
            .order_by(OpeningNode.sort_order.asc(), OpeningNode.id.asc())
        ).scalars().all()

        result = []
        for child in children:
            grandchild_count = len(session.execute(
                select(OpeningNode.id).where(OpeningNode.parent_id == child.id)
            ).scalars().all())
            entry = _node_to_dict(child)
            entry["has_children"] = grandchild_count > 0
            result.append(entry)
        return result


def get_opening_path(node_id: int, session_factory=None) -> list[dict]:
    """
    返回从根节点到指定节点这一整条路径（不含根节点本身），
    供界面显示"当前走到哪一条线上了"的面包屑，以及左侧的走法序列。
    """
    factory = session_factory or SessionLocal
    with factory() as session:
        chain = []
        current_id = node_id
        while current_id is not None:
            node = session.execute(
                select(OpeningNode).where(OpeningNode.id == current_id)
            ).scalar_one_or_none()
            if node is None:
                break
            if node.parent_id is not None:  # 跳过根节点
                chain.append(_node_to_dict(node))
            current_id = node.parent_id
        chain.reverse()
        return chain


def add_opening_move(
    parent_id: int,
    from_sq: str,
    to_sq: str,
    name: Optional[str] = None,
    comment: Optional[str] = None,
    session_factory=None,
) -> dict:
    """
    在指定节点下新增一步棋（也就是录入一个新变例分支）。

    会先重放到父节点局面、校验这步棋合法，再自动生成正式记谱并存下来——
    所以录入时不需要手打记谱，走一步棋就自动记好，也不可能录进非法走法。

    如果这一步在该节点下已经录过了（同样的起止坐标），不会重复创建，
    直接返回已有的那个节点（顺便更新名称/备注，如果这次传了的话）。
    """
    factory = session_factory or SessionLocal
    with factory() as session:
        parent = session.execute(
            select(OpeningNode).where(OpeningNode.id == parent_id)
        ).scalar_one_or_none()
        if parent is None:
            raise ValueError(f"找不到父节点: {parent_id}")

        # 已经录过同样这一步就直接复用，避免树上出现重复分支
        existing = session.execute(
            select(OpeningNode).where(
                OpeningNode.parent_id == parent_id,
                OpeningNode.from_sq == from_sq,
                OpeningNode.to_sq == to_sq,
            )
        ).scalar_one_or_none()
        if existing is not None:
            if name is not None:
                existing.name = name
            if comment is not None:
                existing.comment = comment
            existing.updated_at = datetime.now(timezone.utc)
            session.commit()
            return _node_to_dict(existing)

        game = _replay_to_node(parent_id, session)
        moved_side = game.current_side.value
        record = game.make_move(parse_coord(from_sq), parse_coord(to_sq))  # 非法走法会抛异常

        # 新分支默认排在同层最后面，之后可以在编辑界面里手动调整顺序
        sibling_count = len(session.execute(
            select(OpeningNode.id).where(OpeningNode.parent_id == parent_id)
        ).scalars().all())

        node = OpeningNode(
            parent_id=parent_id,
            move_notation=move_notation(record),
            from_sq=from_sq,
            to_sq=to_sq,
            moved_side=moved_side,
            position_text=board_to_position_string(game.board, game.current_side),
            name=name,
            comment=comment,
            sort_order=sibling_count,
        )
        session.add(node)
        session.commit()
        session.refresh(node)
        return _node_to_dict(node)


def update_opening_node(
    node_id: int,
    name: Optional[str] = None,
    comment: Optional[str] = None,
    sort_order: Optional[int] = None,
    session_factory=None,
) -> Optional[dict]:
    """
    修改某个节点的名称/备注/排序。只有传了值的字段会被改动，
    传 None 表示"这个字段不动"。想清空名称请传空字符串 ""。
    """
    factory = session_factory or SessionLocal
    with factory() as session:
        node = session.execute(
            select(OpeningNode).where(OpeningNode.id == node_id)
        ).scalar_one_or_none()
        if node is None:
            return None
        if name is not None:
            node.name = name or None
        if comment is not None:
            node.comment = comment or None
        if sort_order is not None:
            node.sort_order = sort_order
        node.updated_at = datetime.now(timezone.utc)
        session.commit()
        session.refresh(node)
        return _node_to_dict(node)


def reorder_opening_children(parent_id: int, ordered_ids: list[int], session_factory=None) -> None:
    """
    重排某个节点下所有子节点的显示顺序——编辑界面里"把主流变例拖到上面"用这个。
    ordered_ids 按期望的显示顺序传入，函数会据此重写各自的 sort_order。
    """
    factory = session_factory or SessionLocal
    with factory() as session:
        for index, node_id in enumerate(ordered_ids):
            node = session.execute(
                select(OpeningNode).where(
                    OpeningNode.id == node_id,
                    OpeningNode.parent_id == parent_id,
                )
            ).scalar_one_or_none()
            if node is not None:
                node.sort_order = index
                node.updated_at = datetime.now(timezone.utc)
        session.commit()


def delete_opening_subtree(node_id: int, session_factory=None) -> int:
    """
    删除一个节点，连同它下面的整棵子树一起删掉（删掉一条变例时，
    它派生出的所有后续变化自然也不该留着）。返回一共删了多少个节点。
    根节点不允许删除。
    """
    factory = session_factory or SessionLocal
    with factory() as session:
        node = session.execute(
            select(OpeningNode).where(OpeningNode.id == node_id)
        ).scalar_one_or_none()
        if node is None:
            return 0
        if node.parent_id is None:
            raise ValueError("根节点（初始局面）不能删除")

        # 广度优先收集整棵子树
        to_delete = [node]
        frontier = [node.id]
        while frontier:
            children = session.execute(
                select(OpeningNode).where(OpeningNode.parent_id.in_(frontier))
            ).scalars().all()
            if not children:
                break
            to_delete.extend(children)
            frontier = [c.id for c in children]

        for n in to_delete:
            session.delete(n)
        session.commit()
        return len(to_delete)


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

    # 关键场景：棋钟已经开始走、但还一步棋都没走（模拟匿名对局两个访客都已经
    # 认领完毕、正式开始计时，但还没轮到任何人真正落子的那个瞬间）——
    # 这种情况也应该立刻从大厅消失，不能靠"有没有人走棋"这一个指标来判断
    from clock import TimeControl as _TC
    game8 = Game(_TC(minutes_per_side=5, increment_seconds=3))
    game8.start_clocks()
    save_game("room_clock_started_no_moves", game8, session_factory=test_session_factory)
    rooms_clock_started = list_open_rooms(limit=50, session_factory=test_session_factory)
    assert "room_clock_started_no_moves" not in {r["game_id"] for r in rooms_clock_started}, \
        "棋钟已经开始走的房间，哪怕还没人走过棋，也不应该出现在大厅"

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

    # 10) 第三方登录：首次登录自动建号，重复登录返回同一个用户，用户名冲突自动加数字
    assert find_user_by_oauth("google", "google-uid-001", session_factory=test_session_factory) is None

    google_user = get_or_create_oauth_user(
        "google", "google-uid-001", "farkas", session_factory=test_session_factory
    )
    # "farkas" 已经被前面的密码注册占用了，应该自动变成 "farkas1" 之类的
    assert google_user.username != "farkas"
    assert google_user.username.startswith("farkas")

    # 再用同一个 provider_user_id 登录一次，应该返回同一个用户，不会重复建号
    google_user_again = get_or_create_oauth_user(
        "google", "google-uid-001", "farkas", session_factory=test_session_factory
    )
    assert google_user_again.id == google_user.id

    # 不同的 provider（哪怕 provider_user_id 数值一样）应该被当成不同账号
    facebook_user = get_or_create_oauth_user(
        "facebook", "google-uid-001", "someone", session_factory=test_session_factory
    )
    assert facebook_user.id != google_user.id

    # 11) 取消/删除房间
    cancel_game = Game()
    save_game("to_be_cancelled", cancel_game, black_player="farkas", session_factory=test_session_factory)
    assert game_exists("to_be_cancelled", session_factory=test_session_factory) is True
    assert delete_game("to_be_cancelled", session_factory=test_session_factory) is True
    assert game_exists("to_be_cancelled", session_factory=test_session_factory) is False
    assert delete_game("to_be_cancelled", session_factory=test_session_factory) is False, \
        "已经删过的对局再删一次应该返回False，不报错"

    # 12) 开局库树结构
    root_id = ensure_opening_root(session_factory=test_session_factory)
    assert root_id is not None
    # 重复调用不应该重复建根
    assert ensure_opening_root(session_factory=test_session_factory) == root_id

    # 录入"苍穹开局"的前两手：1.e6 g7
    n_e6 = add_opening_move(root_id, "e5", "e6", name="苍穹开局",
                            session_factory=test_session_factory)
    assert n_e6["move_notation"] == "e6", f"记谱应该自动生成: {n_e6['move_notation']}"
    assert n_e6["moved_side"] == "black"
    n_g7 = add_opening_move(n_e6["id"], "g8", "g7", session_factory=test_session_factory)
    assert n_g7["moved_side"] == "white"

    # 从同一个局面录入另一条分支：1.e6 之后白方改走 e7（天琴反弃兵的起手）
    n_e7 = add_opening_move(n_e6["id"], "e8", "e7", name="天琴反弃兵",
                            session_factory=test_session_factory)

    # 两条分支应该都挂在 e6 这个共享的父节点下
    children = list_opening_children(n_e6["id"], session_factory=test_session_factory)
    assert len(children) == 2, f"e6 下面应该有两条分支，实际 {len(children)}"
    assert {c["move_notation"] for c in children} == {"g7", "e7"}

    # 非法走法必须被拒绝，不能录进库里
    try:
        add_opening_move(root_id, "e5", "e12", session_factory=test_session_factory)
        raise AssertionError("非法走法不应该能录入开局库")
    except Exception:
        pass

    # 重复录同一步不会产生重复分支，而是复用并可顺便补充名称
    again = add_opening_move(n_e6["id"], "g8", "g7", name="苍穹主线",
                             session_factory=test_session_factory)
    assert again["id"] == n_g7["id"], "重复录入同一步应该复用已有节点"
    assert again["name"] == "苍穹主线"
    assert len(list_opening_children(n_e6["id"], session_factory=test_session_factory)) == 2

    # 排序：把 e7 那条调到最前面
    reorder_opening_children(n_e6["id"], [n_e7["id"], n_g7["id"]],
                             session_factory=test_session_factory)
    reordered = list_opening_children(n_e6["id"], session_factory=test_session_factory)
    assert reordered[0]["move_notation"] == "e7", "重排后 e7 应该排在最前面"

    # has_children 标记：g7 下面再录一手，它就应该被标记为"还有后续"
    add_opening_move(n_g7["id"], "c2", "f3", session_factory=test_session_factory)
    refreshed = list_opening_children(n_e6["id"], session_factory=test_session_factory)
    g7_entry = next(c for c in refreshed if c["move_notation"] == "g7")
    e7_entry = next(c for c in refreshed if c["move_notation"] == "e7")
    assert g7_entry["has_children"] is True
    assert e7_entry["has_children"] is False

    # 路径回溯：从最深的节点应该能还原出整条线
    deepest = list_opening_children(n_g7["id"], session_factory=test_session_factory)[0]
    path = get_opening_path(deepest["id"], session_factory=test_session_factory)
    assert [p["move_notation"] for p in path] == ["e6", "g7", "Hcf3"], \
        f"路径还原异常: {[p['move_notation'] for p in path]}"

    # 修改名称与备注
    updated = update_opening_node(n_e7["id"], comment="白方立刻反击中心",
                                  session_factory=test_session_factory)
    assert updated["comment"] == "白方立刻反击中心"

    # 删除子树：删掉 g7 应该连它下面的 Hcf3 一起删掉（共2个节点）
    deleted_count = delete_opening_subtree(n_g7["id"], session_factory=test_session_factory)
    assert deleted_count == 2, f"应该连同子树一起删除2个节点，实际 {deleted_count}"
    assert len(list_opening_children(n_e6["id"], session_factory=test_session_factory)) == 1

    # 根节点不允许删除
    try:
        delete_opening_subtree(root_id, session_factory=test_session_factory)
        raise AssertionError("根节点不应该能被删除")
    except ValueError:
        pass

    # 13) 对局库搜索（search_games）
    from datetime import datetime as _dt, timezone as _tz, timedelta as _td
    from pieces import Side as _Side

    # 造几局不同棋手、不同结果、不同"更新时间"的对局，覆盖各种过滤条件
    db_g1 = Game()
    db_g1.resign(_Side.BLACK)  # 让它有个明确的 result，不是 ongoing
    save_game("db_search_1", db_g1, black_player="Salvador", white_player="KaiWen",
              session_factory=test_session_factory)

    db_g2 = Game()
    db_g2.resign(_Side.WHITE)
    save_game("db_search_2", db_g2, black_player="Vesper", white_player="salvador_alt",
              session_factory=test_session_factory)

    db_g3 = Game()  # 故意不认输，保持 ongoing —— 应该被排除在搜索结果之外
    save_game("db_search_3", db_g3, black_player="Salvador", white_player="Nobody",
              session_factory=test_session_factory)

    # 手动改一条记录的 updated_at，制造一个"很久以前"的对局，用于测试日期范围过滤
    with test_session_factory() as _session:
        _old_record = _session.execute(
            select(GameRecord).where(GameRecord.game_id == "db_search_2")
        ).scalar_one()
        _old_record.updated_at = _dt(2020, 1, 1, tzinfo=_tz.utc)
        _session.commit()

    # 不加任何过滤：应该只有两局（db_search_3 是 ongoing，排除在外）
    all_result = search_games(session_factory=test_session_factory)
    all_ids = {g["game_id"] for g in all_result["games"]}
    assert "db_search_1" in all_ids and "db_search_2" in all_ids
    assert "db_search_3" not in all_ids, "进行中的对局不应该出现在database搜索结果里"
    assert all_result["total"] >= 2

    # 按棋手名子串搜索，且不分大小写：搜 "salvador" 应该同时命中
    # "Salvador"（大写开头）和 "salvador_alt"（子串在中间）
    by_player = search_games(player="salvador", session_factory=test_session_factory)
    player_ids = {g["game_id"] for g in by_player["games"]}
    assert "db_search_1" in player_ids, "大小写不敏感的子串匹配应该命中 Salvador"
    assert "db_search_2" in player_ids, "子串匹配应该命中 salvador_alt"
    assert "db_search_3" not in player_ids, "搜索结果依然要排除ongoing对局"

    # 按日期范围过滤：只要"最近"的，应该筛掉那条被改成2020年的记录
    recent_only = search_games(date_from="2024-01-01", session_factory=test_session_factory)
    recent_ids = {g["game_id"] for g in recent_only["games"]}
    assert "db_search_1" in recent_ids
    assert "db_search_2" not in recent_ids, "2020年的旧记录应该被date_from筛掉"

    # 组合条件：棋手名 + 日期范围都限定，只剩 db_search_1
    combined = search_games(player="salvador", date_from="2024-01-01", session_factory=test_session_factory)
    combined_ids = {g["game_id"] for g in combined["games"]}
    assert combined_ids == {"db_search_1"}, f"组合过滤结果异常: {combined_ids}"

    # 分页：limit=1 时只返回1条，但 total 应该反映真实的匹配总数
    paged = search_games(player="salvador", limit=1, session_factory=test_session_factory)
    assert len(paged["games"]) == 1
    assert paged["total"] == 2, "total应该是全部匹配数，不受limit影响"

    print("db.py 自检全部通过 ✅")

    test_engine.dispose()  # 释放连接池，避免 Windows 下文件被占用无法删除
    os.remove(TEST_DB_PATH)
