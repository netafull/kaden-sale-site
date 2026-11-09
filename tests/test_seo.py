"""トップの meta description と、商品画像の alt のテスト。

実行: python3 -m unittest discover -s tests
ネットワークには出ない(既存の data/ からHTMLをメモリ上で作るだけで、docs/ は書かない)。
"""

import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import generate_site  # noqa: E402


class SeoTest(unittest.TestCase):
    def page(self):
        data = json.loads((ROOT / "data" / "sales.json").read_text(encoding="utf-8"))
        return generate_site.generate_html(data)

    def test_item_image_alt_is_shortened_escaped_title(self):
        title = 'A&B "x" <1>'
        item = {"title": title, "price": 100, "url": "https://example.com/", "image": "https://example.com/a.jpg"}
        out = generate_site.render_book(item)
        expected = generate_site.esc(generate_site.shorten_title(title))
        self.assertIn(f'alt="{expected}"', out)
        self.assertIn("&amp;", expected)

    def test_item_image_alt_matches_displayed_title(self):
        title = "Anker Prime 充電器 (100W)：とても長い説明文がここに入ります。" * 3 + "；ブラック"
        item = {"title": title, "price": 100, "url": "https://example.com/", "image": "https://example.com/a.jpg"}
        out = generate_site.render_book(item)
        m_alt = re.search(r'<img [^>]*alt="([^"]*)"', out)
        m_t = re.search(r'<div class="t">([^<]*)</div>', out)
        self.assertEqual(m_alt.group(1), m_t.group(1))

    def test_top_meta_description(self):
        html_text = self.page()
        desc = generate_site.CONFIG["meta_description"]
        self.assertTrue(80 <= len(desc) <= 110, len(desc))
        self.assertIn(f'<meta name="description" content="{generate_site.esc(desc)}">', html_text)
        self.assertIn(f'<meta property="og:description" content="{generate_site.esc(desc)}">', html_text)

    def test_logo_alt_stays_empty(self):
        # サイト名が隣に文字で出ているロゴは装飾扱いで alt="" のまま
        self.assertRegex(self.page(), r'<img src="assets/logo.png" alt="" ')
