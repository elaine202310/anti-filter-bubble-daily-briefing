"""Validated local topic preferences shared by the web page and daily pipeline."""
from __future__ import annotations

import json
import os
from pathlib import Path

from .core import CATEGORIES

DEFAULT_CATEGORIES = ("technology", "science", "politics")


def validate_categories(categories):
    selected = list(categories)
    if not 1 <= len(selected) < len(CATEGORIES):
        raise ValueError("Choose between 1 and 8 preferred categories")
    if len(selected) != len(set(selected)) or any(category not in CATEGORIES for category in selected):
        raise ValueError("Unknown or duplicate category")
    return selected


def load_preferences(root: Path, env=None):
    path = root / "data" / "preferences.json"
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        return validate_categories(data["preferred_categories"])
    if env is None:
        env = {}
        try:
            for line in (root / ".env").read_text(encoding="utf-8").splitlines():
                if line.startswith("PREFERRED_CATEGORIES="):
                    env["PREFERRED_CATEGORIES"] = line.split("=", 1)[1]
                    break
        except OSError:
            pass
    value = env.get("PREFERRED_CATEGORIES", ",".join(DEFAULT_CATEGORIES))
    return validate_categories([item.strip() for item in value.split(",") if item.strip()])


def save_preferences(root: Path, categories):
    selected = validate_categories(categories)
    path = root / "data" / "preferences.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps({"preferred_categories": selected}, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)
    return selected
