/*
 * pieces.js
 * =========
 * Arkatana（古戰棋）— 棋子素材加载与映射（低多边形版）
 *
 * 素材是带透明背景的独立造型 PNG，放在 frontend/pieces/ 下，
 * 命名规则：{side}_{piece}.png
 *   side  : black / white
 *   piece : pawn rook chariot phoenix knight hussar throne ares ballista turret swordsman
 *
 * 朝向：每种棋子每个阵营**只需要一个朝向**的素材（默认朝右），
 * 棋盘右半边（g~l 列）的棋子由代码水平镜像，让两侧都朝向棋盘中线，
 * 跟实体棋的摆法一致。左右对称的棋子（王城、大将、兵）不参与镜像。
 *
 * 注意列字母已改为 abcdefghjkl（跳过 i，避免与 j 混淆）。
 */

(function (global) {
  "use strict";

  const COLS = "abcdefghjkl";
  const BASE = "pieces/";

  // 记谱字母 -> 素材名里的棋子名
  const PIECE_FILES = {
    "":   "pawn",
    R:    "rook",
    C:    "chariot",
    P:    "phoenix",
    N:    "knight",
    H:    "hussar",
    TH:   "throne",
    A:    "ares",
    B:    "ballista",
    T:    "turret",
    S:    "swordsman",
  };

  const ALL_PIECES = ["pawn", "rook", "chariot", "phoenix", "knight", "hussar",
                      "throne", "ares", "ballista", "turret", "swordsman"];

  // 造型本身左右对称、镜像了也看不出区别的棋子，不做翻转
  // （兵目前是单一朝向的通用造型，先归入这一类；
  //   以后如果补了左右手持械的分版，把 "" 从这里移出去即可）
  const SYMMETRIC = new Set(["TH", "A", ""]);

  /** 这枚棋子该用哪张素材 */
  function pieceImagePath(notation, side) {
    const piece = PIECE_FILES[notation];
    if (piece === undefined) return null;
    return `${BASE}${side}_${piece}.png`;
  }

  /** 这枚棋子在这个位置该不该水平镜像（让它朝向棋盘中线） */
  function shouldMirror(notation, square) {
    if (SYMMETRIC.has(notation)) return false;
    if (!square) return false;
    return COLS.indexOf(square[0]) >= 6;   // g 及其右侧
  }

  // ---------------------------------------------------------------
  // 素材预加载：Canvas 必须等图片加载完才能画，
  // 所以进对局前先把素材全部 load 一遍，避免棋盘一片空白。
  // 缺失的素材不会阻塞流程，绘制时会自动退回占位画法。
  // ---------------------------------------------------------------
  const cache = {};
  let readyPromise = null;

  function preloadAll() {
    if (readyPromise) return readyPromise;
    const paths = [];
    ["black", "white"].forEach((side) => {
      ALL_PIECES.forEach((p) => paths.push(`${BASE}${side}_${p}.png`));
    });
    readyPromise = Promise.all(paths.map((path) => new Promise((resolve) => {
      const img = new Image();
      img.onload = () => { cache[path] = img; resolve(); };
      img.onerror = () => resolve();   // 素材还没画好很正常，不报错
      img.src = path;
    })));
    return readyPromise;
  }

  /** 取已加载好的素材；没有则返回 null（调用方退回占位画法） */
  function getImage(notation, side) {
    const path = pieceImagePath(notation, side);
    return path ? (cache[path] || null) : null;
  }

  /** 已经成功加载了多少素材（用于开发期确认素材齐备度） */
  function loadedCount() {
    return Object.keys(cache).length;
  }

  global.ArkatanaPieces = { pieceImagePath, shouldMirror, preloadAll, getImage, loadedCount };
})(window);
