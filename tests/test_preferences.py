import tempfile
import unittest
from pathlib import Path

from briefing_pipeline.core import rank_diverse, rank_interest
from briefing_pipeline.preferences import load_preferences, save_preferences, validate_categories


class PreferenceTests(unittest.TestCase):
    def test_saved_categories_change_ranking_and_survive_reload(self):
        rows = [
            {"id": "a", "category": "technology", "importance": 3, "published_at": "2026-10-29"},
            {"id": "b", "category": "health", "importance": 3, "published_at": "2026-10-28"},
            {"id": "c", "category": "science", "importance": 2, "published_at": "2026-10-27"},
        ]
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self.assertEqual(load_preferences(root), ["technology", "science", "politics"])
            save_preferences(root, ["health"])
            self.assertEqual(load_preferences(root), ["health"])
            self.assertEqual(rank_interest(rows, load_preferences(root), 1)[0]["id"], "b")
            self.assertEqual(len(rank_diverse(rows, load_preferences(root), 2)), 2)

    def test_invalid_and_all_categories_are_rejected(self):
        for values in ([], ["technology", "technology"], ["unknown"],
                       ["world", "politics", "technology", "science", "business", "health", "environment", "culture", "other"]):
            with self.assertRaises(ValueError):
                validate_categories(values)
