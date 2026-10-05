#!/usr/bin/env python3
"""バリエーション自動探索(fetch_deals.py)のテスト。

ネットワークには一切出ない。Amazon APIの呼び出し(search_with_retry /
get_items_with_retry / get_variations_with_retry / get_access_token)と
time.sleep はすべてスタブに差し替え、状態ファイル・出力先は一時ディレクトリに向ける。

実行: python3 -m unittest discover -s tests
"""

from __future__ import annotations

import contextlib
import datetime
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import fetch_deals as fd  # noqa: E402

TODAY = datetime.date(2026, 10, 5)
NOW_ISO = "2026-10-05T12:00:00+09:00"


# ---------------------------------------------------------------------------
# 偽のAPI応答を作る道具
# ---------------------------------------------------------------------------

def make_item(
    asin,
    title,
    price=10000,
    basis=None,
    pct=None,
    points=None,
    parent=None,
    brand="Apple",
    no_price=False,
):
    """Creators APIのitem(searchItems/getItems/getVariations共通)を模した辞書。"""
    if no_price:
        # 価格が無く violatesMAP だけの出品(実際に返ってくる)
        listing = {"violatesMAP": True}
    else:
        price_block = {"money": {"amount": float(price), "currency": "JPY"}}
        if basis:
            price_block["savingBasis"] = {"money": {"amount": float(basis)}}
        if pct is not None:
            price_block["savings"] = {"percentage": pct}
        listing = {"price": price_block, "isBuyBoxWinner": True}
        if points:
            listing["loyaltyPoints"] = {"points": points}
    item = {
        "asin": asin,
        "itemInfo": {
            "title": {"displayValue": title},
            "byLineInfo": {"brand": {"displayValue": brand}},
        },
        "offersV2": {"listings": [listing]},
    }
    if parent:
        item["parentASIN"] = parent
    return item


def search_response(*items):
    return {"searchResult": {"items": list(items)}}


def variations_response(items, page_count=1):
    return {
        "variationsResult": {
            "items": items,
            "variationSummary": {"pageCount": page_count, "variationCount": len(items)},
        }
    }


def swept_family(key, seed, members, last_sweep_at=NOW_ISO, count=None,
                 no_variations=False, aliases=None):
    """状態ファイル上のファミリー1件。membersは {asin: (pct, points_pct)}。"""
    return {
        "aliases": dict(aliases or {}),
        "seed": seed,
        "label": "",
        "genre": "Apple製品",
        "last_sweep_at": last_sweep_at,
        "variation_count": count if count is not None else len(members),
        "no_variations": no_variations,
        "members": {
            a: {"pct": p, "points_pct": pp, "last_seen": TODAY.isoformat()}
            for a, (p, pp) in members.items()
        },
    }


# ---------------------------------------------------------------------------
# 純粋関数のテスト
# ---------------------------------------------------------------------------

class TestOfferNumbers(unittest.TestCase):
    def test_percentage_and_points(self):
        item = make_item("A", "x", price=10000, pct=20, points=300)
        self.assertEqual(
            fd.offer_numbers(item), {"price": 10000, "pct": 20, "points_pct": 3}
        )

    def test_percentage_computed_from_basis_when_missing(self):
        item = make_item("A", "x", price=8000, basis=10000)
        self.assertEqual(fd.offer_numbers(item)["pct"], 20)

    def test_no_discount_is_zero_not_none(self):
        nums = fd.offer_numbers(make_item("A", "x", price=10000))
        self.assertEqual((nums["pct"], nums["points_pct"]), (0, 0))

    def test_no_price_returns_none(self):
        self.assertIsNone(fd.offer_numbers(make_item("A", "x", no_price=True)))
        self.assertIsNone(fd.offer_numbers({"asin": "A", "offersV2": {"listings": []}}))

    def test_member_entry_keeps_priceless_variation_with_null(self):
        entry = fd.member_entry(make_item("A", "x", no_price=True), "2026-10-05")
        self.assertEqual(entry, {"pct": None, "points_pct": None, "last_seen": "2026-10-05"})


class TestSeedsFromSearchResponse(unittest.TestCase):
    MUST = ["iPad", "iPhone"]
    BRANDS = ["Apple"]
    EXCLUDE = ["整備済"]

    def seeds(self, *items):
        return fd.seeds_from_search_response(
            search_response(*items), self.MUST, self.BRANDS, self.EXCLUDE
        )

    def test_passing_item_is_seed_with_parent(self):
        got = self.seeds(make_item("A1", "iPad Air", parent="P1"))
        self.assertEqual(got, [("A1", "P1", "iPad Air")])

    def test_item_without_price_is_still_a_seed(self):
        got = self.seeds(make_item("A1", "iPad Air", parent="P1", no_price=True))
        self.assertEqual(got, [("A1", "P1", "iPad Air")])

    def test_item_without_parent_gives_none(self):
        got = self.seeds(make_item("A1", "iPad Air"))
        self.assertEqual(got, [("A1", None, "iPad Air")])

    def test_filters_reject_irrelevant_items(self):
        got = self.seeds(
            make_item("B1", "スマホケース", parent="PB"),  # must_include_any に無い
            make_item("B2", "iPad 整備済み品", parent="PB"),  # exclude_any
            make_item("B3", "iPad ケース", brand="Spigen", parent="PB"),  # 無名ブランド
            make_item("B4", "iPad", brand=None, parent="PB"),  # ブランド不明
            make_item("A1", "iPhone 17", parent="P1"),
        )
        self.assertEqual([s[0] for s in got], ["A1"])

    def test_empty_filters_pass_everything_with_a_title(self):
        got = fd.seeds_from_search_response(
            search_response(make_item("X", "なんでも", brand="Foo"), {"asin": "Y"}),
            [], [], None,
        )
        self.assertEqual([s[0] for s in got], ["X"])

    def test_empty_or_missing_response(self):
        self.assertEqual(fd.seeds_from_search_response({}, [], [], []), [])


class TestRegisterSeeds(unittest.TestCase):
    def test_static_and_search_seeds_register_as_unswept(self):
        families = {}
        n, new = fd.register_seeds(
            families, "Apple製品", ["S1", "S2"],
            [("A1", "P1", "iPad Air"), ("N1", None, "親なし")],
        )
        self.assertEqual(n, 4)
        self.assertEqual(sorted(families), ["N1", "P1", "S1", "S2"])
        self.assertTrue(all(f["last_sweep_at"] is None for f in families.values()))
        self.assertEqual(families["P1"]["seed"], "A1")
        self.assertEqual(families["P1"]["label"], "iPad Air")
        self.assertEqual(len(new), 4)

    def test_no_duplicates_for_known_members_seeds_and_parents(self):
        families = {
            "P1": swept_family("P1", "A1", {"A1": (0, 0), "A2": (0, 0)}),
            "S1": fd.new_family("S1", "Apple製品"),
        }
        n, new = fd.register_seeds(
            families, "Apple製品", ["A2", "S1"],
            [("A1", "P1", "t"), ("A9", "P1", "t"), ("S1", None, "t")],
        )
        self.assertEqual(new, [])
        self.assertEqual(sorted(families), ["P1", "S1"])

    def test_search_seed_matching_static_seed_is_not_duplicated(self):
        # 静的ASIN(子)がキーで登録済みのとき、検索で同じ子が親付きで来ても1つのまま
        families = {}
        fd.register_seeds(families, "Apple製品", ["A1"], [])
        fd.register_seeds(families, "Apple製品", [], [("A1", "P1", "iPad")])
        self.assertEqual(sorted(families), ["A1"])


class TestSelectSweepFamilies(unittest.TestCase):
    def fams(self):
        return {
            "old": swept_family("old", "o", {"o": (0, 0)}, last_sweep_at="2026-10-05T01:00:00+09:00", count=10),
            "older": swept_family("older", "x", {"x": (0, 0)}, last_sweep_at="2026-10-04T01:00:00+09:00", count=10),
            "new1": fd.new_family("new1", "Apple製品"),
            "new2": fd.new_family("new2", "Apple製品"),
            "other": {**fd.new_family("other", "ワイヤレスイヤホン")},
        }

    def test_unswept_first_then_oldest_first(self):
        got = fd.select_sweep_families(self.fams(), "Apple製品", 10, 100, today_dt=TODAY)
        self.assertEqual(got, ["new1", "new2", "older", "old"])

    def test_family_count_limit(self):
        got = fd.select_sweep_families(self.fams(), "Apple製品", 3, 100, today_dt=TODAY)
        self.assertEqual(got, ["new1", "new2", "older"])

    def test_request_cap_stops_before_exceeding(self):
        fams = self.fams()
        fams["older"]["variation_count"] = 35  # 4ページ
        # new1(1) + new2(1) = 2, older(4) を足すと6 > 5 で打ち切る(oldは処理しない)
        got = fd.select_sweep_families(fams, "Apple製品", 10, 5, today_dt=TODAY)
        self.assertEqual(got, ["new1", "new2"])

    def test_exclude_and_genre(self):
        got = fd.select_sweep_families(
            self.fams(), "Apple製品", 10, 100, exclude={"new1"}, today_dt=TODAY
        )
        self.assertNotIn("new1", got)
        self.assertNotIn("other", got)

    def test_no_variations_skipped_until_recheck_due(self):
        fams = {
            "single": swept_family("single", "s", {}, last_sweep_at="2026-10-04T00:00:00+09:00", no_variations=True),
            "stale": swept_family("stale", "t", {}, last_sweep_at="2026-09-01T00:00:00+09:00", no_variations=True),
            "unknown": {**fd.new_family("unknown", "Apple製品"), "no_variations": True},
        }
        got = fd.select_sweep_families(fams, "Apple製品", 10, 100, today_dt=TODAY)
        self.assertEqual(got, ["stale"])


class TestApplySweep(unittest.TestCase):
    def sweep(self, families, key, items, grace=14, today=TODAY):
        return fd.apply_sweep(families, key, items, today, NOW_ISO, grace)

    def test_members_recorded_with_pct_points_and_null_for_priceless(self):
        families = {"A1": fd.new_family("A1", "Apple製品")}
        items = [
            make_item("A1", "iPad Air 11", pct=20, points=100, parent="P1"),
            make_item("A2", "iPad Air 13", price=10000, parent="P1"),
            make_item("A3", "iPad Air 13", no_price=True, parent="P1"),
        ]
        key = self.sweep(families, "A1", items)
        self.assertEqual(key, "P1")
        fam = families["P1"]
        self.assertEqual(fam["members"]["A1"], {"pct": 20, "points_pct": 1, "last_seen": "2026-10-05"})
        self.assertEqual(fam["members"]["A2"]["pct"], 0)
        self.assertIsNone(fam["members"]["A3"]["pct"])
        self.assertEqual(fam["variation_count"], 3)
        self.assertEqual(fam["last_sweep_at"], NOW_ISO)
        self.assertEqual(fam["label"], "iPad Air 11")
        self.assertFalse(fam["no_variations"])

    def test_rekey_removes_old_unswept_entry(self):
        families = {"A1": fd.new_family("A1", "Apple製品")}
        self.sweep(families, "A1", [make_item("A1", "t", parent="P1")])
        self.assertEqual(list(families), ["P1"])

    def test_merges_into_existing_family_with_same_parent(self):
        families = {
            "A1": fd.new_family("A1", "Apple製品"),
            "P1": swept_family("P1", "A2", {"A2": (5, 0)}),
        }
        self.sweep(families, "A1", [make_item("A1", "t", parent="P1"), make_item("A2", "t", parent="P1")])
        self.assertEqual(list(families), ["P1"])
        self.assertEqual(sorted(families["P1"]["members"]), ["A1", "A2"])

    def test_absorbs_other_families_whose_seed_is_a_member(self):
        families = {
            "A1": fd.new_family("A1", "Apple製品"),
            "A9": fd.new_family("A9", "Apple製品"),
            "Z1": fd.new_family("Z1", "Apple製品"),
            "E1": {**fd.new_family("E1", "ワイヤレスイヤホン")},
        }
        items = [make_item(a, "t", parent="P1") for a in ("A1", "A9")]
        self.sweep(families, "A1", items)
        # A9 は吸収される。無関係なZ1と、別ジャンルのE1は残る
        self.assertEqual(sorted(families), ["E1", "P1", "Z1"])

    def test_member_missing_from_sweep_kept_within_grace_dropped_after(self):
        old_seen = (TODAY - datetime.timedelta(days=10)).isoformat()
        very_old = (TODAY - datetime.timedelta(days=15)).isoformat()
        families = {"P1": swept_family("P1", "A1", {})}
        families["P1"]["members"] = {
            "KEEP": {"pct": 9, "points_pct": 0, "last_seen": old_seen},
            "DROP": {"pct": 9, "points_pct": 0, "last_seen": very_old},
            "BAD": {"pct": 9, "points_pct": 0, "last_seen": "壊れた日付"},
        }
        self.sweep(families, "P1", [make_item("A1", "t", parent="P1")])
        self.assertEqual(sorted(families["P1"]["members"]), ["A1", "KEEP"])

    def test_boundary_exactly_grace_days_is_kept(self):
        seen = (TODAY - datetime.timedelta(days=14)).isoformat()
        members = {"X": {"pct": 1, "points_pct": 0, "last_seen": seen}}
        self.assertIn("X", fd.prune_members(members, TODAY, 14))
        self.assertNotIn("X", fd.prune_members(members, TODAY + datetime.timedelta(days=1), 14))

    def test_empty_response_marks_no_variations(self):
        families = {"S1": fd.new_family("S1", "Apple製品")}
        key = self.sweep(families, "S1", [])
        self.assertEqual(key, "S1")
        self.assertTrue(families["S1"]["no_variations"])
        self.assertEqual(families["S1"]["last_sweep_at"], NOW_ISO)

    def test_empty_response_for_family_with_members_is_not_single(self):
        # 種が廃番になっただけかもしれない。メンバーを保持して単品扱いにしない
        families = {"P1": swept_family("P1", "A1", {"A1": (10, 0)})}
        self.sweep(families, "P1", [])
        self.assertFalse(families["P1"]["no_variations"])
        self.assertIn("A1", families["P1"]["members"])

    def test_seed_moves_to_a_returned_asin(self):
        families = {"P1": swept_family("P1", "GONE", {"GONE": (0, 0)})}
        self.sweep(families, "P1", [make_item("B1", "t", parent="P1"), make_item("A1", "t", parent="P1")])
        self.assertEqual(families["P1"]["seed"], "A1")


class TestHotAndEffectiveWatch(unittest.TestCase):
    def families(self):
        return {
            "P1": swept_family("P1", "A1", {
                "A1": (20, 0),   # 掲載対象
                "A2": (2, 0),    # 2.5 未満 → コールド
                "A3": (1, 1),    # 合算2 → コールド
                "A4": (2, 1),    # 合算3 → ホット
                "A5": (None, None),  # 価格なし → コールド
                "A6": (0, 0),
            }),
            "S1": swept_family("S1", "S1", {}, no_variations=True),
            "P2": swept_family("P2", "B1", {"B1": (3, 0)}),
            "E": {**swept_family("E", "E1", {"E1": (50, 0)}), "genre": "ワイヤレスイヤホン"},
        }

    def test_hot_threshold_is_half_of_min_saving(self):
        self.assertEqual(
            fd.hot_asins(self.families(), "Apple製品", 5, 0.5), ["A1", "A4", "B1"]
        )

    def test_hot_ignores_no_variations_family_and_other_genres(self):
        fams = self.families()
        fams["S1"]["members"] = {"S1": {"pct": 90, "points_pct": 0, "last_seen": "2026-10-05"}}
        self.assertNotIn("S1", fd.hot_asins(fams, "Apple製品", 5, 0.5))
        self.assertNotIn("E1", fd.hot_asins(fams, "Apple製品", 5, 0.5))

    def test_effective_watch_drops_cold_swept_static_and_keeps_others(self):
        static = ["A1", "A2", "A6", "S1", "UNSWEPT", "S1"]
        got = fd.effective_watch(static, self.families(), "Apple製品", 5, 0.5)
        # 静的: A2/A6/A1 はスイープ済みファミリーのメンバーなので外れる。
        # S1(単品)とUNSWEPT(未スイープ)は残る(重複は除く)。そこへホット(A1,A4,B1)
        self.assertEqual(got, ["S1", "UNSWEPT", "A1", "A4", "B1"])

    def test_effective_watch_is_deterministic(self):
        a = fd.effective_watch(["X"], self.families(), "Apple製品", 5, 0.5)
        b = fd.effective_watch(["X"], dict(reversed(list(self.families().items()))), "Apple製品", 5, 0.5)
        self.assertEqual(a, b)


class TestSplitExcluded(unittest.TestCase):
    def parsed(self, asin, title):
        return {"asin": asin, "title": title}

    def test_set_excluded_appleCare_and_plain_kept(self):
        items = [
            self.parsed("A", "iPad Air Apple Pencil Proセット"),
            self.parsed("B", "iPad Air AppleCare+ 付き"),
            self.parsed("C", "iPad Air 単品"),
        ]
        kept, excluded = fd.split_excluded(items, ["セット"], set())
        self.assertEqual([i["asin"] for i in kept], ["B", "C"])
        self.assertEqual([i["asin"] for i in excluded], ["A"])

    def test_case_insensitive_and_protected_static_asin(self):
        items = [self.parsed("A", "iPad SET"), self.parsed("B", "iPad Set")]
        kept, excluded = fd.split_excluded(items, ["set"], {"B"})
        self.assertEqual([i["asin"] for i in kept], ["B"])
        self.assertEqual([i["asin"] for i in excluded], ["A"])


class TestVariantsFile(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "variants.json"
        patcher = mock.patch.object(fd, "VARIANTS_PATH", self.path)
        patcher.start()
        self.addCleanup(patcher.stop)

    def load(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            return fd.load_variants(), err.getvalue()

    def test_missing_file_starts_empty_silently(self):
        data, err = self.load()
        self.assertEqual(data["families"], {})
        self.assertEqual(err, "")

    def test_broken_json_starts_empty_with_warning(self):
        self.path.write_text("{not json", encoding="utf-8")
        data, err = self.load()
        self.assertEqual(data["families"], {})
        self.assertIn("壊れています", err)

    def test_conflict_marker_starts_empty_with_warning(self):
        self.path.write_text('<<<<<<< HEAD\n{"families": {}}\n=======\n{}\n>>>>>>> x\n', encoding="utf-8")
        data, err = self.load()
        self.assertEqual(data["families"], {})
        self.assertIn("コンフリクトマーカー", err)

    def test_wrong_shape_starts_empty(self):
        self.path.write_text("[1, 2, 3]", encoding="utf-8")
        data, _ = self.load()
        self.assertEqual(data["families"], {})

    def test_round_trip_and_stable_format(self):
        variants = {"version": 1, "families": {
            "P2": swept_family("P2", "B1", {"B1": (1, 0)}),
            "P1": swept_family("P1", "A1", {"A2": (0, 0), "A1": (20, 1)}),
        }}
        fd.save_variants(variants)
        text = self.path.read_text(encoding="utf-8")
        self.assertEqual(
            text,
            json.dumps({"version": 1, "families": variants["families"]},
                       ensure_ascii=False, indent=1, sort_keys=True),
        )
        self.assertLess(text.index('"P1"'), text.index('"P2"'))
        data, err = self.load()
        self.assertEqual(data["families"], variants["families"])
        self.assertEqual(err, "")


class TestOrphanAliases(unittest.TestCase):
    """種のASINがスイープ応答の構成一覧に含まれない(孤児)場合の扱い。"""

    def sweep(self, families, key, items):
        return fd.apply_sweep(families, key, items, TODAY, NOW_ISO, 14)

    def p1_items(self, *extra):
        return [make_item(a, "t", parent="P1") for a in ("A1", "A2") + extra]

    def test_orphan_seed_is_recorded_as_alias_after_sweep(self):
        families = {"Z": fd.new_family("Z", "Apple製品")}
        key = self.sweep(families, "Z", self.p1_items())
        self.assertEqual(key, "P1")
        self.assertEqual(families["P1"]["aliases"], {"Z": "2026-10-05"})
        self.assertNotIn("Z", families["P1"]["members"])
        self.assertNotEqual(families["P1"]["seed"], "Z")  # 種は返ったASINに替わる
        self.assertEqual(list(families), ["P1"])

    def test_seed_that_is_returned_is_not_an_alias(self):
        families = {"A1": fd.new_family("A1", "Apple製品")}
        self.sweep(families, "A1", self.p1_items())
        self.assertEqual(families["P1"]["aliases"], {})

    def test_register_seeds_does_not_resurrect_alias(self):
        families = {"Z": fd.new_family("Z", "Apple製品")}
        self.sweep(families, "Z", self.p1_items())
        before = json.dumps(families, sort_keys=True)
        n, new = fd.register_seeds(
            families, "Apple製品", ["Z"], [("Z", "P1", "t"), ("Z", None, "t")]
        )
        self.assertEqual(new, [])
        self.assertEqual(sorted(families), ["P1"])
        self.assertEqual(json.dumps(families, sort_keys=True), before)

    def test_register_seeds_refreshes_alias_date_when_given(self):
        families = {"P1": swept_family("P1", "A1", {"A1": (0, 0)}, aliases={"Z": "2026-09-01"})}
        fd.register_seeds(families, "Apple製品", ["Z"], [], "2026-10-05")
        self.assertEqual(families["P1"]["aliases"], {"Z": "2026-10-05"})

    def test_effective_watch_keeps_alias_but_not_covered_members(self):
        families = {"P1": swept_family("P1", "A1", {"A1": (0, 0), "A2": (0, 0)}, aliases={"Z": "2026-10-05"})}
        # Z(孤児)は割引中の別出品かもしれないので毎時の取得に残す。A1/A2はコールドなので外れる
        got = fd.effective_watch(["A1", "Z", "A2"], families, "Apple製品", 5, 0.5)
        self.assertEqual(got, ["Z"])

    def test_alias_removed_when_it_later_returns_as_a_member(self):
        families = {"Z": fd.new_family("Z", "Apple製品")}
        self.sweep(families, "Z", self.p1_items())
        self.sweep(families, "P1", self.p1_items("Z"))
        self.assertEqual(families["P1"]["aliases"], {})
        self.assertIn("Z", families["P1"]["members"])

    def test_alias_removed_from_other_families_when_returned(self):
        families = {
            "P1": swept_family("P1", "A1", {"A1": (0, 0)}),
            "P9": swept_family("P9", "Q1", {"Q1": (0, 0)}, aliases={"A2": "2026-10-01"}),
        }
        self.sweep(families, "P1", self.p1_items())
        self.assertEqual(families["P9"]["aliases"], {})

    def test_aliases_carried_over_when_merged_into_existing_parent(self):
        families = {
            "Z": {**fd.new_family("Z", "Apple製品"), "aliases": {"Y": "2026-10-01"}},
            "P1": swept_family("P1", "A1", {"A1": (0, 0)}, aliases={"X": "2026-10-02"}),
        }
        self.sweep(families, "Z", self.p1_items())
        self.assertEqual(list(families), ["P1"])
        self.assertEqual(
            families["P1"]["aliases"],
            {"X": "2026-10-02", "Y": "2026-10-01", "Z": "2026-10-05"},
        )

    def test_aliases_carried_over_when_family_is_rekeyed(self):
        families = {"Z": {**fd.new_family("Z", "Apple製品"), "aliases": {"Y": "2026-10-01"}}}
        self.sweep(families, "Z", self.p1_items())
        self.assertEqual(families["P1"]["aliases"], {"Y": "2026-10-01", "Z": "2026-10-05"})

    def test_aliases_of_absorbed_family_are_kept(self):
        families = {
            "A1": fd.new_family("A1", "Apple製品"),
            "A2": {**fd.new_family("A2", "Apple製品"), "aliases": {"Y": "2026-10-01"}},
        }
        self.sweep(families, "A1", self.p1_items())
        self.assertEqual(list(families), ["P1"])
        self.assertEqual(families["P1"]["aliases"], {"Y": "2026-10-01"})

    def test_old_aliases_are_pruned(self):
        old = (TODAY - datetime.timedelta(days=fd.ALIAS_KEEP_DAYS + 1)).isoformat()
        families = {"P1": swept_family("P1", "A1", {"A1": (0, 0)}, aliases={"OLD": old, "BAD": "x"})}
        self.sweep(families, "P1", self.p1_items())
        self.assertEqual(families["P1"]["aliases"], {})


class TestOldStateFileWithoutAliases(unittest.TestCase):
    def test_state_without_aliases_loads_and_is_usable(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "variants.json"
            old = {"version": 1, "families": {"P1": {
                "seed": "A1", "label": "x", "genre": "Apple製品",
                "last_sweep_at": NOW_ISO, "variation_count": 1, "no_variations": False,
                "members": {"A1": {"pct": 0, "points_pct": 0, "last_seen": "2026-10-05"}},
            }}}
            path.write_text(json.dumps(old), encoding="utf-8")
            with mock.patch.object(fd, "VARIANTS_PATH", path):
                data = fd.load_variants()
        fam = data["families"]["P1"]
        self.assertEqual(fam["aliases"], {})
        n, new = fd.register_seeds(data["families"], "Apple製品", ["A1", "Z"], [])
        self.assertEqual([k for k, _, _ in new], ["Z"])
        fd.apply_sweep(data["families"], "Z", [make_item("A1", "t", parent="P1")], TODAY, NOW_ISO, 14)
        self.assertEqual(data["families"]["P1"]["aliases"], {"Z": "2026-10-05"})

    def test_aliases_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "variants.json"
            fams = {"P1": swept_family("P1", "A1", {"A1": (0, 0)}, aliases={"Z": "2026-10-05"})}
            with mock.patch.object(fd, "VARIANTS_PATH", path):
                fd.save_variants({"version": 1, "families": fams})
                data = fd.load_variants()
        self.assertEqual(data["families"], fams)


class TestSweepInterval(unittest.TestCase):
    NOW = datetime.datetime(2026, 10, 5, 12, 0, 0, tzinfo=datetime.timezone(datetime.timedelta(hours=9)))

    def at(self, hours_ago):
        return (self.NOW - datetime.timedelta(hours=hours_ago)).isoformat(timespec="seconds")

    def select(self, families, max_families=100, max_requests=1000, interval=6):
        return fd.select_sweep_families(
            families, "Apple製品", max_families, max_requests,
            today_dt=TODAY, now=self.NOW, min_interval_hours=interval,
        )

    def test_younger_than_interval_is_not_eligible_and_older_is(self):
        fams = {
            "young": swept_family("young", "a", {"a": (0, 0)}, last_sweep_at=self.at(5.9)),
            "exact": swept_family("exact", "b", {"b": (0, 0)}, last_sweep_at=self.at(6)),
            "old": swept_family("old", "c", {"c": (0, 0)}, last_sweep_at=self.at(7)),
        }
        self.assertEqual(self.select(fams), ["old", "exact"])

    def test_unswept_first_then_oldest(self):
        fams = {
            "older": swept_family("older", "a", {"a": (0, 0)}, last_sweep_at=self.at(30)),
            "old": swept_family("old", "b", {"b": (0, 0)}, last_sweep_at=self.at(8)),
            "new": fd.new_family("new", "Apple製品"),
        }
        self.assertEqual(self.select(fams), ["new", "older", "old"])

    def test_family_count_and_request_limits_still_apply(self):
        fams = {f"u{i}": fd.new_family(f"u{i}", "Apple製品") for i in range(30)}
        self.assertEqual(len(self.select(fams, max_families=20)), 20)
        self.assertEqual(len(self.select(fams, max_families=20, max_requests=7)), 7)

    def test_broken_or_missing_last_sweep_is_treated_as_unswept(self):
        fams = {
            "fresh": swept_family("fresh", "a", {"a": (0, 0)}, last_sweep_at=self.at(1)),
            "broken": swept_family("broken", "b", {"b": (0, 0)}, last_sweep_at="きのう"),
            "missing": {**swept_family("missing", "c", {"c": (0, 0)}), "last_sweep_at": None},
        }
        self.assertEqual(self.select(fams), ["broken", "missing"])

    def test_naive_timestamp_is_read_as_jst(self):
        naive = (self.NOW - datetime.timedelta(hours=1)).replace(tzinfo=None).isoformat(timespec="seconds")
        fams = {"n": swept_family("n", "a", {"a": (0, 0)}, last_sweep_at=naive)}
        self.assertEqual(self.select(fams), [])

    def test_interval_zero_makes_everything_eligible(self):
        fams = {"x": swept_family("x", "a", {"a": (0, 0)}, last_sweep_at=self.at(0))}
        self.assertEqual(self.select(fams, interval=0), ["x"])

    def test_config_reads_interval(self):
        cfg = fd.load_variation_config({"enabled": True, "sweep_min_interval_hours": 3})
        self.assertEqual(cfg["sweep_min_interval_hours"], 3)


class TestFetchFamilyVariationsErrors(unittest.TestCase):
    """getVariations のエラー応答の扱い(単品=NoResults は正常、それ以外は失敗)。"""

    def run_fetch(self, responses):
        """responses を順に返すスタブで fetch_family_variations を呼ぶ。"""
        queue = list(responses)
        calls = []

        def fake(auth, partner_tag, asin, page, label):
            calls.append(page)
            return queue.pop(0)

        with mock.patch.object(fd, "get_variations_with_retry", fake), \
                mock.patch.object(fd.time, "sleep", lambda *_: None):
            result = fd.fetch_family_variations({}, "tag", "SEED", 70, "t")
        return result, calls

    def test_no_results_is_single_asin(self):
        # 実機の応答: 単品ASINはHTTP 200で NoResults が返る。正常な「単品」
        res = {"errors": [{"code": "NoResults", "message": "No results found for your request."}]}
        result, calls = self.run_fetch([res])
        self.assertTrue(result["ok"])
        self.assertEqual(result["items"], [])
        self.assertEqual(calls, [1])

    def test_other_error_code_is_failure_not_single(self):
        # 一時的な内部エラーを単品と誤判定すると、14日間スイープから外れる
        res = {"errors": [{"code": "InternalFailure", "message": "x"}]}
        result, _ = self.run_fetch([res])
        self.assertFalse(result["ok"])
        self.assertIn("InternalFailure", result["reason"])

    def test_mixed_error_codes_is_failure(self):
        res = {"errors": [{"code": "NoResults"}, {"code": "TooManyRequests"}]}
        result, _ = self.run_fetch([res])
        self.assertFalse(result["ok"])
        self.assertIn("TooManyRequests", result["reason"])

    def test_error_on_later_page_is_failure(self):
        page1 = {"variationsResult": {
            "items": [make_item("A1", "iPad Air", parent="P")],
            "variationSummary": {"pageCount": 2, "variationCount": 12},
        }}
        page2 = {"errors": [{"code": "InternalFailure"}]}
        result, calls = self.run_fetch([page1, page2])
        self.assertFalse(result["ok"])
        self.assertEqual(calls, [1, 2])
        self.assertIn("2ページ目", result["reason"])

    def test_empty_dict_is_failure(self):
        # give_up() が返す {} は従来どおり失敗
        result, _ = self.run_fetch([{}])
        self.assertFalse(result["ok"])

    def test_normal_family_unchanged(self):
        page1 = {"variationsResult": {
            "items": [make_item("A1", "iPad Air", parent="P"), make_item("A2", "iPad Air", parent="P")],
            "variationSummary": {"pageCount": 1, "variationCount": 2},
        }}
        result, _ = self.run_fetch([page1])
        self.assertTrue(result["ok"])
        self.assertEqual(len(result["items"]), 2)


class TestVariationConfig(unittest.TestCase):
    def test_missing_or_invalid_is_disabled_and_dry_run(self):
        for raw in (None, {}, "x", []):
            cfg = fd.load_variation_config(raw)
            self.assertFalse(cfg["enabled"])
            self.assertTrue(cfg["dry_run"])
            self.assertEqual(cfg["sweep_families_per_run"], 20)
            self.assertEqual(cfg["sweep_min_interval_hours"], 6)
            self.assertEqual(cfg["exclude_any"], ["セット"])

    def test_values_are_read_and_dry_run_only_false_when_explicit(self):
        cfg = fd.load_variation_config({
            "enabled": True, "dry_run": False, "genres": ["A"],
            "sweep_families_per_run": 2, "max_requests_per_run": 9,
            "hot_floor_ratio": 0.25, "member_grace_days": 3, "exclude_any": ["x"],
        })
        self.assertTrue(cfg["enabled"])
        self.assertFalse(cfg["dry_run"])
        self.assertEqual(
            (cfg["genres"], cfg["sweep_families_per_run"], cfg["max_requests_per_run"],
             cfg["hot_floor_ratio"], cfg["member_grace_days"], cfg["exclude_any"]),
            (["A"], 2, 9, 0.25, 3, ["x"]),
        )
        self.assertTrue(fd.load_variation_config({"enabled": True, "dry_run": "false"})["dry_run"])
        self.assertFalse(fd.load_variation_config({"enabled": "true"})["enabled"])


# ---------------------------------------------------------------------------
# main() の結合テスト(すべてスタブ)
# ---------------------------------------------------------------------------

def build_world(extra_families=0):
    """テスト用の商品カタログ。

    P1: iPad Air 12構成(2ページ)。A1=20%(掲載)、A2=3%(ホット)、A3=0%、
        A4=30%だが「セット」、A5=25%でAppleCare+付き、A6=価格なし、A7=ポイント3%
    P2: iPad mini 3構成。B2=15%
    S1: AirTag(単品。getVariationsが空)
    """
    catalog = {}

    def add(item):
        catalog[item["asin"]] = item

    p1 = [
        make_item("A1", "iPad Air 11インチ 128GB", pct=20, parent="P1"),
        make_item("A2", "iPad Air 11インチ 256GB", pct=3, parent="P1"),
        make_item("A3", "iPad Air 11インチ 512GB", pct=0, parent="P1"),
        make_item("A4", "iPad Air 11インチ Apple Pencil Proセット", pct=30, parent="P1"),
        make_item("A5", "iPad Air 11インチ AppleCare+ 付き", pct=25, parent="P1"),
        make_item("A6", "iPad Air 13インチ 128GB", no_price=True, parent="P1"),
        make_item("A7", "iPad Air 13インチ 256GB", price=10000, points=300, parent="P1"),
    ] + [make_item(f"A{n}", f"iPad Air 13インチ 構成{n}", parent="P1") for n in range(8, 13)]
    p2 = [
        make_item("B1", "iPad mini 128GB", pct=0, parent="P2"),
        make_item("B2", "iPad mini 256GB", pct=15, parent="P2"),
        make_item("B3", "iPad mini 512GB", pct=1, parent="P2"),
    ]
    s1 = make_item("S1", "AirTag 1個", pct=0)
    for it in p1 + p2 + [s1]:
        add(it)
    families = {"A": p1, "B": p2}
    for k in range(extra_families):
        members = [
            make_item(
            f"C{k}_{i}", f"iPhone 構成{k}-{i}", parent=f"PC{k}",
            pct=20 if i == 0 else 0,  # 掲載対象を1件ずつ入れる(全ジャンル0件で中止しないため)
        )
        for i in range(25)
        ]
        for it in members:
            add(it)
        families[f"C{k}"] = members
    return catalog, families


class FakeApi:
    """Amazon APIの代役。呼び出しを記録する。"""

    def __init__(self, catalog, families, search_items, fail_variations=False,
                 fail_get_items=False):
        self.catalog = catalog
        self.families = families
        self.search_items = search_items
        self.fail_variations = fail_variations
        self.fail_get_items = fail_get_items
        # 孤児の種: asin -> families のキー。そのASINで引くと兄弟一覧は返るが、
        # 一覧自身には含まれない(在庫切れ等)
        self.orphans = {}
        self.variation_calls = []
        self.get_items_calls = []
        self.search_calls = 0

    def search(self, auth, partner_tag, **kw):
        self.search_calls += 1
        if kw.get("keywords") in self.search_items:
            return search_response(*self.search_items[kw["keywords"]])
        return {}

    def get_items(self, auth, partner_tag, asins, label):
        self.get_items_calls.append(list(asins))
        if self.fail_get_items:
            return fd.give_up(label, "stub get_items failure")
        found = [self.catalog[a] for a in asins if a in self.catalog]
        return {"itemsResult": {"items": found}}

    def get_variations(self, auth, partner_tag, asin, page, label):
        self.variation_calls.append((asin, page))
        if self.fail_variations:
            return fd.give_up(label, "stub getVariations failure")
        candidates = list(self.families.values())
        if asin in self.orphans:
            candidates = [self.families[self.orphans[asin]]]
        for members in candidates:
            if asin in self.orphans or any(m["asin"] == asin for m in members):
                chunk = members[(page - 1) * 10: page * 10]
                pages = (len(members) + 9) // 10
                return variations_response(chunk, pages)
        return {"variationsResult": {}}  # 単品ASIN: 空


APPLE = {
    "name": "Apple製品",
    "min_saving_percent": 5,
    "keywords": ["iPad Apple"],
    "search_index": "Electronics",
    "must_include_any": ["iPad", "iPhone", "AirTag"],
    "exclude_any": ["整備済"],
    "known_brands": ["Apple"],
    "watch_asins": ["A1", "S1", "A9"],
}


def base_config(vd=None, genres=None):
    cfg = {
        "site_url": "https://example.test/",
        "min_saving_percent": 15,
        "pages_per_genre": 1,
        "genres": genres or [dict(APPLE)],
    }
    if vd is not None:
        cfg["variation_discovery"] = vd
    return cfg


VD_DRY = {"enabled": True, "dry_run": True, "genres": ["Apple製品"],
          "sweep_families_per_run": 6, "max_requests_per_run": 70,
          "hot_floor_ratio": 0.5, "member_grace_days": 14, "exclude_any": ["セット"]}
VD_PROD = dict(VD_DRY, dry_run=False)


class MainTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        (self.dir / "data").mkdir()

    def run_main(self, config, api, variants=None):
        cfg_path = self.dir / "config.json"
        cfg_path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
        if variants is not None:
            (self.dir / "data" / "variants.json").write_text(
                json.dumps(variants, ensure_ascii=False), encoding="utf-8"
            )
        fd.GIVE_UPS = 0
        out, err = io.StringIO(), io.StringIO()
        with contextlib.ExitStack() as stack:
            for name, rel in (
                ("CONFIG_PATH", "config.json"),
                ("OUTPUT_PATH", "data/sales.json"),
                ("STATE_PATH", "data/item_state.json"),
                ("VARIANTS_PATH", "data/variants.json"),
                ("NOTIFY_PATH", "data/pending_notification.json"),
            ):
                stack.enter_context(mock.patch.object(fd, name, self.dir / rel))
            stack.enter_context(mock.patch.dict(os.environ, {
                "CREATORSAPI_CREDENTIAL_ID": "id",
                "CREATORSAPI_CREDENTIAL_SECRET": "secret",
                "CREATORSAPI_PARTNER_TAG": "tag-22",
            }))
            stack.enter_context(mock.patch.object(fd, "get_access_token", return_value="tok"))
            stack.enter_context(mock.patch.object(fd, "search_with_retry", api.search))
            stack.enter_context(mock.patch.object(fd, "get_items_with_retry", api.get_items))
            stack.enter_context(mock.patch.object(fd, "get_variations_with_retry", api.get_variations))
            stack.enter_context(mock.patch.object(fd.time, "sleep", lambda s: None))
            stack.enter_context(contextlib.redirect_stdout(out))
            stack.enter_context(contextlib.redirect_stderr(err))
            rc = fd.main()
        return rc, out.getvalue(), err.getvalue()

    def sales(self):
        """sales.jsonを、実行ごとに変わる項目を除いて返す。"""
        data = json.loads((self.dir / "data" / "sales.json").read_text(encoding="utf-8"))
        data.pop("fetched_at")
        for genre in data["genres"]:
            for item in genre["items"]:
                item.pop("since_at", None)
        return data

    def variants_path(self):
        return self.dir / "data" / "variants.json"

    def load_variants(self):
        return json.loads(self.variants_path().read_text(encoding="utf-8"))

    def asins(self, genre="Apple製品"):
        data = self.sales()
        return [i["asin"] for g in data["genres"] if g["name"] == genre for i in g["items"]]


def make_api(catalog, families, **kw):
    search_items = {
        "iPad Apple": [
            catalog["A1"],
            catalog["B1"],
            make_item("T1", "iPad ケース", brand="Spigen", pct=50, parent="PT"),
        ]
    }
    return FakeApi(catalog, families, search_items, **kw)


class TestMainDisabled(MainTestCase):
    def test_disabled_output_identical_and_nothing_touched(self):
        catalog, families = build_world()
        results = []
        for label, vd in (("missing", None), ("false", dict(VD_PROD, enabled=False)),
                          ("false_dry", dict(VD_DRY, enabled=False))):
            with self.subTest(label):
                (self.dir / "data" / "variants.json").unlink(missing_ok=True)
                api = make_api(catalog, families)
                rc, out, err = self.run_main(base_config(vd), api)
                self.assertEqual(rc, 0, err)
                self.assertEqual(api.variation_calls, [])
                self.assertFalse(self.variants_path().exists())
                self.assertNotIn("バリエーション探索", out)
                # 取得は従来どおり(静的watch_asinsを1バッチ)
                self.assertEqual(api.get_items_calls, [["A1", "S1", "A9"]])
                results.append(self.sales())
        self.assertEqual(results[0], results[1])
        self.assertEqual(results[0], results[2])
        self.assertEqual([i["asin"] for i in results[0]["genres"][0]["items"]], ["A1"])

    def test_disabled_does_not_even_read_a_broken_variants_file(self):
        catalog, families = build_world()
        (self.dir / "data" / "variants.json").write_text("{broken", encoding="utf-8")
        api = make_api(catalog, families)
        rc, out, err = self.run_main(base_config(dict(VD_PROD, enabled=False)), api)
        self.assertEqual(rc, 0, err)
        self.assertNotIn("壊れています", err)
        self.assertEqual(self.variants_path().read_text(encoding="utf-8"), "{broken")


class TestMainDryRun(MainTestCase):
    def test_dry_run_keeps_site_output_but_updates_state_and_logs(self):
        catalog, families = build_world()
        baseline_api = make_api(catalog, families)
        rc, _, err = self.run_main(base_config(), baseline_api)
        self.assertEqual(rc, 0, err)
        baseline = self.sales()

        (self.dir / "data" / "item_state.json").unlink()
        api = make_api(catalog, families)
        rc, out, err = self.run_main(base_config(VD_DRY), api)
        self.assertEqual(rc, 0, err)
        # サイトの出力は従来と同一、取得も静的watch_asinsのまま
        self.assertEqual(self.sales(), baseline)
        self.assertEqual(api.get_items_calls, [["A1", "S1", "A9"]])
        # スイープは実際に行われ、状態が更新される
        variants = self.load_variants()
        self.assertEqual(sorted(variants["families"]), ["P1", "P2", "S1"])
        p1 = variants["families"]["P1"]
        self.assertEqual(len(p1["members"]), 12)
        self.assertEqual(p1["variation_count"], 12)
        self.assertTrue(variants["families"]["S1"]["no_variations"])
        # 静的ASIN A1 / A9 は P1 に吸収されて、重複エントリが残らない
        self.assertNotIn("A1", variants["families"])
        self.assertNotIn("A9", variants["families"])
        # ログ: 要約・ドライランの見出し・本番なら掲載される構成・セット除外
        self.assertIn("[バリエーション探索] Apple製品:", out)
        self.assertIn("[バリエーション探索/ドライラン] Apple製品: 本番なら新たに掲載される構成 2件", out)
        self.assertIn("A5 25% iPad Air 11インチ AppleCare+ 付き", out)
        self.assertIn("B2 15% iPad mini 256GB", out)
        self.assertIn("[除外] A4 iPad Air 11インチ Apple Pencil Proセット", out)
        self.assertIn("セット除外1件", out)
        # すでに掲載されているA1は「新たに」には出ない
        self.assertNotIn("  A1 20%", out)
        # 新ファミリー(検索由来)の発見ログ
        self.assertIn("[新ファミリー] P2 iPad mini 128GB", out)


class TestMainProduction(MainTestCase):
    def test_sweep_results_are_listed_and_sets_excluded(self):
        catalog, families = build_world()
        api = make_api(catalog, families)
        rc, out, err = self.run_main(base_config(VD_PROD), api)
        self.assertEqual(rc, 0, err)
        asins = self.asins()
        self.assertEqual(sorted(asins), ["A1", "A5", "B2"])
        # 並びはお得度順
        self.assertEqual(asins, ["A5", "A1", "B2"])
        self.assertNotIn("A4", asins)  # 「セット」は載せない
        self.assertIn("[除外] A4 iPad Air 11インチ Apple Pencil Proセット", out)
        # 毎時の取得: 単品(S1)と、スイープ後のホット構成だけ。コールド(A3, A9など)は無い
        self.assertEqual(len(api.get_items_calls), 1)
        self.assertEqual(
            api.get_items_calls[0], ["S1", "A1", "A2", "A4", "A5", "A7", "B2"]
        )
        self.assertNotIn("[バリエーション探索/ドライラン]", out)

    def test_hot_refresh_updates_member_pct_and_cools_down(self):
        catalog, families = build_world()
        # 前回のスイープ済み状態。A2は以前3%でホットだったが、いま0%に戻っている
        variants = {"version": 1, "families": {
            "P1": swept_family("P1", "A1", {"A1": (20, 0), "A2": (3, 0), "A3": (0, 0)},
                               last_sweep_at="2026-10-05T01:00:00+09:00"),
            "S1": swept_family("S1", "S1", {}, no_variations=True),
        }}
        catalog["A2"] = make_item("A2", "iPad Air 11インチ 256GB", pct=0, parent="P1")
        api = make_api(catalog, families)
        # スイープの対象を空にする(今回は毎時の更新だけを見る)
        cfg = dict(VD_PROD, sweep_families_per_run=0)
        rc, out, err = self.run_main(base_config(cfg), api, variants=variants)
        self.assertEqual(rc, 0, err)
        self.assertEqual(api.variation_calls, [])
        # ホット: A1(20%) と A2(3%) → 取得に A3 は入らない
        self.assertEqual(api.get_items_calls, [["S1", "A9", "A1", "A2"]])
        after = self.load_variants()["families"]["P1"]["members"]
        self.assertEqual(after["A2"]["pct"], 0)
        self.assertEqual(after["A1"]["pct"], 20)
        self.assertEqual(after["A1"]["last_seen"], datetime.datetime.now(
            datetime.timezone(datetime.timedelta(hours=9))).date().isoformat())
        # 次の実行ではA2はコールドになり取得されない
        api2 = make_api(catalog, families)
        rc, _, err = self.run_main(base_config(cfg), api2)
        self.assertEqual(rc, 0, err)
        self.assertEqual(api2.get_items_calls, [["S1", "A9", "A1"]])

    def test_no_variations_family_not_resweept_and_static_still_fetched(self):
        catalog, families = build_world()
        now_iso = datetime.datetime.now(
            datetime.timezone(datetime.timedelta(hours=9))
        ).isoformat(timespec="seconds")
        variants = {"version": 1, "families": {
            "S1": swept_family("S1", "S1", {}, last_sweep_at=now_iso, no_variations=True),
        }}
        api = make_api(catalog, families)
        rc, out, err = self.run_main(base_config(VD_PROD), api, variants=variants)
        self.assertEqual(rc, 0, err)
        self.assertNotIn(("S1", 1), api.variation_calls)
        self.assertTrue(any("S1" in batch for batch in api.get_items_calls))
        self.assertTrue(self.load_variants()["families"]["S1"]["no_variations"])


class TestMainFailuresAndLimits(MainTestCase):
    def two_genres(self):
        other = {
            "name": "充電器", "keywords": ["充電器"], "search_index": "Electronics",
            "must_include_any": ["充電器"], "known_brands": [], "exclude_any": [],
        }
        apple = dict(APPLE, keywords=["iPad Apple"], watch_asins=["S1"])
        return [apple, other]

    def api_for_two_genres(self, catalog, families, **kw):
        search_items = {
            "iPad Apple": [],  # Apple製品は検索でも何も出ない
            "充電器": [make_item("E1", "USB充電器 65W", brand="Anker", pct=30)],
        }
        return FakeApi(catalog, families, search_items, **kw)

    def test_sweep_failure_does_not_abort_run_or_trip_genre_guard(self):
        catalog, families = build_world()
        # Apple製品は掲載0件、そこでのGIVE_UPSはスイープの失敗だけ。
        # 従来のガードなら「取得失敗で0件」と誤判定して中止してしまう場面
        api = self.api_for_two_genres(catalog, families, fail_variations=True)
        rc, out, err = self.run_main(
            base_config(VD_PROD, genres=self.two_genres()), api
        )
        self.assertEqual(rc, 0, err)
        self.assertIn("スイープを見送り", err)
        self.assertEqual(self.asins("充電器"), ["E1"])
        # 失敗したファミリーは last_sweep_at を更新せず、次回に回す
        fam = self.load_variants()["families"]["S1"]
        self.assertIsNone(fam["last_sweep_at"])
        self.assertFalse(fam["no_variations"])
        self.assertIn("失敗1", out)

    def test_genre_guard_still_trips_on_real_fetch_failure(self):
        # 対照: 本体側の取得(getItems)の失敗は従来どおり中止になる
        catalog, families = build_world()
        api = self.api_for_two_genres(catalog, families, fail_get_items=True)
        rc, out, err = self.run_main(
            base_config(VD_PROD, genres=self.two_genres()), api
        )
        self.assertEqual(rc, 1)
        self.assertIn("取得に失敗してセール品が0件になったジャンル", err)

    def test_empty_variations_response_counts_as_failure_and_keeps_state(self):
        # 戻り値が空dict(give_upなし)でも落ちず、前回の状態を保持する
        catalog, families = build_world()
        variants = {"version": 1, "families": {
            "P1": swept_family("P1", "A1", {"A1": (20, 0)}, last_sweep_at="2026-10-01T00:00:00+09:00"),
        }}
        api = make_api(catalog, families)
        api.get_variations = lambda *a, **k: {}
        rc, out, err = self.run_main(base_config(VD_DRY), api, variants=variants)
        self.assertEqual(rc, 0, err)
        fam = self.load_variants()["families"]["P1"]
        self.assertEqual(fam["last_sweep_at"], "2026-10-01T00:00:00+09:00")
        self.assertEqual(sorted(fam["members"]), ["A1"])

    def test_unexpected_response_shape_is_logged_not_fatal(self):
        catalog, families = build_world()
        api = make_api(catalog, families)
        # 想定外の形(itemsの中身がdictでない)でも、実行は落とさず原因をログに出す
        api.get_variations = lambda *a, **k: {
            "variationsResult": {
                "items": ["壊れた", 1],
                "variationSummary": {"pageCount": 1},
            }
        }
        rc, out, err = self.run_main(base_config(VD_DRY), api)
        self.assertEqual(rc, 0, err)
        self.assertIn("例外", err)

    def test_requests_never_exceed_max_requests_per_run(self):
        catalog, families = build_world(extra_families=5)
        genre = dict(APPLE, watch_asins=[f"C{k}_0" for k in range(5)])
        api = FakeApi(catalog, families, {"iPad Apple": []})
        # 各ファミリーは3ページ。上限7なら 3+3 の後、残り1で3ページ目を要する
        # ファミリーは取り始めずに止まる(1ページ目だけ取って見送る)
        cfg = dict(VD_PROD, max_requests_per_run=7, sweep_families_per_run=6)
        rc, out, err = self.run_main(base_config(cfg, genres=[genre]), api)
        self.assertEqual(rc, 0, err)
        self.assertLessEqual(len(api.variation_calls), 7)
        fams = self.load_variants()["families"]
        swept = [k for k, f in fams.items() if f["last_sweep_at"]]
        self.assertEqual(sorted(swept), ["PC0", "PC1"])

    def test_sweep_family_limit_per_run(self):
        catalog, families = build_world(extra_families=5)
        genre = dict(APPLE, watch_asins=[f"C{k}_0" for k in range(5)])
        api = FakeApi(catalog, families, {"iPad Apple": []})
        cfg = dict(VD_PROD, sweep_families_per_run=2, max_requests_per_run=70)
        rc, out, err = self.run_main(base_config(cfg, genres=[genre]), api)
        self.assertEqual(rc, 0, err)
        pages = {asin for asin, _ in api.variation_calls}
        self.assertEqual(len(pages), 2)
        self.assertEqual(len(api.variation_calls), 6)


class TestMainOrphanAndInterval(MainTestCase):
    def world(self):
        catalog, families = build_world()
        # Z9: P1の構成の一つだが、スイープ応答の一覧には含まれない孤児の種
        catalog["Z9"] = make_item("Z9", "iPad Air 11インチ 在庫切れ", pct=0, parent="P1")
        genre = dict(APPLE, watch_asins=["A1", "Z9"])
        return catalog, families, genre

    def age_state(self, hours):
        path = self.variants_path()
        data = json.loads(path.read_text(encoding="utf-8"))
        for fam in data["families"].values():
            last = datetime.datetime.fromisoformat(fam["last_sweep_at"])
            fam["last_sweep_at"] = (last - datetime.timedelta(hours=hours)).isoformat(timespec="seconds")
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def test_orphan_family_is_not_swept_again_on_second_run(self):
        catalog, families, genre = self.world()
        cfg = base_config(VD_PROD, genres=[genre])

        api1 = make_api(catalog, families)
        api1.orphans = {"Z9": "A"}
        rc, out, err = self.run_main(cfg, api1)
        self.assertEqual(rc, 0, err)
        self.assertIn(("Z9", 1), api1.variation_calls)  # 初回は孤児も種として1回引く
        fams = self.load_variants()["families"]
        self.assertEqual(sorted(fams), ["P1", "P2"])
        self.assertEqual(list(fams["P1"]["aliases"]), ["Z9"])
        self.assertNotIn("Z9", fams["P1"]["members"])

        # 直後(6時間未満)の実行では、どのファミリーも再スイープされない
        api2 = make_api(catalog, families)
        api2.orphans = {"Z9": "A"}
        rc, out, err = self.run_main(cfg, api2)
        self.assertEqual(rc, 0, err)
        self.assertEqual(api2.variation_calls, [])

        # 7時間後(巡回の資格が戻った)でも、孤児は種として復活しない
        self.age_state(7)
        api3 = make_api(catalog, families)
        api3.orphans = {"Z9": "A"}
        rc, out, err = self.run_main(cfg, api3)
        self.assertEqual(rc, 0, err)
        self.assertNotIn("Z9", {a for a, _ in api3.variation_calls})
        fams = self.load_variants()["families"]
        self.assertEqual(sorted(fams), ["P1", "P2"])
        self.assertEqual(list(fams["P1"]["aliases"]), ["Z9"])
        # 孤児(静的ASIN)は「カバー済み」にならず、毎時の取得に残る
        self.assertTrue(any("Z9" in batch for batch in api3.get_items_calls))
        # P1(2ページ)+P2(1ページ)を再スイープしただけ
        self.assertEqual(len(api3.variation_calls), 3)

    def test_default_family_limit_is_20_per_run(self):
        catalog, families = build_world(extra_families=25)
        genre = dict(APPLE, watch_asins=[f"C{k}_0" for k in range(25)])
        api = FakeApi(catalog, families, {"iPad Apple": []})
        vd = {k: v for k, v in VD_PROD.items() if k != "sweep_families_per_run"}
        rc, out, err = self.run_main(base_config(vd, genres=[genre]), api)
        self.assertEqual(rc, 0, err)
        self.assertEqual(len({a for a, _ in api.variation_calls}), 20)
        self.assertEqual(len(api.variation_calls), 60)  # 3ページ×20 <= 70


if __name__ == "__main__":
    unittest.main()
