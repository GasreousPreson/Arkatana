/*
 * learn-data.js
 * =============
 * Learn 模块的章节元数据（图标、标题、副标题）。
 * 每一关的具体棋盘数据在 learn-lessons.js 里，这里只放"lobby 卡片长什么样"。
 */
(function (global) {
  "use strict";

  global.LEARN_CHAPTERS = [
    {
      id: "fundamentals",
      section: "fundamentals",
      icon: "white_throne.png",
      title: "learn some basic rules",
      subtitle: "",
      levelCount: 4,
    },
    {
      id: "pawn",
      section: "pieces",
      icon: "white_pawn.png",
      title: "Pawn",
      subtitle: "it march forward and sideways",
      levelCount: 5,
    },
    {
      id: "rook",
      section: "pieces",
      icon: "white_rook.png",
      title: "Rook",
      subtitle: "same as rook in chess",
      levelCount: 3,
    },
    {
      id: "phoenix",
      section: "pieces",
      icon: "white_phoenix_r.png",
      title: "Phoenix",
      subtitle: "same as bishop in chess",
      levelCount: 3,
    },
    {
      id: "knight",
      section: "pieces",
      icon: "white_knight_r.png",
      title: "Knight",
      subtitle: "it moves in L shapes",
      levelCount: 4,
    },
    {
      id: "hussar",
      section: "pieces",
      icon: "white_hussar_r.png",
      title: "Hussar",
      subtitle: "it jumps in L shapes and straight lines",
      levelCount: 4,
    },
    {
      id: "chariot",
      section: "pieces",
      icon: "white_chariot_r.png",
      title: "Chariot",
      subtitle: "this one jumps in straight lines",
      levelCount: 5,
    },
    {
      id: "ares",
      section: "pieces",
      icon: "white_ares.png",
      title: "Ares",
      subtitle: "similiar to the queen in chess",
      levelCount: 4,
    },
    {
      id: "swordsman",
      section: "pieces",
      icon: "white_swordsman_r.png",
      title: "Swordsman",
      subtitle: "it jumps in straight lines and diagonals",
      levelCount: 5,
    },
    {
      id: "ballista",
      section: "pieces",
      icon: "white_ballista_r.png",
      title: "Ballista",
      subtitle: "jump over to capture diagonally",
      levelCount: 6,
    },
    {
      id: "cannon",
      section: "pieces",
      icon: "white_turret_r.png",
      title: "Cannon",
      subtitle: "jump over to capture in straight lines",
      levelCount: 6,
    },
  ];

  // 章节id -> 记谱代号，训练题引擎用这个知道"这一关用的是哪种棋子的走法"
  global.LEARN_PIECE_NOTATION = {
    pawn: "",
    rook: "R",
    phoenix: "P",
    knight: "N",
    hussar: "H",
    chariot: "C",
    ares: "A",
    swordsman: "S",
    ballista: "B",
    cannon: "T",   // Cannon 章节对应的实际棋子是 Turret(炮塔)
  };
})(window);
