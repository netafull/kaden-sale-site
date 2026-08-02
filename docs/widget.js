(function () {
  "use strict";

  var FALLBACK_SITE_URL = "https://kaden.netaful.jp/";

  // currentScriptはスクリプト評価中しか参照できない。init()はDOMContentLoaded
  // 後に走ることがあるため、ここで(評価時に)一度だけ取得しておく
  var SCRIPT_SRC = document.currentScript && document.currentScript.src;

  function baseUrlFromScript() {
    if (!SCRIPT_SRC) return null;
    return SCRIPT_SRC.replace(/widget\.js.*$/, "");
  }

  function fmtYen(n) {
    return "¥" + Math.round(n).toLocaleString("ja-JP");
  }

  function el(tag, opts) {
    opts = opts || {};
    var e = document.createElement(tag);
    if (opts.className) e.className = opts.className;
    if (opts.text !== undefined) e.textContent = opts.text;
    if (opts.attrs) {
      for (var k in opts.attrs) {
        if (Object.prototype.hasOwnProperty.call(opts.attrs, k)) {
          e.setAttribute(k, opts.attrs[k]);
        }
      }
    }
    return e;
  }

  function injectStyle() {
    if (document.getElementById("kaden-widget-style")) return;
    var style = document.createElement("style");
    style.id = "kaden-widget-style";
    style.textContent = [
      "#kaden-widget{font-size:14px;line-height:1.5;font-family:-apple-system,BlinkMacSystemFont,\"Hiragino Sans\",\"Noto Sans JP\",sans-serif;}",
      ".kdn-box{border:1px solid #e5e2dc;border-radius:10px;overflow:hidden;background:#ffffff;color:#1a1a1a;}",
      ".kdn-head{display:flex;align-items:baseline;gap:8px;padding:8px 14px;font-size:14px;font-weight:700;background:#faf6ef;color:#1a1a1a;text-decoration:none;border-bottom:1px solid #e5e2dc;}",
      ".kdn-more{font-size:11px;font-weight:600;color:#e47911;white-space:nowrap;flex-shrink:0;margin-left:auto;}",
      ".kdn-head:hover{color:#e47911;}",
      ".kdn-list{display:flex;flex-direction:column;}",
      ".kdn-row{display:flex;gap:10px;padding:10px 14px;text-decoration:none;color:#1a1a1a;border-bottom:1px solid #f0ede7;}",
      ".kdn-row:last-child{border-bottom:none;}",
      ".kdn-row:hover{background:#faf8f4;}",
      ".kdn-img{width:52px;height:52px;object-fit:contain;border-radius:4px;flex-shrink:0;background:#e5e2dc;}",
      ".kdn-ph{width:52px;height:52px;border-radius:4px;flex-shrink:0;background:#e5e2dc;}",
      ".kdn-info{min-width:0;flex:1;}",
      ".kdn-badges{margin-bottom:2px;}",
      ".kdn-new{display:inline-block;font-size:9px;font-weight:700;color:#fff;background:#0a7d3c;border-radius:3px;padding:0 4px;line-height:1.5;}",
      ".kdn-title{font-size:13px;font-weight:600;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;}",
      ".kdn-price{margin-top:4px;font-size:13px;}",
      ".kdn-now{font-weight:700;color:#d0342c;}",
      ".kdn-was{font-size:11px;color:#6b6b6b;text-decoration:line-through;margin-left:5px;}",
      ".kdn-off{display:inline-block;font-size:10px;font-weight:700;color:#fff;background:#e47911;border-radius:4px;padding:1px 5px;margin-left:5px;vertical-align:1px;}",
      ".kdn-off.kdn-hi{background:#d0342c;}",
      ".kdn-pt{font-size:10px;color:#0a7d3c;font-weight:600;margin-top:2px;}",
      '@media (prefers-color-scheme: dark) {',
      ".kdn-box{border-color:#2c2e36;background:#1e2027;color:#e8e8e6;}",
      ".kdn-head{background:#20222a;color:#e8e8e6;border-bottom-color:#2c2e36;}",
      ".kdn-row{color:#e8e8e6;border-bottom-color:#282a31;}",
      ".kdn-row:hover{background:#22242c;}",
      ".kdn-img,.kdn-ph{background:#2c2e36;}",
      ".kdn-was{color:#9a9a96;}",
      ".kdn-pt{color:#4fd689;}",
      "}",
    ].join("\n");
    document.head.appendChild(style);
  }

  function renderBookRow(book) {
    var row = el("a", {
      className: "kdn-row no-icon",
      attrs: {
        href: book.url || "#",
        target: "_blank",
        rel: "noopener sponsored",
      },
    });

    if (book.image) {
      var img = el("img", { className: "kdn-img", attrs: { src: book.image, alt: "", loading: "lazy" } });
      row.appendChild(img);
    } else {
      row.appendChild(el("span", { className: "kdn-ph" }));
    }

    var info = el("div", { className: "kdn-info" });
    // 本体サイトと同じく、直近にセール入りした商品に印を付ける
    if (book.is_new) {
      var badges = el("div", { className: "kdn-badges" });
      badges.appendChild(el("span", { className: "kdn-new", text: "NEW" }));
      info.appendChild(badges);
    }
    info.appendChild(el("div", { className: "kdn-title", text: book.title || "" }));

    var price = el("div", { className: "kdn-price" });
    price.appendChild(el("span", { className: "kdn-now", text: fmtYen(book.price) }));
    if (book.list_price) {
      price.appendChild(el("span", { className: "kdn-was", text: fmtYen(book.list_price) }));
    }
    if (book.percent_off) {
      var offCls = "kdn-off" + (book.percent_off >= 50 ? " kdn-hi" : "");
      price.appendChild(el("span", { className: offCls, text: book.percent_off + "%OFF" }));
    }
    info.appendChild(price);

    if (book.points) {
      var pct = book.points_percent ? book.points_percent + "%還元" : "還元";
      info.appendChild(el("div", { className: "kdn-pt", text: "+" + book.points + "pt (" + pct + ")" }));
    }

    row.appendChild(info);
    return row;
  }

  function sampleRandom(arr, n) {
    var copy = arr.slice();
    for (var i = copy.length - 1; i > 0; i--) {
      var j = Math.floor(Math.random() * (i + 1));
      var tmp = copy[i]; copy[i] = copy[j]; copy[j] = tmp;
    }
    return copy.slice(0, n);
  }

  function pickOnePerGenre(genres, count) {
    // ジャンルをcount個ランダムに選び、各ジャンルから1冊ずつ選ぶ。
    // 同じジャンルの商品ばかり並ぶのを避け、掲載ジャンルの幅を見せる
    var chosenGenres = sampleRandom(genres || [], count);
    var books = [];
    for (var i = 0; i < chosenGenres.length; i++) {
      var picked = sampleRandom(chosenGenres[i].books || [], 1);
      if (picked.length) books.push(picked[0]);
    }
    return books;
  }

  function render(container, data) {
    var siteUrl = data.site_url || FALLBACK_SITE_URL;
    var count = parseInt(container.getAttribute("data-count"), 10);
    if (!count || count < 1 || count > 5) count = 3;
    var books = pickOnePerGenre(data.genres || [], count);
    if (books.length === 0) return;

    injectStyle();

    var box = el("div", { className: "kdn-box" });

    // 見出しと末尾ボタンはリンク先が同じで導線が重複していた。
    // 見出しに寄せて1つにまとめ、40px分の縦幅を削る。
    // ただし見出しだけではリンクと分からないため、右端に誘導文言を添える
    var itemCount = data.item_count || 0;
    var head = el("a", {
      className: "kdn-head no-icon",
      attrs: { href: siteUrl, target: "_blank", rel: "noopener" },
    });
    head.appendChild(el("span", { text: "⚡ 本日のガジェットセール" }));
    head.appendChild(
      el("span", {
        className: "kdn-more",
        text: itemCount ? "セール" + itemCount + "品を見る →" : "すべて見る →",
      })
    );
    box.appendChild(head);

    var list = el("div", { className: "kdn-list" });
    for (var i = 0; i < books.length; i++) {
      list.appendChild(renderBookRow(books[i]));
    }
    box.appendChild(list);

    container.textContent = "";
    container.appendChild(box);
  }

  function init() {
    var container = document.getElementById("kaden-widget");
    if (!container) return;

    var base = baseUrlFromScript() || FALLBACK_SITE_URL;
    var url = base + "widget.json";

    fetch(url, { cache: "no-store" })
      .then(function (res) {
        if (!res.ok) throw new Error("bad response");
        return res.json();
      })
      .then(function (data) {
        render(container, data);
      })
      .catch(function () {
        /* fetch失敗時は何もしない(既存のnoscriptリンクを残す) */
      });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
