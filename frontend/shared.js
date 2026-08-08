/*
 * shared.js
 * =========
 * Arkatana（古戰棋）— 三个页面共用的小工具
 *
 * 放在这里是因为「对局结果怎么写」和「先后手图标长什么样」这两件事
 * 在对局页、首页 Game History、对局库三个地方都要用到，
 * 各写一份迟早会不一致。
 */

(function (global) {
  "use strict";

  // -----------------------------------------------------------------
  // 先后手图标：城堡剪影
  //   黑方 = 空心描边 / 白方 = 实心填充 / 随机 = 左实心右空心
  // -----------------------------------------------------------------
  const CASTLE_PATH =
    "M6 40 L13 16 L20 16 L20 11 L26 11 L26 16 L30 16 L34 8 L38 16 L42 16 " +
    "L42 11 L48 11 L48 16 L55 16 L62 40 Z";

  function sideIcon(side, size) {
    size = size || 18;
    const stroke = "currentColor";
    let inner;
    if (side === "white") {
      inner = `<path d="${CASTLE_PATH}" fill="${stroke}" stroke="${stroke}" stroke-width="3" stroke-linejoin="round"/>`;
    } else if (side === "random" || side === "either") {
      // 左半实心、右半空心：用一个矩形裁切出左半边再填充
      inner = `
        <defs><clipPath id="ark-half-${size}"><rect x="0" y="0" width="34" height="48"/></clipPath></defs>
        <path d="${CASTLE_PATH}" fill="none" stroke="${stroke}" stroke-width="3" stroke-linejoin="round"/>
        <path d="${CASTLE_PATH}" fill="${stroke}" clip-path="url(#ark-half-${size})"/>`;
    } else {
      inner = `<path d="${CASTLE_PATH}" fill="none" stroke="${stroke}" stroke-width="3" stroke-linejoin="round"/>`;
    }
    return `<svg class="side-icon" viewBox="0 0 68 48" width="${size}" height="${size * 48 / 68}" ` +
           `xmlns="http://www.w3.org/2000/svg" aria-hidden="true">${inner}</svg>`;
  }

  // -----------------------------------------------------------------
  // 对局结果文案
  //   格式：{结束原因} + {谁获胜} + {手数}
  //   例："Black resigned, White is victorious, 34 moves"
  // -----------------------------------------------------------------
  const REASON_LABEL = {
    resigned:     (loser) => `${loser} resigned`,
    checkmate:    (loser) => `${loser} was checkmated`,
    stalemate:    (loser) => `${loser} was stalemated`,
    timedout:     (loser) => `${loser} timed out`,
    disconnected: (loser) => `${loser} disconnected`,
    aborted:      () => "Game aborted",
    agreement:    () => "Draw by agreement",
  };

  /**
   * 把一条对局记录转成结果文案。
   * @returns {{text: string, outcome: "black"|"white"|"draw"|"none"}}
   *   outcome 供调用方决定文字颜色（各页面配色规则不同，这里只报事实）
   */
  function resultText(game) {
    const moves = game.move_count != null ? `${game.move_count} moves` : "";
    const reason = game.end_reason;

    if (game.result === "draw") {
      const head = reason === "agreement" ? "Draw by agreement" : "Drawn in peace";
      return { text: [head, moves].filter(Boolean).join(", "), outcome: "draw" };
    }

    if (game.result === "black_wins" || game.result === "white_wins") {
      const blackWon = game.result === "black_wins";
      const winner = blackWon ? "Black" : "White";
      const loser = blackWon ? "White" : "Black";
      const head = REASON_LABEL[reason] ? REASON_LABEL[reason](loser) : null;
      const parts = [];
      if (head) parts.push(head);
      parts.push(`${winner} is victorious`);
      if (moves) parts.push(moves);
      return { text: parts.join(", "), outcome: blackWon ? "black" : "white" };
    }

    return { text: game.result || "", outcome: "none" };
  }

  /**
   * 站在某个用户的角度看这局是赢是输——首页 Game History 用它决定颜色。
   * @returns "win" | "loss" | "draw" | "unknown"
   */
  function outcomeForUser(game, username) {
    if (!username) return "unknown";
    const isBlack = game.black_player === username;
    const isWhite = game.white_player === username;
    if (!isBlack && !isWhite) return "unknown";
    if (game.result === "draw") return "draw";
    if (game.result === "black_wins") return isBlack ? "win" : "loss";
    if (game.result === "white_wins") return isWhite ? "win" : "loss";
    return "unknown";
  }

  global.ArkatanaShared = { sideIcon, resultText, outcomeForUser };
})(window);
