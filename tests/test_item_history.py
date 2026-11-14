#!/usr/bin/env python3
"""セール履歴の蓄積(fetch_deals.py の item_state 拡張)のテスト。

ネットワークには一切出ない。API呼び出しはスタブに差し替え、状態ファイル・
出力先は一時ディレクトリに向ける(test_variations.py の MainTestCase と
同じ流儀)。

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

JST = datetime.timezone(datetime.timedelta(hours=9))
TODAY = datetime.datetime.now(JST).date()
NOW_ISO = datetime.datetime.now(JST).isoformat(timespec="seconds")


def make_item(asin, title, price=5000, basis=8000, pct=38, brand="Anker"):
    return {
        "asin": asin,
        "itemInfo": {
            "title": {"displayValue": title},
            "byLineInfo": {"brand": {"displayValue": brand}},
        },
        "offersV2": {
            "listings": [
                {
                    "price": {
                        "money": {"amount": float(price), "currency": "JPY"},
                        "savingBasis": {"money": {"amount": float(basis)}},
                        "savings": {"percentage": pct},
                    },
                    "isBuyBoxWinner": True,
                }
            ]
        },
    }


def days_ago(n: int) -> str:
    return (TODAY - datetime.timedelta(days=n)).isoformat()


class TestRoundRecord(unittest.TestCase):
    def test_extracts_round_fields(self):
        entry = {
            "first_seen": "2026-09-01", "last_seen": "2026-09-15",
            "title": "商品", "brand": "Anker", "price": 4500,
            "percent_off": 35, "first_seen_at": "2026-09-01T00:00:00+09:00",
            "archived": True,
        }
        self.assertEqual(
            fd.round_record(entry),
            {
                "first_seen": "2026-09-01", "last_seen": "2026-09-15",
                "title": "商品", "brand": "Anker", "genre": None,
                "price": 4500, "percent_off": 35,
            },
        )

    def test_missing_fields_become_none(self):
        r = fd.round_record({"first_seen": "2026-09-01"})
        self.assertEqual(r["last_seen"], "2026-09-01")
        self.assertIsNone(r["brand"])
        self.assertIsNone(r["genre"])
        self.assertIsNone(r["price"])


class TestBuildStateEntry(unittest.TestCase):
    def item(self):
        return {"title": "Anker 充電器", "percent_off": 30,
                "brand": "Anker", "price": 5000}

    def test_new_item(self):
        e = fd.build_state_entry({}, self.item(), TODAY.isoformat(), NOW_ISO)
        self.assertEqual(e["first_seen"], TODAY.isoformat())
        self.assertEqual(e["first_seen_at"], NOW_ISO)
        self.assertEqual(e["last_seen"], TODAY.isoformat())
        self.assertEqual(e["brand"], "Anker")
        self.assertEqual(e["price"], 5000)
        self.assertNotIn("past", e)
        self.assertNotIn("archived", e)

    def test_continuing_item_keeps_first_seen_and_past(self):
        prev = {
            "first_seen": "2026-10-01",
            "first_seen_at": "2026-10-01T09:00:00+09:00",
            "last_seen": "2026-10-04", "title": "旧タイトル",
            "percent_off": 20, "brand": "Anker", "price": 6000,
            "past": [{"first_seen": "2026-08-01", "last_seen": "2026-08-10",
                      "title": "Anker 充電器", "brand": "Anker",
                      "price": 7000, "percent_off": 10}],
        }
        e = fd.build_state_entry(prev, self.item(), TODAY.isoformat(), NOW_ISO)
        self.assertEqual(e["first_seen"], "2026-10-01")
        self.assertEqual(e["first_seen_at"], "2026-10-01T09:00:00+09:00")
        self.assertEqual(e["price"], 5000)  # 最新の観測値に更新される
        self.assertEqual(len(e["past"]), 1)  # past は持ち越される

    def test_archived_prev_starts_new_round_and_folds_old_one(self):
        prev = {
            "first_seen": "2026-08-01",
            "first_seen_at": "2026-08-01T09:00:00+09:00",
            "last_seen": "2026-08-20", "title": "Anker 充電器",
            "percent_off": 20, "brand": "Anker", "price": 6000,
            "archived": True,
        }
        e = fd.build_state_entry(prev, self.item(), TODAY.isoformat(), NOW_ISO)
        self.assertEqual(e["first_seen"], TODAY.isoformat())
        self.assertEqual(e["first_seen_at"], NOW_ISO)
        self.assertNotIn("archived", e)
        self.assertEqual(len(e["past"]), 1)
        self.assertEqual(e["past"][0]["first_seen"], "2026-08-01")
        self.assertEqual(e["past"][0]["last_seen"], "2026-08-20")
        self.assertEqual(e["past"][0]["price"], 6000)

    def test_past_rounds_are_capped(self):
        past = [
            {"first_seen": f"2026-01-{i + 1:02d}", "last_seen": "2026-01-20",
             "title": "t", "brand": "Anker", "price": 1, "percent_off": 1}
            for i in range(fd.PAST_ROUNDS_MAX)
        ]
        prev = {
            "first_seen": "2026-08-01", "last_seen": "2026-08-20",
            "title": "Anker 充電器", "percent_off": 20, "brand": "Anker",
            "price": 6000, "archived": True, "past": past,
        }
        e = fd.build_state_entry(prev, self.item(), TODAY.isoformat(), NOW_ISO)
        self.assertEqual(len(e["past"]), fd.PAST_ROUNDS_MAX)
        # 一番古いラウンドが落ち、新しいラウンドが末尾に付く
        self.assertEqual(e["past"][-1]["first_seen"], "2026-08-01")
        self.assertNotEqual(e["past"][0]["first_seen"], "2026-01-01")


class TestCarryOverState(unittest.TestCase):
    def run_carry(self, state, new_state=None):
        new_state = new_state if new_state is not None else {}
        kept, archived = fd.carry_over_state(state, new_state, TODAY)
        return new_state, kept, archived

    def test_within_grace_kept_untouched(self):
        entry = {"first_seen": days_ago(10), "last_seen": days_ago(3),
                 "title": "t", "percent_off": 20}
        new_state, kept, archived = self.run_carry({"A": entry})
        self.assertEqual((kept, archived), (1, 0))
        self.assertIs(new_state["A"], entry)
        self.assertNotIn("archived", new_state["A"])

    def test_beyond_grace_becomes_archived_copy(self):
        entry = {"first_seen": days_ago(40), "last_seen": days_ago(30),
                 "title": "t", "percent_off": 20, "brand": "Anker",
                 "price": 3000}
        new_state, kept, archived = self.run_carry({"A": entry})
        self.assertEqual((kept, archived), (0, 1))
        self.assertTrue(new_state["A"]["archived"])
        self.assertEqual(new_state["A"]["brand"], "Anker")
        # 元の辞書は書き換えない(呼び出し側の state を汚さない)
        self.assertNotIn("archived", entry)

    def test_already_archived_is_kept_forever(self):
        entry = {"first_seen": days_ago(200), "last_seen": days_ago(190),
                 "title": "t", "percent_off": 20, "archived": True}
        new_state, kept, archived = self.run_carry({"A": entry})
        self.assertEqual((kept, archived), (0, 0))
        self.assertIs(new_state["A"], entry)

    def test_broken_date_dropped(self):
        entry = {"first_seen": "壊れた日付", "last_seen": "壊れた日付",
                 "title": "t"}
        new_state, kept, archived = self.run_carry({"A": entry})
        self.assertEqual((kept, archived), (0, 0))
        self.assertNotIn("A", new_state)

    def test_listed_items_are_skipped(self):
        entry = {"first_seen": days_ago(40), "last_seen": days_ago(30),
                 "title": "t"}
        current = {"first_seen": TODAY.isoformat()}
        new_state, kept, archived = self.run_carry({"A": entry}, {"A": current})
        self.assertEqual((kept, archived), (0, 0))
        self.assertIs(new_state["A"], current)


class TestMainHistoryFlow(unittest.TestCase):
    """main()を通しで動かし、状態ファイルの遷移を確認する。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        (self.dir / "data").mkdir()

    def run_main(self, state, response_items):
        config = {
            "site_url": "https://example.test/",
            "min_saving_percent": 15,
            "pages_per_genre": 1,
            "genres": [{
                "name": "充電器", "keywords": ["充電器"],
                "search_index": "Electronics",
                "must_include_any": ["充電器"],
                "known_brands": [], "exclude_any": [],
            }],
        }
        (self.dir / "config.json").write_text(
            json.dumps(config, ensure_ascii=False), encoding="utf-8")
        (self.dir / "data" / "item_state.json").write_text(
            json.dumps(state, ensure_ascii=False), encoding="utf-8")
        response = {"searchResult": {"items": response_items}}

        def fake_search(auth, tag, **kwargs):
            return response

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
            stack.enter_context(
                mock.patch.object(fd, "get_access_token", return_value="tok"))
            stack.enter_context(
                mock.patch.object(fd, "search_with_retry", fake_search))
            stack.enter_context(
                mock.patch.object(fd, "get_items_with_retry",
                                  lambda *a, **k: {}))
            stack.enter_context(
                mock.patch.object(fd, "get_variations_with_retry",
                                  lambda *a, **k: {}))
            stack.enter_context(mock.patch.object(fd.time, "sleep", lambda s: None))
            stack.enter_context(contextlib.redirect_stdout(out))
            stack.enter_context(contextlib.redirect_stderr(err))
            rc = fd.main()
        self.assertEqual(rc, 0, err.getvalue())
        written = json.loads(
            (self.dir / "data" / "item_state.json").read_text(encoding="utf-8"))
        sales = json.loads(
            (self.dir / "data" / "sales.json").read_text(encoding="utf-8"))
        return written, sales

    def test_round_transitions_end_to_end(self):
        state = {
            # 終了済みラウンドを持つ商品が再登場する
            "OLD1": {
                "first_seen": "2026-08-01",
                "first_seen_at": "2026-08-01T09:00:00+09:00",
                "last_seen": "2026-08-20", "title": "Anker 充電器 旧",
                "percent_off": 20, "brand": "Anker", "price": 6000,
                "archived": True,
                "past": [{
                    "first_seen": "2026-06-01", "last_seen": "2026-06-15",
                    "title": "Anker 充電器 旧", "brand": "Anker",
                    "price": 7000, "percent_off": 10,
                }],
            },
            # 猶予を超えて検出されなかった商品はアーカイブされる
            "GONE1": {
                "first_seen": days_ago(40),
                "first_seen_at": days_ago(40) + "T09:00:00+09:00",
                "last_seen": days_ago(30), "title": "Anker モバイルバッテリー",
                "percent_off": 30, "brand": "Anker", "price": 3000,
            },
            # 猶予内の商品はそのまま保持される
            "KEEP1": {
                "first_seen": days_ago(10),
                "first_seen_at": days_ago(10) + "T09:00:00+09:00",
                "last_seen": days_ago(3), "title": "Anker ケーブル",
                "percent_off": 25, "brand": "Anker", "price": 2000,
            },
        }
        written, sales = self.run_main(
            state, [make_item("OLD1", "Anker 充電器 65W")])

        old = written["OLD1"]
        self.assertEqual(old["first_seen"], TODAY.isoformat())
        self.assertNotIn("archived", old)
        self.assertEqual(old["brand"], "Anker")
        self.assertEqual(old["price"], 5000)
        self.assertEqual(len(old["past"]), 2)
        self.assertEqual(old["past"][-1]["first_seen"], "2026-08-01")
        self.assertEqual(old["past"][-1]["price"], 6000)

        gone = written["GONE1"]
        self.assertTrue(gone["archived"])
        self.assertEqual(gone["first_seen"], days_ago(40))
        self.assertEqual(gone["brand"], "Anker")

        keep = written["KEEP1"]
        self.assertNotIn("archived", keep)
        self.assertEqual(keep["first_seen"], days_ago(10))

        # 再登場した商品は「新着」として通知され、掲載開始日も今日になる
        item = sales["genres"][0]["items"][0]
        self.assertEqual(item["asin"], "OLD1")
        self.assertEqual(item["since"], TODAY.isoformat())
        notify = json.loads(
            (self.dir / "data" / "pending_notification.json").read_text(
                encoding="utf-8"))
        self.assertIn("新着1件", notify[0]["title"])


if __name__ == "__main__":
    unittest.main()
