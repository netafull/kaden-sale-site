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

import datetime
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

TOKEN_URL = "https://api.amazon.co.jp/auth/o2/token"
API_URL = "https://creatorsapi.amazon/catalog/v1/searchItems"
SCOPE = "creatorsapi::default"
MARKETPLACE = "www.amazon.co.jp"

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.json"
OUTPUT_PATH = ROOT / "data" / "sales.json"

RESOURCES = [
    "itemInfo.title",
    "itemInfo.byLineInfo",
    "images.primary.medium",
    # savingBasis(定価)とsavings(割引)はpriceリソースに内包されて返る
    "offersV2.listings.price",
    "offersV2.listings.isBuyBoxWinner",
    "offersV2.listings.loyaltyPoints",
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
    keywords: str,
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
        "keywords": keywords,
        "itemPage": item_page,
        "itemCount": 10,
        "sortBy": sort_by,
        "resources": RESOURCES,
    }

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


def search_with_retry(
    auth: dict,
    partner_tag: str,
    *,
    keywords: str,
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
            print(
                f"[warn] {label}: HTTP {e.code} "
                f"{e.read().decode('utf-8', 'replace')[:300]}",
                file=sys.stderr,
            )
            return {}
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            if attempt < 2:
                time.sleep(5 * (attempt + 1))
                continue
            print(f"[warn] {label}: {e}", file=sys.stderr)
            return {}
    return {}


def parse_items(
    response: dict, partner_tag: str, min_saving: int, must_include_any: list[str]
) -> tuple[list[dict], int, int]:
    """(掲載対象のリスト, 割引不足で除外した件数, 関連性フィルタで除外した件数) を返す。"""
    items = []
    no_discount = 0
    irrelevant = 0
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
    return items, no_discount, irrelevant


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

    genres = []
    for genre in config["genres"]:
        seen = set()
        items = []
        dropped = 0
        irrelevant_total = 0
        keywords_list = genre.get("keywords") or []
        search_index = genre.get("search_index", "All")
        must_include_any = genre.get("must_include_any") or []
        for kw, sort_by, page in (
            (k, s, p)
            for k in keywords_list
            for s in SORT_ORDERS
            for p in range(1, pages + 1)
        ):
            res = search_with_retry(
                auth,
                partner_tag,
                keywords=kw,
                search_index=search_index,
                item_page=page,
                sort_by=sort_by,
                label=f"{genre['name']} ({kw}) page {page}",
            )
            parsed_items, no_discount, irrelevant = parse_items(
                res, partner_tag, min_saving, must_include_any
            )
            dropped += no_discount
            irrelevant_total += irrelevant
            for parsed in parsed_items:
                if parsed["asin"] not in seen:
                    seen.add(parsed["asin"])
                    items.append(parsed)
            time.sleep(1.2)

        items.sort(key=sort_key, reverse=True)
        genres.append({"name": genre["name"], "items": items})
        print(
            f"{genre['name']}スキャン: セール品{len(items)}件 "
            f"(割引不足で{dropped}件除外, 関連性フィルタで{irrelevant_total}件除外)"
        )

    total = sum(len(g["items"]) for g in genres)
    if total == 0:
        # 全ジャンル0件はAPI障害・キー失効の可能性が高い。
        # 空サイトで前回のデプロイを上書きしないよう失敗させる
        print("[error] 全ジャンルとも0件のため中止します", file=sys.stderr)
        return 1

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
    return 0


if __name__ == "__main__":
    sys.exit(main())
