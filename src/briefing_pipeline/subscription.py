"""One local subscriber, explicit send consent, and per-recipient delivery records."""
from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .core import Store, deliver_email, load_env, render_digest

LOG = logging.getLogger(__name__)
EMAIL_RE = re.compile(r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\.[A-Za-z]{2,63}$")


def validate_email(value: str) -> str:
    email = value.strip().lower()
    if len(email) > 254 or not EMAIL_RE.fullmatch(email) or ".." in email:
        raise ValueError("Enter a valid email address.")
    return email


def load_subscription(root: Path) -> dict:
    path = root / "data" / "subscription.json"
    if not path.exists():
        return {"email": "", "daily_enabled": False}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {"email": validate_email(data["email"]), "daily_enabled": data.get("daily_enabled") is True}


def save_subscription(root: Path, email: str, daily_enabled: bool) -> dict:
    subscription = {"email": validate_email(email), "daily_enabled": bool(daily_enabled),
                    "updated_at": datetime.now(timezone.utc).isoformat()}
    path = root / "data" / "subscription.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(subscription, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)
    return subscription


def cancel_subscription(root: Path) -> dict:
    current = load_subscription(root)
    if current["email"]:
        return save_subscription(root, current["email"], False)
    return current


def send_subscription(root: Path, email: str, *, require_daily: bool = False) -> str:
    """Submit a real digest only after UI confirmation or explicit daily opt-in."""
    email = validate_email(email)
    if require_daily:
        subscription = load_subscription(root)
        if not subscription["daily_enabled"] or subscription["email"] != email:
            raise ValueError("Daily email has not been authorized.")
    env = load_env(root / ".env")
    if not all(env.get(key) for key in ("SMTP_HOST", "SMTP_FROM", "SMTP_USERNAME", "SMTP_PASSWORD")):
        raise ValueError("SMTP sender settings are incomplete.")
    try:
        items = json.loads((root / "output" / "digest_items.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError("Generate a digest first.") from exc
    if not items or any(not item.get("provider") for item in items):
        raise ValueError("A nonempty digest with real model results is required.")
    cutoff = datetime.now(timezone.utc) - timedelta(hours=48)
    if any(datetime.fromisoformat(item["published_at"]) < cutoff for item in items):
        raise ValueError("The current digest has expired. Generate a new one first.")
    store = Store(root / "data" / "live.sqlite3")
    day = datetime.now().date().isoformat()
    try:
        store.db.execute("CREATE TABLE IF NOT EXISTS subscription_deliveries (day TEXT NOT NULL, email TEXT NOT NULL, status TEXT NOT NULL, detail TEXT, PRIMARY KEY(day,email))")
        store.db.commit()
        if store.db.execute("SELECT 1 FROM subscription_deliveries WHERE day=? AND email=?", (day, email)).fetchone():
            raise ValueError("Delivery was already attempted for this address today. Check your inbox before trying again.")
        store.db.execute("INSERT INTO subscription_deliveries VALUES (?,?,?,?)", (day, email, "sending", "SMTP acknowledgement pending"))
        store.db.commit()
        try:
            deliver_email(render_digest(items), dict(env, SMTP_TO=email, EMAIL_PREVIEW_ONLY="false"), root / "output" / "email_preview.txt")
        except Exception as exc:
            store.db.execute("UPDATE subscription_deliveries SET status=?,detail=? WHERE day=? AND email=?", ("uncertain", type(exc).__name__, day, email))
            store.db.commit()
            LOG.exception("subscription send failed recipient=%s", email)
            raise
        store.db.execute("UPDATE subscription_deliveries SET status=?,detail=? WHERE day=? AND email=?", ("accepted", "SMTP server accepted; inbox receipt not verified", day, email))
        store.db.commit()
        LOG.info("subscription email accepted recipient=%s day=%s", email, day)
        return "accepted"
    finally:
        store.close()


def send_daily_if_subscribed(root: Path) -> str:
    subscription = load_subscription(root)
    if not subscription["daily_enabled"]:
        return "skipped_no_consent"
    return send_subscription(root, subscription["email"], require_daily=True)
