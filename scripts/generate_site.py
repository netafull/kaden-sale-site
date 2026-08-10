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
header h1 a { color: var(--text); text-decoration: none;
  display: inline-flex; align-items: center; gap: 8px; }
/* ロゴは文字とほぼ同じ高さに揃える(96px画像を縮小して表示) */
header h1 img { width: 32px; height: 32px; }
header p { color: var(--muted); font-size: 13px; margin-top: 4px; }
.sites { margin-top: 10px; display: flex; gap: 8px; flex-wrap: wrap;
  align-items: baseline; }
.sites .lbl { font-size: 12px; color: var(--muted); }
.sites a { font-size: 12px; padding: 3px 10px; border-radius: 999px;
  border: 1px solid var(--line); background: var(--card);
  color: var(--text); text-decoration: none; }
.sites a:hover { border-color: var(--accent); color: var(--accent); }
footer .sites { margin-top: 10px; }
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
/* flexアイテムはデフォルトでmin-width:autoのため、長い英数字が
   続くタイトルがあるとカード枠をはみ出す。0にして縮小を許可する */
.book > div { min-width: 0; }
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
.since { font-size: 11px; color: var(--muted); margin-top: 2px; }
.badges { display: flex; gap: 4px; align-items: center; margin-bottom: 3px;
  flex-wrap: wrap; }
/* 直近にセール入りした商品の印。各ジャンルは割引率順に並ぶため、
   新しい商品が埋もれないようこれで見分ける */
.nbadge { display: inline-block; font-size: 10px; font-weight: 700;
  color: #fff; background: #0a7d3c; border-radius: 4px; padding: 0 5px; }
@media (prefers-color-scheme: dark) { .nbadge { background: #2f9e5f; } }
/* 「新着セール」でジャンル名を示すラベル */
.gbadge { display: inline-block; font-size: 10px; font-weight: 600;
  color: var(--muted); border: 1px solid var(--line); border-radius: 4px;
  padding: 0 5px; }
footer { max-width: 960px; margin: 0 auto; padding: 16px;
  color: var(--muted); font-size: 12px; border-top: 1px solid var(--line); }
.empty { color: var(--muted); font-size: 14px; padding: 12px 0; }
/* ジャンルの絞り込みタブ。別ページを作らずCSSで表示を切り替えるので、
   URLは1つのままでコンテンツも全てDOMに残る(検索エンジンには全件見える) */
/* 狭い画面では折り返すと縦に伸びて商品が押し下げられるため横スクロールにする。
   ただしPCはマウスホイールが縦スクロールに使われ、スクロールバーも隠している
   ので横に動かす手段が無くなる。広い画面では折り返しに切り替える */
.tabs { display: flex; gap: 6px; margin: 20px auto 4px; padding: 0 16px;
  max-width: 960px; overflow-x: auto; scrollbar-width: none;
  -webkit-overflow-scrolling: touch; }
.tabs::-webkit-scrollbar { display: none; }
.tabs button { flex: 0 0 auto; white-space: nowrap; }
@media (min-width: 700px) {
  .tabs { flex-wrap: wrap; overflow-x: visible; }
}
.tabs button { font-size: 13px; padding: 5px 12px; border-radius: 999px;
  border: 1px solid var(--line); background: var(--card); color: var(--text);
  cursor: pointer; font-family: inherit; }
.tabs button:hover { border-color: var(--accent); }
.tabs button[aria-selected="true"] { background: var(--text); color: var(--bg);
  border-color: var(--text); font-weight: 600; }
.tabs button .n { color: var(--muted); font-size: 11px; margin-left: 4px; }
.tabs button[aria-selected="true"] .n { color: var(--bg); opacity: .7; }
.tabs button:disabled { opacity: .4; cursor: default; }
/* JSが無い環境ではタブを出さない(全件表示のままにする) */
.tabs { display: none; }
.js .tabs { display: flex; }
/* サイトの説明。訪問者の目的(セール情報)を邪魔しないよう本文の最後に置く。
   AIは位置に関わらずページ全体を読むため、下でも検索・AI向けの効果は落ちない */
.about { max-width: 960px; margin: 40px auto 0; padding: 20px 16px 0;
  border-top: 1px solid var(--line); color: var(--muted); font-size: 13px;
  line-height: 1.9; }
.about h2 { font-size: 14px; border-left-width: 3px; margin-bottom: 8px;
  color: var(--text); }
.about p { margin-top: 8px; }
"""


def esc(s):
    return html.escape(s or "", quote=True)


def shorten_title(title: str) -> str:
    """Appleのように説明文が長く、末尾に色名が付くタイトルを読めるようにする。

    「iPhone Air 256GB(SIMフリー)：史上最薄の…一日中使えるバッテリー；
    スカイブルー」のような形式は、カードが2行で省略されるため色名が消える。
    その結果、色違いの2商品が同じカードに見えてしまう。
    「：」以降の説明を畳み、末尾の色名だけを残す。
    """
    # 「；」で区切られた末尾に色名が来る形式だけを対象にする。
    # 「：」以降を無条件に畳むと、MacBook Proのように型番やスペックが
    # 「：」の後ろにある商品から情報が落ちてしまう
    for csep in ("；", ";"):
        if csep not in title:
            continue
        head, color = title.rsplit(csep, 1)
        color = color.strip()
        # 色名は短い語。長ければ説明文の一部なので採用しない
        if not (0 < len(color) <= 14):
            continue
        # 説明文が長すぎて色名まで表示されないケースだけ畳む
        if len(head) <= 40:
            continue
        for sep in ("：", ":"):
            desc_head, found, _ = head.partition(sep)
            if found and len(desc_head) >= 10:
                return f"{desc_head.strip()} {color}"
        return f"{head[:40].strip()}… {color}"
    return title


def hours_since(item: dict, now: datetime.datetime, today: datetime.date):
    """商品がセールとして初めて検出されてからの経過時間を返す。

    first_seen_at を記録する前から掲載されている商品には時刻が無いので、
    その場合は日付から概算する(商品の入れ替わりが速いため数日で解消する)。
    """
    at = item.get("since_at")
    if at:
        try:
            return (now - datetime.datetime.fromisoformat(at)).total_seconds() / 3600
        except ValueError:
            return None
    since = item.get("since")
    if not since:
        return None
    try:
        return (today - datetime.date.fromisoformat(since)).days * 24
    except ValueError:
        return None


# AdSenseダッシュボードではvignette(全画面)広告をサブドメイン単位で
# 無効化できないため、リンクごとにdata-google-vignette="false"を付与する
def render_book(item: dict, badge: str | None = None, is_new: bool = False) -> str:
    """商品カードを組み立てる。

    badgeを渡すと、どのジャンルの商品かを示すラベルをタイトル上に添える
    (「新着セール」など、ジャンル横断で並べる場所で使う)。
    """
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
    # 各ジャンルは実質お得度の順に並べているため、新しくセール入りした商品が
    # 埋もれる。並びは変えずに印だけ付けて見分けられるようにする
    badges = []
    if is_new:
        badges.append('<span class="nbadge">NEW</span>')
    if badge:
        badges.append(f'<span class="gbadge">{esc(badge)}</span>')
    badge_html = f'<div class="badges">{"".join(badges)}</div>' if badges else ""
    since_html = ""
    if item.get("since"):
        try:
            since_date = datetime.date.fromisoformat(item["since"])
            since_html = (
                f'<div class="since">{since_date.month}/{since_date.day}から掲載</div>'
            )
        except ValueError:
            pass
    return f"""<a class="book" href="{esc(item["url"])}" data-google-vignette="false" target="_blank" rel="noopener sponsored">
  {img_html}
  <div>
    {badge_html}<div class="t">{esc(shorten_title(item["title"]))}</div>
    {brand}
    <div class="price"><span class="now">&yen;{int(item["price"]):,}</span>{was_html}{off_html}</div>
    {points_html}
    {since_html}
  </div>
</a>"""


def generate_html(data: dict) -> str:
    fetched = datetime.datetime.fromisoformat(data["fetched_at"]).astimezone(
        datetime.timezone(datetime.timedelta(hours=9))
    )
    updated = fetched.strftime("%Y年%m月%d日 %H:%M")

    # 0件のジャンルは末尾に回す(config.jsonの並び順は維持しつつ、
    # 空のセクションが上位を占めないようにする)。値引きが稀な
    # Apple製品のようなジャンルでも、セール時には自然に上位へ戻る
    genres = sorted(
        data.get("genres") or [],
        key=lambda g: 0 if (g.get("items") or []) else 1,
    )

    sections = []

    # 「実質お得度」= 割引率 + ポイント還元率。fetch_deals.py の並び替えと
    # 同じ基準を使い、サイト側でも順位が食い違わないようにする
    def sort_key(b):
        return (b.get("percent_off") or 0) + (b.get("points_percent") or 0)

    today = fetched.date()

    # 新しくセール入りした商品をジャンル横断で集める。
    # 林檎ポチと同じ考え方だが、こちらはセールの入れ替わりが速く、
    # 7日窓だと掲載のほぼ全件が該当してしまうため窓をずっと短くする
    new_hours = CONFIG.get("new_arrival_hours", 12)
    # ガジェット中心に見せるため、値引き率が構造的に大きく上位を占めやすい
    # 消耗品系のジャンルは最上部の枠から外す(各ジャンルの節では通常どおり出る)
    excluded = set(CONFIG.get("new_arrival_exclude_genres") or [])
    arrivals = []
    for g in genres:
        if g["name"] in excluded:
            continue
        for b in g.get("items") or []:
            elapsed_h = hours_since(b, fetched, today)
            if elapsed_h is None:
                continue
            if elapsed_h <= new_hours:
                arrivals.append((sort_key(b), g["name"], b))
    if arrivals:
        arrivals.sort(key=lambda x: x[0], reverse=True)
        # ここは新着だけを集めた枠だが、各ジャンルの一覧と印を揃えて
        # 「これは新着だ」と一目で分かるようにする
        cards = "\n".join(
            render_book(b, badge=name, is_new=True) for _, name, b in arrivals
        )
        sections.append(
            '<details open id="new">\n'
            f'<summary><h2>🆕 新着セール ({len(arrivals)}件)</h2></summary>\n'
            f'<p class="cmeta">直近{new_hours}時間以内に'
            'セールを検出した商品です</p>\n'
            f'<div class="grid">\n{cards}\n</div>\n'
            '</details>'
        )

    for i, g in enumerate(genres):
        items = g.get("items") or []
        if items:
            books = "\n".join(
                render_book(
                    b,
                    is_new=(lambda h: h is not None and h <= new_hours)(
                        hours_since(b, fetched, today)
                    ),
                )
                for b in items
            )
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
    # 姉妹サイト・運営ブログへの相互リンク(ヘッダーとフッターの両方に出す)
    # ジャンルの絞り込みタブ。件数を添え、0件は押せないようにする
    tab_defs = [("all", "すべて", sum(len(g.get("items") or []) for g in genres))]
    for i, g in enumerate(genres):
        tab_defs.append((f"g{i}", g["name"], len(g.get("items") or [])))
    buttons = "\n".join(
        f'<button type="button" data-target="{tid}" '
        f'aria-selected="{"true" if tid == "all" else "false"}"'
        f'{" disabled" if n == 0 and tid != "all" else ""}>'
        f'{esc(name)}<span class="n">{n}</span></button>'
        for tid, name, n in tab_defs
    )
    tabs_html = f'<nav class="tabs" aria-label="ジャンル">\n{buttons}\n</nav>'

    # サイトの説明。データ元・更新頻度・掲載基準・運営者を明記して、
    # 検索エンジンやAIが「このサイトは何者か」を判断できるようにする
    about = CONFIG.get("about") or []
    about_html = ""
    if about:
        paras = "\n".join(f"<p>{esc(x)}</p>" for x in about)
        about_html = (
            f'<section class="about">\n'
            f'<h2>{esc(CONFIG["site_title"])}について</h2>\n{paras}\n</section>'
        )

    related = CONFIG.get("related_sites") or []
    related_html = ""
    if related:
        links = "\n".join(
            f'<a href="{esc(s["url"])}" data-google-vignette="false">{esc(s["name"])}'
            + (f'<span class="lbl"> {esc(s["desc"])}</span>' if s.get("desc") else "")
            + "</a>"
            for s in related
        )
        related_html = (
            f'<div class="sites"><span class="lbl">関連サイト</span>\n{links}\n</div>'
        )
    # 掲載の閾値はジャンルごとに上書きできるため、全体の値だけを書くと
    # 実態と食い違う。個別設定があるジャンルは併記する
    base_th = data["min_saving_percent"]
    overrides = [
        (g["name"], g["min_saving_percent"])
        for g in CONFIG["genres"]
        if g.get("min_saving_percent") is not None
        and g["min_saving_percent"] != base_th
    ]
    threshold_note = f"割引率とポイント還元率の合計が{base_th}%以上の商品を掲載"
    if overrides:
        detail = "、".join(f"{n}は{v}%" for n, v in overrides)
        threshold_note += f"({detail})"

    # メディアポリシー(プライバシーポリシー・AdSenseのCookie告知を含む)は
    # netaful.jp/policy.html に既にある。3サイトとも netaful.jp 配下なので
    # 各サイトに複製せずリンクで参照する
    policy_url = CONFIG.get("policy_url", "")
    policy_link = (
        f'｜ <a href="{esc(policy_url)}" data-google-vignette="false" style="color:inherit">メディアポリシー</a>\n'
        if policy_url
        else ""
    )

    # AdSenseの広告コード。ads.txtはルートドメイン(netaful.jp)のものが
    # サブドメインにも適用されるため、各サイトでの設置は不要
    adsense_id = CONFIG.get("adsense_client_id", "")
    adsense_tag = (
        '<script async src="https://pagead2.googlesyndication.com/pagead/js/'
        f'adsbygoogle.js?client={esc(adsense_id)}" crossorigin="anonymous"></script>'
        if adsense_id
        else ""
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
                # 毎時更新はこのサイトの強みだが、画面上の「最終更新」表記は
                # 機械には読めない。検索エンジンやAIに鮮度を伝えるため
                # 構造化データにも入れる
                "dateModified": fetched.isoformat(timespec="seconds"),
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
{adsense_tag}
<link rel="icon" type="image/png" href="assets/favicon.png">
<link rel="apple-touch-icon" href="assets/apple-touch-icon.png">
<meta property="og:type" content="website">
<meta property="og:title" content="{esc(page_title)}">
<meta property="og:description" content="{esc(CONFIG["site_description"])}">
<meta property="og:url" content="{esc(site_url)}">
<meta property="og:site_name" content="{esc(CONFIG["site_title"])}">
<meta property="og:locale" content="ja_JP">
<meta property="og:image" content="{esc(site_url)}assets/ogp.jpg">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta name="twitter:card" content="summary_large_image">
<link rel="alternate" type="application/rss+xml" title="RSS" href="rss.xml">
<script type="application/ld+json">{json_ld}</script>
<style>{CSS}</style>
</head>
<body>
<header>
<h1><a href="./" data-google-vignette="false"><img src="assets/logo.png" alt="" width="32" height="32">{esc(CONFIG["site_title"])}</a></h1>
<p>{esc(CONFIG["site_description"])} ｜ {esc(threshold_note)} ｜ 最終更新: {updated}</p>
{related_html}
</header>
{tabs_html}
<main>
{chr(10).join(sections)}
{about_html}
</main>
<footer>
価格・割引率は取得時点のものです。購入前にAmazonの商品ページで最新の価格をご確認ください。
Amazonのアソシエイトとして、当サイトは適格販売により収入を得ています。
当サイトはアクセス解析のためGoogle Analyticsを利用しています(データは匿名で収集され、Googleに送信されます)。
{policy_link}｜ <a href="rss.xml" data-google-vignette="false" style="color:inherit">RSS</a>
{related_html}
</footer>
<script>
// ジャンルタブ。該当セクション以外を隠すだけで、DOMからは取り除かない。
// 「すべて」に戻せば元通りになり、検索エンジンには常に全件が見えている
document.documentElement.classList.add("js");
(function () {{
  var tabs = document.querySelector(".tabs");
  if (!tabs) return;
  var sections = Array.prototype.slice.call(
    document.querySelectorAll("main > details")
  );
  function apply(target) {{
    sections.forEach(function (s) {{
      // 新着セールは全ジャンル横断の情報なので「すべて」のときだけ出す
      s.style.display = target === "all" || s.id === target ? "" : "none";
    }});
    tabs.querySelectorAll("button").forEach(function (b) {{
      b.setAttribute(
        "aria-selected", b.dataset.target === target ? "true" : "false"
      );
    }});
    if (history.replaceState) {{
      history.replaceState(null, "", target === "all" ? location.pathname : "#" + target);
    }}
  }}
  tabs.addEventListener("click", function (e) {{
    var b = e.target.closest("button");
    if (!b || b.disabled) return;
    apply(b.dataset.target);
  }});
  var initial = location.hash.replace("#", "");
  if (initial && document.getElementById(initial)) apply(initial);
}})();
</script>
</body>
</html>
"""


def generate_rss(data: dict) -> str:
    """RSSは在庫一覧ではなく「新しくセールに入った商品」のフィードにする。

    掲載中の全商品を流すと毎回ほぼ同じ内容になり、購読しても
    「何が新しいのか」が分からない。サイトの「新着セール」と同じ判定を使い、
    同じものが届くようにする。
    """
    site_url = CONFIG.get("site_url", "")
    now = datetime.datetime.now(datetime.timezone.utc).strftime(
        "%a, %d %b %Y %H:%M:%S +0000"
    )
    fetched = datetime.datetime.fromisoformat(data["fetched_at"]).astimezone(
        datetime.timezone(datetime.timedelta(hours=9))
    )
    today = fetched.date()
    new_hours = CONFIG.get("new_arrival_hours", 24)
    excluded = set(CONFIG.get("new_arrival_exclude_genres") or [])

    rows = []
    seen: set[str] = set()
    for genre in data.get("genres") or []:
        if genre["name"] in excluded:
            continue
        for b in genre.get("items") or []:
            if b["asin"] in seen:
                continue
            elapsed = hours_since(b, fetched, today)
            if elapsed is None or elapsed > new_hours:
                continue
            seen.add(b["asin"])
            rows.append((
                (b.get("percent_off") or 0) + (b.get("points_percent") or 0),
                genre["name"], b,
            ))
    rows.sort(key=lambda x: x[0], reverse=True)

    items_xml = []
    for _, gname, b in rows:
        off = f"【{b['percent_off']}%OFF】" if b.get("percent_off") else ""
        # guidに検出日を含める。同じ商品が後日また安くなったとき、
        # 購読者に新しい記事として届くようにするため
        since = (b.get("since_at") or b.get("since") or "")[:10]
        # pubDateを入れないとRSSリーダーは取得時刻を代わりに使い、
        # 前日に検出した商品も「今」届いたように見えてしまう
        pub = ""
        at = b.get("since_at")
        if at:
            try:
                pub = (
                    "\n<pubDate>"
                    + datetime.datetime.fromisoformat(at).strftime(
                        "%a, %d %b %Y %H:%M:%S %z"
                    )
                    + "</pubDate>"
                )
            except ValueError:
                pass
        items_xml.append(
            f"""<item>
<title>{esc(off + b["title"] + f" ¥{int(b['price']):,}")}</title>
<link>{esc(b["url"])}</link>
<guid isPermaLink="false">{esc(b["asin"] + "-" + since)}</guid>
<category>{esc(gname)}</category>{pub}
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
    # 本体の「新着セール」で外しているジャンルはウィジェットにも出さない。
    # ガジェット中心に見せる方針を埋め込み先でも揃えるため
    excluded = set(CONFIG.get("new_arrival_exclude_genres") or [])
    new_hours = CONFIG.get("new_arrival_hours", 12)
    generated = datetime.datetime.fromisoformat(data["fetched_at"]).astimezone(
        datetime.timezone(datetime.timedelta(hours=9))
    )
    today = generated.date()

    genre_pools = []
    for g in genres:
        if g["name"] in excluded:
            continue
        items = sorted(g.get("items") or [], key=savings, reverse=True)
        books = [
            {
                # 本体と同じくタイトルを整理する。iPhone Airのように
                # 末尾の色名が省略で消えると色違いが同じ商品に見えるため
                "title": shorten_title(b.get("title") or ""),
                "is_new": (lambda h: h is not None and h <= new_hours)(
                    hours_since(b, generated, today)
                ),
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
        # 見出しの誘導文言に出す掲載総数。除外ジャンルを引いた後の
        # プールではなくサイト全体の掲載数を使う
        "item_count": sum(len(g.get("items") or []) for g in genres),
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
