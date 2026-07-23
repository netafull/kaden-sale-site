#!/usr/bin/env python3
"""data/sales.json から docs/index.html と docs/rss.xml を生成する。"""

from __future__ import annotations

import datetime
import html
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
DATA_PATH = ROOT / "data" / "sales.json"
DOCS = ROOT / "docs"

CSS = """
:root {
  --bg: #fafaf7; --card: #ffffff; --text: #1a1a1a; --muted: #6b6b6b;
  --accent: #e47911; --line: #e5e2dc; --badge-hi: #d0342c; --badge-mid: #e47911;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #14151a; --card: #1e2027; --text: #e8e8e6; --muted: #9a9a96;
    --line: #2c2e36;
  }
}
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  background: var(--bg); color: var(--text);
  font-family: "Hiragino Sans", "Noto Sans JP", sans-serif;
  line-height: 1.6;
}
header { padding: 28px 16px 12px; max-width: 960px; margin: 0 auto; }
header h1 { font-size: 24px; }
header h1 a { color: var(--text); text-decoration: none; }
header p { color: var(--muted); font-size: 13px; margin-top: 4px; }
main { max-width: 960px; margin: 0 auto; padding: 8px 16px 48px; }
h2 { font-size: 18px; margin: 0; padding-left: 10px;
  border-left: 4px solid var(--accent); display: inline; }
details { margin-top: 28px; }
summary { cursor: pointer; list-style: none; user-select: none; }
summary::-webkit-details-marker { display: none; }
summary::before { content: "▼"; font-size: 11px; color: var(--muted);
  margin-right: 8px; }
details:not([open]) summary::before { content: "▶"; }
summary:hover h2 { color: var(--accent); }
details > .grid, details > .empty { margin-top: 12px; }
.grid { display: grid; gap: 10px;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); }
.book { display: flex; gap: 12px; background: var(--card);
  border: 1px solid var(--line); border-radius: 10px; padding: 12px;
  text-decoration: none; color: var(--text); }
.book:hover { border-color: var(--accent); }
.book img { width: 76px; height: 76px; object-fit: contain; border-radius: 4px;
  flex-shrink: 0; background: var(--line); }
.book .t { font-size: 14px; font-weight: 600; display: -webkit-box;
  -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
.book .a { font-size: 12px; color: var(--muted); margin-top: 2px;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.price { margin-top: 6px; font-size: 14px; }
.price .now { font-weight: 700; color: var(--badge-hi); }
.price .was { font-size: 12px; color: var(--muted);
  text-decoration: line-through; margin-left: 6px; }
.off { display: inline-block; font-size: 11px; font-weight: 700;
  color: #fff; background: var(--badge-mid); border-radius: 4px;
  padding: 1px 6px; margin-left: 6px; vertical-align: 1px; }
.off.hi { background: var(--badge-hi); }
.points { font-size: 11px; color: #0a7d3c; font-weight: 600; margin-top: 2px; }
@media (prefers-color-scheme: dark) { .points { color: #4fd689; } }
footer { max-width: 960px; margin: 0 auto; padding: 16px;
  color: var(--muted); font-size: 12px; border-top: 1px solid var(--line); }
.empty { color: var(--muted); font-size: 14px; padding: 12px 0; }
"""


def esc(s):
    return html.escape(s or "", quote=True)


def render_book(item: dict) -> str:
    off = item.get("percent_off")
    off_html = ""
    if off:
        cls = "off hi" if off >= 50 else "off"
        off_html = f'<span class="{cls}">{off}%OFF</span>'
    was_html = (
        f'<span class="was">&yen;{int(item["list_price"]):,}</span>'
        if item.get("list_price")
        else ""
    )
    img_html = (
        f'<img src="{esc(item.get("image"))}" alt="" loading="lazy">'
        if item.get("image")
        else "<img alt=''>"
    )
    # brand(ブランド名)はKindle版のauthor(著者)に相当する表示欄
    brand = f'<div class="a">{esc(item["brand"])}</div>' if item.get("brand") else ""
    points_html = ""
    if item.get("points"):
        pct = item.get("points_percent")
        pct_txt = f"{pct}%還元" if pct else "還元"
        points_html = f'<div class="points">+{item["points"]}pt ({pct_txt})</div>'
    return f"""<a class="book" href="{esc(item["url"])}" target="_blank" rel="noopener sponsored">
  {img_html}
  <div>
    <div class="t">{esc(item["title"])}</div>
    {brand}
    <div class="price"><span class="now">&yen;{int(item["price"]):,}</span>{was_html}{off_html}</div>
    {points_html}
  </div>
</a>"""


def generate_html(data: dict) -> str:
    fetched = datetime.datetime.fromisoformat(data["fetched_at"]).astimezone(
        datetime.timezone(datetime.timedelta(hours=9))
    )
    updated = fetched.strftime("%Y年%m月%d日 %H:%M")

    genres = data.get("genres") or []

    sections = []
    for i, g in enumerate(genres):
        items = g.get("items") or []
        if items:
            books = "\n".join(render_book(b) for b in items)
            body = f'<div class="grid">\n{books}\n</div>'
        else:
            body = '<p class="empty">現在セール中の商品はありません。</p>'
        sections.append(
            f'<details open id="g{i}">\n'
            f'<summary><h2>{esc(g["name"])} ({len(items)}件)</h2></summary>\n'
            f'{body}\n'
            f'</details>'
        )

    site_url = CONFIG.get("site_url", "")
    tagline = CONFIG.get("site_tagline", "")
    page_title = (
        f'{CONFIG["site_title"]}｜{tagline}' if tagline else CONFIG["site_title"]
    )
    gsv = CONFIG.get("google_site_verification", "")
    gsv_tag = (
        f'<meta name="google-site-verification" content="{esc(gsv)}">' if gsv else ""
    )
    ga_id = CONFIG.get("ga_measurement_id", "")
    ga_tag = (
        f"""<script async src="https://www.googletagmanager.com/gtag/js?id={esc(ga_id)}"></script>
<script>
window.dataLayer = window.dataLayer || [];
function gtag(){{dataLayer.push(arguments);}}
gtag('js', new Date());
gtag('config', '{esc(ga_id)}');
</script>"""
        if ga_id
        else ""
    )

    # 構造化データ: サイト情報とジャンル一覧
    json_ld = json.dumps(
        [
            {
                "@context": "https://schema.org",
                "@type": "WebSite",
                "name": CONFIG["site_title"],
                "url": site_url,
                "description": CONFIG["site_description"],
            },
            {
                "@context": "https://schema.org",
                "@type": "ItemList",
                "name": "セール中のガジェット・家電ジャンル",
                "itemListElement": [
                    {
                        "@type": "ListItem",
                        "position": i + 1,
                        "name": g["name"],
                    }
                    for i, g in enumerate(genres)
                ],
            },
        ],
        ensure_ascii=False,
    ).replace("</", "<\\/")

    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(page_title)}</title>
<meta name="description" content="{esc(CONFIG["site_description"])}">
<link rel="canonical" href="{esc(site_url)}">
{gsv_tag}
{ga_tag}
<meta property="og:type" content="website">
<meta property="og:title" content="{esc(page_title)}">
<meta property="og:description" content="{esc(CONFIG["site_description"])}">
<meta property="og:url" content="{esc(site_url)}">
<meta property="og:site_name" content="{esc(CONFIG["site_title"])}">
<meta property="og:locale" content="ja_JP">
<meta name="twitter:card" content="summary">
<link rel="alternate" type="application/rss+xml" title="RSS" href="rss.xml">
<script type="application/ld+json">{json_ld}</script>
<style>{CSS}</style>
</head>
<body>
<header>
<h1><a href="./">{esc(CONFIG["site_title"])}</a></h1>
<p>{esc(CONFIG["site_description"])} ｜ 割引率とポイント還元率の合計が{data["min_saving_percent"]}%以上の商品を掲載 ｜ 最終更新: {updated}</p>
</header>
<main>
{chr(10).join(sections)}
</main>
<footer>
価格・割引率は取得時点のものです。購入前にAmazonの商品ページで最新の価格をご確認ください。
Amazonのアソシエイトとして、当サイトは適格販売により収入を得ています。
当サイトはアクセス解析のためGoogle Analyticsを利用しています(データは匿名で収集され、Googleに送信されます)。
｜ <a href="rss.xml" style="color:inherit">RSS</a>
</footer>
</body>
</html>
"""


def generate_rss(data: dict) -> str:
    site_url = CONFIG.get("site_url", "")
    now = datetime.datetime.now(datetime.timezone.utc).strftime(
        "%a, %d %b %Y %H:%M:%S +0000"
    )
    items_xml = []
    seen: set[str] = set()
    for genre in data.get("genres") or []:
        for b in (genre.get("items") or [])[:20]:
            if b["asin"] in seen:
                continue
            seen.add(b["asin"])
            off = f"【{b['percent_off']}%OFF】" if b.get("percent_off") else ""
            items_xml.append(
                f"""<item>
<title>{esc(off + b["title"] + f" ¥{int(b['price']):,}")}</title>
<link>{esc(b["url"])}</link>
<guid isPermaLink="false">{esc(b["asin"])}</guid>
<category>{esc(genre["name"])}</category>
</item>"""
            )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
<channel>
<title>{esc(CONFIG["site_title"])}</title>
<link>{esc(site_url)}</link>
<description>{esc(CONFIG["site_description"])}</description>
<lastBuildDate>{now}</lastBuildDate>
{chr(10).join(items_xml)}
</channel>
</rss>
"""


def generate_sitemap(data: dict) -> str:
    site_url = CONFIG.get("site_url", "")
    lastmod = data["fetched_at"][:10]
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
<url>
<loc>{esc(site_url)}</loc>
<lastmod>{lastmod}</lastmod>
<changefreq>hourly</changefreq>
</url>
</urlset>
"""


WIDGET_JS = r"""(function () {
  "use strict";

  var FALLBACK_SITE_URL = "__FALLBACK_SITE_URL__";

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
"""


def generate_widget_data(data: dict) -> dict:
    """ジャンルごとに候補プールを分けて出力する。

    表示のたびにジャンルを1つずつランダム抽出することで、同じジャンルの
    商品ばかりが並ぶのを避け、掲載ジャンルの幅を毎回見せられるようにする
    (widget.js側のrender()参照)。
    """
    site_url = CONFIG.get("site_url", "")
    genres = data.get("genres") or []

    def savings(item: dict) -> int:
        return (item.get("percent_off") or 0) + (item.get("points_percent") or 0)

    pool_per_genre = CONFIG.get("widget_pool_per_genre", 8)

    genre_pools = []
    for g in genres:
        items = sorted(g.get("items") or [], key=savings, reverse=True)
        books = [
            {
                "title": b.get("title"),
                "price": b.get("price"),
                "list_price": b.get("list_price"),
                "percent_off": b.get("percent_off"),
                "points": b.get("points"),
                "points_percent": b.get("points_percent"),
                "image": b.get("image"),
                "url": b.get("url"),
            }
            for b in items[:pool_per_genre]
        ]
        if books:
            genre_pools.append({"name": g["name"], "books": books})

    return {
        "updated": data.get("fetched_at"),
        "site_url": site_url,
        "site_title": CONFIG.get("site_title", ""),
        "genres": genre_pools,
    }


def generate_widget_assets(data: dict) -> tuple[str, str]:
    widget_json = json.dumps(generate_widget_data(data), ensure_ascii=False, indent=2)
    site_url = CONFIG.get("site_url", "")
    widget_js = WIDGET_JS.replace("__FALLBACK_SITE_URL__", site_url)
    return widget_json, widget_js


WIDGET_TEST_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ウィジェット埋め込みテスト｜{site_title}</title>
<style>
  body {{
    max-width: 700px;
    margin: 0 auto;
    padding: 24px 16px 64px;
    font-family: -apple-system, BlinkMacSystemFont, "Hiragino Sans", "Noto Sans JP", sans-serif;
    line-height: 1.8;
    color: #1a1a1a;
    background: #fafaf7;
  }}
  @media (prefers-color-scheme: dark) {{
    body {{ background: #14151a; color: #e8e8e6; }}
  }}
  h1 {{ font-size: 22px; margin-bottom: 16px; }}
  p {{ margin-bottom: 16px; }}
</style>
</head>
<body>
<h1>今日のガジェットレビュー(ダミー記事)</h1>

<p>
このページはブログ記事を模した、外部埋め込みウィジェットの動作確認用テストページです。
本文はダミーです。実際のブログ記事では、この位置にレビューや感想などが入ります。
</p>

<p>
記事の本文がひとしきり続いたあと、末尾に「{site_title}」の自動更新ウィジェットを
埋め込むと、以下のように現在セール中のガジェット・家電が自動表示されます。
</p>

<!-- ここから埋め込みスニペット(相対パスでローカル検証用) -->
<div id="kaden-widget"><a href="{site_url}">ガジェット・家電セール情報「{site_title}」</a></div>
<script src="./widget.js" async></script>
<!-- ここまで -->

<p>
ウィジェットの上下にはさらに文章が続く想定です。以上、テストページでした。
</p>

</body>
</html>
"""


def generate_widget_test_html() -> str:
    return WIDGET_TEST_HTML_TEMPLATE.format(
        site_title=CONFIG.get("site_title", ""),
        site_url=CONFIG.get("site_url", ""),
    )


def main():
    data = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    site_url = CONFIG.get("site_url", "")
    DOCS.mkdir(exist_ok=True)
    (DOCS / "index.html").write_text(generate_html(data), encoding="utf-8")
    (DOCS / "rss.xml").write_text(generate_rss(data), encoding="utf-8")
    (DOCS / "sitemap.xml").write_text(generate_sitemap(data), encoding="utf-8")
    (DOCS / "robots.txt").write_text(
        f"User-agent: *\nAllow: /\nSitemap: {site_url}sitemap.xml\n",
        encoding="utf-8",
    )
    widget_json, widget_js = generate_widget_assets(data)
    (DOCS / "widget.json").write_text(widget_json, encoding="utf-8")
    (DOCS / "widget.js").write_text(widget_js, encoding="utf-8")
    (DOCS / "widget-test.html").write_text(
        generate_widget_test_html(), encoding="utf-8"
    )
    total = sum(len(g.get("items") or []) for g in data.get("genres") or [])
    print(
        f"generated: index.html, rss.xml, sitemap.xml, robots.txt, "
        f"widget.json, widget.js, widget-test.html ({total}件)"
    )


if __name__ == "__main__":
    main()
