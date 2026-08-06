/*
 * pieces.js
 * =========
 * Arkatana（古戰棋）— 棋子矢量图形
 *
 * 设计说明：
 *   - 采用扁平矢量风格（对应"方案二"），不做立体木片造型，
 *     木片风格留给以后作为设置里可切换的皮肤。
 *   - 配色沿用实体棋的逻辑：深色木片配金漆 → 黑方（先手）用金色；
 *     浅色木片配白漆 → 白方（后手）用银白色。两者都带深色描边，
 *     保证在宣纸色棋盘上都有足够对比度。
 *   - 所有棋子默认朝右绘制；棋盘右半边（g~k列）的棋子会被水平镜像，
 *     这样左右两侧的棋子都朝向棋盘中线，跟实体棋的摆法一致。
 *     f列（正中）的棋子不镜像——兵在这一列是双手持械于胸前的正面造型，
 *     王城和大将本身就是左右对称的图案。
 *
 * 用法：
 *   pieceSVG("R", "black", { col: 0 })   -> 返回一段 SVG 字符串
 *   记谱字母对照：兵="" / 弩车=B / 炮塔=T / 大将=A / 轻骑=H /
 *                重骑=N / 攻城塔=R / 凤凰=P / 剑士=S / 战车=C / 王城=TH
 */

(function (global) {
  "use strict";

  const COLORS = {
    black: { fill: "#c9a227", stroke: "#3a2c14", accent: "#e5c757" },
    white: { fill: "#ece7db", stroke: "#3a3730", accent: "#ffffff" },
  };

  // 每种棋子的图形主体（默认朝右，viewBox 0 0 100 100）
  // 用函数生成，方便按阵营取不同的配色
  const SHAPES = {
    // 兵：侧面人形，持长戟，身披披风
    "": (c) => `
      <path d="M62 22 L62 84" stroke="${c.stroke}" stroke-width="4" stroke-linecap="round"/>
      <path d="M62 22 L57 30 L67 30 Z" fill="${c.fill}" stroke="${c.stroke}" stroke-width="2.5" stroke-linejoin="round"/>
      <path d="M62 34 q10 3 12 9 q-7 1 -12 -2 Z" fill="${c.accent}" stroke="${c.stroke}" stroke-width="2" stroke-linejoin="round"/>
      <path d="M44 34 q-4 -9 4 -12 q8 -3 10 5 q1 5 -3 7 Z" fill="${c.fill}" stroke="${c.stroke}" stroke-width="2.5" stroke-linejoin="round"/>
      <path d="M40 38 q10 -4 18 0 q6 3 5 12 l3 32 q-16 5 -32 0 l4 -32 q-1 -9 2 -12 Z" fill="${c.fill}" stroke="${c.stroke}" stroke-width="2.5" stroke-linejoin="round"/>
      <path d="M46 52 q6 -2 12 0" stroke="${c.stroke}" stroke-width="2" fill="none" stroke-linecap="round"/>
      <path d="M32 84 q18 5 36 0 l2 6 q-20 5 -40 0 Z" fill="${c.fill}" stroke="${c.stroke}" stroke-width="2.5" stroke-linejoin="round"/>`,

    // 攻城塔 Rook：石砌塔楼，顶部城垛
    R: (c) => `
      <path d="M28 30 h8 v-8 h8 v8 h8 v-8 h8 v8 h8 v-8 h8 v8 h4 v10 H24 V30 Z" fill="${c.fill}" stroke="${c.stroke}" stroke-width="2.5" stroke-linejoin="round"/>
      <path d="M30 40 h40 l4 40 H26 Z" fill="${c.fill}" stroke="${c.stroke}" stroke-width="2.5" stroke-linejoin="round"/>
      <path d="M44 52 q6 -6 12 0 v14 h-12 Z" fill="${c.stroke}" opacity="0.55"/>
      <path d="M34 46 h6 M60 46 h6" stroke="${c.stroke}" stroke-width="2" stroke-linecap="round"/>
      <path d="M20 80 h60 l3 8 H17 Z" fill="${c.fill}" stroke="${c.stroke}" stroke-width="2.5" stroke-linejoin="round"/>`,

    // 战车 Chariot：木质轮车，车厢带structure
    C: (c) => `
      <path d="M26 44 h44 l6 20 H22 Z" fill="${c.fill}" stroke="${c.stroke}" stroke-width="2.5" stroke-linejoin="round"/>
      <path d="M32 44 v-10 h6 v10 M48 44 v-14 h6 v14 M62 44 v-8 h6 v8" fill="${c.fill}" stroke="${c.stroke}" stroke-width="2.5" stroke-linejoin="round"/>
      <path d="M28 30 h44 l3 6 H25 Z" fill="${c.accent}" stroke="${c.stroke}" stroke-width="2.5" stroke-linejoin="round"/>
      <circle cx="36" cy="72" r="11" fill="none" stroke="${c.stroke}" stroke-width="3"/>
      <circle cx="36" cy="72" r="3.5" fill="${c.fill}" stroke="${c.stroke}" stroke-width="2"/>
      <path d="M36 61 v22 M25 72 h22 M28 64 l16 16 M44 64 l-16 16" stroke="${c.stroke}" stroke-width="1.8"/>
      <circle cx="66" cy="72" r="11" fill="none" stroke="${c.stroke}" stroke-width="3"/>
      <circle cx="66" cy="72" r="3.5" fill="${c.fill}" stroke="${c.stroke}" stroke-width="2"/>
      <path d="M66 61 v22 M55 72 h22 M58 64 l16 16 M74 64 l-16 16" stroke="${c.stroke}" stroke-width="1.8"/>
      <path d="M70 50 l16 -6" stroke="${c.stroke}" stroke-width="3" stroke-linecap="round"/>`,

    // 凤凰 Phoenix：展翅飞鸟
    P: (c) => `
      <path d="M56 42 q10 -12 24 -12 q-4 8 -10 12 q10 -2 16 4 q-8 6 -18 6 Z" fill="${c.fill}" stroke="${c.stroke}" stroke-width="2.5" stroke-linejoin="round"/>
      <path d="M52 44 q-14 -14 -32 -12 q6 10 16 14 q-12 0 -18 8 q12 6 26 2 Z" fill="${c.accent}" stroke="${c.stroke}" stroke-width="2.5" stroke-linejoin="round"/>
      <path d="M46 46 q10 -4 18 2 q8 6 6 18 q-2 12 -14 16 q-12 -4 -14 -16 q-2 -14 4 -20 Z" fill="${c.fill}" stroke="${c.stroke}" stroke-width="2.5" stroke-linejoin="round"/>
      <path d="M62 34 q8 -8 4 -16 q-8 4 -10 12" fill="${c.accent}" stroke="${c.stroke}" stroke-width="2.5" stroke-linejoin="round"/>
      <circle cx="62" cy="30" r="6" fill="${c.fill}" stroke="${c.stroke}" stroke-width="2.5"/>
      <path d="M67 29 l8 3 l-8 3 Z" fill="${c.stroke}"/>
      <circle cx="63.5" cy="28.5" r="1.6" fill="${c.stroke}"/>
      <path d="M44 78 q12 8 22 2 q-4 10 -12 12 q-8 -4 -10 -14 Z" fill="${c.accent}" stroke="${c.stroke}" stroke-width="2.5" stroke-linejoin="round"/>`,

    // 重骑士 Knight：粗壮马头，鬃毛厚重
    N: (c) => `
      <path d="M34 84 q-4 -26 8 -40 q8 -10 6 -20 q10 2 14 10 q14 4 18 20 q3 14 -2 30 Z" fill="${c.fill}" stroke="${c.stroke}" stroke-width="2.5" stroke-linejoin="round"/>
      <path d="M48 24 q-3 -10 2 -14 q4 6 4 12 Z" fill="${c.accent}" stroke="${c.stroke}" stroke-width="2.5" stroke-linejoin="round"/>
      <path d="M60 26 q0 -10 5 -12 q2 7 -1 13 Z" fill="${c.accent}" stroke="${c.stroke}" stroke-width="2.5" stroke-linejoin="round"/>
      <path d="M62 40 q12 4 14 14 q-8 2 -14 -4 Z" fill="${c.accent}" stroke="${c.stroke}" stroke-width="2.5" stroke-linejoin="round"/>
      <path d="M44 42 q-10 6 -8 18 q6 -2 10 -8" fill="${c.accent}" stroke="${c.stroke}" stroke-width="2.5" stroke-linejoin="round"/>
      <circle cx="56" cy="38" r="2.6" fill="${c.stroke}"/>
      <path d="M64 48 q6 1 8 4" stroke="${c.stroke}" stroke-width="2" fill="none" stroke-linecap="round"/>
      <path d="M30 84 h44 l2 6 H28 Z" fill="${c.fill}" stroke="${c.stroke}" stroke-width="2.5" stroke-linejoin="round"/>`,

    // 轻骑士 Hussar：修长马头，鬃毛飘逸
    H: (c) => `
      <path d="M38 84 q-2 -24 8 -36 q7 -9 4 -20 q11 4 14 12 q12 6 14 20 q2 12 -2 24 Z" fill="${c.fill}" stroke="${c.stroke}" stroke-width="2.5" stroke-linejoin="round"/>
      <path d="M50 26 q-2 -11 3 -14 q3 7 2 13 Z" fill="${c.accent}" stroke="${c.stroke}" stroke-width="2.5" stroke-linejoin="round"/>
      <path d="M62 28 q1 -10 6 -11 q1 7 -2 12 Z" fill="${c.accent}" stroke="${c.stroke}" stroke-width="2.5" stroke-linejoin="round"/>
      <path d="M46 36 q-14 10 -12 26 q8 -4 12 -12 q-4 10 0 16 q8 -8 8 -20" fill="${c.accent}" stroke="${c.stroke}" stroke-width="2.5" stroke-linejoin="round"/>
      <circle cx="58" cy="40" r="2.4" fill="${c.stroke}"/>
      <path d="M66 44 q8 4 8 12 q-6 0 -10 -6 Z" fill="${c.accent}" stroke="${c.stroke}" stroke-width="2.5" stroke-linejoin="round"/>
      <path d="M34 84 h40 l2 6 H32 Z" fill="${c.fill}" stroke="${c.stroke}" stroke-width="2.5" stroke-linejoin="round"/>`,

    // 王城 Throne：多塔城堡，中央拱门
    TH: (c) => `
      <path d="M18 44 h12 v-8 h4 v8 h6 V32 h4 v12 h6 v-8 h4 v8 h12 v-8 h4 v8 h12 v10 H14 V44 Z" fill="${c.fill}" stroke="${c.stroke}" stroke-width="2.5" stroke-linejoin="round"/>
      <path d="M50 18 l5 10 h-10 Z" fill="${c.accent}" stroke="${c.stroke}" stroke-width="2.5" stroke-linejoin="round"/>
      <path d="M20 54 h60 l3 30 H17 Z" fill="${c.fill}" stroke="${c.stroke}" stroke-width="2.5" stroke-linejoin="round"/>
      <path d="M42 84 v-16 q8 -10 16 0 v16 Z" fill="${c.stroke}" opacity="0.6"/>
      <path d="M26 62 h8 v10 h-8 Z M66 62 h8 v10 h-8 Z" fill="${c.stroke}" opacity="0.45"/>
      <path d="M12 84 h76 l3 8 H9 Z" fill="${c.fill}" stroke="${c.stroke}" stroke-width="2.5" stroke-linejoin="round"/>`,

    // 大将 Ares：羽冠头盔 + 桂冠
    A: (c) => `
      <path d="M50 20 q-4 -8 2 -12 q10 4 14 14 q4 10 0 18 q-6 -8 -16 -20 Z" fill="${c.accent}" stroke="${c.stroke}" stroke-width="2.5" stroke-linejoin="round"/>
      <path d="M34 46 q0 -20 18 -22 q18 2 18 22 q0 6 -4 8 H38 q-4 -2 -4 -8 Z" fill="${c.fill}" stroke="${c.stroke}" stroke-width="2.5" stroke-linejoin="round"/>
      <path d="M38 54 h28 l-2 12 q-12 6 -24 0 Z" fill="${c.fill}" stroke="${c.stroke}" stroke-width="2.5" stroke-linejoin="round"/>
      <path d="M42 44 h20" stroke="${c.stroke}" stroke-width="2.5" stroke-linecap="round"/>
      <path d="M52 30 v14" stroke="${c.stroke}" stroke-width="2" stroke-linecap="round"/>
      <path d="M24 60 q-6 14 6 24 q10 6 22 4" fill="none" stroke="${c.stroke}" stroke-width="3" stroke-linecap="round"/>
      <path d="M80 60 q6 14 -6 24 q-10 6 -22 4" fill="none" stroke="${c.stroke}" stroke-width="3" stroke-linecap="round"/>
      <path d="M26 66 q-5 3 -4 8 q5 -1 6 -7 Z M32 78 q-5 2 -5 7 q5 0 7 -6 Z" fill="${c.fill}" stroke="${c.stroke}" stroke-width="1.8" stroke-linejoin="round"/>
      <path d="M78 66 q5 3 4 8 q-5 -1 -6 -7 Z M72 78 q5 2 5 7 q-5 0 -7 -6 Z" fill="${c.fill}" stroke="${c.stroke}" stroke-width="1.8" stroke-linejoin="round"/>`,

    // 弩车 Ballista：木架弩炮
    B: (c) => `
      <path d="M24 62 L74 34" stroke="${c.stroke}" stroke-width="4" stroke-linecap="round"/>
      <path d="M40 26 q16 8 22 26" fill="none" stroke="${c.stroke}" stroke-width="3.5" stroke-linecap="round"/>
      <path d="M40 26 q22 4 22 26" fill="none" stroke="${c.fill}" stroke-width="2" stroke-linecap="round"/>
      <path d="M40 26 L62 52" stroke="${c.stroke}" stroke-width="1.8"/>
      <path d="M46 40 l22 -6 l-4 8 Z" fill="${c.accent}" stroke="${c.stroke}" stroke-width="2" stroke-linejoin="round"/>
      <path d="M30 56 l14 -8 l6 10 l-14 8 Z" fill="${c.fill}" stroke="${c.stroke}" stroke-width="2.5" stroke-linejoin="round"/>
      <path d="M28 60 L20 84 M40 54 L44 84" stroke="${c.stroke}" stroke-width="3.5" stroke-linecap="round"/>
      <path d="M24 72 L42 68" stroke="${c.stroke}" stroke-width="2.5" stroke-linecap="round"/>
      <path d="M14 84 h40 l2 6 H12 Z" fill="${c.fill}" stroke="${c.stroke}" stroke-width="2.5" stroke-linejoin="round"/>
      <circle cx="62" cy="76" r="8" fill="none" stroke="${c.stroke}" stroke-width="3"/>
      <path d="M62 68 v16 M54 76 h16" stroke="${c.stroke}" stroke-width="1.8"/>`,

    // 炮塔 Turret：轮式火炮
    T: (c) => `
      <path d="M34 48 l38 -10 q6 -1 6 5 q0 6 -6 6 l-36 8 Z" fill="${c.fill}" stroke="${c.stroke}" stroke-width="2.5" stroke-linejoin="round"/>
      <path d="M72 38 q7 -1 7 5 q0 6 -7 6" fill="${c.accent}" stroke="${c.stroke}" stroke-width="2.5" stroke-linejoin="round"/>
      <path d="M30 46 q-8 2 -8 10 q0 8 10 8 l6 -2 l-2 -18 Z" fill="${c.fill}" stroke="${c.stroke}" stroke-width="2.5" stroke-linejoin="round"/>
      <path d="M26 62 l10 16 M38 58 l6 20" stroke="${c.stroke}" stroke-width="3.5" stroke-linecap="round"/>
      <circle cx="38" cy="76" r="12" fill="none" stroke="${c.stroke}" stroke-width="3.5"/>
      <circle cx="38" cy="76" r="4" fill="${c.fill}" stroke="${c.stroke}" stroke-width="2"/>
      <path d="M38 64 v24 M26 76 h24 M30 68 l16 16 M46 68 l-16 16" stroke="${c.stroke}" stroke-width="1.8"/>
      <path d="M18 84 h44 l2 6 H16 Z" fill="${c.fill}" stroke="${c.stroke}" stroke-width="2.5" stroke-linejoin="round"/>`,

    // 剑士 Swordsman：持剑武士 + 背旗
    S: (c) => `
      <path d="M70 20 L70 74" stroke="${c.stroke}" stroke-width="3.5" stroke-linecap="round"/>
      <path d="M70 22 q12 2 14 12 q-9 2 -14 -4 Z" fill="${c.accent}" stroke="${c.stroke}" stroke-width="2.5" stroke-linejoin="round"/>
      <path d="M44 32 q-3 -10 5 -13 q9 -2 10 6 q1 6 -4 8 Z" fill="${c.fill}" stroke="${c.stroke}" stroke-width="2.5" stroke-linejoin="round"/>
      <path d="M46 24 q6 -6 12 -1" stroke="${c.stroke}" stroke-width="2" fill="none" stroke-linecap="round"/>
      <path d="M38 38 q12 -6 22 0 q7 4 6 14 l2 32 q-18 6 -36 0 l4 -32 q-1 -10 2 -14 Z" fill="${c.fill}" stroke="${c.stroke}" stroke-width="2.5" stroke-linejoin="round"/>
      <path d="M30 60 L30 44 L36 42" stroke="${c.stroke}" stroke-width="3" fill="none" stroke-linecap="round"/>
      <path d="M30 44 L30 78" stroke="${c.stroke}" stroke-width="3" stroke-linecap="round"/>
      <path d="M26 48 h8 M27 52 h6" stroke="${c.stroke}" stroke-width="2" stroke-linecap="round"/>
      <path d="M44 54 q8 -3 16 0" stroke="${c.stroke}" stroke-width="2" fill="none" stroke-linecap="round"/>
      <path d="M28 84 h42 l2 6 H26 Z" fill="${c.fill}" stroke="${c.stroke}" stroke-width="2.5" stroke-linejoin="round"/>`,
  };

  // 记谱字母 -> 中文名（备用，做 tooltip / 无障碍标签用）
  const PIECE_NAMES = {
    "": "兵", R: "攻城塔", C: "战车", P: "凤凰", N: "重骑士",
    H: "轻骑士", TH: "王城", A: "大将", B: "弩车", T: "炮塔", S: "剑士",
  };

  // 左右对称、不需要镜像的棋子（本身就是正面对称造型）
  const SYMMETRIC = new Set(["TH", "A", "R"]);

  /**
   * 生成一枚棋子的 SVG 字符串。
   * @param {string} notation 记谱字母（兵是空字符串）
   * @param {string} side "black" | "white"
   * @param {object} opts { col: 列索引0~10, size: 像素尺寸 }
   */
  function pieceSVG(notation, side, opts) {
    opts = opts || {};
    const c = COLORS[side] || COLORS.black;
    const shapeFn = SHAPES[notation];
    if (!shapeFn) return "";

    // 棋盘右半边（g~k，索引6~10）的棋子水平镜像，让两侧都朝向中线；
    // f列（索引5）居中不镜像，左右对称的棋子也不需要镜像
    const col = typeof opts.col === "number" ? opts.col : 0;
    const shouldMirror = col >= 6 && !SYMMETRIC.has(notation);
    const transform = shouldMirror ? ' transform="translate(100,0) scale(-1,1)"' : "";

    const size = opts.size || 100;
    const label = PIECE_NAMES[notation] || "";

    return `<svg viewBox="0 0 100 100" width="${size}" height="${size}" role="img" aria-label="${side === "black" ? "黑方" : "白方"}${label}" xmlns="http://www.w3.org/2000/svg"><g${transform}>${shapeFn(c)}</g></svg>`;
  }

  /** 生成一枚棋子的 Image 对象（供 Canvas 绘制使用） */
  function pieceImage(notation, side, opts) {
    const svg = pieceSVG(notation, side, opts);
    const img = new Image();
    img.src = "data:image/svg+xml;charset=utf-8," + encodeURIComponent(svg);
    return img;
  }

  global.ArkatanaPieces = { pieceSVG, pieceImage, PIECE_NAMES, COLORS };
})(window);
