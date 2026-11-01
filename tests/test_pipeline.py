import json
import tempfile
import unittest
from pathlib import Path

from briefing_pipeline.core import QwenClient, Store, parse_feed, rank_diverse, rank_interest, validate_result, write_email_preview
from briefing_pipeline.evaluation import metrics


def record(i, category, importance=3):
    return {"id": str(i), "category": category, "importance": importance, "published_at": "2026-01-%02dT00:00:00+00:00" % (i % 28 + 1),
            "title": "Article %s" % i, "source": "test", "url": "https://example.test/%s" % i, "summary": "Summary", "reason": "Reason"}


class PipelineTests(unittest.TestCase):
    def test_sqlite_deduplication(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / "db.sqlite3")
            article = {"id":"same", "source":"s", "title":"t", "url":"https://example.test", "description":"", "published_at":"2026-01-01T00:00:00+00:00"}
            self.assertTrue(store.add(article))
            self.assertFalse(store.add(article))
            self.assertEqual(store.db.execute("select count(*) from articles").fetchone()[0], 1)
            store.close()

    def test_schema_validation(self):
        good = {"category":"science", "summary":"A summary", "importance":4, "reason":"Broad impact"}
        self.assertEqual(validate_result(good), good)
        with self.assertRaises(ValueError):
            validate_result(dict(good, importance=True))

    def test_qwen_retry_then_success(self):
        calls = []
        payload = {"category":"science", "summary":"Summary", "importance":4, "reason":"Reason"}
        def transport(body, headers, timeout):
            calls.append(1)
            if len(calls) == 1:
                return b'{bad json'
            return json.dumps({"choices":[{"message":{"content":json.dumps(payload)}}]}).encode()
        result = QwenClient("fake", "fake", "https://example.test", retries=1, transport=transport).classify({"id":"x","title":"t","description":"d"})
        self.assertEqual(result, payload)
        self.assertEqual(len(calls), 2)

    def test_failed_article_isolated(self):
        items = [{"id":"a","title":"one","description":""},{"id":"b","title":"two","description":""}]
        def transport(body, headers, timeout):
            prompt = json.loads(body)["messages"][1]["content"]
            if '"title": "one"' in prompt:
                return b'{broken'
            result = {"category":"world","summary":"ok","importance":2,"reason":"ok"}
            return json.dumps({"choices":[{"message":{"content":json.dumps(result)}}]}).encode()
        client = QwenClient("fake", "fake", "https://example.test", retries=0, transport=transport)
        outcomes = []
        for item in items:
            try:
                outcomes.append(client.classify(dict(item, id=item["id"])))
            except RuntimeError:
                outcomes.append(None)
        self.assertIsNone(outcomes[0])
        self.assertIsNotNone(outcomes[1])

    def test_diversity_quota(self):
        rows = [record(i, "technology") for i in range(1, 9)] + [record(9, "environment"), record(10, "culture")]
        preferred = ["technology", "science", "politics"]
        base = rank_interest(rows, preferred, 10)
        diverse = rank_diverse(rows, preferred, 10, 0.2)
        self.assertEqual(sum(r["category"] not in preferred for r in base), 2)
        self.assertEqual(sum(r["category"] not in preferred for r in diverse), 2)
        # With a sufficiently large preferred pool, baseline is zero and quota adds two.
        rows = [record(i, "technology") for i in range(1, 15)] + [record(15, "environment"), record(16, "culture")]
        self.assertEqual(sum(r["category"] not in preferred for r in rank_interest(rows, preferred, 10)), 0)
        self.assertEqual(sum(r["category"] not in preferred for r in rank_diverse(rows, preferred, 10)), 2)

    def test_metrics(self):
        result = metrics([1, 2, 3, 4, 5], [1, 3, 3, 2, 5])
        self.assertEqual(result["exact_agreement"], 0.6)
        self.assertEqual(result["within_1_point"], 0.8)

    def test_email_preview(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "email.txt"
            write_email_preview(path, "test@example.test", "Subject", "Digest")
            self.assertIn("Subject: Subject", path.read_text())
            self.assertIn("Digest", path.read_text())

    def test_feed_invalid_link_and_bad_xml(self):
        xml = b'<rss><channel><item><title>Missing link</title></item><item><title>Valid</title><link>https://example.test/a</link></item></channel></rss>'
        self.assertEqual(len(parse_feed(xml, "x")), 1)
        with self.assertRaises(ValueError):
            parse_feed(b'<rss>', "x")


if __name__ == "__main__":
    unittest.main()
