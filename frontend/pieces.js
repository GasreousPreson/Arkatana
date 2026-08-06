/*
 * pieces.js
 * =========
 * Arkatana（古戰棋）— 棋子素材加载与映射（低多边形版）
 *
 * 素材是带透明背景的独立造型 PNG，放在 frontend/pieces/ 下，
 * 命名规则：{side}_{piece}.png（王城/大将/攻城塔/兵）
 *           {side}_{piece}_l.png / _r.png（其余棋子的左版/右版）
 *   side  : black / white
 *   piece : pawn rook chariot phoenix knight hussar throne ares ballista turret swordsman
 *
 * 朝向：左右两版素材是实际画好的（不是代码翻转）——
 * a~f 列用左版 _l，g~l 列用右版 _r，让两侧棋子都朝向棋盘中线。
 * 王城、大将、攻城塔、兵只有单一造型，不带 _l/_r 后缀。
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

  // 只有单一造型、不分左右的棋子（王城、大将、攻城塔、兵）
  const NO_VARIANT = new Set(["TH", "A", "R", ""]);

  // 有左右两版素材的棋子（素材本身就画好了朝向，代码不做镜像）
  const VARIANT_PIECES = ["ballista", "chariot", "hussar", "knight",
                          "phoenix", "swordsman", "turret"];

  /**
   * 这枚棋子该用哪张素材。
   * 左右两版是实际画好的（不是代码翻转），a~f 列用左版，g~l 列用右版，
   * 这样两侧棋子都朝向棋盘中线，跟实体棋的摆法一致。
   */
  function pieceImagePath(notation, side, square) {
    const piece = PIECE_FILES[notation];
    if (piece === undefined) return null;
    if (NO_VARIANT.has(notation)) return `${BASE}${side}_${piece}.png`;
    const colIndex = square ? COLS.indexOf(square[0]) : 0;
    const variant = colIndex >= 6 ? "r" : "l";
    return `${BASE}${side}_${piece}_${variant}.png`;
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
      ["ares", "rook", "throne", "pawn"].forEach((p) => {
        paths.push(`${BASE}${side}_${p}.png`);
      });
      VARIANT_PIECES.forEach((p) => {
        paths.push(`${BASE}${side}_${p}_l.png`);
        paths.push(`${BASE}${side}_${p}_r.png`);
      });
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
  function getImage(notation, side, square) {
    const path = pieceImagePath(notation, side, square);
    return path ? (cache[path] || null) : null;
  }

  /** 已经成功加载了多少素材（用于开发期确认素材齐备度） */
  function loadedCount() {
    return Object.keys(cache).length;
  }

  global.ArkatanaPieces = { pieceImagePath, preloadAll, getImage, loadedCount };
})(window);
