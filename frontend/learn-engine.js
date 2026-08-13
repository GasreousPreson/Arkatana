/*
 * learn-engine.js
 * ===============
 * Learn 模块专用的走法引擎——只需要支持"棋盘上唯一一颗会动的棋子"，
 * 所以不需要完整搬运后端的规则引擎（合法性过滤、杀城判断等），
 * 只需要精确复刻每种棋子的几何走法本身。
 *
 * 每种棋子的实现都是对照真实的 movement.py / pieces.py 逐一核对过的，
 * 不是凭印象写的——这点很重要，因为走法错一点，练习题就会显得"打不动"
 * 或者"明明该赢却输了"。
 *
 * 坐标系统：列 a~l（跳过i），行 1~12，和正式对局完全一致。
 */
(function (global) {
  "use strict";

  const COLS = "abcdefghjkl";

  function parseCoord(sq) {
    const col = COLS.indexOf(sq[0]);
    const row = parseInt(sq.slice(1), 10);
    return [col, row];
  }
  function coordToStr(col, row) {
    return COLS[col] + row;
  }
  function isValid(col, row) {
    return col >= 0 && col < 11 && row >= 1 && row <= 12;
  }

  const DIAGONAL_DIRS = [[1, 1], [1, -1], [-1, 1], [-1, -1]];
  const STRAIGHT_DIRS = [[0, 1], [0, -1], [1, 0], [-1, 0]];
  const EIGHT_DIRS = DIAGONAL_DIRS.concat(STRAIGHT_DIRS);

  /**
   * board: 普通对象 { "d5": {notation, side, promoted}, ... }
   * side: 走这颗棋子的一方标识（字符串，任意值，只要跟"敌方"标识不同即可——
   *       Learn 模块里 star 固定标成敌方，不需要真的区分黑白）
   */
  function isEmpty(board, sq) { return board[sq] === undefined; }
  function occupant(board, sq) { return board[sq]; }

  // ---- 通用走法生成工具（对照 movement.py 逐一复刻）----

  function rangedMoves(board, origin, directions, maxRange) {
    const [ox, oy] = origin;
    const dests = [];
    for (const [dx, dy] of directions) {
      for (let dist = 1; dist <= maxRange; dist++) {
        const col = ox + dx * dist, row = oy + dy * dist;
        if (!isValid(col, row)) break;
        const sq = coordToStr(col, row);
        if (isEmpty(board, sq)) dests.push(sq);
      }
    }
    return dests;
  }

  function directCaptureTargets(board, origin, side, directions, distance) {
    distance = distance || 1;
    const [ox, oy] = origin;
    const dests = [];
    for (const [dx, dy] of directions) {
      const col = ox + dx * distance, row = oy + dy * distance;
      if (!isValid(col, row)) continue;
      const sq = coordToStr(col, row);
      const occ = occupant(board, sq);
      if (occ && occ.side !== side) dests.push(sq);
    }
    return dests;
  }

  function screenCaptureTargets(board, origin, side, directions, maxRange) {
    const [ox, oy] = origin;
    const dests = [];
    for (const [dx, dy] of directions) {
      let screenFound = false;
      for (let dist = 1; dist <= maxRange; dist++) {
        const col = ox + dx * dist, row = oy + dy * dist;
        if (!isValid(col, row)) break;
        const sq = coordToStr(col, row);
        const occ = occupant(board, sq);
        if (!occ) continue;
        if (!screenFound) { screenFound = true; continue; }
        if (occ.side !== side) dests.push(sq);
      }
    }
    return dests;
  }

  function leapTargets(board, origin, side, offsets) {
    const [ox, oy] = origin;
    const moveTargets = [], captureTargets = [];
    for (const [dx, dy] of offsets) {
      const col = ox + dx, row = oy + dy;
      if (!isValid(col, row)) continue;
      const sq = coordToStr(col, row);
      const occ = occupant(board, sq);
      if (!occ) moveTargets.push(sq);
      else if (occ.side !== side) captureTargets.push(sq);
    }
    return [moveTargets, captureTargets];
  }

  function slidingMoves(board, origin, directions, side, maxRange) {
    const [ox, oy] = origin;
    const moveTargets = [], captureTargets = [];
    for (const [dx, dy] of directions) {
      let dist = 0;
      while (true) {
        dist += 1;
        if (maxRange !== undefined && dist > maxRange) break;
        const col = ox + dx * dist, row = oy + dy * dist;
        if (!isValid(col, row)) break;
        const sq = coordToStr(col, row);
        const occ = occupant(board, sq);
        if (!occ) { moveTargets.push(sq); }
        else { if (occ.side !== side) captureTargets.push(sq); break; }
      }
    }
    return [moveTargets, captureTargets];
  }

  const HUSSAR_OFFSETS = [
    [3, 1], [3, -1], [-3, 1], [-3, -1],
    [1, 3], [1, -3], [-1, 3], [-1, -3],
    [3, 0], [-3, 0], [0, 3], [0, -3],
  ];
  const KNIGHT_OFFSETS = [
    [2, 1], [2, -1], [-2, 1], [-2, -1],
    [1, 2], [1, -2], [-1, 2], [-1, -2],
    [3, 1], [3, -1], [-3, 1], [-3, -1],
    [1, 3], [1, -3], [-1, 3], [-1, -3],
  ];

  /**
   * 计算一颗棋子当前的合法走法。
   * @param board 棋盘对象
   * @param sq 棋子当前坐标（字符串，如 "d5"）
   * @param notation 记谱代号：""=兵 R=塔 P=凤凰 N=重骑 H=轻骑 C=战车 A=大将 S=剑士 B=弩 T=炮塔
   * @param side 这颗棋子的阵营标识
   * @param promoted 是否已升变
   * @param forward 前进方向：+1 或 -1（黑方向上、白方向下，跟正式对局一致）
   * @param hasMoved 是否已经走过（兵的双步特权、炮塔首步横向5格特权要用）
   * @returns {moves: string[], captures: string[]}  可移动的空格 / 可吃子的目标格
   */
  function legalMoves(board, sq, notation, side, promoted, forward, hasMoved) {
    const origin = parseCoord(sq);
    const fwd = forward || 1;
    const moves = [], captures = [];

    if (notation === "") {
      // 兵
      if (promoted) {
        const dirs = [[0, fwd], [1, fwd], [-1, fwd], [1, 0], [-1, 0]];
        for (const [dx, dy] of dirs) {
          const col = origin[0] + dx, row = origin[1] + dy;
          if (!isValid(col, row)) continue;
          const dsq = coordToStr(col, row);
          const occ = occupant(board, dsq);
          if (!occ) moves.push(dsq);
          else if (occ.side !== side) captures.push(dsq);
        }
        return { moves, captures };
      }
      const oneStep = [origin[0], origin[1] + fwd];
      if (!isValid(...oneStep)) return { moves, captures };
      const oneSq = coordToStr(...oneStep);
      const occ1 = occupant(board, oneSq);
      if (!occ1) {
        moves.push(oneSq);
        if (!hasMoved) {
          const twoStep = [origin[0], origin[1] + fwd * 2];
          if (isValid(...twoStep)) {
            const twoSq = coordToStr(...twoStep);
            if (isEmpty(board, twoSq)) moves.push(twoSq);
          }
        }
      } else if (occ1.side !== side) {
        captures.push(oneSq);
      }
      return { moves, captures };
    }

    if (notation === "B") {
      // 弩车
      const MAX_RANGE = 4;
      rangedMoves(board, origin, DIAGONAL_DIRS, MAX_RANGE).forEach((d) => moves.push(d));
      directCaptureTargets(board, origin, side, [[1, fwd], [-1, fwd]], 1).forEach((d) => captures.push(d));
      screenCaptureTargets(board, origin, side, DIAGONAL_DIRS, MAX_RANGE).forEach((d) => captures.push(d));
      return { moves, captures };
    }

    if (notation === "T") {
      // 炮塔
      const BASE_RANGE = 4, UPGRADED_RANGE = 5, FIRST_MOVE_H_RANGE = 5;
      const maxRange = promoted ? UPGRADED_RANGE : BASE_RANGE;
      const hRange = !hasMoved ? FIRST_MOVE_H_RANGE : maxRange;
      const vRange = maxRange;
      const H_DIRS = [[1, 0], [-1, 0]], V_DIRS = [[0, 1], [0, -1]];
      rangedMoves(board, origin, H_DIRS, hRange).forEach((d) => moves.push(d));
      rangedMoves(board, origin, V_DIRS, vRange).forEach((d) => moves.push(d));
      directCaptureTargets(board, origin, side, [[0, fwd]], 1).forEach((d) => captures.push(d));
      screenCaptureTargets(board, origin, side, H_DIRS, hRange).forEach((d) => captures.push(d));
      screenCaptureTargets(board, origin, side, V_DIRS, vRange).forEach((d) => captures.push(d));
      return { moves, captures };
    }

    if (notation === "A") {
      // 大将
      const MAX_RANGE = 2;
      rangedMoves(board, origin, EIGHT_DIRS, MAX_RANGE).forEach((d) => moves.push(d));
      for (const [dx, dy] of EIGHT_DIRS) {
        for (let dist = 1; dist <= MAX_RANGE; dist++) {
          const col = origin[0] + dx * dist, row = origin[1] + dy * dist;
          if (!isValid(col, row)) break;
          const dsq = coordToStr(col, row);
          const occ = occupant(board, dsq);
          if (occ && occ.side !== side) captures.push(dsq);
        }
      }
      return { moves, captures };
    }

    if (notation === "H") {
      const [m, c] = leapTargets(board, origin, side, HUSSAR_OFFSETS);
      return { moves: m, captures: c };
    }
    if (notation === "N") {
      const [m, c] = leapTargets(board, origin, side, KNIGHT_OFFSETS);
      return { moves: m, captures: c };
    }
    if (notation === "R") {
      const [m, c] = slidingMoves(board, origin, STRAIGHT_DIRS, side);
      return { moves: m, captures: c };
    }
    if (notation === "P") {
      const [m, c] = slidingMoves(board, origin, DIAGONAL_DIRS, side);
      return { moves: m, captures: c };
    }

    if (notation === "S") {
      // 剑士
      if (promoted) {
        for (const [dx, dy] of EIGHT_DIRS) {
          const col = origin[0] + dx * 2, row = origin[1] + dy * 2;
          if (!isValid(col, row)) continue;
          const dsq = coordToStr(col, row);
          const occ = occupant(board, dsq);
          if (!occ) moves.push(dsq);
          else if (occ.side !== side) captures.push(dsq);
        }
        for (const [dx, dy] of DIAGONAL_DIRS) {
          const col = origin[0] + dx, row = origin[1] + dy;
          if (!isValid(col, row)) continue;
          const dsq = coordToStr(col, row);
          if (isEmpty(board, dsq)) moves.push(dsq);
        }
        return { moves, captures };
      }
      const twoStepDirs = [[0, fwd], [1, fwd], [-1, fwd]];
      for (const [dx, dy] of twoStepDirs) {
        const col = origin[0] + dx * 2, row = origin[1] + dy * 2;
        if (!isValid(col, row)) continue;
        const dsq = coordToStr(col, row);
        const occ = occupant(board, dsq);
        if (!occ) moves.push(dsq);
        else if (occ.side !== side) captures.push(dsq);
      }
      const oneStepDiag = [[1, fwd], [-1, fwd]];
      for (const [dx, dy] of oneStepDiag) {
        const col = origin[0] + dx, row = origin[1] + dy;
        if (!isValid(col, row)) continue;
        const dsq = coordToStr(col, row);
        if (isEmpty(board, dsq)) moves.push(dsq);
      }
      return { moves, captures };
    }

    if (notation === "C") {
      // 战车
      const distances = promoted ? [2, 3, 4] : [2, 3];
      for (const [dx, dy] of STRAIGHT_DIRS) {
        for (const dist of distances) {
          const col = origin[0] + dx * dist, row = origin[1] + dy * dist;
          if (!isValid(col, row)) continue;
          const dsq = coordToStr(col, row);
          const occ = occupant(board, dsq);
          if (!occ) moves.push(dsq);
          else if (occ.side !== side) captures.push(dsq);
        }
      }
      return { moves, captures };
    }

    return { moves, captures };   // 未知记谱代号 / 王城（不可动）
  }

  global.LearnEngine = { parseCoord, coordToStr, isValid, legalMoves, COLS };
})(window);
