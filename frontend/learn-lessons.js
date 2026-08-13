/*
 * learn-lessons.js
 * ================
 * 每一关的具体数据：棋盘摆位、目标棋子/绿格、提示箭头、过关条件。
 *
 * 这次先只填了 Fundamentals 章节（4关），其余章节陆续在后续几轮补上——
 * 播放器（learn.html）本身是完全数据驱动的，加新章节只需要往这个文件里
 * 加数据，不需要碰播放器代码。
 *
 * 棋盘数据格式：{ 坐标: {notation, side} }
 *   notation 是记谱代号（兵是空字符串 ""），side 是 "black"/"white"。
 *   star 用特殊 notation "STAR" 表示（走法引擎里当"敌方棋子"处理，
 *   所以任何一方都能吃掉它）。
 */
(function (global) {
  "use strict";

  const STANDARD_OPENING = {
    a1: {notation: "R", side: "black"},
    a12: {notation: "R", side: "white"},
    a4: {notation: "T", side: "black"},
    a5: {notation: "", side: "black"},
    a8: {notation: "", side: "white"},
    a9: {notation: "T", side: "white"},
    b1: {notation: "C", side: "black"},
    b12: {notation: "C", side: "white"},
    b5: {notation: "", side: "black"},
    b8: {notation: "", side: "white"},
    c1: {notation: "P", side: "black"},
    c11: {notation: "H", side: "white"},
    c12: {notation: "P", side: "white"},
    c2: {notation: "H", side: "black"},
    c5: {notation: "", side: "black"},
    c8: {notation: "", side: "white"},
    d1: {notation: "N", side: "black"},
    d12: {notation: "N", side: "white"},
    d4: {notation: "B", side: "black"},
    d5: {notation: "", side: "black"},
    d8: {notation: "", side: "white"},
    d9: {notation: "B", side: "white"},
    e1: {notation: "S", side: "black"},
    e12: {notation: "S", side: "white"},
    e5: {notation: "", side: "black"},
    e8: {notation: "", side: "white"},
    f1: {notation: "TH", side: "black"},
    f12: {notation: "TH", side: "white"},
    f4: {notation: "A", side: "black"},
    f5: {notation: "", side: "black"},
    f8: {notation: "", side: "white"},
    f9: {notation: "A", side: "white"},
    g1: {notation: "S", side: "black"},
    g12: {notation: "S", side: "white"},
    g5: {notation: "", side: "black"},
    g8: {notation: "", side: "white"},
    h1: {notation: "N", side: "black"},
    h12: {notation: "N", side: "white"},
    h4: {notation: "B", side: "black"},
    h5: {notation: "", side: "black"},
    h8: {notation: "", side: "white"},
    h9: {notation: "B", side: "white"},
    j1: {notation: "P", side: "black"},
    j11: {notation: "H", side: "white"},
    j12: {notation: "P", side: "white"},
    j2: {notation: "H", side: "black"},
    j5: {notation: "", side: "black"},
    j8: {notation: "", side: "white"},
    k1: {notation: "C", side: "black"},
    k12: {notation: "C", side: "white"},
    k5: {notation: "", side: "black"},
    k8: {notation: "", side: "white"},
    l1: {notation: "R", side: "black"},
    l12: {notation: "R", side: "white"},
    l4: {notation: "T", side: "black"},
    l5: {notation: "", side: "black"},
    l8: {notation: "", side: "white"},
    l9: {notation: "T", side: "white"},
  };

  // 第1关专用：只有黑方的开局摆位，白方要到第2关才出现
  const BLACK_ONLY_OPENING = {
    a1: {notation: "R", side: "black"},
    a4: {notation: "T", side: "black"},
    a5: {notation: "", side: "black"},
    b1: {notation: "C", side: "black"},
    b5: {notation: "", side: "black"},
    c1: {notation: "P", side: "black"},
    c2: {notation: "H", side: "black"},
    c5: {notation: "", side: "black"},
    d1: {notation: "N", side: "black"},
    d4: {notation: "B", side: "black"},
    d5: {notation: "", side: "black"},
    e1: {notation: "S", side: "black"},
    e5: {notation: "", side: "black"},
    f1: {notation: "TH", side: "black"},
    f4: {notation: "A", side: "black"},
    f5: {notation: "", side: "black"},
    g1: {notation: "S", side: "black"},
    g5: {notation: "", side: "black"},
    h1: {notation: "N", side: "black"},
    h4: {notation: "B", side: "black"},
    h5: {notation: "", side: "black"},
    j1: {notation: "P", side: "black"},
    j2: {notation: "H", side: "black"},
    j5: {notation: "", side: "black"},
    k1: {notation: "C", side: "black"},
    k5: {notation: "", side: "black"},
    l1: {notation: "R", side: "black"},
    l4: {notation: "T", side: "black"},
    l5: {notation: "", side: "black"},
  };

  function cloneBoard(b) {
    const out = {};
    for (const sq in b) out[sq] = Object.assign({}, b[sq]);
    return out;
  }

  global.LEARN_LESSONS = {

    fundamentals: [
      {
        level: 1,
        title: "Introduction",
        description: "this is the opening formation\nmove a piece to continue→",
        board: cloneBoard(BLACK_ONLY_OPENING),   // 第1关只显示黑方，白方留到第2关再登场
        movableSide: "black",
        hintArrows: [{ from: "g5", to: "g6" }],   // f6是软招（既不利于出子，又暴露弱点），改引导g6
        goal: { type: "any_move" },
      },
      {
        level: 2,
        title: "Introduction",
        description: "Here, black side makes the first move\nmove a piece to continue→",
        board: cloneBoard(STANDARD_OPENING),
        movableSide: "black",
        goal: { type: "any_move" },
      },
      {
        level: 3,
        title: "Introduction",
        description: "when some of your pieces reached their\npromoting zone, it will automatically\npromote and become stronger\nnow promote your pawn to continue→",
        // 这颗兵已经离开过第5排（走到了f7），首步双步特权已经用掉了
        board: { f7: { notation: "", side: "black", hasMoved: true } },
        movablePiece: "f7",
        movableSide: "black",
        promotionHighlightRows: [8, 9, 10, 11, 12],   // 视觉示意整个升变区域（7~12排的黑方一侧对应白方视角是1~6，这里固定用8~12+当前排）
        hintArrows: [{ from: "f7", to: "f8" }],
        goal: { type: "reach_square", to: "f8" },
      },
      {
        level: 4,
        title: "Introduction",
        description: "The \u201cThrone\u201d is the most important piece\nit's unmovable and have no capability to\ncapture since it's a structure\nonce the throne been captured,\nbattle ended\nnow destory white's throne\nwith your pawn!",
        board: {
          f10: { notation: "", side: "black", promoted: true },   // 已经是升变过的铜兵
          f12: { notation: "TH", side: "white" },
          f1: { notation: "TH", side: "black" },
        },
        movablePiece: "f10",
        movableSide: "black",
        hintArrows: [{ from: "f10", to: "f11" }],
        goal: { type: "check_throne" },   // 将死即通关，不需要真的把王城从棋盘上吃掉
      },
    ],

    pawn: [
      {
        level: 1,
        title: "Pawn",
        description: "pawn moves to the square directly in front\nof them before promotion,\nthey capture  the same way\nnow grab the star to continue→",
        board: {
          e5: { notation: "", side: "black" },
          e7: { notation: "STAR", side: "white" },
        },
        movablePiece: "e5",
        movableSide: "black",
        hintArrows: [{ from: "e5", to: "e6" }, { from: "e6", to: "e7" }],
        goal: { type: "capture_all_stars" },
      },
      {
        level: 2,
        title: "Pawn",
        description: "pawn can move two squares at once of it's\nfrist move, but they can't capture in\nthe same way\ngrab all star to continue→",
        board: {
          d5: { notation: "", side: "black" },
          h5: { notation: "", side: "black" },
          d7: { notation: "STAR", side: "white" },
          h8: { notation: "STAR", side: "white" },
        },
        movableSide: "black",   // 两颗兵都要走，所以不锁定单一棋子
        hintArrows: [
          { from: "d5", to: "d6" }, { from: "d6", to: "d7" },
          { from: "h5", to: "h7" }, { from: "h7", to: "h8" },
        ],
        goal: { type: "capture_all_stars" },
      },
      {
        level: 3,
        title: "Pawn",
        description: "pawn will be promoted after reached\n8th row, the promoted pawn moves in\n5 direction,  and cannot move backwards\ngrab all star to continue→",
        board: {
          f5: { notation: "", side: "black" },
          g9: { notation: "STAR", side: "white" },
          h9: { notation: "STAR", side: "white" },
          h10: { notation: "STAR", side: "white" },
        },
        movablePiece: "f5",
        movableSide: "black",
        hintArrows: [
          { from: "f5", to: "f7" }, { from: "f7", to: "f8" },
          { from: "f8", to: "g9" }, { from: "g9", to: "h9" }, { from: "h9", to: "h10" },
        ],
        goal: { type: "capture_all_stars" },
        // 未吃完第9排的星星就跑到第10排，就走岔了（升变兵不能后退，回不去了）
        wrongMoveCheck: ({ to, stars }) => {
          const row = parseInt(to.slice(1), 10);
          const row9StarsLeft = stars.some((s) => parseInt(s.slice(1), 10) === 9);
          if (row >= 10 && row9StarsLeft) return true;
          return false;
        },
      },
      {
        level: 4,
        title: "Pawn",
        description: "try to remember the moves",
        board: {
          f5: { notation: "", side: "black" },
          f7: { notation: "STAR", side: "white" },
        },
        movablePiece: "f5",
        movableSide: "black",
        moveHintSquares: [{ sq: "f6", color: "purple" }],
        goal: { type: "capture_all_stars" },
      },
      {
        level: 5,
        title: "Pawn",
        description: "try to remember these moves",
        board: {
          f8: { notation: "", side: "black", promoted: true },
          g10: { notation: "STAR", side: "white" },
        },
        movablePiece: "f8",
        movableSide: "black",
        moveHintSquares: [
          { sq: "e8", color: "purple" }, { sq: "g8", color: "purple" },
          { sq: "e9", color: "purple" }, { sq: "f9", color: "purple" }, { sq: "g9", color: "purple" },
        ],
        goal: { type: "capture_all_stars" },
        // 未吃完第10排的星星就跑到第11排同理走岔
        wrongMoveCheck: ({ to, stars }) => {
          const row = parseInt(to.slice(1), 10);
          const row10StarsLeft = stars.some((s) => parseInt(s.slice(1), 10) === 10);
          if (row >= 11 && row10StarsLeft) return true;
          return false;
        },
      },
    ],

    rook: [
      {
        level: 1,
        title: "Rook",
        description: "It moves in straight lines\nsame as chess",
        board: { f3: { notation: "R", side: "black" }, f9: { notation: "STAR", side: "white" } },
        movablePiece: "f3",
        movableSide: "black",
        hintArrows: [{ from: "f3", to: "f9" }],
        goal: { type: "capture_all_stars" },
      },
      {
        level: 2,
        title: "Rook",
        description: "up, down, left, right!",
        board: { f6: { notation: "R", side: "black" }, l6: { notation: "STAR", side: "white" } },
        movablePiece: "f6",
        movableSide: "black",
        moveHintSquares: ["f1","f2","f3","f4","f5","f7","f8","f9","f10","f11","f12",
          "a6","b6","c6","d6","e6","g6","h6","j6","k6"].map((sq) => ({ sq, color: "purple" })),
        goal: { type: "capture_all_stars" },
      },
      {
        level: 3,
        title: "Rook",
        description: "grab all pawns!",
        board: {
          a1: { notation: "R", side: "black" },
          a12: { notation: "STAR", side: "white" },
          l12: { notation: "STAR", side: "white" },
          d9: { notation: "STAR", side: "white" },
          d4: { notation: "STAR", side: "white" },
          l1: { notation: "STAR", side: "white" },
        },
        movablePiece: "a1",
        movableSide: "black",
        goal: { type: "capture_all_stars" },
      },
    ],

    phoenix: [
      {
        level: 1,
        title: "Phoenix",
        description: "It moves diagonally\nsame as \u201cbishop\u201d in chess",
        board: { d4: { notation: "P", side: "black" }, h8: { notation: "STAR", side: "white" } },
        movablePiece: "d4",
        movableSide: "black",
        hintArrows: [{ from: "d4", to: "h8" }],
        goal: { type: "capture_all_stars" },
      },
      {
        level: 2,
        title: "Phoenix",
        description: "\u201cX\u201d",
        board: { f6: { notation: "P", side: "black" }, a11: { notation: "STAR", side: "white" } },
        movablePiece: "f6",
        movableSide: "black",
        moveHintSquares: ["g7","h8","j9","k10","l11","g5","h4","j3","k2","l1",
          "e7","d8","c9","b10","e5","d4","c3","b2","a1"].map((sq) => ({ sq, color: "purple" })),
        goal: { type: "capture_all_stars" },
      },
      {
        level: 3,
        title: "Phoenix",
        description: "takes them all!",
        board: {
          c1: { notation: "P", side: "black" },
          f12: { notation: "STAR", side: "white" },
          b8: { notation: "STAR", side: "white" },
          k8: { notation: "STAR", side: "white" },
          j1: { notation: "STAR", side: "white" },
        },
        movablePiece: "c1",
        movableSide: "black",
        goal: { type: "capture_all_stars" },
      },
    ],

    knight: [
      {
        level: 1,
        title: "Knight",
        description: "The knight can move in an L-shape\nfor 2\u20133 squares.\nwhich is two to three squares\nforward and one square sideways",
        board: {
          d1: { notation: "N", side: "black" },
          e3: { notation: "STAR", side: "white" },
          d6: { notation: "STAR", side: "white" },
        },
        movablePiece: "d1",
        movableSide: "black",
        hintArrows: [{ from: "d1", to: "e3" }, { from: "e3", to: "d6" }],
        goal: { type: "capture_all_stars" },
      },
      {
        level: 2,
        title: "Knight",
        description: "takes the stars with your fancy\njumps",
        board: {
          f6: { notation: "N", side: "black" },
          d6: { notation: "STAR", side: "white" },
          e9: { notation: "STAR", side: "white" },
          g8: { notation: "STAR", side: "white" },
          h6: { notation: "STAR", side: "white" },
          g4: { notation: "STAR", side: "white" },
          d3: { notation: "STAR", side: "white" },
        },
        movablePiece: "f6",
        movableSide: "black",
        goal: { type: "capture_all_stars" },
      },
      {
        level: 3,
        title: "Knight",
        description: "try to remember these moves",
        board: { f6: { notation: "N", side: "black" }, j7: { notation: "STAR", side: "white" } },
        movablePiece: "f6",
        movableSide: "black",
        moveHintSquares: ["h7","h5","d7","d5","g8","g4","e8","e4","j5","c7","c5","g9","g3","e9","e3"]
          .map((sq) => ({ sq, color: "purple" })),
        goal: { type: "capture_all_stars" },
      },
      {
        level: 4,
        title: "Knight",
        description: "let\u2019s clear the board!",
        board: {
          g5: { notation: "N", side: "black" },
          f5: { notation: "STAR", side: "white" },
          e7: { notation: "STAR", side: "white" },
          g6: { notation: "STAR", side: "white" },
          f7: { notation: "STAR", side: "white" },
          f8: { notation: "STAR", side: "white" },
          h7: { notation: "STAR", side: "white" },
          h8: { notation: "STAR", side: "white" },
          k7: { notation: "STAR", side: "white" },
          j4: { notation: "STAR", side: "white" },
        },
        movablePiece: "g5",
        movableSide: "black",
        goal: { type: "capture_all_stars" },
      },
    ],

    hussar: [
      {
        level: 1,
        title: "Hussar",
        description: "it jumps 3 squares in an L-shape\nor in a straight line.\nwhich is three squares forward\nand one square sideways,\nor three squares forward only",
        board: {
          c2: { notation: "H", side: "black" },
          f3: { notation: "STAR", side: "white" },
          f6: { notation: "STAR", side: "white" },
        },
        movablePiece: "c2",
        movableSide: "black",
        hintArrows: [{ from: "c2", to: "f3" }, { from: "f3", to: "f6" }],
        goal: { type: "capture_all_stars" },
      },
      {
        level: 2,
        title: "Hussar",
        description: "grab them elegantly",
        board: {
          f2: { notation: "H", side: "black" },
          e9: { notation: "STAR", side: "white" },
          h9: { notation: "STAR", side: "white" },
          j6: { notation: "STAR", side: "white" },
          f5: { notation: "STAR", side: "white" },
        },
        movablePiece: "f2",
        movableSide: "black",
        goal: { type: "capture_all_stars" },
      },
      {
        level: 3,
        title: "Hussar",
        description: "You will surely remember these\nmoves with some practice",
        board: { f6: { notation: "H", side: "black" }, f3: { notation: "STAR", side: "white" } },
        movablePiece: "f6",
        movableSide: "black",
        moveHintSquares: ["j7","j5","c7","c5","g9","g3","e9","e3","j6","c6","f9"]
          .map((sq) => ({ sq, color: "purple" })),
        goal: { type: "capture_all_stars" },
      },
      {
        level: 4,
        title: "Hussar",
        description: "dance the waltz!",
        board: {
          e6: { notation: "H", side: "black" },
          d7: { notation: "STAR", side: "white" },
          e7: { notation: "STAR", side: "white" },
          g8: { notation: "STAR", side: "white" },
          h7: { notation: "STAR", side: "white" },
          h5: { notation: "STAR", side: "white" },
          d4: { notation: "STAR", side: "white" },
          e4: { notation: "STAR", side: "white" },
          h4: { notation: "STAR", side: "white" },
        },
        movablePiece: "e6",
        movableSide: "black",
        goal: { type: "capture_all_stars" },
      },
    ],

    chariot: [
      {
        level: 1,
        title: "Chariot",
        description: "it jumps 2~3 squares in a\nstraight line.\nbut it cannot only moves one\nsquare forward",
        board: {
          f3: { notation: "C", side: "black" },
          f5: { notation: "STAR", side: "white" },
          f8: { notation: "STAR", side: "white" },
        },
        movablePiece: "f3",
        movableSide: "black",
        hintArrows: [{ from: "f3", to: "f5" }, { from: "f5", to: "f8" }],
        goal: { type: "capture_all_stars" },
      },
      {
        level: 2,
        title: "Chariot",
        description: "chariot can also jump across\nother pieces like Knight\nTakes down the white pieces",
        board: {
          f1: { notation: "C", side: "black" },
          f2: { notation: "H", side: "black" },
          f3: { notation: "N", side: "white" },
          f4: { notation: "S", side: "black" },
          f5: { notation: "P", side: "black" },
          f6: { notation: "S", side: "white" },
        },
        movablePiece: "f1",
        movableSide: "black",
        goal: { type: "capture_all_white" },
      },
      {
        level: 3,
        title: "Chariot",
        description: "it will be automatically promoted after\nreached the 7th rank\nit moves one square longer after\npromotion",
        board: {
          c4: { notation: "C", side: "black" },
          c7: { notation: "STAR", side: "white" },
          g7: { notation: "STAR", side: "white" },
          g11: { notation: "STAR", side: "white" },
        },
        movablePiece: "c4",
        movableSide: "black",
        hintArrows: [{ from: "c4", to: "c7" }, { from: "c7", to: "g7" }, { from: "g7", to: "g11" }],
        goal: { type: "capture_all_stars" },
      },
      {
        level: 4,
        title: "Chariot",
        description: "you can think the chariot as a knight\nthat jumps in straight lines",
        board: { f6: { notation: "C", side: "black" }, j6: { notation: "STAR", side: "white" } },
        movablePiece: "f6",
        movableSide: "black",
        moveHintSquares: ["c6","d6","h6","j6","f8","f9","f3","f4"].map((sq) => ({ sq, color: "purple" })),
        goal: { type: "capture_all_stars" },
      },
      {
        level: 5,
        title: "Chariot",
        description: "promoted",
        board: { f6: { notation: "C", side: "black", promoted: true }, f10: { notation: "STAR", side: "white" } },
        movablePiece: "f6",
        movableSide: "black",
        moveHintSquares: ["b6","c6","d6","h6","j6","k6","f8","f9","f10","f2","f3","f4"]
          .map((sq) => ({ sq, color: "purple" })),
        goal: { type: "capture_all_stars" },
      },
    ],

    ares: [
      {
        level: 1,
        title: "Ares",
        description: "Ares can move up to 2 squares along\nany straight or diagonal line.",
        board: {
          e2: { notation: "A", side: "black" },
          c4: { notation: "STAR", side: "white" },
          d5: { notation: "STAR", side: "white" },
          f5: { notation: "STAR", side: "white" },
        },
        movablePiece: "e2",
        movableSide: "black",
        hintArrows: [{ from: "e2", to: "c4" }, { from: "c4", to: "d5" }, { from: "d5", to: "f5" }],
        goal: { type: "capture_all_stars" },
      },
      {
        level: 2,
        title: "Ares",
        description: "it can also jump over pieces\ntakes all white",
        board: {
          f4: { notation: "A", side: "black" },
          g5: { notation: "", side: "black" },
          h6: { notation: "", side: "white" },
          h7: { notation: "H", side: "black" },
          h8: { notation: "C", side: "white" },
        },
        movablePiece: "f4",
        movableSide: "black",
        hintArrows: [{ from: "f4", to: "h6" }],
        goal: { type: "capture_all_white" },
      },
      {
        level: 3,
        title: "Ares",
        description: "you can understand Ares as a\n\u201csmaller queen\u201d in chess",
        board: { f6: { notation: "A", side: "black" }, d4: { notation: "STAR", side: "white" } },
        movablePiece: "f6",
        movableSide: "black",
        moveHintSquares: [
          "d8","f8","h8","e7","f7","g7","d6","e6","g6","h6","d4","e5","f5","g5","f4","h4",
        ].map((sq) => ({ sq, color: "purple" })),
        goal: { type: "capture_all_stars" },
      },
      {
        level: 4,
        title: "Ares",
        description: "grab all stars!",
        board: {
          f6: { notation: "A", side: "black" },
          e7: { notation: "STAR", side: "white" },
          g7: { notation: "STAR", side: "white" },
          d8: { notation: "STAR", side: "white" },
          f8: { notation: "STAR", side: "white" },
          h8: { notation: "STAR", side: "white" },
        },
        movablePiece: "f6",
        movableSide: "black",
        goal: { type: "capture_all_stars" },
      },
    ],

    swordsman: [
      {
        level: 1,
        title: "Swordsman",
        description: "Swordsman moves exactly 2 squares\northogonally or diagonally\nHowever, before promotion, it can\nonly move forward",
        board: {
          f1: { notation: "S", side: "black" },
          d3: { notation: "STAR", side: "white" },
          e6: { notation: "STAR", side: "white" },
        },
        movablePiece: "f1",
        movableSide: "black",
        hintArrows: [{ from: "f1", to: "d3" }, { from: "d3", to: "e4" }, { from: "e4", to: "e6" }],
        goal: { type: "capture_all_stars" },
        // 第一步必须吃d3的星星，第三步必须吃e6的星星，中间那步(e4)随意
        wrongMoveCheck: ({ to, moveCount }) => {
          if (moveCount === 1 && to !== "d3") return true;
          if (moveCount === 3 && to !== "e6") return true;
          return false;
        },
      },
      {
        level: 2,
        title: "Swordsman",
        description: "It can also move diagonally forward\nby one square, but it cannot capture\nthis way, now, which piece you can\ncapture?",
        board: {
          f4: { notation: "S", side: "black" },
          e5: { notation: "R", side: "white" },
          h6: { notation: "A", side: "white" },
        },
        movablePiece: "f4",
        movableSide: "black",
        hintArrows: [{ from: "f4", to: "h6" }],
        goal: { type: "capture_target", target: "h6" },
        wrongMoveCheck: ({ to }) => to !== "h6",
      },
      {
        level: 3,
        title: "Swordsman",
        description: "after reaches the 7th rank,it promotes\nthen the Swordsman can move exact\nsquares in any of the 8 directions,\ntry to grab teh stars with promotion",
        board: {
          g5: { notation: "S", side: "black" },
          h4: { notation: "STAR", side: "white" },
          e7: { notation: "STAR", side: "white" },
          g7: { notation: "STAR", side: "white" },
          e9: { notation: "STAR", side: "white" },
        },
        movablePiece: "g5",
        movableSide: "black",
        goal: { type: "capture_all_stars" },
      },
      {
        level: 4,
        title: "Swordsman",
        description: "Here, The green squares are for\nmovement only\n\u2014 you cannot capture on them.",
        board: { f6: { notation: "S", side: "black" }, f8: { notation: "STAR", side: "white" } },
        movablePiece: "f6",
        movableSide: "black",
        moveHintSquares: [
          { sq: "d8", color: "purple" }, { sq: "h8", color: "purple" },
          { sq: "e7", color: "green" }, { sq: "g7", color: "green" },
        ],
        goal: { type: "capture_all_stars" },
      },
      {
        level: 5,
        title: "Swordsman",
        description: "try to remember these moves",
        board: { f6: { notation: "S", side: "black", promoted: true }, h4: { notation: "STAR", side: "white" } },
        movablePiece: "f6",
        movableSide: "black",
        moveHintSquares: [
          { sq: "d8", color: "purple" }, { sq: "f8", color: "purple" }, { sq: "h8", color: "purple" },
          { sq: "d6", color: "purple" }, { sq: "h6", color: "purple" },
          { sq: "d4", color: "purple" }, { sq: "f4", color: "purple" },
          { sq: "e7", color: "green" }, { sq: "g7", color: "green" },
          { sq: "e5", color: "green" }, { sq: "g5", color: "green" },
        ],
        goal: { type: "capture_all_stars" },
      },
    ],

    ballista: [
      {
        level: 1,
        title: "Ballista",
        description: "The Ballista can move up to 4 squares\ndiagonally, now move to the green\nsquares to continue→",
        board: { d4: { notation: "B", side: "black" } },
        movablePiece: "d4",
        movableSide: "black",
        greenSquares: ["h8", "e11"],
        hintArrows: [{ from: "d4", to: "h8" }, { from: "h8", to: "e11" }],
        goal: { type: "reach_all_green" },
      },
      {
        level: 2,
        title: "Ballista",
        description: "It can also jump over other pieces to\nmove, please head to the green\nsquares to keep going→",
        board: {
          b6: { notation: "B", side: "black" },
          d8: { notation: "H", side: "black" },
          g9: { notation: "C", side: "white" },
          h8: { notation: "", side: "white" },
          j5: { notation: "A", side: "black" },
          h4: { notation: "T", side: "white" },
          g3: { notation: "P", side: "black" },
        },
        movablePiece: "b6",
        movableSide: "black",
        greenSquares: ["f10", "k6", "f2"],
        hintArrows: [{ from: "b6", to: "f10" }],
        goal: { type: "reach_all_green" },
      },
      {
        level: 3,
        title: "Ballista",
        description: "Ballista jump over a piece to capture.\nHowever, it cannot capture the frist\npiece it jumped over\ncapture all the white pieces!",
        board: {
          b6: { notation: "B", side: "black" },
          d8: { notation: "H", side: "black" },
          f10: { notation: "N", side: "white" },
          h8: { notation: "", side: "white" },
          j7: { notation: "", side: "black" },
          k6: { notation: "C", side: "white" },
          j5: { notation: "S", side: "black" },
          h4: { notation: "A", side: "black" },
          g3: { notation: "P", side: "black" },
          f2: { notation: "R", side: "white" },
        },
        movablePiece: "b6",
        movableSide: "black",
        // 蓝色箭头：b6 借 d8 的"炮架"跳吃 f10 的白马——这是关卡开局唯一能立刻
        // 看懂的一步，所以照旧用 hintArrows 引导。
        hintArrows: [{ from: "b6", to: "f10" }],
        // 红色箭头 + 🚫：f10 -> h8 这一步是"陷阱"提示——f10、g9、h8、j7、k6 在
        // 同一条斜线上，很容易让人误以为吃完 f10 之后能顺着同一条线直接隔山打牛
        // 吃掉 h8，但 h8 在这条线上只隔了1格（近距离吃子只能吃正斜前方一格，
        // 隔子吃子至少要隔1个炮架才能吃第2个目标），此时h8前面没有炮架可用，
        // 所以这一步是错的：h8 只能等黑弩绕到 k6，借 j7 的炮架才能吃到。
        // 跟 hintArrows 用同样的生命周期——玩家点过一次棋子后永久消失。
        wrongHintArrows: [{ from: "f10", to: "h8" }],
        goal: { type: "capture_all_white" },
      },
      {
        level: 4,
        title: "Ballista",
        description: "The Ballista can also capture a piece\nthat is one square diagonally in front\nof it.\ncapture the pawn→",
        board: { f6: { notation: "B", side: "black" }, g7: { notation: "", side: "white" } },
        movablePiece: "f6",
        movableSide: "black",
        hintArrows: [{ from: "f6", to: "g7" }],
        goal: { type: "capture_target", target: "g7" },
        wrongMoveCheck: ({ to }) => to !== "g7",
      },
      {
        level: 5,
        title: "Ballista",
        description: "which piece can be capture in one\nmove?",
        board: {
          d6: { notation: "B", side: "black" },
          c7: { notation: "N", side: "white" },
          e5: { notation: "S", side: "black" },
          c5: { notation: "H", side: "white" },
          f8: { notation: "", side: "white" },
          j1: { notation: "R", side: "white" },
        },
        movablePiece: "d6",
        movableSide: "black",
        goal: { type: "capture_target", target: "c7" },
        wrongMoveCheck: ({ to }) => to !== "c7",
      },
      {
        level: 6,
        title: "Ballista",
        description: "Remember: Red squares indicate\nwhere Ballista can captures without\njumping.\nnow please grab the star",
        board: { f6: { notation: "B", side: "black" }, b10: { notation: "STAR", side: "white" } },
        movablePiece: "f6",
        movableSide: "black",
        moveHintSquares: [
          { sq: "e7", color: "red" }, { sq: "g7", color: "red" },
          { sq: "d8", color: "purple" }, { sq: "c9", color: "purple" },
          { sq: "h8", color: "purple" }, { sq: "j9", color: "purple" }, { sq: "k10", color: "purple" },
          { sq: "e5", color: "purple" }, { sq: "d4", color: "purple" }, { sq: "c3", color: "purple" }, { sq: "b2", color: "purple" },
          { sq: "g5", color: "purple" }, { sq: "h4", color: "purple" }, { sq: "j3", color: "purple" }, { sq: "k2", color: "purple" },
        ],
        goal: { type: "capture_all_stars" },
      },
    ],

    cannon: [
      {
        level: 1,
        title: "Cannon",
        description: "can move 5 squares horizontally on its\nfirst move, then it moves 4 squares in a\nstraight line before promotion,\nAfter promotion, it moves 5 squares in\na straight line.",
        board: { a5: { notation: "T", side: "black" } },
        movablePiece: "a5",
        movableSide: "black",
        greenSquares: ["f5", "k5", "k9", "e9"],
        // a5→f5 用掉首步横向5格特权；f5→k5 是第二步，横向变回4格基础射程；
        // k5→k9 是竖直4格；k9→e9 距离5，此时已经不是首步了，横向基础射程只有4格，
        // 单步走不到——跟 Ballista 第2关的箭头一样，这里的箭头只是示意"接下来去这
        // 几个目标"，不是严格的单步路径，玩家实际点击时引擎会用真实合法走法
        // （比如中途经过 j9 或 f9 落一步脚）来完成，不影响关卡可解性。
        hintArrows: [
          { from: "a5", to: "f5" }, { from: "f5", to: "k5" },
          { from: "k5", to: "k9" }, { from: "k9", to: "e9" },
        ],
        goal: { type: "reach_all_green" },
      },
      {
        level: 2,
        title: "Cannon",
        description: "it can also jump over other pieces\nto move→",
        board: {
          a5: { notation: "T", side: "black" },
          c5: { notation: "S", side: "black" },
          j5: { notation: "B", side: "white" },
          f9: { notation: "N", side: "black" },
          g9: { notation: "R", side: "black" },
          h9: { notation: "A", side: "white" },
          k8: { notation: "C", side: "white" },
          k7: { notation: "P", side: "black" },
          k6: { notation: "A", side: "black" },
        },
        movablePiece: "a5",
        movableSide: "black",
        greenSquares: ["f5", "k5", "k9", "e9"],
        hintArrows: [
          { from: "a5", to: "f5" }, { from: "f5", to: "k5" },
          { from: "k5", to: "k9" }, { from: "k9", to: "e9" },
        ],
        goal: { type: "reach_all_green" },
      },
      {
        level: 3,
        title: "Cannon",
        description: "Cannon jump over a piece to capture.\nHowever, it cannot capture the frist\npiece it jumped over\ncapture all the white pieces!",
        board: {
          b9: { notation: "C", side: "white" },
          c9: { notation: "R", side: "black" },
          d9: { notation: "N", side: "black" },
          e9: { notation: "C", side: "white", promoted: true },
          f9: { notation: "P", side: "black" },
          g9: { notation: "T", side: "white" },
          b8: { notation: "A", side: "black" },
          b7: { notation: "", side: "black" },
          b6: { notation: "N", side: "black" },
          b5: { notation: "B", side: "white" },
          c5: { notation: "", side: "black" },
          d5: { notation: "N", side: "white" },
          f5: { notation: "R", side: "white" },
          h5: { notation: "B", side: "black" },
          l5: { notation: "T", side: "black" },
        },
        movablePiece: "l5",
        movableSide: "black",
        // 蓝色箭头：l5 借 h5 的"炮架"横向隔子吃掉 f5 的白攻城塔——首步用掉横向
        // 5格特权。之后可以一路连续吃下去：f5 借 d5 当炮架吃掉 b5 的白弩
        // （距离4）→ b5 借 c5 当炮架吃掉 d5 的白马（距离2）→ 退回 b5 → 借 b6
        // 当炮架竖直吃掉 b9 的白战车（距离4）→ 借 c9 当炮架吃掉 e9 升变白战车
        // （距离3）→ 借 f9 当炮架吃掉 g9 的白炮（距离2），7步吃光全部白棋。
        hintArrows: [{ from: "l5", to: "f5" }],
        // 红色箭头 + 🚫：f5 -> d5 是陷阱——从 f5 往左看，d5 是这条线上第一个
        // 挡路的棋子，只能当"炮架"本身不能被吃；d5 真正要吃，得先吃掉更远的
        // b5（借 d5 自己当炮架，距离4），到了 b5 之后借 c5 当炮架才能反过来吃 d5。
        wrongHintArrows: [{ from: "f5", to: "d5" }],
        goal: { type: "capture_all_white" },
      },
      {
        level: 4,
        title: "Cannon",
        description: "The Cannon can also capture a piece\nthat is one square in front of it.\ncapture the pawn→",
        board: { f5: { notation: "T", side: "black" }, f6: { notation: "", side: "white" } },
        movablePiece: "f5",
        movableSide: "black",
        hintArrows: [{ from: "f5", to: "f6" }],
        goal: { type: "capture_target", target: "f6" },
        wrongMoveCheck: ({ to }) => to !== "f6",
      },
      {
        level: 5,
        title: "Cannon",
        description: "Remember: Red squares indicate\nwhere Cannon can captures without\njumping.\nnow please grab the star",
        board: {
          f6: { notation: "T", side: "black", hasMoved: true },
          d7: { notation: "STAR", side: "white" },
        },
        movablePiece: "f6",
        movableSide: "black",
        moveHintSquares: [
          { sq: "b6", color: "purple" }, { sq: "c6", color: "purple" }, { sq: "d6", color: "purple" }, { sq: "e6", color: "purple" },
          { sq: "g6", color: "purple" }, { sq: "h6", color: "purple" }, { sq: "j6", color: "purple" }, { sq: "k6", color: "purple" },
          { sq: "f2", color: "purple" }, { sq: "f3", color: "purple" }, { sq: "f4", color: "purple" }, { sq: "f5", color: "purple" },
          { sq: "f8", color: "purple" }, { sq: "f9", color: "purple" }, { sq: "f10", color: "purple" },
          { sq: "f7", color: "red" },
        ],
        goal: { type: "capture_all_stars" },
      },
      {
        level: 6,
        title: "Cannon",
        description: "promoted, now please grab the star.",
        board: {
          f6: { notation: "T", side: "black", hasMoved: true, promoted: true },
          f8: { notation: "S", side: "black", promoted: true },
          f11: { notation: "STAR", side: "white" },
        },
        movablePiece: "f6",
        movableSide: "black",
        moveHintSquares: [
          { sq: "a6", color: "purple" }, { sq: "b6", color: "purple" }, { sq: "c6", color: "purple" }, { sq: "d6", color: "purple" }, { sq: "e6", color: "purple" },
          { sq: "g6", color: "purple" }, { sq: "h6", color: "purple" }, { sq: "j6", color: "purple" }, { sq: "k6", color: "purple" }, { sq: "l6", color: "purple" },
          { sq: "f1", color: "purple" }, { sq: "f2", color: "purple" }, { sq: "f3", color: "purple" }, { sq: "f4", color: "purple" }, { sq: "f5", color: "purple" },
          { sq: "f9", color: "purple" }, { sq: "f10", color: "purple" }, { sq: "f11", color: "purple" },
          { sq: "f7", color: "red" },
        ],
        goal: { type: "capture_all_stars" },
      },
    ],
  };

  // 章节顺序（跟 lobby 卡片的排列顺序一致，播放器翻页要用）
  global.LEARN_CHAPTER_ORDER = [
    "fundamentals", "pawn", "rook", "phoenix", "knight", "hussar",
    "chariot", "ares", "swordsman", "ballista", "cannon",
  ];
})(window);
