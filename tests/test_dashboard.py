import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from briefing_pipeline.dashboard import render_dashboard


class DashboardTests(unittest.TestCase):
    def test_reads_local_data_and_escapes_article_html(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "output").mkdir()
            (root / "data").mkdir()
            (root / "output" / "digest_items.json").write_text(json.dumps([{
                "title": "<script>alert(1)</script>", "url": "javascript:alert(1)",
                "source": "Example", "category": "science", "importance": 4,
                "summary": "A summary", "reason": "A reason",
            }]))
            connection = sqlite3.connect(root / "data" / "live.sqlite3")
            connection.execute("CREATE TABLE articles (title TEXT,source TEXT,category TEXT,importance INTEGER,model_status TEXT,published_at TEXT,url TEXT,fetched_at TEXT)")
            connection.execute("CREATE TABLE deliveries (day TEXT,status TEXT,detail TEXT)")
            connection.execute("INSERT INTO articles VALUES ('One','Example','science',4,'complete','2026-10-28','https://example.org','2026-10-28')")
            connection.commit()
            connection.close()
            page = render_dashboard(root)
            self.assertIn("Analyzed", page)
            self.assertIn('action="/preferences"', page)
            self.assertIn('name="category"', page)
            self.assertIn('action="/subscribe"', page)
            self.assertIn('name="email"', page)
            self.assertIn('name="daily"', page)
            self.assertIn("A summary", page)
            self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", page)
            self.assertNotIn("javascript:alert(1)", page)


    def test_has_empty_state(self):
        with tempfile.TemporaryDirectory() as folder:
            page = render_dashboard(Path(folder))
            self.assertIn("No digest yet", page)
            self.assertIn("No delivery attempts yet", page)
            self.assertEqual(page.translate({ord(c): None for c in page if "\u4e00" <= c <= "\u9fff"}), page)
            self.assertNotIn("Operation not permitted", page)
