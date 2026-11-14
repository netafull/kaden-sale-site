#!/usr/bin/env python3
"""ジャンルページ(generate_site.py / fetch_deals.py)のテスト。

ブランドページのテスト(test_brand_pages.py)と同方針で、ネットワーク
には一切出ない。sales.json / item_state.json はテスト内で組み立てた
辞書を渡し、実データには依存しない。

実行: python3 -m unittest discover -s tests
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import fetch_deals as fd  # noqa: E402
import generate_site as gs  # noqa: E402

GENRE_DEFS = [
    {"slug": "charger", "name": "充電器"},
    {"slug": "mobile-battery", "name": "モバイルバッテリー"},
    {"slug": "wireless-earbuds", "name": "ワイヤレスイヤホン"},
]
BRAND_DEFS = [
    {"slug": "anker", "name": "Anker", "match": ["Anker", "アンカー", "Soundcore"]},
]

TEST_CONFIG = {
    "site_title": "家電ポチ",
    "site_tagline": "",
    "site_description": "セール情報サイト",
    "meta_description": "テスト用の説明文",
    "site_url": "https://example.test/",
    "brand_pages": BRAND_DEFS,
    "genre_pages": GENRE_DEFS,
}


def item(asin, title, brand, price=5000, pct=30):
    return {
        "asin": asin,
        "title": title,
        "brand": brand,
        "price": price,
        "list_price": 8000,
        "percent_off": pct,
        "points": None,
        "points_percent": None,
        "image": "https://example.test/img.jpg",
        "url": f"https://www.amazon.co.jp/dp/{asin}?tag=test-22",
    }


def sales(*genre_items):
    """genre_items は (ジャンル名, [item...]) の並び。"""
    return {
        "fetched_at": "2026-11-14T03:00:00+00:00",
        "min_saving_percent": 15,
        "genres": [
            {"name": name, "items": items} for name, items in genre_items
        ],
    }


class GenreConfigPatch(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.dict(gs.CONFIG, TEST_CONFIG, clear=False)
        self.addCleanup(patcher.stop)
        patcher.start()


class TestGenrePageDefs(GenreConfigPatch):
    def test_realistic_defs_pass(self):
        self.assertEqual(
            [d["slug"] for d in gs.genre_page_defs()],
            ["charger", "mobile-battery", "wireless-earbuds"],
        )

    def test_broken_entries_dropped(self):
        broken = [
            {"slug": "ok", "name": "OK"},
            {"slug": "", "name": "NG"},
            {"name": "NG2"},
            {"slug": "ng3"},
            "not-a-dict",
        ]
        with mock.patch.dict(gs.CONFIG, {"genre_pages": broken}):
            self.assertEqual([d["slug"] for d in gs.genre_page_defs()], ["ok"])


class TestCollectGenreData(GenreConfigPatch):
    def test_current_items_from_buckets_and_deduped(self):
        data = sales(
            ("充電器", [item("A1", "Anker 充電器", "Anker"),
                        item("A2", "無名 充電器", "無名")]),
            # A1が別ジャンルのバケツにも出る場合は最初のバケツが優先
            ("モバイルバッテリー", [item("A1", "Anker 充電器", "Anker"),
                                     item("A3", "Anker バッテリー", "Anker")]),
            # genre_pages に無いジャンルは対象外
            ("未知ジャンル", [item("A9", "何か", "Anker")]),
        )
        genres = gs.collect_genre_data(data, {})
        self.assertEqual([g["slug"] for g in genres],
                         ["charger", "mobile-battery"])
        self.assertEqual([i["asin"] for i in genres[0]["current"]],
                         ["A1", "A2"])
        self.assertEqual([i["asin"] for i in genres[1]["current"]], ["A3"])

    def test_history_from_past_and_archived_by_genre(self):
        state = {
            "H1": {
                "first_seen": "2026-11-01", "last_seen": "2026-11-10",
                "title": "Anker 充電器 新", "brand": "Anker",
                "genre": "充電器", "price": 4000, "percent_off": 40,
                "archived": True,
                "past": [{
                    "first_seen": "2026-09-01", "last_seen": "2026-09-15",
                    "title": "Anker 充電器 旧", "brand": "Anker",
                    "genre": "充電器", "price": 4500, "percent_off": 35,
                }],
            },
            # 掲載中エントリの当ラウンドは履歴に入らない(pastのみ入る)
            "H2": {
                "first_seen": "2026-11-12", "last_seen": "2026-11-14",
                "title": "Soundcore イヤホン", "brand": "Soundcore",
                "genre": "ワイヤレスイヤホン", "price": 6000,
                "percent_off": 25,
                "past": [{
                    "first_seen": "2026-08-01", "last_seen": "2026-08-20",
                    "title": "Soundcore イヤホン", "brand": "Soundcore",
                    "genre": "ワイヤレスイヤホン", "price": 7000,
                    "percent_off": 20,
                }],
            },
            # genreが記録されていない旧エントリは帰属できないので入らない
            "H3": {
                "first_seen": "2026-10-01", "last_seen": "2026-10-10",
                "title": "ジャンル不明の何か", "brand": "Anker",
                "percent_off": 50, "archived": True,
            },
        }
        genres = gs.collect_genre_data(sales(), state)
        self.assertEqual([g["slug"] for g in genres],
                         ["charger", "wireless-earbuds"])
        charger_hist = genres[0]["history"]
        # 新しい順(archivedの最新ラウンド → past)
        self.assertEqual([r["title"] for r in charger_hist],
                         ["Anker 充電器 新", "Anker 充電器 旧"])
        self.assertEqual(charger_hist[0]["asin"], "H1")
        self.assertEqual([r["title"] for r in genres[1]["history"]],
                         ["Soundcore イヤホン"])

    def test_genre_without_content_excluded(self):
        self.assertEqual(gs.collect_genre_data(sales(), {}), [])


class TestGenerateGenrePage(GenreConfigPatch):
    def genre(self, current=None, history=None):
        return {
            "slug": "charger", "name": "充電器",
            "current": current or [], "history": history or [],
        }

    def test_page_with_current_and_history(self):
        g = self.genre(
            current=[item("A1", "Anker 充電器 65W", "Anker")],
            history=[{
                "asin": "H1", "first_seen": "2026-09-01",
                "last_seen": "2026-09-15", "title": "Anker 旧充電器",
                "brand": "Anker", "genre": "充電器", "price": 4500,
                "percent_off": 35,
            }],
        )
        brands = gs.collect_brand_data(
            sales(("充電器", [item("A1", "Anker 充電器 65W", "Anker")])), {}
        )
        html_text = gs.generate_genre_page(g, sales(), brands, [g])
        self.assertIn(
            '<link rel="canonical" href="https://example.test/genres/charger/">',
            html_text,
        )
        # h1はページ主題だけ(サイト名は見出しにしない)
        self.assertEqual(html_text.count("<h1"), 1)
        self.assertIn(
            '<h1 class="ptitle">充電器のセール・値下げ情報</h1>', html_text
        )
        self.assertIn('<p class="brand">', html_text)
        self.assertIn("Anker 充電器 65W", html_text)
        self.assertIn("現在セール中の商品 (1件)", html_text)
        self.assertIn("Anker 旧充電器", html_text)
        self.assertIn("2026年9月1日〜2026年9月15日", html_text)
        # ジャンルナビは自ジャンルが現在地、ブランドナビも併せて出る
        self.assertIn('href="../charger/" data-google-vignette="false" '
                      'class="cur" aria-current="page"', html_text)
        self.assertIn('href="../anker/"', html_text)
        self.assertIn('href="../../"', html_text)

    def test_page_history_only_shows_empty_current(self):
        g = self.genre(history=[{
            "asin": "H1", "first_seen": "2026-09-01", "last_seen": "2026-09-01",
            "title": "Anker 旧充電器", "brand": "Anker", "genre": "充電器",
            "price": None, "percent_off": None,
        }])
        html_text = gs.generate_genre_page(g, sales(), [], [g])
        self.assertIn("現在セール中の商品はありません。", html_text)
        self.assertIn("2026年9月1日", html_text)
        self.assertNotIn("2026年9月1日〜", html_text)


class TestBrandPageIncludesGenreNav(GenreConfigPatch):
    def test_brand_page_has_both_navs(self):
        data = sales(("充電器", [item("A1", "Anker 充電器", "Anker")]))
        brands = gs.collect_brand_data(data, {})
        genres = gs.collect_genre_data(data, {})
        html_text = gs.generate_brand_page(brands[0], data, brands, genres)
        self.assertIn('aria-label="ジャンル別セール"', html_text)
        self.assertIn('href="../charger/"', html_text)
        # 現在地ハイライトはブランドナビ側だけに付く
        self.assertEqual(html_text.count('class="cur"'), 1)
        self.assertIn('href="../anker/" data-google-vignette="false" '
                      'class="cur" aria-current="page"', html_text)


class TestTopNavAndSitemap(GenreConfigPatch):
    def data(self):
        return sales(("充電器", [item("A1", "Anker 充電器", "Anker")]))

    def test_top_has_genre_and_brand_navs_genre_first(self):
        data = self.data()
        brands = gs.collect_brand_data(data, {})
        genres = gs.collect_genre_data(data, {})
        html_text = gs.generate_html(data, brands, genres)
        self.assertIn('href="genres/charger/"', html_text)
        self.assertIn('href="brands/anker/"', html_text)
        self.assertLess(
            html_text.index('aria-label="ジャンル別セール"'),
            html_text.index('aria-label="ブランド別セール"'),
        )

    def test_top_has_no_filter_tabs(self):
        data = self.data()
        brands = gs.collect_brand_data(data, {})
        genres = gs.collect_genre_data(data, {})
        html_text = gs.generate_html(data, brands, genres)
        self.assertNotIn('class="tabs"', html_text)
        self.assertNotIn("data-target", html_text)
        self.assertNotIn('querySelector(".tabs")', html_text)
        self.assertNotIn("classList.add(\"js\")", html_text)
        # セクション構成(新着+ジャンル別details)はそのまま残る
        self.assertIn('<details open id="g0">', html_text)
        # 広告の同期処理は残る
        self.assertIn("function syncAds()", html_text)

    def test_top_without_data_has_no_identity_navs(self):
        html_text = gs.generate_html(self.data())
        self.assertNotIn("genres/charger/", html_text)
        self.assertNotIn("brands/anker/", html_text)

    def test_sitemap_includes_genre_urls(self):
        data = self.data()
        brands = gs.collect_brand_data(data, {})
        genres = gs.collect_genre_data(data, {})
        sm = gs.generate_sitemap(data, brands, genres)
        self.assertIn("<loc>https://example.test/genres/charger/</loc>", sm)
        self.assertIn("<loc>https://example.test/brands/anker/</loc>", sm)
        sm_plain = gs.generate_sitemap(data)
        self.assertNotIn("genres/", sm_plain)


class TestFetchRecordsGenre(unittest.TestCase):
    def item(self):
        return {
            "title": "Anker 充電器", "percent_off": 30,
            "brand": "Anker", "price": 5000,
        }

    def test_build_state_entry_records_genre(self):
        e = fd.build_state_entry(
            {}, self.item(), "2026-11-14", "2026-11-14T00:00:00+09:00",
            genre="充電器",
        )
        self.assertEqual(e["genre"], "充電器")

    def test_build_state_entry_keeps_prev_genre_when_unspecified(self):
        prev = {"first_seen": "2026-11-01", "genre": "充電器"}
        e = fd.build_state_entry(
            prev, self.item(), "2026-11-14", "2026-11-14T00:00:00+09:00"
        )
        self.assertEqual(e["genre"], "充電器")

    def test_round_record_carries_genre(self):
        r = fd.round_record({
            "first_seen": "2026-09-01", "last_seen": "2026-09-15",
            "title": "商品", "brand": "Anker", "genre": "充電器",
            "price": 4500, "percent_off": 35,
        })
        self.assertEqual(r["genre"], "充電器")


if __name__ == "__main__":
    unittest.main()
