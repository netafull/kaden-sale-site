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
      ".dpy-box{border:1px solid #e5e2dc;border-radius:10px;overflow:hidden;background:#ffffff;color:#1a1a1a;}",
      ".dpy-head{display:block;padding:10px 14px;font-size:14px;font-weight:700;background:#faf6ef;color:#1a1a1a;text-decoration:none;border-bottom:1px solid #e5e2dc;}",
      ".dpy-head:hover{color:#e47911;}",
      ".dpy-list{display:flex;flex-direction:column;}",
      ".dpy-row{display:flex;gap:10px;padding:10px 14px;text-decoration:none;color:#1a1a1a;border-bottom:1px solid #f0ede7;}",
      ".dpy-row:last-child{border-bottom:none;}",
      ".dpy-row:hover{background:#faf8f4;}",
      ".dpy-img{width:52px;height:52px;object-fit:contain;border-radius:4px;flex-shrink:0;background:#e5e2dc;}",
      ".dpy-ph{width:52px;height:52px;border-radius:4px;flex-shrink:0;background:#e5e2dc;}",
      ".dpy-info{min-width:0;flex:1;}",
      ".dpy-title{font-size:13px;font-weight:600;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;}",
      ".dpy-price{margin-top:4px;font-size:13px;}",
      ".dpy-now{font-weight:700;color:#d0342c;}",
      ".dpy-was{font-size:11px;color:#6b6b6b;text-decoration:line-through;margin-left:5px;}",
      ".dpy-off{display:inline-block;font-size:10px;font-weight:700;color:#fff;background:#e47911;border-radius:4px;padding:1px 5px;margin-left:5px;vertical-align:1px;}",
      ".dpy-off.dpy-hi{background:#d0342c;}",
      ".dpy-pt{font-size:10px;color:#0a7d3c;font-weight:600;margin-top:2px;}",
      ".dpy-foot{display:block;padding:10px 14px;font-size:13px;font-weight:700;color:#fff;text-decoration:none;background:#e47911;text-align:center;}",
      ".dpy-foot:hover{opacity:0.85;}",
      '@media (prefers-color-scheme: dark) {',
      ".dpy-box{border-color:#2c2e36;background:#1e2027;color:#e8e8e6;}",
      ".dpy-head{background:#20222a;color:#e8e8e6;border-bottom-color:#2c2e36;}",
      ".dpy-row{color:#e8e8e6;border-bottom-color:#282a31;}",
      ".dpy-row:hover{background:#22242c;}",
      ".dpy-img,.dpy-ph{background:#2c2e36;}",
      ".dpy-was{color:#9a9a96;}",
      ".dpy-pt{color:#4fd689;}",
      "}",
    ].join("\n");
    document.head.appendChild(style);
  }

  function renderBookRow(book) {
    var row = el("a", {
      className: "dpy-row",
      attrs: {
        href: book.url || "#",
        target: "_blank",
        rel: "noopener sponsored",
      },
    });

    if (book.image) {
      var img = el("img", { className: "dpy-img", attrs: { src: book.image, alt: "", loading: "lazy" } });
      row.appendChild(img);
    } else {
      row.appendChild(el("span", { className: "dpy-ph" }));
    }

    var info = el("div", { className: "dpy-info" });
    info.appendChild(el("div", { className: "dpy-title", text: book.title || "" }));

    var price = el("div", { className: "dpy-price" });
    price.appendChild(el("span", { className: "dpy-now", text: fmtYen(book.price) }));
    if (book.list_price) {
      price.appendChild(el("span", { className: "dpy-was", text: fmtYen(book.list_price) }));
    }
    if (book.percent_off) {
      var offCls = "dpy-off" + (book.percent_off >= 50 ? " dpy-hi" : "");
      price.appendChild(el("span", { className: offCls, text: book.percent_off + "%OFF" }));
    }
    info.appendChild(price);

    if (book.points) {
      var pct = book.points_percent ? book.points_percent + "%還元" : "還元";
      info.appendChild(el("div", { className: "dpy-pt", text: "+" + book.points + "pt (" + pct + ")" }));
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

    var box = el("div", { className: "dpy-box" });

    var head = el("a", {
      className: "dpy-head",
      text: "⚡ 本日の家電・ガジェットセール",
      attrs: { href: siteUrl, target: "_blank", rel: "noopener" },
    });
    box.appendChild(head);

    var list = el("div", { className: "dpy-list" });
    for (var i = 0; i < books.length; i++) {
      list.appendChild(renderBookRow(books[i]));
    }
    box.appendChild(list);

    var foot = el("a", {
      className: "dpy-foot",
      text: "🛒 セール中の商品をすべて見る",
      attrs: { href: siteUrl, target: "_blank", rel: "noopener" },
    });
    box.appendChild(foot);

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
