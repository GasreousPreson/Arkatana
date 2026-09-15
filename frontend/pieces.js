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
 * 按棋子在**屏幕上的显示位置**选版本（左半屏用 _l、右半屏用 _r），
 * 这样不管黑方视角还是白方视角，棋子看上去都朝向棋盘中心。
 * 王城、大将、攻城塔、兵只有单一造型，不带 _l/_r 后缀。
 *
 * 注意列字母已改为 abcdefghjkl（跳过 i，避免与 j 混淆）。
 */

(function (global) {
  "use strict";

  const COLS = "abcdefghjkl";
  // 素材目录。默认 "pieces/"（相对于页面所在目录），跟以前完全一样。
  // 页面可以在加载本脚本**之前**设置 window.ARKATANA_PIECES_BASE 来覆盖它——
  // ai/explorer.html 那个本地查看器不在 frontend/ 目录下，需要指到
  // "../frontend/pieces/" 才能找到素材，就是靠这个。
  const BASE = (global.ARKATANA_PIECES_BASE || "pieces/");

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

  // 各棋子相对格子的显示比例（1.0 = 正好一格）。
  // 造型繁简不同，统一比例会显得有的臃肿有的干瘪，这里逐个微调。
  const PIECE_SCALE = {
    TH: 1.02,   // 王城：撑到接近格子边界
    A:  0.99,   // 大将
    C:  0.99,   // 战车
    N:  0.95,   // 重骑
    H:  0.95,   // 轻骑
    P:  0.95,   // 凤凰
    S:  0.82,   // 剑士：略微缩小
  };
  const DEFAULT_SCALE = 0.9;

  /** 这枚棋子该按多大比例画（相对格子边长） */
  function pieceScale(notation) {
    return PIECE_SCALE[notation] !== undefined ? PIECE_SCALE[notation] : DEFAULT_SCALE;
  }

  // 有左右两版素材的棋子（素材本身就画好了朝向，代码不做镜像）
  const VARIANT_PIECES = ["ballista", "chariot", "hussar", "knight",
                          "phoenix", "swordsman", "turret"];

  // 能升变的棋子：战车/剑士/炮塔（有左右两版）+ 兵（单一朝向）——
  // 升变后瞬间切换成专门画的"升变造型"素材，不再用旧版的金边叠加效果。
  const PROMOTABLE = new Set(["T", "S", "C", ""]);

  /**
   * 这枚棋子该用哪张素材。
   *
   * 关键：左右两版按**棋子在屏幕上的显示位置**来选，不是按逻辑列。
   * 实体棋平躺在盘上、图案朝向中线，从任何一侧看过去它都朝向
   * "你眼中的棋盘中心"——所以翻转视角后，原本在左边的 a 列跑到右边，
   * 它就该换成右版才对。按逻辑列选会导致白方视角下棋子集体朝外。
   *
   * @param {boolean} flipped 当前是否是白方视角（棋盘左右上下翻转）
   */
  function pieceImagePath(notation, side, square, flipped, stickyVariant, promoted) {
    const piece = PIECE_FILES[notation];
    if (piece === undefined) return null;
    const suffix = (promoted && PROMOTABLE.has(notation)) ? "_promoted" : "";
    if (NO_VARIANT.has(notation)) return `${BASE}${side}_${piece}${suffix}.png`;
    return `${BASE}${side}_${piece}${suffix}_${resolveVariant(square, flipped, stickyVariant)}.png`;
  }

  /**
   * 决定用左版还是右版：
   *   - 显示位置在左半屏 -> 左版；右半屏 -> 右版（永远朝向屏幕中心）
   *   - **正好在正中的 f 线**：不立即翻面，沿用它原来的朝向（stickyVariant）；
   *     没有历史朝向可继承时才退回左版。这样棋子路过中线不会突兀地翻一下。
   */
  function resolveVariant(square, flipped, stickyVariant) {
    const colIndex = square ? COLS.indexOf(square[0]) : 0;
    const displayCol = flipped ? (COLS.length - 1 - colIndex) : colIndex;
    if (displayCol === 5) return stickyVariant || "l";   // 正中一列，保持原朝向
    return displayCol >= 6 ? "r" : "l";
  }

  // ---------------------------------------------------------------
  // 素材预加载
  //
  // 2026-09 性能改版（针对"棋子图案显示得太慢"）：
  //
  // 改版前是一次性 Promise.all 把全部 50 张素材一起加载，全部完成之后
  // 才回调重绘一次。两个问题：
  //   1. 14 张"升变造型"占了总体积的一多半，可是**开局盘面上一枚升变棋子
  //      都没有**——它们要等到有兵走到底线才用得上，却在首屏就跟普通造型
  //      抢带宽、抢浏览器那六个并发连接位。
  //   2. Promise.all 是"全有或全无"：哪怕 49 张早就到了，只要最后一张还在
  //      路上，棋盘就一直维持在没有造型的状态。
  //
  // 现在改成两级 + 逐张回调：
  //   第一级 essential：普通造型（开局盘面上真正会出现的那些）。
  //   第二级 deferred ：升变造型，等第一级全部落地之后才开始下载，
  //                     不跟首屏抢资源。
  //   每加载完**一张**就通知一次调用方（notifyProgress），页面可以立刻把
  //   这一枚棋子画出来，不必等整批——所以棋盘是逐渐"长"出来的，
  //   而不是卡着不动然后突然全部出现。
  // ---------------------------------------------------------------
  const cache = {};
  let readyPromise = null;
  const progressListeners = [];

  /** 注册"又有一张素材到位了"的回调。页面拿它触发重绘即可。 */
  function onProgress(fn) {
    if (typeof fn === "function") progressListeners.push(fn);
  }

  function notifyProgress() {
    progressListeners.forEach((fn) => {
      try { fn(); } catch (e) { /* 某个页面的重绘出错不该拖垮素材加载 */ }
    });
  }

  function essentialPaths() {
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
    return paths;
  }

  function deferredPaths() {
    const paths = [];
    ["black", "white"].forEach((side) => {
      ["chariot", "swordsman", "turret"].forEach((p) => {
        paths.push(`${BASE}${side}_${p}_promoted_l.png`);
        paths.push(`${BASE}${side}_${p}_promoted_r.png`);
      });
      paths.push(`${BASE}${side}_pawn_promoted.png`);
    });
    return paths;
  }

  function loadOne(path) {
    return new Promise((resolve) => {
      if (cache[path]) { resolve(); return; }
      const img = new Image();
      img.onload = () => {
        cache[path] = img;
        notifyProgress();   // 每到一张就通知，页面可以马上把这枚棋子画上
        resolve();
      };
      img.onerror = () => resolve();   // 素材缺失不阻塞整批
      img.src = path;
    });
  }

  function loadBatch(paths) {
    return Promise.all(paths.map(loadOne));
  }

  /**
   * 预加载全部素材。返回的 Promise 在**第一级（普通造型）**加载完就 resolve——
   * 调用方等到的是"开局盘面能完整画出来"这个时刻，而不是"连升变造型都齐了"。
   * 升变造型在后台继续下载，到位时同样会通过 onProgress 通知。
   */
  function preloadAll() {
    if (readyPromise) return readyPromise;
    readyPromise = loadBatch(essentialPaths()).then((result) => {
      // 不 await 第二级：让它在后台慢慢下，不拖住首屏。
      // 升变棋子真正出现在盘上通常是几十步之后的事，那时候早就下完了；
      // 万一真的赶在下完之前就升变了，getImage 会返回 null，
      // 调用方（learn.html 里那套按需补加载）会单独把这一张拉下来。
      loadBatch(deferredPaths());
      return result;
    });
    return readyPromise;
  }

  /** 只等普通造型（等价于 preloadAll，保留成独立名字让意图更清楚） */
  function preloadEssential() {
    return preloadAll();
  }

  /** 取已加载好的素材；没有则返回 null（调用方退回占位画法） */
  function getImage(notation, side, square, flipped, stickyVariant, promoted) {
    const path = pieceImagePath(notation, side, square, flipped, stickyVariant, promoted);
    return path ? (cache[path] || null) : null;
  }

  /** 已经成功加载了多少素材（用于开发期确认素材齐备度） */
  function loadedCount() {
    return Object.keys(cache).length;
  }

  global.ArkatanaPieces = { pieceImagePath, resolveVariant, pieceScale, preloadAll,
                            preloadEssential, onProgress, getImage, loadedCount };
})(window);
