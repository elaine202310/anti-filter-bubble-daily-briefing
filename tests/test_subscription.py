import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from briefing_pipeline.subscription import (
    cancel_subscription, load_subscription, save_subscription,
    send_daily_if_subscribed, send_subscription, validate_email,
)


class SubscriptionTests(unittest.TestCase):
    def test_consent_can_be_saved_and_cancelled_without_sending(self):
        with tempfile.TemporaryDirectory() as folder, patch("briefing_pipeline.subscription.deliver_email") as send:
            root = Path(folder)
            self.assertEqual(send_daily_if_subscribed(root), "skipped_no_consent")
            save_subscription(root, "Reader@Example.org", True)
            self.assertEqual(load_subscription(root)["email"], "reader@example.org")
            self.assertTrue(load_subscription(root)["daily_enabled"])
            cancel_subscription(root)
            self.assertFalse(load_subscription(root)["daily_enabled"])
            send.assert_not_called()

    def test_real_digest_send_is_mocked_and_same_day_duplicate_is_blocked(self):
        with tempfile.TemporaryDirectory() as folder, patch("briefing_pipeline.subscription.deliver_email", return_value="sent") as send:
            root = Path(folder)
            (root / "output").mkdir()
            (root / ".env").write_text("SMTP_HOST=smtp.example.org\nSMTP_FROM=sender@example.org\nSMTP_USERNAME=sender@example.org\nSMTP_PASSWORD=fake-test-only\n")
            item = {"id": "one", "title": "Example", "source": "Example", "url": "https://example.org/a",
                    "category": "science", "importance": 3, "summary": "Summary", "reason": "Reason",
                    "published_at": datetime.now(timezone.utc).isoformat(), "provider": "gemini"}
            (root / "output" / "digest_items.json").write_text(json.dumps([item]))
            self.assertEqual(send_subscription(root, "reader@example.org"), "accepted")
            self.assertEqual(send.call_count, 1)
            self.assertEqual(send.call_args.args[1]["SMTP_TO"], "reader@example.org")
            with self.assertRaisesRegex(ValueError, "Delivery was already attempted"):
                send_subscription(root, "reader@example.org")
            self.assertEqual(send.call_count, 1)
            db = sqlite3.connect(root / "data" / "live.sqlite3")
            self.assertEqual(db.execute("SELECT status FROM subscription_deliveries").fetchone()[0], "accepted")
            db.close()

    def test_invalid_email_and_unconsented_daily_send_are_rejected(self):
        for address in ("", "a@", "a@bad", "a\nb@example.org", "a..b@example.org"):
            with self.assertRaises(ValueError):
                validate_email(address)
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            save_subscription(root, "reader@example.org", False)
            self.assertEqual(send_daily_if_subscribed(root), "skipped_no_consent")


if __name__ == "__main__":
    unittest.main()
