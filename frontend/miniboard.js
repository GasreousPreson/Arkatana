/*
 * miniboard.js
 * ============
 * Arkatana（古战棋）— 观战用小棋盘渲染器
 *
 * 首页观战区每一局对局都是一块小画布，这个文件负责把 /live 接口返回的
 * 棋盘数据画上去，并在棋子移动时播一段滑行动画。
 *
 * 为什么不直接复用 game.html 里那套 drawBoard：
 *   那套代码跟对局页的一大堆状态（选中格、合法走法高亮、吃子栏、回看历史、
 *   我是哪一方……）绑得很死，都写在同一个闭包里，拆不出来。观战小棋盘
 *   要的只是"把一个局面画出来"这一件事，硬搬过去反而要拖一堆用不上的状态。
 *   所以这里重写一份精简版，只保留：棋盘格 + 上一步高亮 + 将军红光 + 棋子 +
 *   移动动画。几何和配色刻意跟对局页保持一致（同样的 COLS/MIN_ROW/MAX_ROW、
 *   同样的 parchLight/parchDark），这样从首页点进对局页不会有割裂感。
 *
 * 棋子素材复用 pieces.js 的 ArkatanaPieces（同一套 PNG、同一套左右版逻辑），
 * 所以本文件必须在 pieces.js **之后**加载。
 *
 * 用法：
 *     const mb = ArkatanaMiniBoard.create(canvasEl, { flipped: false });
 *     mb.update({ board, last_move_from, last_move_to, in_check, current_side });
 *     // 再次 update 时，如果检测到某枚棋子从 A 走到了 B，会自动播放滑行动画
 *     mb.destroy();   // 元素移除前调用，停掉动画循环
 */

(function (global) {
  "use strict";

  const COLS = "abcdefghjkl";   // 跳过 i，跟后端/对局页一致
  const MIN_ROW = 1;
  const MAX_ROW = 12;
  const NUM_COLS = COLS.length;         // 11
  const NUM_ROWS = MAX_ROW - MIN_ROW + 1; // 12

  // 配色跟 game.html 的 COLORS 保持一致，只是省掉了小棋盘用不到的几个
  const COLORS = {
    parchLight: "#ede3c8",
    parchDark: "#cbb98d",
    grid: "rgba(27,27,30,0.22)",
    lastMove: "rgba(228, 166, 103, 0.55)",
    checkGlow: "rgba(196, 45, 45, 0.5)",
  };

  const ANIM_MS = 260;   // 比对局页略快——小棋盘上动画太慢会显得拖沓

  /* ---------------------------------------------------------------
   * 几何
   * ------------------------------------------------------------- */

  function parseSquare(sq) {
    if (!sq || sq.length < 2) return null;
    const col = COLS.indexOf(sq[0]);
    const row = parseInt(sq.slice(1), 10);
    if (col < 0 || !Number.isFinite(row) || row < MIN_ROW || row > MAX_ROW) return null;
    return [col, row];
  }

  /* ---------------------------------------------------------------
   * 单块小棋盘
   * ------------------------------------------------------------- */

  function create(canvas, options) {
    const opts = options || {};
    const state = {
      canvas: canvas,
      ctx: canvas.getContext("2d"),
      flipped: !!opts.flipped,
      board: {},            // { 坐标: {piece, side, promoted} }
      lastFrom: null,
      lastTo: null,
      inCheck: false,
      currentSide: "black",
      anim: null,           // { fromSq, toSq, info, capturedInfo, startedAt }
      rafId: null,
      destroyed: false,
      // 棋子朝向记忆：棋子走到正中 f 列时不翻面，沿用原朝向（跟 pieces.js
      // 的 stickyVariant 是同一件事）。键是坐标，值是 "l"/"r"。
      variantMemo: {},
    };

    function cell() {
      // 按画布实际像素算格子大小，这样同一份代码能适应任意尺寸的小棋盘；
      // 取两个方向里较小的那个，保证整盘一定放得下、不会被裁掉。
      return Math.min(state.canvas.width / NUM_COLS, state.canvas.height / NUM_ROWS);
    }

    function originOffset(cellSize) {
      // 棋盘在画布里居中：11×12 的长宽比未必跟画布完全一致，多出来的部分
      // 平均分到两边，不要让棋盘贴着一边。
      return [
        (state.canvas.width - cellSize * NUM_COLS) / 2,
        (state.canvas.height - cellSize * NUM_ROWS) / 2,
      ];
    }

    function squareTopLeft(col, row, cellSize, ox, oy) {
      const displayCol = state.flipped ? (NUM_COLS - 1 - col) : col;
      const displayRowIndex = state.flipped ? (row - MIN_ROW) : (MAX_ROW - row);
      return [ox + displayCol * cellSize, oy + displayRowIndex * cellSize];
    }

    function squareCenter(sq, cellSize, ox, oy) {
      const parsed = parseSquare(sq);
      if (!parsed) return null;
      const [x, y] = squareTopLeft(parsed[0], parsed[1], cellSize, ox, oy);
      return [x + cellSize / 2, y + cellSize / 2];
    }

    function fillSquare(sq, color, cellSize, ox, oy) {
      const parsed = parseSquare(sq);
      if (!parsed) return;
      const [x, y] = squareTopLeft(parsed[0], parsed[1], cellSize, ox, oy);
      state.ctx.fillStyle = color;
      state.ctx.fillRect(x, y, cellSize, cellSize);
    }

    /* -------------------------------------------------------------
     * 画一枚棋子
     * ----------------------------------------------------------- */

    function drawPieceAt(info, sq, cx, cy, cellSize) {
      const ctx = state.ctx;
      const notation = info.piece === undefined ? "" : info.piece;

      // 朝向：优先用记住的朝向（棋子路过正中列时不突兀翻面）
      const sticky = state.variantMemo[sq];
      let img = null;
      if (global.ArkatanaPieces) {
        img = global.ArkatanaPieces.getImage(
          notation, info.side, sq, state.flipped, sticky, !!info.promoted
        );
        // 记下这枚棋子这次用的是哪一版，供它下次走到中列时沿用
        const variant = global.ArkatanaPieces.resolveVariant(sq, state.flipped, sticky);
        state.variantMemo[sq] = variant;
      }

      if (img) {
        const scale = global.ArkatanaPieces.pieceScale(notation);
        const size = cellSize * scale;
        ctx.drawImage(img, cx - size / 2, cy - size / 2, size, size);
      }
      // 素材还没到位就这一帧留空——理由同 game.html 里那处：
      // 占位造型跟正式造型差太远，加载完成时整盘突然换皮比短暂空着更难看。
      // pieces.js 每加载好一张就会回调重绘，棋子是逐枚浮现的。
    }

    /* -------------------------------------------------------------
     * 整盘重绘
     * ----------------------------------------------------------- */

    function draw() {
      if (state.destroyed) return;
      const ctx = state.ctx;
      const cellSize = cell();
      const [ox, oy] = originOffset(cellSize);

      ctx.clearRect(0, 0, state.canvas.width, state.canvas.height);

      // 棋盘格
      for (let row = MIN_ROW; row <= MAX_ROW; row++) {
        for (let c = 0; c < NUM_COLS; c++) {
          const [x, y] = squareTopLeft(c, row, cellSize, ox, oy);
          ctx.fillStyle = ((c + row) % 2 === 0) ? COLORS.parchDark : COLORS.parchLight;
          ctx.fillRect(x, y, cellSize, cellSize);
        }
      }

      // 上一步高亮
      if (state.lastFrom) fillSquare(state.lastFrom, COLORS.lastMove, cellSize, ox, oy);
      if (state.lastTo) fillSquare(state.lastTo, COLORS.lastMove, cellSize, ox, oy);

      // 将军红光：当前行棋方的王城格
      if (state.inCheck) {
        const entry = Object.entries(state.board).find(
          ([, info]) => info.piece === "TH" && info.side === state.currentSide
        );
        if (entry) fillSquare(entry[0], COLORS.checkGlow, cellSize, ox, oy);
      }

      // 网格线
      ctx.strokeStyle = COLORS.grid;
      ctx.lineWidth = 1;
      for (let c = 0; c <= NUM_COLS; c++) {
        const x = ox + c * cellSize;
        ctx.beginPath();
        ctx.moveTo(x, oy);
        ctx.lineTo(x, oy + NUM_ROWS * cellSize);
        ctx.stroke();
      }
      for (let r = 0; r <= NUM_ROWS; r++) {
        const y = oy + r * cellSize;
        ctx.beginPath();
        ctx.moveTo(ox, y);
        ctx.lineTo(ox + NUM_COLS * cellSize, y);
        ctx.stroke();
      }

      // 棋子（正在做动画的那枚先跳过，最后单独画在插值位置上）
      Object.entries(state.board).forEach(([sq, info]) => {
        if (state.anim && state.anim.toSq === sq) return;
        const center = squareCenter(sq, cellSize, ox, oy);
        if (center) drawPieceAt(info, sq, center[0], center[1], cellSize);
      });

      if (state.anim) {
        const a = state.anim;
        // 被吃的棋子留在原地，等动画走完才消失——跟对局页的处理一致
        if (a.capturedInfo) {
          const c = squareCenter(a.toSq, cellSize, ox, oy);
          if (c) drawPieceAt(a.capturedInfo, a.toSq, c[0], c[1], cellSize);
        }
        const t = Math.min(1, (performance.now() - a.startedAt) / ANIM_MS);
        const eased = 1 - Math.pow(1 - t, 3);   // easeOutCubic，落子更稳
        const from = squareCenter(a.fromSq, cellSize, ox, oy);
        const to = squareCenter(a.toSq, cellSize, ox, oy);
        if (from && to) {
          const cx = from[0] + (to[0] - from[0]) * eased;
          const cy = from[1] + (to[1] - from[1]) * eased;
          drawPieceAt(a.info, a.toSq, cx, cy, cellSize);
        }
        if (t >= 1) {
          state.anim = null;
          state.rafId = null;
          draw();          // 收尾重画一次，把棋子落到终点格
          return;
        }
        state.rafId = requestAnimationFrame(draw);
      } else {
        state.rafId = null;
      }
    }

    /* -------------------------------------------------------------
     * 更新局面
     * ----------------------------------------------------------- */

    function update(data) {
      if (state.destroyed) return;
      const nextBoard = data.board || {};
      const nextFrom = data.last_move_from || null;
      const nextTo = data.last_move_to || null;

      // 判断要不要播动画：上一步的终点格跟我们记着的不一样，说明这次轮询
      // 之间真的走了棋。用 last_move_from/to 而不是自己 diff 两个棋盘——
      // 后端已经明确告诉我们这一步从哪走到哪了，没必要再猜一遍，
      // 而且吃子的情况下 diff 也分不清"谁吃了谁"。
      const movedSinceLast = nextTo && (nextTo !== state.lastTo || nextFrom !== state.lastFrom);
      const isFirstPaint = Object.keys(state.board).length === 0;

      if (movedSinceLast && !isFirstPaint && nextFrom && nextBoard[nextTo]) {
        state.anim = {
          fromSq: nextFrom,
          toSq: nextTo,
          info: nextBoard[nextTo],
          // 终点格原来有子 = 这是一次吃子，把被吃的那枚记下来晚点再抹掉
          capturedInfo: state.board[nextTo] || null,
          startedAt: performance.now(),
        };
      }

      state.board = nextBoard;
      state.lastFrom = nextFrom;
      state.lastTo = nextTo;
      state.inCheck = !!data.in_check;
      state.currentSide = data.current_side || "black";

      if (state.rafId === null) {
        if (state.anim) {
          state.rafId = requestAnimationFrame(draw);
        } else {
          draw();
        }
      }
    }

    function setFlipped(flipped) {
      state.flipped = !!flipped;
      state.variantMemo = {};   // 翻转视角后左右版要重算，旧的记忆全作废
      if (state.rafId === null) draw();
    }

    function destroy() {
      state.destroyed = true;
      if (state.rafId !== null) {
        cancelAnimationFrame(state.rafId);
        state.rafId = null;
      }
    }

    draw();   // 先画一块空棋盘，别让位置空着
    return { update, setFlipped, destroy, redraw: draw };
  }

  global.ArkatanaMiniBoard = { create };
})(window);
