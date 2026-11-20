"""docs/feed.json(ポチフル用の新着フィード)のテスト。Amazon APIは呼ばない。

実行: python3 -m unittest discover -s tests -v
"""

import datetime
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import generate_site  # noqa: E402

JST = datetime.timezone(datetime.timedelta(hours=9))
NOW = datetime.datetime(2026, 11, 20, 22, 0, tzinfo=JST)


def item(asin, since_at=None, since=None, pct=30, price=1980):
    b = {
        "asin": asin, "title": f"商品{asin}", "price": price, "percent_off": pct,
        "image": f"https://m.media-amazon.com/images/I/{asin}.jpg",
        "url": f"https://www.amazon.co.jp/dp/{asin}?tag=t-22",
    }
    if since_at:
        b["since_at"] = since_at
    if since:
        b["since"] = since
    return b


class FeedTest(unittest.TestCase):
    def feed(self, genres):
        return generate_site.generate_feed_data({"genres": genres}, NOW)

    def test_shape(self):
        f = self.feed([{"name": "G0", "items": []}, {"name": "G1", "items": [item("A", "2026-11-20T21:00:00+09:00")]}])
        self.assertEqual((f["version"], f["site"]), (1, "kaden"))
        it = f["items"][0]
        self.assertEqual(it["id"], "kaden:A")
        self.assertEqual(it["note"], "30%OFF ¥1,980")
        self.assertTrue(it["url"].endswith("#g1"))
        self.assertEqual(it["date"], "2026-11-20")
        self.assertIn("tag=", it["image_link"])
        self.assertEqual(it["source"], "kaden")

    def test_window_order_and_dedupe(self):
        f = self.feed([
            {"name": "G0", "items": [
                item("OLD", "2026-11-17T23:00:00+09:00"),
                item("MID", "2026-11-19T10:00:00+09:00"),
                item("NEW", "2026-11-20T21:00:00+09:00"),
                item("DAYONLY", since="2026-11-18"),
            ]},
            {"name": "G1", "items": [item("NEW", "2026-11-20T21:00:00+09:00")]},
        ])
        self.assertEqual([i["id"] for i in f["items"]], ["kaden:NEW", "kaden:MID", "kaden:DAYONLY"])

    def test_utc_converted_to_jst(self):
        f = self.feed([{"name": "G", "items": [item("A", "2026-11-19T20:00:00+00:00")]}])
        self.assertEqual(f["items"][0]["date"], "2026-11-20")

    def test_no_date_skipped_and_max_20(self):
        items = [item("X")] + [item(f"N{n}", "2026-11-20T10:00:00+09:00") for n in range(30)]
        f = self.feed([{"name": "G", "items": items}])
        self.assertEqual(len(f["items"]), 20)
        self.assertNotIn("kaden:X", [i["id"] for i in f["items"]])


if __name__ == "__main__":
    unittest.main()
