import importlib.util
import unittest
from unittest.mock import patch


@unittest.skipUnless(importlib.util.find_spec("flask"), "Flask is installed only for the Vercel runtime")
class CloudWebTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import app as cloud_app
        cls.app = cloud_app
        cls.client = cloud_app.app.test_client()

    def test_dashboard_renders_without_exposing_email_addresses(self):
        fixture = {
            "counts": {"complete": 2, "pending": 1},
            "digest": [{"source": "Example", "category": "science", "importance": 4,
                        "title": "Safe title", "url": "https://example.org/a",
                        "summary": "Summary", "reason": "Reason"}],
            "report": {"finished_at": "2026-11-02T00:00:00+00:00"},
            "deliveries": [{"email": "private@example.org", "status": "accepted"}],
        }
        with patch.object(self.app, "dashboard_data", return_value=fixture):
            response = self.client.get("/")
        body = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("Create your briefing", body)
        self.assertIn("Safe title", body)
        self.assertNotIn("private@example.org", body)

    def test_subscription_requires_same_origin_and_valid_action(self):
        denied = self.client.post("/subscribe", data={"action": "save"})
        self.assertEqual(denied.status_code, 403)
        invalid = self.client.post("/subscribe", headers={"Origin": "http://localhost"},
                                   data={"action": "invalid"})
        self.assertEqual(invalid.status_code, 400)

    def test_save_and_send_use_separate_operations(self):
        with patch.object(self.app, "save_subscription") as save, patch.object(self.app, "send_to") as send:
            saved = self.client.post("/subscribe", headers={"Origin": "http://localhost"}, data={
                "action": "save", "email": "reader@example.org", "category": ["science"], "daily": "yes"})
            sent = self.client.post("/subscribe", headers={"Origin": "http://localhost"}, data={
                "action": "send", "email": "reader@example.org", "category": ["science"]})
        self.assertEqual(saved.status_code, 303)
        self.assertEqual(sent.status_code, 303)
        self.assertEqual(save.call_count, 2)
        send.assert_called_once_with("reader@example.org", ["science"])
