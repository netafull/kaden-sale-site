#!/usr/bin/env python3
"""ブランドページ(generate_site.py)のテスト。

ネットワークには一切出ない。sales.json / item_state.json はテスト内で
組み立てた辞書を渡し、実データには依存しない。

実行: python3 -m unittest discover -s tests
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import generate_site as gs  # noqa: E402

DEFS = [
    {"slug": "anker", "name": "Anker", "match": ["Anker", "アンカー", "Soundcore"]},
    {"slug": "switchbot", "name": "SwitchBot", "match": ["SwitchBot", "スイッチボット"]},
    {"slug": "elecom", "name": "エレコム", "match": ["ELECOM", "エレコム"]},
]

TEST_CONFIG = {
    "site_title": "家電ポチ",
    "site_tagline": "",
    "site_description": "セール情報サイト",
    "meta_description": "テスト用の説明文",
    "site_url": "https://example.test/",
    "brand_pages": DEFS,
}


def item(asin, title, brand, price=5000, pct=30, genre_items=None):
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


class BrandConfigPatch(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.dict(gs.CONFIG, TEST_CONFIG, clear=False)
        # site_url など既存キーを確実にテスト値へ寄せるため clear せず
        # 上書きする。brand_pages は実 config のものと差し替わる
        self.addCleanup(patcher.stop)
        patcher.start()


class TestMatchBrandPage(unittest.TestCase):
    def test_substring_case_insensitive(self):
        d = gs.match_brand_page(DEFS, "スイッチボット(SwitchBot)")
        self.assertEqual(d["slug"], "switchbot")
        d = gs.match_brand_page(DEFS, "ANKER")
        self.assertEqual(d["slug"], "anker")

    def test_first_match_wins(self):
        defs = [
            {"slug": "a", "name": "A", "match": ["X"]},
            {"slug": "b", "name": "B", "match": ["X"]},
        ]
        self.assertEqual(gs.match_brand_page(defs, "X-brand")["slug"], "a")

    def test_no_brand_or_no_match(self):
        self.assertIsNone(gs.match_brand_page(DEFS, None))
        self.assertIsNone(gs.match_brand_page(DEFS, ""))
        self.assertIsNone(gs.match_brand_page(DEFS, "無名メーカー"))


class TestBrandPageDefs(BrandConfigPatch):
    def test_realistic_defs_pass(self):
        self.assertEqual([d["slug"] for d in gs.brand_page_defs()],
                         ["anker", "switchbot", "elecom"])

    def test_broken_entries_dropped(self):
        broken = [
            {"slug": "ok", "name": "OK", "match": ["X"]},
            {"slug": "", "name": "NG", "match": ["X"]},
            {"slug": "ng2", "name": "NG2", "match": []},
            {"name": "NG3", "match": ["X"]},
            "not-a-dict",
        ]
        with mock.patch.dict(gs.CONFIG, {"brand_pages": broken}):
            self.assertEqual([d["slug"] for d in gs.brand_page_defs()], ["ok"])


class TestCollectBrandData(BrandConfigPatch):
    def test_current_items_attributed_with_genre_and_deduped(self):
        data = sales(
            ("充電器", [item("A1", "Anker 充電器", "Anker"),
                        item("A2", "エレコム 充電器", "エレコム(ELECOM)")]),
            ("モバイルバッテリー", [item("A1", "Anker 充電器", "Anker"),
                                     item("A3", "Soundcore イヤホン", "Soundcore")]),
        )
        brands = gs.collect_brand_data(data, {})
        self.assertEqual([b["slug"] for b in brands], ["anker", "elecom"])
        anker = brands[0]
        # ジャンルをまたいだ重複(A1)は1回だけ、ジャンル名が添えられる
        self.assertEqual([(g, i["asin"]) for g, i in anker["current"]],
                         [("充電器", "A1"), ("モバイルバッテリー", "A3")])
        self.assertEqual(brands[1]["current"][0][1]["asin"], "A2")

    def test_history_from_past_and_archived(self):
        state = {
            "H1": {
                "first_seen": "2026-11-01", "last_seen": "2026-11-10",
                "title": "Anker 新モデル", "brand": "Anker", "price": 4000,
                "percent_off": 40, "archived": True,
                "past": [{
                    "first_seen": "2026-09-01", "last_seen": "2026-09-15",
                    "title": "Anker 旧モデル", "brand": "Anker", "price": 4500,
                    "percent_off": 35,
                }],
            },
            # 掲載中(archivedでない)エントリの当ラウンドは履歴に入らない
            "H2": {
                "first_seen": "2026-11-12", "last_seen": "2026-11-14",
                "title": "SwitchBot ハブ", "brand": "スイッチボット(SwitchBot)",
                "price": 6000, "percent_off": 25,
                "past": [{
                    "first_seen": "2026-08-01", "last_seen": "2026-08-20",
                    "title": "SwitchBot ハブ", "brand": "スイッチボット(SwitchBot)",
                    "price": 7000, "percent_off": 20,
                }],
            },
            # brandを持たない旧形式のエントリは帰属できないので履歴に入らない
            "H3": {
                "first_seen": "2026-10-01", "last_seen": "2026-10-10",
                "title": "ブランド不明の何か", "percent_off": 50,
                "archived": True,
            },
        }
        brands = gs.collect_brand_data(sales(), state)
        self.assertEqual([b["slug"] for b in brands], ["anker", "switchbot"])
        anker_hist = brands[0]["history"]
        # 新しい順(archivedの最新ラウンド → past)
        self.assertEqual([r["title"] for r in anker_hist],
                         ["Anker 新モデル", "Anker 旧モデル"])
        self.assertEqual(anker_hist[0]["asin"], "H1")
        self.assertEqual([r["title"] for r in brands[1]["history"]],
                         ["SwitchBot ハブ"])

    def test_brand_without_content_excluded(self):
        brands = gs.collect_brand_data(sales(), {})
        self.assertEqual(brands, [])


class TestGenerateBrandPage(BrandConfigPatch):
    def brand(self, current=None, history=None):
        return {
            "slug": "anker", "name": "Anker",
            "current": current or [], "history": history or [],
        }

    def test_page_with_current_and_history(self):
        b = self.brand(
            current=[("充電器", item("A1", "Anker 充電器 65W", "Anker"))],
            history=[{
                "asin": "H1", "first_seen": "2026-09-01",
                "last_seen": "2026-09-15", "title": "Anker 旧充電器",
                "brand": "Anker", "price": 4500, "percent_off": 35,
            }],
        )
        html_text = gs.generate_brand_page(b, sales(), [b])
        self.assertIn('<link rel="canonical" href="https://example.test/brands/anker/">', html_text)
        self.assertIn("Anker 充電器 65W", html_text)
        self.assertIn("現在セール中の商品 (1件)", html_text)
        self.assertIn("Anker 旧充電器", html_text)
        self.assertIn("2026年9月1日〜2026年9月15日", html_text)
        self.assertIn("&yen;4,500", html_text)
        # 自ブランドのナビが現在ページとして印付けされる
        self.assertIn('class="cur" aria-current="page"', html_text)
        self.assertIn('href="../../"', html_text)

    def test_page_history_only_shows_empty_current(self):
        b = self.brand(history=[{
            "asin": "H1", "first_seen": "2026-09-01", "last_seen": "2026-09-01",
            "title": "Anker 旧充電器", "brand": "Anker", "price": None,
            "percent_off": None,
        }])
        html_text = gs.generate_brand_page(b, sales(), [b])
        self.assertIn("現在セール中の商品はありません。", html_text)
        # 期間が1日だけなら範囲表記にしない
        self.assertIn("2026年9月1日", html_text)
        self.assertNotIn("2026年9月1日〜", html_text)


class TestTopNavAndSitemap(BrandConfigPatch):
    def test_top_includes_brand_nav_only_when_data_given(self):
        data = sales(("充電器", [item("A1", "Anker 充電器", "Anker")]))
        brands = gs.collect_brand_data(data, {})
        with_nav = gs.generate_html(data, brands)
        self.assertIn('href="brands/anker/"', with_nav)
        without = gs.generate_html(data)
        self.assertNotIn("brands/anker/", without)

    def test_sitemap_includes_brand_urls(self):
        data = sales(("充電器", [item("A1", "Anker 充電器", "Anker")]))
        brands = gs.collect_brand_data(data, {})
        sm = gs.generate_sitemap(data, brands)
        self.assertIn("<loc>https://example.test/brands/anker/</loc>", sm)
        sm_plain = gs.generate_sitemap(data)
        self.assertNotIn("brands/", sm_plain)


class TestLoadStateForDisplay(unittest.TestCase):
    def test_missing_and_broken_files_are_empty(self):
        with tempfile.TemporaryDirectory() as td:
            missing = Path(td) / "none.json"
            with mock.patch.object(gs, "STATE_PATH", missing):
                self.assertEqual(gs.load_state_for_display(), {})
            broken = Path(td) / "broken.json"
            broken.write_text("{oops", encoding="utf-8")
            with mock.patch.object(gs, "STATE_PATH", broken):
                self.assertEqual(gs.load_state_for_display(), {})

    def test_valid_file_loaded(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "state.json"
            p.write_text(json.dumps({"A1": {"first_seen": "2026-01-01"}}),
                         encoding="utf-8")
            with mock.patch.object(gs, "STATE_PATH", p):
                self.assertEqual(gs.load_state_for_display(),
                                 {"A1": {"first_seen": "2026-01-01"}})


if __name__ == "__main__":
    unittest.main()
