#!/usr/bin/env python3
"""Amazon Creators API からセール中のガジェット・家電を取得して data/sales.json に保存する。

2026年、Amazonは旧PA-API v5 (AWS Signature V4認証) を廃止し、
OAuth2認証のCreators APIに全面移行した。認証情報バージョン3.3
(Far East: JP/IN/AU) 向けのLwA(Login with Amazon)フローを使う。
姉妹サイト「電書ポチ」(kindle-sale-site) と同一のCreators API認証情報
(Amazonアソシエイトのアカウント単位で発行される) を使い回せる。

Kindle版との主な違い:
  - browseNodeId (カテゴリID) ではなく keywords (検索キーワード文字列)
    で検索する。家電・ガジェットは適切なbrowse node IDが無いため
  - 関連性フィルタは productGroup="Ebook" ではなく、config.json の
    genre.must_include_any のいずれかがタイトルに含まれるかで判定する
  - シリーズ重複排除(巻数を畳む処理)は書籍固有のロジックのため実装しない。
    ASINでの重複排除のみ行う
  - セール企画自動発見機能はv1では見送り。data/sales.jsonはcampaignsを
    持たないシンプルな構造にする

必要な環境変数:
  CREATORSAPI_CREDENTIAL_ID     : Creators APIの認証情報ID
  CREATORSAPI_CREDENTIAL_SECRET : Creators APIの認証情報シークレット
  CREATORSAPI_PARTNER_TAG       : アソシエイトタグ (例: xxxx-22)
"""

from __future__ import annotations

import collections
import datetime
import json
import math
import os
import sys
import time
import traceback
import urllib.error
import urllib.request
from pathlib import Path

TOKEN_URL = "https://api.amazon.co.jp/auth/o2/token"
API_URL = "https://creatorsapi.amazon/catalog/v1/searchItems"
# ASINを直接指定して引くエンドポイント。Amazonのキーワード検索は
# MacBook本体など一部のApple製品を結果に返さない(実測で確認)ため、
# 検索では絶対に届かない商品をconfigのwatch_asinsで補うのに使う
GET_ITEMS_URL = "https://creatorsapi.amazon/catalog/v1/getItems"
# getItemsが1回で受け付けるASIN数の上限
GET_ITEMS_BATCH = 10
# 子ASINを渡すと、その兄弟(同じ親ASINの全構成)を10件ずつ返すエンドポイント。
# 構成の多いApple製品を手動のwatch_asinsだけで追うと漏れるため、
# バリエーション自動探索(variation_discovery)が使う
GET_VARIATIONS_URL = "https://creatorsapi.amazon/catalog/v1/getVariations"
SCOPE = "creatorsapi::default"
MARKETPLACE = "www.amazon.co.jp"

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.json"
OUTPUT_PATH = ROOT / "data" / "sales.json"
# 商品(ASIN)ごとの初検出日。このサイトは毎時ゼロから検索し直す仕組みで
# 過去の掲載履歴を持たないため、「いつから掲載しているか」を自前で記録する。
# CIがこのファイルをコミットして毎時実行をまたいで永続化する
STATE_PATH = ROOT / "data" / "item_state.json"
# 検出されなくなった商品の状態を保持する日数。検索の取りこぼしによる
# 一時的な消失で初検出日がリセットされるのを防ぐための猶予期間
STATE_GRACE_DAYS = 14
# 送信予定の通知の置き場。実際の送信はサイトが公開されたあとに
# scripts/notify.py が行う。コミットしない一時ファイル
NOTIFY_PATH = ROOT / "data" / "pending_notification.json"
# バリエーション自動探索の状態(ファミリー=親ASINごとの構成と直近の割引率)。
# item_state.jsonと同様にCIがコミットして毎時実行をまたいで永続化する
VARIANTS_PATH = ROOT / "data" / "variants.json"

RESOURCES = [
    "itemInfo.title",
    "itemInfo.byLineInfo",
    "images.primary.medium",
    # savingBasis(定価)とsavings(割引)はpriceリソースに内包されて返る
    "offersV2.listings.price",
    "offersV2.listings.isBuyBoxWinner",
    "offersV2.listings.loyaltyPoints",
    # 親ASIN。検索・getItemsの応答に付くと、追加リクエスト無しで
    # 「その商品の構成違いの一族」を特定できる(バリエーション自動探索の種)
    "parentASIN",
]


def pick(d: dict, *keys):
    """複数の想定キー名から最初に見つかった値を返す(レスポンスの大文字小文字ゆれ対策)。"""
    for key in keys:
        if key in d:
            return d[key]
    return None


def get_access_token(credential_id: str, credential_secret: str) -> str:
    body = json.dumps(
        {
            "grant_type": "client_credentials",
            "client_id": credential_id,
            "client_secret": credential_secret,
            "scope": SCOPE,
        }
    )
    req = urllib.request.Request(
        TOKEN_URL,
        data=body.encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as res:
        payload = json.loads(res.read().decode("utf-8"))
    return payload["access_token"]


# セール品の発見効率を上げるため複数のソート順で検索する。
# Featuredだけだと割引品の遭遇率が低く、安い順は特価品が上位に集まりやすい
SORT_ORDERS = ["Featured", "Price:LowToHigh"]


def search_items(
    access_token: str,
    partner_tag: str,
    *,
    keywords: str | None = None,
    browse_node_id: str | None = None,
    search_index: str,
    item_page: int,
    sort_by: str,
) -> dict:
    # 注意: minSavingPercentは絶対に送らないこと。Creators APIのバグで、
    # このパラメータを付けると検索結果が壊れる(件数が激減し、対象外の
    # 商品が混入し、savings情報も返らなくなる)ことを実データで確認済み
    # (kindle-sale-site側で検証済み)。割引の絞り込みはparse_items側の
    # クライアントフィルタで行う
    body = {
        "partnerTag": partner_tag,
        "partnerType": "Associates",
        "marketplace": MARKETPLACE,
        "searchIndex": search_index,
        "itemPage": item_page,
        "itemCount": 10,
        "sortBy": sort_by,
        "resources": RESOURCES,
    }
    if keywords:
        body["keywords"] = keywords
    if browse_node_id:
        body["browseNodeId"] = browse_node_id

    payload = json.dumps(body)
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "x-marketplace": MARKETPLACE,
    }
    req = urllib.request.Request(
        API_URL, data=payload.encode("utf-8"), headers=headers, method="POST"
    )
    with urllib.request.urlopen(req, timeout=30) as res:
        return json.loads(res.read().decode("utf-8"))


# リトライを使い切って取得を諦めた回数。ジャンルが0件になったとき
# 「本当にセール品が無い」のか「APIが落ちて取れなかった」のかを区別する
# ために数える。区別できないと、障害のときに空のセクションだらけのサイトで
# 正常だった前回の公開内容を上書きしてしまう
GIVE_UPS = 0


def give_up(label: str, reason: str) -> dict:
    """リトライ上限に達したことを記録する。戻り値は従来どおり空のdict。"""
    global GIVE_UPS
    GIVE_UPS += 1
    print(f"[warn] {label}: {reason}", file=sys.stderr)
    return {}


def search_with_retry(
    auth: dict,
    partner_tag: str,
    *,
    keywords: str | None = None,
    browse_node_id: str | None = None,
    search_index: str,
    item_page: int,
    sort_by: str,
    label: str,
) -> dict:
    """search_itemsを429/401/ネットワークエラーに耐性を持たせて呼ぶ。

    authは {"token", "id", "secret"} を持つdict。401時はtokenを再取得して
    差し替える(呼び出し側にも新tokenが見えるようdictで持ち回る)。
    """
    for attempt in range(3):
        try:
            return search_items(
                auth["token"],
                partner_tag,
                keywords=keywords,
                browse_node_id=browse_node_id,
                search_index=search_index,
                item_page=item_page,
                sort_by=sort_by,
            )
        except urllib.error.HTTPError as e:
            if e.code == 401 and attempt < 2:
                try:
                    auth["token"] = get_access_token(auth["id"], auth["secret"])
                except (urllib.error.URLError, TimeoutError, OSError):
                    pass
                continue
            if e.code == 429 and attempt < 2:
                time.sleep(5 * (attempt + 1))
                continue
            return give_up(
                label,
                f"HTTP {e.code} {e.read().decode('utf-8', 'replace')[:300]}",
            )
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            if attempt < 2:
                time.sleep(5 * (attempt + 1))
                continue
            return give_up(label, str(e))
    return give_up(label, "リトライ上限に達しました")


def get_items(access_token: str, partner_tag: str, asins: list[str]) -> dict:
    body = {
        "partnerTag": partner_tag,
        "partnerType": "Associates",
        "marketplace": MARKETPLACE,
        "itemIds": asins,
        "resources": RESOURCES,
    }
    payload = json.dumps(body)
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "x-marketplace": MARKETPLACE,
    }
    req = urllib.request.Request(
        GET_ITEMS_URL, data=payload.encode("utf-8"), headers=headers, method="POST"
    )
    with urllib.request.urlopen(req, timeout=30) as res:
        return json.loads(res.read().decode("utf-8"))


def get_items_with_retry(
    auth: dict, partner_tag: str, asins: list[str], label: str
) -> dict:
    """get_itemsを401/429/ネットワークエラーに耐性を持たせて呼ぶ。

    search_with_retryと同じ方針。レスポンス形式もsearchItemsと違い
    searchResultではなくitemsResultに入るため、呼び出し側で詰め替える。
    """
    for attempt in range(3):
        try:
            return get_items(auth["token"], partner_tag, asins)
        except urllib.error.HTTPError as e:
            if e.code == 401 and attempt < 2:
                try:
                    auth["token"] = get_access_token(auth["id"], auth["secret"])
                except (urllib.error.URLError, TimeoutError, OSError):
                    pass
                continue
            if e.code == 429 and attempt < 2:
                time.sleep(5 * (attempt + 1))
                continue
            return give_up(
                label,
                f"HTTP {e.code} {e.read().decode('utf-8', 'replace')[:300]}",
            )
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            if attempt < 2:
                time.sleep(5 * (attempt + 1))
                continue
            return give_up(label, str(e))
    return give_up(label, "リトライ上限に達しました")


def get_variations(
    access_token: str, partner_tag: str, asin: str, page: int
) -> dict:
    body = {
        "partnerTag": partner_tag,
        "partnerType": "Associates",
        "marketplace": MARKETPLACE,
        "asin": asin,
        "variationPage": page,
        "resources": RESOURCES,
    }
    payload = json.dumps(body)
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "x-marketplace": MARKETPLACE,
    }
    req = urllib.request.Request(
        GET_VARIATIONS_URL, data=payload.encode("utf-8"), headers=headers, method="POST"
    )
    with urllib.request.urlopen(req, timeout=30) as res:
        return json.loads(res.read().decode("utf-8"))


def get_variations_with_retry(
    auth: dict, partner_tag: str, asin: str, page: int, label: str
) -> dict:
    """get_variationsを401/429/ネットワークエラーに耐性を持たせて呼ぶ。

    get_items_with_retryと同じ方針。諦めたときは空のdictを返す
    (呼び出し側のスイープは、空応答を「失敗」として前回の状態を保持する)。
    """
    for attempt in range(3):
        try:
            return get_variations(auth["token"], partner_tag, asin, page)
        except urllib.error.HTTPError as e:
            if e.code == 401 and attempt < 2:
                try:
                    auth["token"] = get_access_token(auth["id"], auth["secret"])
                except (urllib.error.URLError, TimeoutError, OSError):
                    pass
                continue
            if e.code == 429 and attempt < 2:
                time.sleep(5 * (attempt + 1))
                continue
            return give_up(
                label,
                f"HTTP {e.code} {e.read().decode('utf-8', 'replace')[:300]}",
            )
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            if attempt < 2:
                time.sleep(5 * (attempt + 1))
                continue
            return give_up(label, str(e))
    return give_up(label, "リトライ上限に達しました")


def parse_items(
    response: dict,
    partner_tag: str,
    min_saving: int,
    must_include_any: list[str],
    known_brands: list[str],
    exclude_any: list[str] | None = None,
) -> tuple[list[dict], int, int, int]:
    """(掲載対象のリスト, 割引不足で除外した件数, 関連性フィルタで除外した件数,
    ブランド不明で除外した件数) を返す。

    exclude_anyはタイトルに含まれていたら除外する語。「充電器」で検索すると
    ケーブルやモバイルバッテリーも混ざるといった、must_include_anyだけでは
    切り分けられないケースに使う。
    """
    items = []
    no_discount = 0
    irrelevant = 0
    unknown_brand = 0
    search_result = pick(response, "searchResult", "SearchResult") or {}
    for item in pick(search_result, "items", "Items") or []:
        asin = pick(item, "asin", "ASIN")
        item_info = pick(item, "itemInfo", "ItemInfo") or {}
        title = pick(pick(item_info, "title", "Title") or {}, "displayValue", "DisplayValue")
        offers = pick(item, "offersV2", "OffersV2") or {}
        listings = pick(offers, "listings", "Listings") or []
        if not asin or not title or not listings:
            continue

        # searchIndex+keywords検索は関連性の低い商品も拾いやすいため、
        # タイトルにmust_include_anyのいずれかを含む商品だけに絞り込む
        # (大文字小文字は区別しない)
        title_lower = title.lower()
        # 他ジャンルの商品や周辺グッズを取り除く(例: 充電器ジャンルから
        # ケーブル・モバイルバッテリーを外す)。関連性フィルタの一種として
        # まとめてirrelevantに計上する
        if exclude_any and any(kw.lower() in title_lower for kw in exclude_any):
            irrelevant += 1
            continue
        if must_include_any and not any(
            kw.lower() in title_lower for kw in must_include_any
        ):
            irrelevant += 1
            continue

        # 複数出品がある場合は購入ボックス(実際に買われる出品)を優先する
        listing = next(
            (
                l
                for l in listings
                if pick(l, "isBuyBoxWinner", "IsBuyBoxWinner")
            ),
            listings[0],
        )
        price_block = pick(listing, "price", "Price") or {}
        money = pick(price_block, "money", "Money") or {}
        price = pick(money, "amount", "Amount")
        if price is None:
            continue
        # 金額は浮動小数点数(例: 4990.0)で返る。円は整数なので丸める
        price = int(round(price))
        if price == 0:
            no_discount += 1
            continue

        basis_block = pick(price_block, "savingBasis", "SavingBasis") or {}
        basis_money = pick(basis_block, "money", "Money") or {}
        basis = pick(basis_money, "amount", "Amount")
        basis = int(round(basis)) if basis is not None else None

        savings = pick(price_block, "savings", "Savings") or {}
        percent_off = pick(savings, "percentage", "Percentage")
        if percent_off is None and basis and basis > price:
            percent_off = round((basis - price) / basis * 100)

        loyalty = pick(listing, "loyaltyPoints", "LoyaltyPoints") or {}
        points = pick(loyalty, "points", "Points")
        # ポイント数のみが返るため、還元率は価格から自前で算出する
        points_percent = (
            round(points / price * 100) if points and price else None
        )

        # minSavingPercentはAPI側で無視されることが実データで確認された
        # (割引なし商品が多数返ってくる)ため、割引の有無はここで判定する。
        # 割引率とポイント還元率の合算が閾値を下回る商品は掲載しない
        if (percent_off or 0) + (points_percent or 0) < min_saving:
            no_discount += 1
            continue

        # ブランド名。家電には著者の概念が無いため、byLineInfo.brandを
        # 「ブランド」として使う(Kindle版のauthorに相当するフィールド)。
        # titleと同様にdisplayValueを持つオブジェクトとして返ってくる
        byline = pick(item_info, "byLineInfo", "ByLineInfo") or {}
        brand_block = pick(byline, "brand", "Brand") or {}
        brand = pick(brand_block, "displayValue", "DisplayValue")

        # 無名ブランドは「定価を吊り上げてから大幅値引きに見せる」手口が
        # 実データで多数確認されたため、known_brandsに一致しない商品は除外する
        if known_brands and not (
            brand and any(b.lower() in brand.lower() for b in known_brands)
        ):
            unknown_brand += 1
            continue

        images = pick(item, "images", "Images") or {}
        medium = pick(pick(images, "primary", "Primary") or {}, "medium", "Medium") or {}
        image = pick(medium, "url", "URL")

        url = pick(item, "detailPageURL", "DetailPageURL") or (
            f"https://www.amazon.co.jp/dp/{asin}?tag={partner_tag}"
        )

        items.append(
            {
                "asin": asin,
                "title": title,
                "brand": brand,
                "price": price,
                "list_price": basis,
                "percent_off": percent_off,
                "points": points,
                "points_percent": points_percent,
                "image": image,
                "url": url,
            }
        )
    return items, no_discount, irrelevant, unknown_brand


# ---------------------------------------------------------------------------
# バリエーション自動探索
#
# 構成(バリエーション)の多いApple製品は、手動のwatch_asins登録だけでは
# 漏れる(Apple Watch Ultra 3は30構成中14件、iPad Airは60〜72構成中3件が
# 漏れていた)。親ASINごとの「ファミリー」を覚えておき、数ファミリーずつ
# getVariationsで全構成をスイープして、割引が出ている構成を拾う。
# 毎時のgetItemsで追うのは「ホット」な構成(割引が閾値の半分以上)だけにして、
# リクエスト数の増加を抑える(設計: memory/project_kaden_variation_design.md)。
# 純粋関数(ネットワークに出ない)と、APIを叩く関数(run_sweep以下)に分けてある。
# ---------------------------------------------------------------------------

VARIANTS_VERSION = 1
# getVariationsは1ページ10件
VARIATIONS_PAGE_SIZE = 10
# 「構成なし(単品)」と判定したファミリーを再確認する間隔(日)。
# 一時的な空応答で単品と誤判定しても、永久に探索から外れないようにする
NO_VARIATIONS_RECHECK_DAYS = 14
LABEL_LENGTH = 40
# 孤児(種なのにスイープ応答の構成一覧に含まれないASIN)の記録を保持する日数。
# 種として観測されるたびに日付は更新されるので、使われなくなった記録だけが消える
ALIAS_KEEP_DAYS = 90

# configに無いキーの既定値。enabledを既定falseにしているのは、
# 設定が無いサイトの出力を従来と完全に同じに保つため。dry_runは既定trueで、
# 本番有効化は設定で明示的にfalseにしたときだけ(誤設定で掲載を変えない)
VARIATION_DEFAULTS = {
    "enabled": False,
    "dry_run": True,
    "genres": ["Apple製品"],
    # 1回の実行での最大件数。巡回の速さを決めるのは下のinterval(定常状態)で、
    # こちらは立ち上げ時や突発的な負荷の上限として働く
    "sweep_families_per_run": 20,
    # 同じファミリーを再スイープするまでの最短間隔(時間)。ファミリー数が増えても
    # 巡回が「約6時間で一巡」から意図せず伸びないよう、件数ではなく時間で決める
    "sweep_min_interval_hours": 6,
    "max_requests_per_run": 70,
    "hot_floor_ratio": 0.5,
    "member_grace_days": 14,
    "exclude_any": ["セット"],
}


def load_variation_config(raw) -> dict:
    """config.jsonのvariation_discoveryを、既定値で補って検証した形で返す。"""
    cfg = {k: (list(v) if isinstance(v, list) else v) for k, v in VARIATION_DEFAULTS.items()}
    if not isinstance(raw, dict):
        return cfg
    cfg["enabled"] = raw.get("enabled") is True
    cfg["dry_run"] = raw.get("dry_run", True) is not False
    for key in ("genres", "exclude_any"):
        value = raw.get(key)
        if isinstance(value, list) and all(isinstance(v, str) for v in value):
            cfg[key] = list(value)
    for key in ("sweep_families_per_run", "max_requests_per_run", "member_grace_days"):
        value = raw.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            cfg[key] = value
    for key in ("hot_floor_ratio", "sweep_min_interval_hours"):
        value = raw.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0:
            cfg[key] = float(value)
    return cfg


def new_family(seed: str, genre: str, label: str = "") -> dict:
    return {
        "seed": seed,
        "label": label[:LABEL_LENGTH],
        "genre": genre,
        "last_sweep_at": None,
        "variation_count": None,
        "no_variations": False,
        "members": {},
        # 孤児の種(ASIN→最終確認日)。メンバーではないので「カバー済み」にはしない
        "aliases": {},
    }


def load_variants() -> dict:
    """variants.jsonを読む。無い・壊れている・マーカー混入のときは空で始める。

    item_state.jsonと違い、壊れていても中止しない。ここの内容は掲載の
    初検出日のような「失うと戻せない履歴」ではなく、スイープで作り直せる
    キャッシュだから。中止すると毎時の更新まで止まってしまい、割に合わない
    (失うのは、再スイープに要する数時間分のAPIリクエストだけ)。
    """
    empty = {"version": VARIANTS_VERSION, "families": {}}
    try:
        raw = VARIANTS_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        return empty  # 初回実行時は無くて当然
    except OSError as e:
        print(f"[warn] {VARIANTS_PATH.name} を読めません ({e})。空から始めます", file=sys.stderr)
        return empty
    if "<<<<<<<" in raw or ">>>>>>>" in raw:
        print(
            f"[warn] {VARIANTS_PATH.name} にコンフリクトマーカーが混入しています。"
            "空から始めて再スイープします",
            file=sys.stderr,
        )
        return empty
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        print(
            f"[warn] {VARIANTS_PATH.name} が壊れています ({e})。空から始めて再スイープします",
            file=sys.stderr,
        )
        return empty
    families = data.get("families") if isinstance(data, dict) else None
    if not isinstance(families, dict):
        print(
            f"[warn] {VARIANTS_PATH.name} の形式が不正です。空から始めて再スイープします",
            file=sys.stderr,
        )
        return empty
    clean = {}
    for key, fam in families.items():
        if not isinstance(fam, dict):
            continue
        members = fam.get("members")
        aliases = fam.get("aliases")  # 古い状態ファイルには無い
        clean[key] = {
            "seed": fam.get("seed") or key,
            "label": fam.get("label") or "",
            "genre": fam.get("genre"),
            "last_sweep_at": fam.get("last_sweep_at"),
            "variation_count": fam.get("variation_count"),
            "no_variations": bool(fam.get("no_variations")),
            "members": {
                asin: m
                for asin, m in (members.items() if isinstance(members, dict) else [])
                if isinstance(m, dict)
            },
            "aliases": {
                a: d
                for a, d in (aliases.items() if isinstance(aliases, dict) else [])
                if isinstance(d, str)
            },
        }
    return {"version": VARIANTS_VERSION, "families": clean}


def save_variants(variants: dict) -> None:
    # 毎時コミットされるため、キー順と字下げを固定して差分を安定させる
    VARIANTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    VARIANTS_PATH.write_text(
        json.dumps(
            {"version": VARIANTS_VERSION, "families": variants["families"]},
            ensure_ascii=False,
            indent=1,
            sort_keys=True,
        ),
        encoding="utf-8",
    )


def item_title(item: dict):
    item_info = pick(item, "itemInfo", "ItemInfo") or {}
    return pick(pick(item_info, "title", "Title") or {}, "displayValue", "DisplayValue")


def offer_numbers(item: dict):
    """商品の購入ボックス出品から {"price", "pct", "points_pct"} を返す。

    価格が取れない(出品なし・価格なし・0円)ときはNone。計算はparse_itemsと
    同じ(割引率はsavings.percentage、無ければ定価との差から四捨五入)。
    parse_itemsは割引不足の商品を捨ててしまうため、「今いくら割引か」を
    ホット判定の材料として別に取り出すのに使う
    """
    offers = pick(item, "offersV2", "OffersV2") or {}
    listings = pick(offers, "listings", "Listings") or []
    if not listings:
        return None
    listing = next(
        (l for l in listings if pick(l, "isBuyBoxWinner", "IsBuyBoxWinner")),
        listings[0],
    )
    price_block = pick(listing, "price", "Price") or {}
    money = pick(price_block, "money", "Money") or {}
    price = pick(money, "amount", "Amount")
    if price is None:
        return None  # violatesMAPだけの出品など
    price = int(round(price))
    if price == 0:
        return None
    basis_block = pick(price_block, "savingBasis", "SavingBasis") or {}
    basis_money = pick(basis_block, "money", "Money") or {}
    basis = pick(basis_money, "amount", "Amount")
    basis = int(round(basis)) if basis is not None else None
    savings = pick(price_block, "savings", "Savings") or {}
    pct = pick(savings, "percentage", "Percentage")
    if pct is None and basis and basis > price:
        pct = round((basis - price) / basis * 100)
    loyalty = pick(listing, "loyaltyPoints", "LoyaltyPoints") or {}
    points = pick(loyalty, "points", "Points")
    points_pct = round(points / price * 100) if points else None
    return {"price": price, "pct": pct or 0, "points_pct": points_pct or 0}


def member_entry(item: dict, today_iso: str) -> dict:
    """構成1件の記録。価格が無い構成は落とさず pct/points_pct を null にして残す。"""
    nums = offer_numbers(item)
    return {
        "pct": nums["pct"] if nums else None,
        "points_pct": nums["points_pct"] if nums else None,
        "last_seen": today_iso,
    }


def seeds_from_search_response(
    response: dict,
    must_include_any: list[str],
    known_brands: list[str],
    exclude_any: list[str] | None,
) -> list[tuple]:
    """検索応答から、バリエーション探索の種 (asin, parentASIN, title) を取り出す。

    parse_itemsと同じ関連性フィルタ(exclude_any → must_include_any →
    known_brands)を通った商品だけを返す。通さないと第三者製のケースなど
    無関係な商品の親まで拾ってしまう。割引の有無・価格の有無は問わない
    (今は割引していない構成も、スイープで兄弟の割引を見つける入口になる)。
    parentASINが付いていない商品はparentをNoneで返す。
    """
    seeds = []
    search_result = pick(response, "searchResult", "SearchResult") or {}
    for item in pick(search_result, "items", "Items") or []:
        asin = pick(item, "asin", "ASIN")
        title = item_title(item)
        if not asin or not title:
            continue
        title_lower = title.lower()
        if exclude_any and any(kw.lower() in title_lower for kw in exclude_any):
            continue
        if must_include_any and not any(
            kw.lower() in title_lower for kw in must_include_any
        ):
            continue
        if known_brands:
            item_info = pick(item, "itemInfo", "ItemInfo") or {}
            byline = pick(item_info, "byLineInfo", "ByLineInfo") or {}
            brand_block = pick(byline, "brand", "Brand") or {}
            brand = pick(brand_block, "displayValue", "DisplayValue")
            if not (brand and any(b.lower() in brand.lower() for b in known_brands)):
                continue
        seeds.append((asin, pick(item, "parentASIN", "ParentASIN") or None, title))
    return seeds


def register_seeds(
    families: dict,
    genre: str,
    static_asins: list[str],
    search_seeds: list[tuple],
    today_iso: str | None = None,
) -> tuple:
    """種をfamiliesに「未スイープ」で登録する(APIは使わない)。

    返り値は (種の件数, 新規ファミリーのリスト[(キー, タイトル, 検索由来か)])。
    watch_asinsの種は親ASINがまだ分からないので、子ASIN自身をキーにして
    登録し、スイープで親が分かった時点で付け替える。既にどこかのファミリーの
    メンバー・種になっているASINは登録しない(重複ファミリーを作らない)。
    孤児(aliases)も登録しない。孤児を再登録すると、毎回そのスイープに枠を
    食われ、本物のファミリーの巡回が進まなくなる
    """
    known = set()  # メンバー・種・孤児として既に追跡されているASIN
    alias_owner = {}
    for key, fam in families.items():
        known.add(fam.get("seed"))
        known.update(fam.get("members") or {})
        for alias in fam.get("aliases") or {}:
            known.add(alias)
            alias_owner[alias] = fam
    seed_asins = set()
    new = []

    def touch(asin):
        # 種として観測された孤児は、最終確認日を更新して保持を延ばす
        if today_iso and asin in alias_owner:
            alias_owner[asin]["aliases"][asin] = today_iso

    for asin in static_asins:
        seed_asins.add(asin)
        touch(asin)
        if asin in known or asin in families:
            continue
        families[asin] = new_family(asin, genre)
        known.add(asin)
        new.append((asin, "", False))
    for asin, parent, title in search_seeds:
        seed_asins.add(asin)
        touch(asin)
        key = parent or asin
        if key in families or asin in known:
            continue
        families[key] = new_family(asin, genre, title)
        known.add(asin)
        new.append((key, title, True))
    return len(seed_asins), new


def _parse_iso_date(value):
    try:
        return datetime.datetime.fromisoformat(value).date()
    except (TypeError, ValueError):
        return None


def _parse_iso_datetime(value, tz=None):
    """ISO文字列をdatetimeにする。壊れた・欠けた値はNone(=未スイープ扱い)。

    タイムゾーン無しの値はtz(既定はJST)とみなす。awareとnaiveの混在で
    比較が例外にならないようにするため
    """
    try:
        dt = datetime.datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz or datetime.timezone(datetime.timedelta(hours=9)))
    return dt


def _estimated_requests(fam: dict) -> int:
    """そのファミリーのスイープに要するリクエスト数の見積もり。

    未スイープは構成数が分からないので1ページ分(実際のページ数はスイープ時に
    分かり、実行内の上限チェックで超過を止める)。スイープ済みは前回の構成数から
    """
    if fam.get("no_variations") or _parse_iso_datetime(fam.get("last_sweep_at")) is None:
        return 1
    count = fam.get("variation_count")
    if not isinstance(count, int) or count <= 0:
        return 1
    return max(1, math.ceil(count / VARIATIONS_PAGE_SIZE))


def select_sweep_families(
    families: dict,
    genre: str,
    max_families: int,
    max_requests: int,
    exclude=(),
    today_dt=None,
    now=None,
    min_interval_hours: float = 0,
) -> list[str]:
    """今回スイープするファミリーのキーを、優先順に返す。

    資格は「未スイープ(last_sweep_atが無い・壊れている)、または前回のスイープから
    min_interval_hours時間以上経過」。nowを渡さなければ時間の資格は問わない。
    未スイープが最優先、次にlast_sweep_atが古い順。max_familiesと、見積もりの
    累計がmax_requestsを超える直前で打ち切る(超えるファミリーは処理しない)。
    no_variations(単品)のファミリーは、NO_VARIATIONS_RECHECK_DAYS日経つまで
    対象にしない。
    """
    candidates = []
    for key, fam in families.items():
        if fam.get("genre") != genre or key in exclude:
            continue
        last = fam.get("last_sweep_at")
        if fam.get("no_variations"):
            last_date = _parse_iso_date(last)
            if (
                last_date is None
                or today_dt is None
                or (today_dt - last_date).days < NO_VARIATIONS_RECHECK_DAYS
            ):
                continue
        last_dt = _parse_iso_datetime(last)
        if last_dt is None:
            # 未スイープ。壊れた値も同じ扱いにして、次のスイープで直す
            candidates.append((0, "", key))
            continue
        if (
            now is not None
            and (now - last_dt).total_seconds() < min_interval_hours * 3600
        ):
            continue
        candidates.append((1, last_dt.astimezone(datetime.timezone.utc).isoformat(), key))
    candidates.sort()
    picked = []
    used = 0
    for _, _, key in candidates:
        if len(picked) >= max_families:
            break
        est = _estimated_requests(families[key])
        if used + est > max_requests:
            break
        picked.append(key)
        used += est
    return picked


def prune_members(members: dict, today_dt, grace_days: int) -> dict:
    """猶予期間を過ぎたメンバーを落とす。

    構成数は揺れる(在庫切れの構成が一時的に外れる)ため、スイープで
    返らなくても猶予の間は保持する。即座に落とすと掲載が点滅する
    """
    kept = {}
    for asin, m in members.items():
        last = _parse_iso_date(m.get("last_seen"))
        if last is not None and (today_dt - last).days <= grace_days:
            kept[asin] = m
    return kept


def prune_aliases(aliases: dict, today_dt) -> dict:
    """最終確認日が古すぎる(または壊れた)孤児の記録を捨てる。"""
    kept = {}
    for asin, seen in aliases.items():
        seen_date = _parse_iso_date(seen)
        if seen_date is not None and (today_dt - seen_date).days <= ALIAS_KEEP_DAYS:
            kept[asin] = seen
    return kept


def family_parent(items: list[dict]):
    """スイープ応答のitemsの親ASIN(最頻値)。1つも付いていなければNone。"""
    counts = collections.Counter(
        p for p in (pick(i, "parentASIN", "ParentASIN") for i in items) if p
    )
    return counts.most_common(1)[0][0] if counts else None


def apply_sweep(
    families: dict, key: str, items: list[dict], today_dt, now_iso: str, grace_days: int
) -> str:
    """スイープの結果をfamiliesに反映し、最終的なファミリーのキーを返す。

    - itemsが空: 一度もメンバーが取れていないなら単品(no_variations)。
      以前メンバーが取れていたなら、種のASINが廃番になっただけかもしれないので
      単品扱いにせず、メンバーは猶予期間で保持する
    - 応答の親ASINがキーと違えば、親を正としてキーを付け替える
    - 他のファミリーの種になっていたASINが返ったら、そのファミリーを吸収する
    - 種のASINが返った構成一覧に含まれなければ(在庫切れ等の孤児)、aliasesに
      記録する。記録しないと次回の種の登録で復活し、毎回スイープ枠を食う
    """
    fam = families[key]
    today_iso = today_dt.isoformat()
    if not items:
        if fam["members"]:
            fam["members"] = prune_members(fam["members"], today_dt, grace_days)
            fam["no_variations"] = False
        else:
            fam["no_variations"] = True
        fam["last_sweep_at"] = now_iso
        return key

    fetched = {}
    first_title = None
    titles = {}
    for item in items:
        asin = pick(item, "asin", "ASIN")
        if not asin:
            continue
        fetched[asin] = member_entry(item, today_iso)
        titles[asin] = item_title(item)
        if first_title is None:
            first_title = titles[asin]

    new_key = family_parent(items) or key
    previous = dict(fam["members"])
    aliases = dict(fam.get("aliases") or {})
    target = fam
    if new_key != key:
        old = families.pop(key)
        existing = families.get(new_key)
        if existing is None:
            families[new_key] = old
            target = old
        else:
            # 同じ親のファミリーが既にあれば、そちらに統合する(孤児の記録も引き継ぐ)
            previous.update(existing["members"])
            aliases.update(existing.get("aliases") or {})
            target = existing
    old_seeds = {fam.get("seed"), target.get("seed")}
    members = prune_members(previous, today_dt, grace_days)
    members.update(fetched)
    target["members"] = members
    target["genre"] = fam.get("genre")
    target["last_sweep_at"] = now_iso
    target["no_variations"] = False
    target["variation_count"] = len(fetched)
    # 次回のスイープに使う種は、今回実際に返ったASINにしておく
    # (種が廃番になって空応答に変わるのを防ぐ)
    if target.get("seed") not in fetched:
        target["seed"] = fam["seed"] if fam.get("seed") in fetched else sorted(fetched)[0]
    if not target.get("label"):
        target["label"] = (titles.get(target["seed"]) or first_title or "")[:LABEL_LENGTH]

    member_keys = set(members)
    for other_key in list(families):
        if other_key == new_key:
            continue
        other = families[other_key]
        if other.get("genre") != target["genre"]:
            continue
        if other.get("seed") in member_keys or other_key in member_keys:
            aliases.update(other.get("aliases") or {})
            del families[other_key]
    # 種だったのに返らなかったASINは孤児として記録する。後で構成として
    # 返ったものは外す。古い記録は捨てる
    for asin in old_seeds:
        if asin and asin not in fetched:
            aliases[asin] = today_iso
    for other in families.values():
        for asin in fetched:
            (other.get("aliases") or {}).pop(asin, None)
    target["aliases"] = prune_aliases(
        {a: d for a, d in aliases.items() if a not in fetched}, today_dt
    )
    return new_key


def hot_asins(
    families: dict, genre: str, min_saving, hot_floor_ratio: float
) -> list[str]:
    """割引+ポイント還元が掲載閾値のhot_floor_ratio倍以上のメンバー。順序は決定的。

    掲載閾値ちょうどの構成だけを追うと、割引が出始めた構成を次のスイープまで
    見逃すため、閾値の半分くらいから毎時の更新対象にしておく
    """
    threshold = min_saving * hot_floor_ratio
    hot = []
    for key in sorted(families):
        fam = families[key]
        if fam.get("genre") != genre or fam.get("no_variations"):
            continue
        for asin in sorted(fam["members"]):
            m = fam["members"][asin]
            if (m.get("pct") or 0) + (m.get("points_pct") or 0) >= threshold:
                hot.append(asin)
    return hot


def effective_watch(
    static_watch: list[str],
    families: dict,
    genre: str,
    min_saving,
    hot_floor_ratio: float,
) -> list[str]:
    """毎時getItemsで取得するASINのリスト。

    静的watch_asinsのうち、スイープ済みファミリーのメンバーになっているものは
    外す(コールドな構成はスイープでだけ見る)。まだスイープされていない
    ASINやno_variations(単品)は従来どおり残す。そこへ各ファミリーのホット構成を足す。
    """
    covered = set()
    for fam in families.values():
        if fam.get("genre") == genre and not fam.get("no_variations"):
            covered.update(fam["members"])
    result = []
    seen = set()
    for asin in static_watch:
        if asin not in covered and asin not in seen:
            seen.add(asin)
            result.append(asin)
    for asin in hot_asins(families, genre, min_saving, hot_floor_ratio):
        if asin not in seen:
            seen.add(asin)
            result.append(asin)
    return result


def build_member_index(families: dict, genre: str) -> dict:
    """メンバーASIN → ファミリーキー。"""
    index = {}
    for key, fam in families.items():
        if fam.get("genre") == genre:
            for asin in fam["members"]:
                index[asin] = key
    return index


def refresh_members(
    families: dict, member_index: dict, raw_items: list[dict], today_iso: str
) -> int:
    """毎時のgetItems結果で、メンバーの割引率と最終確認日を更新する。

    parse_itemsが捨てた(割引不足の)商品も対象にする。割引が閾値未満に
    戻った構成をコールドに戻すには、その「今の割引率」が要るため。
    getItemsが返さなかったASINは触らない。
    """
    updated = 0
    for raw in raw_items:
        asin = pick(raw, "asin", "ASIN")
        key = member_index.get(asin)
        if key is None or key not in families:
            continue
        families[key]["members"][asin] = member_entry(raw, today_iso)
        updated += 1
    return updated


def is_excluded(title: str, exclude_any: list[str]) -> bool:
    title_lower = (title or "").lower()
    return any(kw.lower() in title_lower for kw in exclude_any if kw)


def split_excluded(
    parsed_items: list[dict], exclude_any: list[str], protected_asins
) -> tuple:
    """掲載候補を (掲載するもの, 除外するもの) に分ける。

    除外語(既定は「セット」。別商品とのセットは載せない方針)を含むタイトルを
    除く。人が名指しで登録した静的watch_asins(protected_asins)は対象外
    """
    kept, excluded = [], []
    for parsed in parsed_items:
        if parsed["asin"] not in protected_asins and is_excluded(
            parsed["title"], exclude_any
        ):
            excluded.append(parsed)
        else:
            kept.append(parsed)
    return kept, excluded


def listings_from_swept_items(
    raw_items: list[dict], partner_tag: str, min_saving
) -> list[dict]:
    """スイープで取れた構成から、掲載条件を満たすものをparse_itemsと同じ形で返す。

    関連性フィルタは掛けない(親ファミリーは検索・登録時に関連性を確認済みで、
    構成違いはタイトルが変わるため must_include_any 等で落ちる恐れがある)。
    """
    wrapped = {"searchResult": {"items": raw_items}}
    parsed, _, _, _ = parse_items(wrapped, partner_tag, min_saving, [], [], None)
    return parsed


def api_error_reason(res: dict):
    """応答のエラーのうち、NoResults(=バリエーション無し)以外のコードを返す。無ければNone。

    単品ASINに getVariations を投げると、HTTP 200で
    {"errors":[{"code":"NoResults"}]} が返る(2026-10-05に実機で確認)。
    これは正常な「単品」だが、それ以外のエラー(一時的な内部エラー等)まで
    単品と判定すると、そのファミリーが14日間スイープから外れてしまう
    """
    codes = []
    for err in pick(res, "errors", "Errors") or []:
        code = pick(err, "code", "Code") if isinstance(err, dict) else None
        if code != "NoResults":
            codes.append(str(code))
    return ",".join(codes) or None


def fetch_family_variations(
    auth: dict, partner_tag: str, seed: str, max_requests: int, label: str
) -> dict:
    """1ファミリーの全ページを取る。

    返り値: {"ok", "items", "requests", "reason"}。okがFalseなら、状態は
    更新せず次回に回す(失敗・上限超過)。構成なし(単品)は ok=True, items=[]。
    使ったリクエスト数(リトライの内側は数えない)をrequestsに入れる。
    """
    result = {"ok": False, "items": [], "requests": 0, "reason": ""}
    if max_requests < 1:
        result["reason"] = "リクエスト上限"
        return result
    res = get_variations_with_retry(auth, partner_tag, seed, 1, label)
    result["requests"] = 1
    time.sleep(1.2)
    if not res:
        result["reason"] = "取得失敗"
        return result
    vr = pick(res, "variationsResult", "VariationsResult") or {}
    first = pick(vr, "items", "Items") or []
    summary = pick(vr, "variationSummary", "VariationSummary") or {}
    if not first or not summary:
        # 単品ASIN(AirTag等)は NoResults が返る。それ以外のエラーは失敗として
        # 次回に回す(単品と誤判定すると14日間スイープから外れるため)
        reason = api_error_reason(res)
        if reason:
            result["reason"] = f"APIエラー {reason}"
            return result
        result["ok"] = True
        return result
    page_count = pick(summary, "pageCount", "PageCount")
    if not isinstance(page_count, int) or page_count < 1:
        page_count = 1
    # 1ページ目は取得済み。残りを取ると上限を超えるなら、中途半端に取らない
    if page_count > max_requests:
        result["reason"] = f"{page_count}ページで上限超過"
        return result
    items = list(first)
    for page in range(2, page_count + 1):
        res = get_variations_with_retry(auth, partner_tag, seed, page, label)
        result["requests"] += 1
        time.sleep(1.2)
        if not res:
            result["reason"] = f"{page}ページ目の取得失敗"
            return result
        vr = pick(res, "variationsResult", "VariationsResult") or {}
        page_items = pick(vr, "items", "Items") or []
        if not page_items and api_error_reason(res):
            result["reason"] = f"{page}ページ目のAPIエラー {api_error_reason(res)}"
            return result
        items.extend(page_items)
    seen = set()
    unique = []
    for item in items:
        asin = pick(item, "asin", "ASIN")
        if asin and asin not in seen:
            seen.add(asin)
            unique.append(item)
    result["ok"] = True
    result["items"] = unique
    return result


def run_sweep(
    auth: dict,
    partner_tag: str,
    families: dict,
    genre: str,
    vd: dict,
    budget: dict,
    today_dt,
    now_iso: str,
) -> tuple:
    """上限の範囲でファミリーを順にスイープする。

    budgetは実行全体で共有する {"requests", "families"} のカウンタ。
    返り値は (統計, スイープで取れた構成のraw item一覧)。
    1件ずつ選び直すのは、スイープで他ファミリーを吸収すると、事前に選んだ
    リストにそのキーが残って枠を無駄にするため。
    """
    stats = {"swept": 0, "requests": 0, "failed": 0}
    swept_items = []
    attempted = set()
    # 実行時刻(mainのvd_now)。last_sweep_atとの比較で、再スイープの資格を判定する
    now_dt = _parse_iso_datetime(now_iso)
    while True:
        left_families = vd["sweep_families_per_run"] - budget["families"]
        left_requests = vd["max_requests_per_run"] - budget["requests"]
        if left_families <= 0 or left_requests <= 0:
            break
        keys = select_sweep_families(
            families, genre, 1, left_requests, exclude=attempted, today_dt=today_dt,
            now=now_dt, min_interval_hours=vd["sweep_min_interval_hours"],
        )
        if not keys:
            break
        key = keys[0]
        attempted.add(key)
        budget["families"] += 1
        stats["swept"] += 1
        fam = families[key]
        label = f"{genre} (getVariations {fam['seed']})"
        result = fetch_family_variations(
            auth, partner_tag, fam["seed"], left_requests, label
        )
        budget["requests"] += result["requests"]
        stats["requests"] += result["requests"]
        if not result["ok"]:
            # 前回のメンバー情報とlast_sweep_atは変えず、次回に回す
            stats["failed"] += 1
            print(
                f"[warn] {label}: スイープを見送り ({result['reason']})",
                file=sys.stderr,
            )
            continue
        new_key = apply_sweep(
            families, key, result["items"], today_dt, now_iso, vd["member_grace_days"]
        )
        attempted.add(new_key)
        swept_items.extend(result["items"])
        done = families[new_key]
        if done["no_variations"]:
            print(f"  [スイープ] {key} 構成なし(単品)")
        else:
            print(
                f"  [スイープ] {new_key} {done['label']} "
                f"{done['variation_count']}構成 ({result['requests']}リクエスト)"
            )
    return stats, swept_items


def discover_variations(
    auth: dict,
    partner_tag: str,
    families: dict,
    genre: str,
    static_watch: list[str],
    search_seeds: list[tuple],
    genre_min_saving,
    vd: dict,
    budget: dict,
    today_dt,
    now_iso: str,
) -> tuple:
    """種の登録 → スイープ。返り値は (統計, スイープで取れたraw itemの一覧)。

    追加機能なので、ここで何が起きても実行は落とさない。応答の形が変わった
    ときなどは握りつぶさず、原因をstderrに出して前回の状態のまま続ける
    """
    n_seeds, new_families = register_seeds(
        families, genre, static_watch, search_seeds, today_dt.isoformat()
    )
    for key, title, from_search in new_families:
        if from_search:
            print(f"  [新ファミリー] {key} {title[:LABEL_LENGTH]}")
    stats = {
        "seeds": n_seeds,
        "new": len(new_families),
        "swept": 0,
        "requests": 0,
        "failed": 0,
    }
    swept_items = []
    try:
        sweep_stats, swept_items = run_sweep(
            auth, partner_tag, families, genre, vd, budget, today_dt, now_iso
        )
        stats.update(sweep_stats)
    except Exception as e:  # noqa: BLE001 - 追加機能の失敗で本体を止めない
        print(
            f"[warn] {genre}: バリエーション探索のスイープで例外 {e!r}。"
            "前回の状態のまま続けます",
            file=sys.stderr,
        )
        print(traceback.format_exc(), file=sys.stderr)
    mine = [f for f in families.values() if f.get("genre") == genre]
    stats["unswept"] = sum(
        1 for f in mine if not f.get("last_sweep_at") and not f.get("no_variations")
    )
    stats["members"] = sum(len(f["members"]) for f in mine)
    stats["hot"] = len(hot_asins(families, genre, genre_min_saving, vd["hot_floor_ratio"]))
    return stats, swept_items


def print_variation_summary(
    genre: str,
    stats: dict,
    candidates: int,
    excluded: dict,
    would_list: list[dict],
    dry_run: bool,
) -> None:
    """1ジャンル分の探索の要約と、除外・ドライランの内訳をログに出す。"""
    print(
        f"[バリエーション探索] {genre}: 種{stats['seeds']}件(新規{stats['new']}) / "
        f"スイープ{stats['swept']}ファミリー・{stats['requests']}リクエスト"
        f"(失敗{stats['failed']}・残り未スイープ{stats['unswept']}) / "
        f"構成の総数{stats['members']} / ホット{stats['hot']}件 / "
        f"掲載候補{candidates}件(セット除外{len(excluded)}件)"
    )
    # 除外の見直しができるよう、除外したタイトルは必ず出す
    for asin, title in sorted(excluded.items()):
        print(f"  [除外] {asin} {title}")
    if dry_run:
        print(
            f"[バリエーション探索/ドライラン] {genre}: "
            f"本番なら新たに掲載される構成 {len(would_list)}件"
        )
        for parsed in would_list:
            print(
                f"  {parsed['asin']} {parsed['percent_off'] or 0}% "
                f"{parsed['title'][:50]}"
            )


def write_pending_notifications(notifications: list[dict]) -> None:
    """送信予定の通知を書き出す。送信するのはデプロイ後のscripts/notify.py。

    以前はここで直接ntfyへ送っていたが、この後に控えるサイト生成・状態の
    コミット・Pagesデプロイのどれかが失敗すると「通知は届いたのにサイトは
    前回のまま」になっていた。なお、ジャンルをまたいで同時に何件も新着が
    見つかることがあるため、商品ごとではなく1回の実行につき1通にまとめる
    方針は変えていない(新着と値下げで各1通)。送るものが無いときは前回の
    残骸を送ってしまわないようファイルごと消す
    """
    if not notifications:
        NOTIFY_PATH.unlink(missing_ok=True)
        return
    NOTIFY_PATH.parent.mkdir(parents=True, exist_ok=True)
    NOTIFY_PATH.write_text(
        json.dumps(notifications, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def load_state() -> dict:
    """商品(ASIN)ごとの初検出日を読む。壊れていたら中止する。

    そのまま先に進むと初検出日が全件今日にリセットされ、全商品にNEWの印が
    付いてしまう。CIがその結果をコミットする前に止める
    """
    try:
        raw = STATE_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}  # 初回実行時は状態ファイルが無くて当然
    # gitのコンフリクトマーカーが混入したまま
    # コミットされた事故が実際に起きたため明示的に検出する
    if "<<<<<<<" in raw or ">>>>>>>" in raw:
        print(
            f"[error] {STATE_PATH.name} にコンフリクトマーカーが混入しています。"
            "初検出日が失われるため中止します",
            file=sys.stderr,
        )
        sys.exit(1)
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        print(
            f"[error] {STATE_PATH.name} が壊れています ({e})。"
            "初検出日が失われるため中止します",
            file=sys.stderr,
        )
        sys.exit(1)


def main() -> int:
    credential_id = os.environ.get("CREATORSAPI_CREDENTIAL_ID")
    credential_secret = os.environ.get("CREATORSAPI_CREDENTIAL_SECRET")
    partner_tag = os.environ.get("CREATORSAPI_PARTNER_TAG")
    if not all([credential_id, credential_secret, partner_tag]):
        print(
            "環境変数 CREATORSAPI_CREDENTIAL_ID / CREATORSAPI_CREDENTIAL_SECRET / "
            "CREATORSAPI_PARTNER_TAG を設定してください",
            file=sys.stderr,
        )
        return 1

    # 状態ファイルの検証はAPIを叩く前に済ませる。壊れているまま取得を
    # 走らせても最後に中止するだけで、API消費が丸ごと無駄になる
    state = load_state()

    try:
        access_token = get_access_token(credential_id, credential_secret)
    except urllib.error.HTTPError as e:
        print(
            f"[error] トークン取得に失敗: HTTP {e.code} "
            f"{e.read().decode('utf-8', 'replace')[:300]}",
            file=sys.stderr,
        )
        return 1

    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    min_saving = config.get("min_saving_percent", 20)
    pages = config.get("pages_per_genre", 3)

    auth = {
        "token": access_token,
        "id": credential_id,
        "secret": credential_secret,
    }
    sort_key = lambda x: (x["percent_off"] or 0) + (x["points_percent"] or 0)  # noqa: E731

    # バリエーション自動探索。enabledでなければ以降すべて何もしない
    # (variants.jsonも読み書きしない)。状態の読み込みは、壊れていても
    # 中止せず空で始める(load_variantsの説明を参照)
    vd = load_variation_config(config.get("variation_discovery"))
    vd_enabled = vd["enabled"]
    variants = load_variants() if vd_enabled else None
    vd_families = variants["families"] if variants else {}
    # スイープの上限は実行全体で共有する(ジャンルが増えても合計が上限を超えない)
    vd_budget = {"requests": 0, "families": 0}
    jst = datetime.timezone(datetime.timedelta(hours=9))
    vd_now = datetime.datetime.now(jst)
    vd_today_dt = vd_now.date()
    vd_today = vd_today_dt.isoformat()
    vd_now_iso = vd_now.isoformat(timespec="seconds")

    genres = []
    # 取得に失敗したせいで0件になったジャンルを控える。合計件数だけを見る
    # 従来のガードでは、1ジャンルでも生き残っていれば素通りしてしまい、
    # 「現在セール中の商品はありません」が並ぶサイトを公開してしまう
    genre_failures = []
    for genre in config["genres"]:
        seen = set()
        items = []
        give_ups_before = GIVE_UPS
        dropped = 0
        irrelevant_total = 0
        unknown_brand_total = 0
        search_index = genre.get("search_index", "All")
        vd_genre_on = vd_enabled and genre["name"] in vd["genres"]
        # dry_runの間は、スイープと状態の更新だけを行い、掲載は一切変えない
        vd_production = vd_genre_on and not vd["dry_run"]
        search_seeds = []
        # 掲載の閾値はジャンルごとに上書きできる。Apple製品は元々値引きが
        # 小さく(セール時でも10〜20%程度)、全体と同じ基準だとセールに
        # なっていても載らないため、このジャンルだけ低く設定している
        genre_min_saving = genre.get("min_saving_percent", min_saving)
        must_include_any = genre.get("must_include_any") or []
        known_brands = genre.get("known_brands") or []
        exclude_any = genre.get("exclude_any") or []
        # 検索源: キーワード検索に加え、Amazon運営の「〜特集」棚が
        # 見つかっているジャンルはbrowseNodeIdでの検索も追加する
        # (キーワード検索より新着・関連性の高い商品に出会いやすい反面、
        # 完全にクリーンではないためmust_include_any/known_brandsは
        # 変わらず適用する)
        sources = [("keywords", kw) for kw in genre.get("keywords") or []]
        sources += [("node", nid) for nid in genre.get("browse_node_ids") or []]
        for (src_type, src_value), sort_by, page in (
            (s, so, p)
            for s in sources
            for so in SORT_ORDERS
            for p in range(1, pages + 1)
        ):
            res = search_with_retry(
                auth,
                partner_tag,
                keywords=src_value if src_type == "keywords" else None,
                browse_node_id=src_value if src_type == "node" else None,
                search_index=search_index,
                item_page=page,
                sort_by=sort_by,
                label=f"{genre['name']} ({src_type}:{src_value}) page {page}",
            )
            if vd_genre_on:
                search_seeds.extend(
                    seeds_from_search_response(
                        res, must_include_any, known_brands, exclude_any
                    )
                )
            parsed_items, no_discount, irrelevant, unknown_brand = parse_items(
                res, partner_tag, genre_min_saving, must_include_any, known_brands,
                exclude_any,
            )
            dropped += no_discount
            irrelevant_total += irrelevant
            unknown_brand_total += unknown_brand
            for parsed in parsed_items:
                if parsed["asin"] not in seen:
                    seen.add(parsed["asin"])
                    items.append(parsed)
            time.sleep(1.2)

        # 検索に出てこない商品をASIN直指定で補う。
        # MacBook本体のようにAmazonのキーワード検索が結果に返さない商品が
        # 実在し、キーワードをどう変えても届かないことを実測で確認している。
        # 人が明示的に選んだASINなので関連性フィルタ(must_include_any /
        # known_brands / exclude_any)は掛けず、割引率の条件だけを適用する。
        # これにより「普段は載らないが、セールで安くなった時だけ載る」動きになる
        static_watch = genre.get("watch_asins") or []
        watch_asins = static_watch
        sweep_give_ups = 0
        vd_stats = None
        vd_swept_items = []
        vd_excluded = {}  # 除外した構成 asin -> title(後で見直せるようログに出す)
        member_index = {}
        if vd_genre_on:
            # 種の登録とスイープ。スイープの失敗はgenre_failuresの判定に数えない
            # (追加機能の失敗で、本体の「取得失敗なら中止」ガードを動かさないため)
            # ので、GIVE_UPSの増分を測って後で差し引く
            give_ups_pre_sweep = GIVE_UPS
            vd_stats, vd_swept_items = discover_variations(
                auth, partner_tag, vd_families, genre["name"], static_watch,
                search_seeds, genre_min_saving, vd, vd_budget, vd_today_dt, vd_now_iso,
            )
            sweep_give_ups = GIVE_UPS - give_ups_pre_sweep
            member_index = build_member_index(vd_families, genre["name"])
        if vd_production:
            # 毎時の取得は「未スイープ・単品の静的ASIN + ホット構成」だけにする
            watch_asins = effective_watch(
                static_watch, vd_families, genre["name"], genre_min_saving,
                vd["hot_floor_ratio"],
            )
        static_set = set(static_watch)
        watch_hits = 0
        watch_no_discount = 0
        # 名指しで登録したのに出てこないASINの理由を残す。割引が足りないのか、
        # APIがそもそも返していないのかを、ログだけで切り分けられるようにする
        watch_misses = []
        for i in range(0, len(watch_asins), GET_ITEMS_BATCH):
            batch = watch_asins[i : i + GET_ITEMS_BATCH]
            res = get_items_with_retry(
                auth, partner_tag, batch, label=f"{genre['name']} (watch_asins)"
            )
            # getItemsはitemsResultに入るため、parse_itemsが読むsearchResultの
            # 形に詰め替えて同じ解析・整形ロジックを使い回す
            wrapped = {"searchResult": pick(res, "itemsResult", "ItemsResult") or {}}
            if vd_genre_on:
                # ホット構成の今の割引率を状態に反映する(割引不足で
                # parse_itemsが捨てるものも含め、コールドに戻すために必要)
                refresh_members(
                    vd_families,
                    member_index,
                    pick(wrapped["searchResult"], "items", "Items") or [],
                    vd_today,
                )
            reasons = {}
            for raw in pick(wrapped["searchResult"], "items", "Items") or []:
                raw_asin = pick(raw, "asin", "ASIN")
                raw_offers = pick(raw, "offersV2", "OffersV2") or {}
                raw_listings = pick(raw_offers, "listings", "Listings") or []
                if not raw_listings:
                    reasons[raw_asin] = "オファーなし"
                    continue
                raw_listing = next(
                    (
                        l
                        for l in raw_listings
                        if pick(l, "isBuyBoxWinner", "IsBuyBoxWinner")
                    ),
                    raw_listings[0],
                )
                raw_price = pick(raw_listing, "price", "Price") or {}
                raw_savings = pick(raw_price, "savings", "Savings") or {}
                raw_points = pick(raw_listing, "loyaltyPoints", "LoyaltyPoints") or {}
                reasons[raw_asin] = (
                    f"{pick(raw_savings, 'percentage', 'Percentage') or 0}%"
                    f"+{pick(raw_points, 'points', 'Points') or 0}pt"
                )
            parsed_items, no_discount, _, _ = parse_items(
                wrapped, partner_tag, genre_min_saving, [], [], None
            )
            watch_no_discount += no_discount
            hit_asins = {parsed["asin"] for parsed in parsed_items}
            for asin in batch:
                # 探索で足したホット構成(割引が閾値の半分以上)は、掲載に届かない
                # ものが毎時大量に並んでログが埋まるため、未掲載の一覧は静的ASINだけにする
                if asin not in hit_asins and (not vd_production or asin in static_set):
                    watch_misses.append(f"{asin}({reasons.get(asin, 'API未返却')})")
            for parsed in parsed_items:
                if parsed["asin"] not in seen:
                    # 探索で見つけた構成(静的watch_asins以外)は、別商品との
                    # セットなどを除く。人が名指しで登録したASINは対象外
                    if (
                        vd_production
                        and parsed["asin"] not in static_set
                        and is_excluded(parsed["title"], vd["exclude_any"])
                    ):
                        vd_excluded[parsed["asin"]] = parsed["title"]
                        continue
                    seen.add(parsed["asin"])
                    items.append(parsed)
                    watch_hits += 1
            time.sleep(1.2)

        # スイープで取れた構成のうち掲載条件を満たすもの。本番なら
        # 同じ実行内で掲載に加える(毎時のホット更新を待たない)。
        # dry_runでは加えず、加えたらどうなるかをログに出すだけ
        vd_candidates = 0
        vd_would_list = []
        if vd_genre_on:
            kept, excluded = split_excluded(
                listings_from_swept_items(vd_swept_items, partner_tag, genre_min_saving),
                vd["exclude_any"],
                static_set,
            )
            vd_candidates = len(kept)
            for parsed in excluded:
                vd_excluded[parsed["asin"]] = parsed["title"]
            for parsed in kept:
                if parsed["asin"] in seen:
                    continue
                if vd_production:
                    seen.add(parsed["asin"])
                    items.append(parsed)
                else:
                    vd_would_list.append(parsed)

        items.sort(key=sort_key, reverse=True)
        genres.append({"name": genre["name"], "items": items})
        # 0件でも、取得が全部成功しているなら本当にセールが無いだけ
        # (エアフライヤーのように元々件数の少ないジャンルでは普通に起きる)。
        # 取得を諦めた回数が1回でもあるなら、取りこぼしを疑う
        genre_give_ups = GIVE_UPS - give_ups_before - sweep_give_ups
        if not items and genre_give_ups:
            genre_failures.append(f"{genre['name']}({genre_give_ups}回失敗)")
        msg = (
            f"{genre['name']}スキャン: セール品{len(items)}件 "
            f"(割引不足で{dropped}件, 関連性フィルタで{irrelevant_total}件, "
            f"無名ブランドで{unknown_brand_total}件を除外)"
        )
        if watch_asins:
            msg += (
                f" [注目ASIN{len(watch_asins)}件中 {watch_hits}件を追加"
                f"・{watch_no_discount}件は割引不足]"
            )
        print(msg)
        if watch_misses:
            print(f"  {genre['name']}の未掲載ASIN: {' '.join(watch_misses)}")
        if vd_genre_on:
            print_variation_summary(
                genre["name"], vd_stats, vd_candidates, vd_excluded,
                vd_would_list, vd["dry_run"],
            )

    if genre_failures:
        # 障害はジャンル単位で起きる(ジャンルごとに別々の検索を投げているため)。
        # 空のセクションを並べて公開するより、前回の公開内容を残す方がマシ。
        # 「最終更新」の時刻が古いままになるので、読者からも古さは分かる
        print(
            "[error] 取得に失敗してセール品が0件になったジャンルがあります: "
            + " / ".join(genre_failures)
            + "。空のセクションで前回の公開内容を上書きしないよう中止します",
            file=sys.stderr,
        )
        return 1

    total = sum(len(g["items"]) for g in genres)
    if total == 0:
        # 取得はすべて成功したのに全ジャンル0件、というのも通常ありえない。
        # 空サイトで前回のデプロイを上書きしないよう失敗させる
        print("[error] 全ジャンルとも0件のため中止します", file=sys.stderr)
        return 1

    # 初検出日(stateはAPIを叩く前にload_state()で読んである)を
    # 掲載開始日として表示する
    today_dt = datetime.datetime.now(
        datetime.timezone(datetime.timedelta(hours=9))
    ).date()
    today = today_dt.isoformat()
    now_iso = datetime.datetime.now(
        datetime.timezone(datetime.timedelta(hours=9))
    ).isoformat(timespec="seconds")
    # 既にセール中の商品がさらに値下げされても「新着」には出てこない
    # (asinはstateに既存のため)。割引率の深掘りだけを別枠で検知する。
    # 数十円単位の揺れで誤検知しないよう5pt以上の深掘りだけを対象にする
    PRICE_DROP_THRESHOLD = 5
    new_state = {}
    new_items: list[str] = []
    price_drops: list[str] = []
    for g in genres:
        for item in g["items"]:
            asin = item["asin"]
            prev = state.get(asin) or {}
            if asin not in state:
                new_items.append(item["title"])
            else:
                prev_off, cur_off = prev.get("percent_off"), item.get("percent_off")
                if (
                    isinstance(prev_off, (int, float))
                    and isinstance(cur_off, (int, float))
                    and cur_off - prev_off >= PRICE_DROP_THRESHOLD
                ):
                    price_drops.append(
                        f"{item['title']}({prev_off}%→{cur_off}%)"
                    )
            first_seen = prev.get("first_seen") or today
            # 「新着セール」を時間単位で出せるよう初検出の時刻も残す。
            # 日付だけだと深夜0時をまたいだ瞬間に新着が消えてしまう
            first_seen_at = prev.get("first_seen_at") or now_iso
            item["since"] = first_seen
            item["since_at"] = first_seen_at
            new_state[asin] = {
                "first_seen": first_seen,
                "first_seen_at": first_seen_at,
                "last_seen": today,
                "title": item["title"],
                "percent_off": item.get("percent_off"),
            }

    # 今回検出されなかった商品も猶予期間内は状態を保持する。
    # 検索は「キーワードごとに数ページ」という限られた窓しか見ないため、
    # セール継続中の商品でもランキング変動で一時的に窓の外に出ることが
    # 頻繁にある。即座に削除すると復活時に初検出日が今日にリセットされ、
    # 「7/23から掲載」の表示が実態と食い違ってしまう
    kept = 0
    for asin, entry in state.items():
        if asin in new_state:
            continue
        last_seen = entry.get("last_seen") or entry.get("first_seen")
        try:
            elapsed = (today_dt - datetime.date.fromisoformat(last_seen)).days
        except (TypeError, ValueError):
            continue  # 日付が壊れているエントリは破棄する
        if elapsed <= STATE_GRACE_DAYS:
            new_state[asin] = entry
            kept += 1
    if kept:
        print(f"(一時的に検出されなかった{kept}件は掲載開始日を保持)")

    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(
        json.dumps(new_state, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if vd_enabled:
        # item_state.jsonと同じく、取得ガードを通過した実行でだけ保存する
        save_variants(variants)

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(
        json.dumps(
            {
                "fetched_at": datetime.datetime.now(
                    datetime.timezone.utc
                ).isoformat(),
                "min_saving_percent": min_saving,
                "genres": genres,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"saved: {OUTPUT_PATH}")

    notifications = []
    if new_items:
        shown = new_items[:5]
        rest = len(new_items) - len(shown)
        summary = "、".join(shown) + (f"(ほか{rest}件)" if rest > 0 else "")
        notifications.append(
            {
                "title": f"家電ポチ: 新着{len(new_items)}件",
                "message": summary,
                "click": config.get("site_url", ""),
            }
        )

    if price_drops:
        shown = price_drops[:5]
        rest = len(price_drops) - len(shown)
        summary = "、".join(shown) + (f"(ほか{rest}件)" if rest > 0 else "")
        notifications.append(
            {
                "title": f"家電ポチ: 値下げ{len(price_drops)}件",
                "message": summary,
                "click": config.get("site_url", ""),
            }
        )

    write_pending_notifications(notifications)

    return 0


if __name__ == "__main__":
    sys.exit(main())
