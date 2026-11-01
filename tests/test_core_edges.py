import unittest
from briefing_pipeline.core import canonical_url, normalize_date, parse_feed, rank_diverse, validate_result

class CoreEdgeTests(unittest.TestCase):
    def test_canonical_url_removes_tracking_only(self):
        self.assertEqual(canonical_url('HTTPS://Example.org/a?utm_source=x&id=1#top'),'https://example.org/a?id=1')
        for url in ('javascript:alert(1)','https://','https://user:pass@example.org/a'):
            with self.assertRaises(ValueError):canonical_url(url)
    def test_utc_and_missing_date(self):
        self.assertEqual(normalize_date('Tue, 27 Oct 2026 21:00:00 +1100'),'2026-10-27T10:00:00+00:00')
        self.assertTrue(normalize_date('bad').endswith('+00:00'))
    def test_atom_and_nonfeed(self):
        xml=b'<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Test</title><link rel="alternate" href="https://example.org/a"/><updated>2026-10-27T00:00:00Z</updated></entry></feed>'
        self.assertEqual(parse_feed(xml,'test')[0]['url'],'https://example.org/a')
        with self.assertRaises(ValueError):parse_feed(b'<html/>','test')
    def test_quota_keeps_existing_outside_items(self):
        rows=[]
        for i in range(20): rows.append(dict(id=str(i),category='technology' if i<17 else 'health',importance=5 if i==17 else 3,published_at=str(i)))
        selected=rank_diverse(rows,['technology'],10)
        self.assertEqual(sum(r['category']=='health' for r in selected),2)
        self.assertIn('17',[r['id'] for r in selected])
