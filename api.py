"""
api.py  (第6版：接入时间控制 + 评分系统)
==========================================
运行方式（终端里，确保在项目文件夹下）：

    uvicorn api:app --reload

然后打开 http://127.0.0.1:8000/docs 测试各个接口。

设计说明：
    - 对局状态仍然优先保存在内存里的 GAMES 字典中（{game_id: Game 实例}），
      访问速度快；但每次创建/走棋/悔棋/认输之后都会调用 db.save_game()
      同步落盘到 SQLite/PostgreSQL。
    - 如果内存里找不到某个对局（比如服务器重启过），get_game_or_404()
      会自动尝试从数据库读取并回放重建；同时会顺带做一次"惰性超时检测"——
      每次有人跟这局对局交互，都会检查当前行棋方是不是已经超时。
    - 账号与对局的绑定：创建对局时，登录用户可以指定执黑/执白/随机；
      另一个登录用户可以调用 /games/{id}/join 认领剩下的一方。
      一旦某一方绑定了账号，走棋/悔棋/认输都会校验调用者身份。
      完全不登录也可以创建和游玩，此时是纯匿名对局，不做任何身份限制。
    - 时间控制与棋钟：创建对局时可以指定 minutes_per_side/increment_seconds
      （留空表示无时间限制）。棋钟什么时候开始走是有讲究的——纯匿名对局
      创建即开始计时；绑定了账号、还留着空位等人加入的房间，
      要等 /join 把最后一个空位填满才开始。
    - rated（排位）对局：只能随机先后手，必须设置时间控制。
      对局分出胜负后会自动触发评分结算（db.finalize_rated_game 内部
      有防重复触发保护）。
    - WebSocket（/ws/games/{game_id}）：连接后立即收到当前状态，
      之后每次走棋/悔棋/认输/加入都会自动收到最新状态推送，
      不需要客户端手动刷新或轮询。
    - 棋盘状态用 JSON 格式返回：{坐标字符串: {piece, side, promoted}}，
      方便未来前端直接用这份数据渲染棋盘。
    - 坐标统一用 "d5" 这种字符串格式在网络层传输，
      进出 Game/board 内部时再转换成 (col, row) 元组。
"""

from __future__ import annotations
import uuid
import secrets
import random

from fastapi import FastAPI, HTTPException, Header, WebSocket, WebSocketDisconnect, Security
from fastapi.security import APIKeyHeader
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from board import parse_coord, coord_to_str
from pieces import Side, Throne
from game import Game, IllegalMoveError, GameOverError
from notation import format_game, move_notation, position_string_to_board
from rules import is_in_check, GameResult
from clock import TimeControl
import db


app = FastAPI(title="Arkatana API", version="0.6.0")

# 注册一个具名的安全方案，Swagger 页面右上角会出现一个"Authorize"锁头按钮，
# 登录后把 token 粘贴进去点一次，之后所有接口请求会自动带上，不用每个接口都手填。
auth_scheme = APIKeyHeader(name="X-Auth-Token", auto_error=False)

# 启动时建表（如果表已存在，这行不会做任何事，可以放心每次都调用）
db.init_db()

# 托管前端静态文件：frontend/ 文件夹里的 index.html 等文件，
# 通过 http://127.0.0.1:8000/app/ 访问。
# 这样前端和后端同源，浏览器里 fetch('/games') 不会有跨域问题。
app.mount("/app", StaticFiles(directory="frontend", html=True), name="frontend")

# 内存对局存储：{game_id: Game 实例}
GAMES: dict[str, Game] = {}

# 内存对局玩家绑定：{game_id: {"black": username|None, "white": username|None}}
# 服务器重启后会从数据库里的 black_player/white_player 字段重新读取补全。
GAME_PLAYERS: dict[str, dict] = {}

# 内存对局的 rated 标记缓存：{game_id: bool}
GAME_RATED: dict[str, bool] = {}

# 内存登录令牌存储：{token: username}
# 注意：这是临时方案，服务器重启后所有人都需要重新登录。
# 以后如果需要"记住登录状态"，可以把这张表也存进数据库。
TOKENS: dict[str, str] = {}


class ConnectionManager:
    """
    管理 WebSocket 连接：记录"谁正在围观哪局对局"，
    并在对局状态变化时把最新状态广播给所有围观者。
    """

    def __init__(self):
        self.active: dict[str, list[WebSocket]] = {}

    async def connect(self, game_id: str, websocket: WebSocket) -> None:
        await websocket.accept()
        self.active.setdefault(game_id, []).append(websocket)

    def disconnect(self, game_id: str, websocket: WebSocket) -> None:
        conns = self.active.get(game_id)
        if conns and websocket in conns:
            conns.remove(websocket)
            if not conns:
                del self.active[game_id]

    async def broadcast(self, game_id: str, message: dict) -> None:
        conns = list(self.active.get(game_id, []))
        for ws in conns:
            try:
                await ws.send_json(message)
            except Exception:
                # 连接已经断了但还没被清理，直接移除，不影响其他人收消息
                self.disconnect(game_id, ws)


manager = ConnectionManager()


# ---------------------------------------------------------------------------
# 请求体格式（Pydantic 模型，FastAPI 会自动校验请求数据格式是否正确）
# ---------------------------------------------------------------------------

class MoveRequest(BaseModel):
    from_sq: str   # 例如 "d5"
    to_sq: str     # 例如 "d7"


class ResignRequest(BaseModel):
    side: str      # "black" 或 "white"


class CreateGameRequest(BaseModel):
    side_preference: str = "random"     # "black" / "white" / "random"
    rated: bool = False
    minutes_per_side: int | None = None  # None 表示无时间限制（比如"线下对练"）
    increment_seconds: int = 0


class RegisterRequest(BaseModel):
    username: str
    password: str


class LoginRequest(BaseModel):
    username: str
    password: str


# ---------------------------------------------------------------------------
# 内部工具函数
# ---------------------------------------------------------------------------

def board_to_json(board) -> dict:
    """把棋盘转换成 JSON 友好的格式：{坐标字符串: {棋子缩写, 中文符号, 阵营, 是否升变}}"""
    result = {}
    for coord in board.occupied_coords():
        piece = board.get(coord)
        result[coord_to_str(*coord)] = {
            "piece": piece.notation,
            "symbol": piece.symbol,
            "side": piece.side.value,
            "promoted": piece.promoted,
        }
    return result


def game_state(game_id: str, game: Game) -> dict:
    """组装一局对局当前状态的完整 JSON 响应"""
    players = get_players_cached(game_id)
    clock = game.clock

    if clock.is_unlimited:
        time_info = {"time_control": None, "black_time": None, "white_time": None}
    else:
        time_info = {
            "time_control": {
                "minutes_per_side": clock.time_control.minutes_per_side,
                "increment_seconds": clock.time_control.increment_seconds,
            },
            "black_time": clock.time_left("black"),
            "white_time": clock.time_left("white"),
        }

    return {
        "game_id": game_id,
        "current_side": game.current_side.value,
        "result": game.result.value,
        "move_count": len(game.move_log),
        "board": board_to_json(game.board),
        "black_player": players["black"],
        "white_player": players["white"],
        "rated": GAME_RATED.get(game_id, False),
        "clock_started": game.clock.active_side is not None,
        **time_info,
    }


def get_game_or_404(game_id: str) -> Game:
    game = GAMES.get(game_id)
    if game is None:
        # 内存里没有（比如服务器刚重启过），尝试从数据库读取重建
        restored = db.load_game(game_id)
        if restored is None:
            raise HTTPException(status_code=404, detail=f"找不到对局 ID: {game_id}")
        GAMES[game_id] = restored
        GAME_RATED[game_id] = db.is_rated(game_id)
        game = restored

    # 惰性超时检测：每次有人跟这局对局交互（不只是走棋），
    # 顺带看一眼当前行棋方是不是已经超时，超时就直接结束对局。
    if game.check_timeout():
        persist(game_id, game)
        maybe_finalize_rating(game_id, game)

    return game


def persist(
    game_id: str,
    game: Game,
    black_player: str | None = None,
    white_player: str | None = None,
    rated: bool | None = None,
) -> None:
    """把当前对局状态存入数据库（每次影响状态的操作后都应调用）"""
    db.save_game(
        game_id, game,
        black_player=black_player, white_player=white_player, rated=rated,
    )


def maybe_finalize_rating(game_id: str, game: Game) -> None:
    """
    对局刚结束时调用：如果是 rated 对局，尝试触发评分结算。
    db.finalize_rated_game 内部已经做好了各种前提条件检查
    （是否rated、双方是否都注册、是否有时间控制、是否已经结算过），
    这里只需要负责"游戏一结束就调用一下"，不需要重复判断。
    """
    if game.result == GameResult.ONGOING:
        return
    winner = "black" if game.result == GameResult.BLACK_WINS else "white"
    db.finalize_rated_game(game_id, winner)


def start_game_if_needed(game: Game) -> bool:
    """
    幂等地"正式开始"一局对局：如果棋钟还没走过（active_side 为 None），
    就调用 start_clocks() 让它从现在开始计时；已经开始过了则什么都不做。
    返回是否真的触发了开始（供调用方决定要不要落盘）。
    """
    if game.clock.is_unlimited:
        return False  # 无时间限制的对局没有"开始计时"这回事
    if game.clock.active_side is not None:
        return False  # 已经开始过了
    game.start_clocks()
    return True


def get_current_user(x_auth_token: str | None) -> str | None:
    """根据请求头里的令牌查找对应的用户名；令牌无效或没提供都返回 None"""
    if not x_auth_token:
        return None
    return TOKENS.get(x_auth_token)


def require_login(x_auth_token: str | None) -> str:
    """跟 get_current_user 一样，但找不到用户时直接抛出 401，供需要登录才能用的接口调用"""
    if not x_auth_token:
        raise HTTPException(status_code=401, detail="需要登录（请在请求头带上 X-Auth-Token）")
    username = TOKENS.get(x_auth_token)
    if username is None:
        raise HTTPException(status_code=401, detail="登录令牌无效或已过期，请重新登录")
    return username


def get_players_cached(game_id: str) -> dict:
    """获取某局对局的黑白双方玩家绑定情况；内存里没有则从数据库读取并缓存"""
    if game_id not in GAME_PLAYERS:
        black, white = db.get_players(game_id)
        GAME_PLAYERS[game_id] = {"black": black, "white": white}
    return GAME_PLAYERS[game_id]


def authorize_participant(game_id: str, x_auth_token: str | None) -> str | None:
    """
    校验调用者是否有权对这局对局做出影响状态的操作（悔棋/认输/走棋等）。
    - 如果这局对局黑白双方都没有绑定账号（纯匿名对局），不做任何限制，返回 None。
    - 如果绑定了账号，调用者必须登录，且必须是这两位玩家之一，否则抛出异常。
    返回调用者的用户名（匿名对局则返回 None）。
    """
    players = get_players_cached(game_id)
    if players["black"] is None and players["white"] is None:
        return None  # 纯匿名对局，不做身份限制

    username = require_login(x_auth_token)
    if username not in (players["black"], players["white"]):
        raise HTTPException(status_code=403, detail="你不是这局对局的玩家")
    return username


# ---------------------------------------------------------------------------
# 基础接口（保留自第1版，方便随时确认服务是否存活）
# ---------------------------------------------------------------------------

@app.get("/")
def read_root():
    """最基础的接口：访问网站首页，返回一句问候"""
    return {"message": "Arkatana 后端已经跑起来了！"}


# ---------------------------------------------------------------------------
# 账号接口
# ---------------------------------------------------------------------------

@app.post("/register")
def register(body: RegisterRequest):
    """注册新账号"""
    username = body.username.strip()
    if len(username) < 3:
        raise HTTPException(status_code=400, detail="用户名至少需要3个字符")
    if len(body.password) < 6:
        raise HTTPException(status_code=400, detail="密码至少需要6个字符")

    user = db.create_user(username, body.password)
    if user is None:
        raise HTTPException(status_code=409, detail="用户名已被使用")

    return {"username": user.username, "message": "注册成功，请登录"}


@app.post("/login")
def login(body: LoginRequest):
    """登录，成功后返回一个令牌（token），之后的请求在请求头带上
    'X-Auth-Token: <token>' 就能证明"这是谁在操作"。"""
    username = body.username.strip()
    if not db.verify_user_login(username, body.password):
        raise HTTPException(status_code=401, detail="用户名或密码错误")

    token = secrets.token_hex(16)
    TOKENS[token] = username
    return {"token": token, "username": username}


@app.get("/me")
def read_current_user(x_auth_token: str = Security(auth_scheme)):
    """查询当前令牌对应的登录用户，主要用于验证登录流程是否正常"""
    username = require_login(x_auth_token)
    return {"username": username}


@app.get("/users/{username}/rating")
def get_user_rating(username: str):
    """查询某个用户的当前评分（公开信息，不需要登录），用户不存在则返回默认初始评分"""
    rating, games_played = db.get_rating(username)
    return {"username": username, "rating": rating, "games_played": games_played}


# ---------------------------------------------------------------------------
# 对局管理接口
# ---------------------------------------------------------------------------

@app.post("/games")
async def create_game(body: CreateGameRequest | None = None, x_auth_token: str = Security(auth_scheme)):
    """
    创建一局新对局（也就是"开一个房间"）。
    如果携带了有效的登录令牌，可以指定 side_preference：
      - "black"：创建者执黑（先手）
      - "white"：创建者执白（后手）
      - "random"（默认）：随机决定创建者执哪一方
    未指定或不登录时，等同于纯匿名对局，双方都不绑定账号，也不会出现在大厅里。

    rated（排位）对局只能随机先后手，不能指定执黑/执白；
    而且 rated 对局必须设置时间控制（不能是无时间限制的"线下对练"模式）。

    时间控制：minutes_per_side 留空表示无时间限制；否则两个数值都必须落在
    约定好的离散档位上（TimeControl 内部会自动校验，不合法会返回 400）。

    棋钟什么时候开始走：不管匿名还是账号对局，创建的这一刻都**不会**开始计时——
    对局会先"晾在大厅里"等人进来，真正开始的时机由 /games/{id}/start 或
    /games/{id}/join（账号对局凑齐两人时）触发，避免创建者一个人干等的时候
    自己的棋钟却在空转。
    """
    creator = get_current_user(x_auth_token)
    preference = (body.side_preference if body else "random").strip().lower()
    rated = bool(body.rated) if body else False
    minutes_per_side = body.minutes_per_side if body else None
    increment_seconds = (body.increment_seconds if body else 0) or 0

    if rated and preference != "random":
        raise HTTPException(status_code=400, detail="rated 对局只能随机先后手，不能指定执黑/执白")
    if rated and minutes_per_side is None:
        raise HTTPException(status_code=400, detail="rated 对局必须设置时间控制")

    try:
        time_control = TimeControl(minutes_per_side, increment_seconds) if minutes_per_side is not None else None
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    game_id = uuid.uuid4().hex[:8]
    GAMES[game_id] = Game(time_control=time_control)

    black_slot: str | None = None
    white_slot: str | None = None

    if creator is not None:
        if preference == "black":
            black_slot = creator
        elif preference == "white":
            white_slot = creator
        else:  # "random" 或其他任何值，一律按随机处理
            if random.choice([True, False]):
                black_slot = creator
            else:
                white_slot = creator

    GAME_PLAYERS[game_id] = {"black": black_slot, "white": white_slot}
    GAME_RATED[game_id] = rated

    persist(game_id, GAMES[game_id], black_player=black_slot, white_player=white_slot, rated=rated)

    state = game_state(game_id, GAMES[game_id])
    await manager.broadcast(game_id, state)
    return state


@app.post("/games/{game_id}/join")
async def join_game(game_id: str, x_auth_token: str = Security(auth_scheme)):
    """
    登录用户加入一局对局，认领还没人认领的那一方。
    如果两方都已经有人认领，返回错误；如果自己已经在这局里了，也会提示。
    """
    game = get_game_or_404(game_id)  # 确保对局存在（顺带把内存缓存补全）
    username = require_login(x_auth_token)
    players = get_players_cached(game_id)

    if username in (players["black"], players["white"]):
        raise HTTPException(status_code=400, detail="你已经是这局对局的玩家了")

    if players["black"] is None:
        players["black"] = username
    elif players["white"] is None:
        players["white"] = username
    else:
        raise HTTPException(status_code=409, detail="这局对局的黑白双方都已经有人认领了")

    # 刚好把最后一个空位填满：对局正式凑齐两个人，开始计时
    if players["black"] is not None and players["white"] is not None:
        start_game_if_needed(game)

    persist(game_id, GAMES[game_id], black_player=players["black"], white_player=players["white"])
    state = game_state(game_id, GAMES[game_id])
    await manager.broadcast(game_id, state)
    return state


@app.post("/games/{game_id}/start")
async def start_game(game_id: str):
    """
    正式开始一局对局（棋钟从此刻开始走）。这是"匿名对局"用的入口——
    因为匿名玩家没有账号，没法走 /join 那套"认领身份"的流程，
    所以只要是第二个真正打算下棋的人点了"进入对局"，前端就会调用这个接口。
    如果棋钟已经在走了（比如已经有人触发过），这个接口不会做任何事，
    可以放心重复调用。
    """
    game = get_game_or_404(game_id)
    started_now = start_game_if_needed(game)
    if started_now:
        persist(game_id, game)
    state = game_state(game_id, game)
    await manager.broadcast(game_id, state)
    return state


@app.get("/games/mine")
def list_my_games(limit: int = 20, x_auth_token: str = Security(auth_scheme)):
    """
    列出当前登录用户自己参与过的对局（网页首页"Game History"用这个）。
    需要登录；匿名对局或别人的对局不会出现在这里。
    注意：这个路由必须写在 /games/{game_id} 之前，否则 FastAPI 会把
    "mine" 当成 game_id 处理，永远匹配不到这个接口。
    """
    username = require_login(x_auth_token)
    return {"games": db.list_user_games(username, limit=limit)}


@app.get("/games/{game_id}")
def get_game(game_id: str):
    """查询某局对局当前的完整状态"""
    game = get_game_or_404(game_id)
    return game_state(game_id, game)


@app.get("/games/{game_id}/legal-moves")
def get_legal_moves(game_id: str, coord: str):
    """查询指定坐标上的棋子，当前有哪些合法走法"""
    game = get_game_or_404(game_id)
    try:
        origin = parse_coord(coord)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    moves = game.legal_moves_from(origin)
    return {
        "coord": coord,
        "legal_destinations": [coord_to_str(*m.to_sq) for m in moves],
    }


@app.post("/games/{game_id}/move")
async def make_move(game_id: str, move: MoveRequest, x_auth_token: str = Security(auth_scheme)):
    """执行一步棋。如果当前轮到走棋的这一方已经绑定了账号，必须以该账号登录才能提交这步棋。"""
    game = get_game_or_404(game_id)
    start_game_if_needed(game)  # 兜底：万一前端没调用 /start 就直接走棋了，这里保证棋钟正确开始

    players = get_players_cached(game_id)
    mover_slot = players[game.current_side.value]
    if mover_slot is not None:
        username = require_login(x_auth_token)
        if username != mover_slot:
            raise HTTPException(status_code=403, detail="还没轮到你，这不是你的回合")

    try:
        from_sq = parse_coord(move.from_sq)
        to_sq = parse_coord(move.to_sq)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    try:
        record = game.make_move(from_sq, to_sq)
    except IllegalMoveError:
        piece = game.board.get(from_sq)
        if isinstance(piece, Throne):
            raise HTTPException(status_code=400, detail="王城不可移动")
        if is_in_check(game.board, game.current_side):
            raise HTTPException(status_code=400, detail="你的王城正在被攻击")
        raise HTTPException(status_code=400, detail="非法走法")
    except GameOverError as e:
        raise HTTPException(status_code=409, detail=str(e))

    response = game_state(game_id, game)
    response["last_move"] = move_notation(record)
    persist(game_id, game)
    maybe_finalize_rating(game_id, game)
    await manager.broadcast(game_id, response)
    return response


@app.post("/games/{game_id}/undo")
async def undo_move(game_id: str, x_auth_token: str = Security(auth_scheme)):
    """悔棋。如果这局对局绑定了账号，必须是黑方或白方玩家本人才能悔棋。"""
    game = get_game_or_404(game_id)
    authorize_participant(game_id, x_auth_token)
    try:
        game.undo()
    except IllegalMoveError as e:
        raise HTTPException(status_code=400, detail=str(e))
    persist(game_id, game)
    state = game_state(game_id, game)
    await manager.broadcast(game_id, state)
    return state


@app.post("/games/{game_id}/resign")
async def resign(game_id: str, body: ResignRequest, x_auth_token: str = Security(auth_scheme)):
    """指定一方认输。如果这局对局绑定了账号，只能替自己那一方认输，不能替对方认输。"""
    game = get_game_or_404(game_id)
    if game.result.value != "ongoing":
        raise HTTPException(status_code=409, detail="对局已结束")
    side_key = body.side.strip().lower()
    if side_key not in ("black", "white"):
        raise HTTPException(status_code=400, detail="side 字段必须是 'black' 或 'white'")

    players = get_players_cached(game_id)
    resigning_slot = players[side_key]
    if resigning_slot is not None:
        username = require_login(x_auth_token)
        if username != resigning_slot:
            raise HTTPException(status_code=403, detail="只能替自己那一方认输")

    side = Side.BLACK if side_key == "black" else Side.WHITE
    game.resign(side)
    persist(game_id, game)
    maybe_finalize_rating(game_id, game)
    state = game_state(game_id, game)
    await manager.broadcast(game_id, state)
    return state


@app.get("/lobby")
def get_lobby(limit: int = 20):
    """列出大厅里正在等待对手的开放房间（恰好只有一方绑定了账号的对局）"""
    return {"rooms": db.list_open_rooms(limit=limit)}


@app.get("/games")
def list_games(limit: int = 20):
    """列出最近更新过的对局摘要（内部/管理用途，不是网页首页"Game History"用的那个）"""
    return {"games": db.list_recent_games(limit=limit)}


@app.get("/games/{game_id}/history")
def get_history(game_id: str):
    """查看整局走子记录（记谱格式文本）"""
    game = get_game_or_404(game_id)
    return {"history": format_game(game.move_log)}


@app.get("/games/{game_id}/replay")
def get_replay(game_id: str):
    """
    复盘：逐步返回这局对局每一步棋之后的完整棋盘状态（JSON格式），
    供前端做"一步步回放"的复盘界面。对局不存在则返回 404。
    """
    if not db.game_exists(game_id):
        raise HTTPException(status_code=404, detail=f"找不到对局 ID: {game_id}")

    try:
        steps = db.replay_steps(game_id)
        result = []
        for step in steps:
            board, _ = position_string_to_board(step["position"])
            result.append({
                "move_number": step["move_number"],
                "side": step["side"],
                "notation": step["notation"],
                "board": board_to_json(board),
            })
    except Exception as e:
        # 常见原因：这局对局是用旧版记谱格式存的，现在的解析器读不懂了
        # （记谱格式在开发过程中调整过，早期存的数据可能跟当前版本不兼容）
        raise HTTPException(
            status_code=422,
            detail=f"这局对局的棋谱回放失败，可能是用旧格式存储的历史数据: {type(e).__name__}: {e}",
        )
    return {"game_id": game_id, "steps": result}


# ---------------------------------------------------------------------------
# WebSocket：实时围观某一局对局
# ---------------------------------------------------------------------------

@app.websocket("/ws/games/{game_id}")
async def game_socket(websocket: WebSocket, game_id: str):
    """
    连接后立即推送一次当前对局状态；之后每当有人走棋/悔棋/认输/加入，
    都会自动收到最新状态，不需要客户端主动发消息或轮询。
    """
    try:
        game = get_game_or_404(game_id)
    except HTTPException:
        await websocket.close(code=4404)
        return

    await manager.connect(game_id, websocket)
    try:
        await websocket.send_json(game_state(game_id, game))
        while True:
            # 目前不处理客户端发来的具体内容，只用来检测连接是否还活着
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(game_id, websocket)
