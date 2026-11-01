"""PostgreSQL-backed runtime used by the Vercel web app and daily cron job."""
from __future__ import annotations

import json
import os
import secrets
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .core import CATEGORIES, deliver_email, parse_feed, rank_diverse, render_digest
from .gemini import GeminiClient
from .preferences import DEFAULT_CATEGORIES, validate_categories
from .subscription import validate_email

ROOT = Path(__file__).resolve().parents[2]


def connect():
    database_url = os.environ.get("DATABASE_URL", "")
    if not database_url:
        raise RuntimeError("DATABASE_URL is not configured")
    import psycopg
    from psycopg.rows import dict_row
    return psycopg.connect(database_url, row_factory=dict_row)


def initialize(db) -> None:
    """Create the cloud schema. Every operation is safe to repeat."""
    db.execute("""CREATE TABLE IF NOT EXISTS articles (
        id TEXT PRIMARY KEY, source TEXT NOT NULL, title TEXT NOT NULL, url TEXT NOT NULL,
        description TEXT NOT NULL, published_at TIMESTAMPTZ NOT NULL, fetched_at TIMESTAMPTZ NOT NULL,
        category TEXT, summary TEXT, importance INTEGER, reason TEXT,
        model_status TEXT NOT NULL DEFAULT 'pending', model_error TEXT,
        provider TEXT, model TEXT, processed_at TIMESTAMPTZ
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS subscriptions (
        email TEXT PRIMARY KEY, preferred_categories JSONB NOT NULL,
        daily_enabled BOOLEAN NOT NULL DEFAULT FALSE,
        unsubscribe_token TEXT NOT NULL UNIQUE, updated_at TIMESTAMPTZ NOT NULL
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS subscription_deliveries (
        day DATE NOT NULL, email TEXT NOT NULL, status TEXT NOT NULL, detail TEXT,
        created_at TIMESTAMPTZ NOT NULL, PRIMARY KEY(day, email)
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS run_reports (
        id BIGSERIAL PRIMARY KEY, started_at TIMESTAMPTZ NOT NULL,
        finished_at TIMESTAMPTZ, report JSONB NOT NULL
    )""")
    db.commit()


def _fetch(url: str, timeout: int = 12) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "AntiFilterBubble/1.0"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        if response.status != 200:
            raise RuntimeError(f"RSS HTTP {response.status}")
        return response.read(2_000_001)[:2_000_000]


def save_subscription(email: str, categories, daily_enabled: bool) -> dict:
    email = validate_email(email)
    categories = validate_categories(categories)
    with connect() as db:
        initialize(db)
        row = db.execute("SELECT unsubscribe_token FROM subscriptions WHERE email=%s", (email,)).fetchone()
        token = row["unsubscribe_token"] if row else secrets.token_urlsafe(32)
        db.execute("""INSERT INTO subscriptions(email,preferred_categories,daily_enabled,unsubscribe_token,updated_at)
            VALUES(%s,%s::jsonb,%s,%s,%s)
            ON CONFLICT(email) DO UPDATE SET preferred_categories=excluded.preferred_categories,
            daily_enabled=excluded.daily_enabled, updated_at=excluded.updated_at""",
            (email, json.dumps(categories), bool(daily_enabled), token, datetime.now(timezone.utc)))
        db.commit()
    return {"email": email, "preferred_categories": categories, "daily_enabled": bool(daily_enabled), "unsubscribe_token": token}


def unsubscribe(token: str) -> bool:
    if not token or len(token) > 100:
        return False
    with connect() as db:
        initialize(db)
        cursor = db.execute("UPDATE subscriptions SET daily_enabled=FALSE,updated_at=%s WHERE unsubscribe_token=%s",
                            (datetime.now(timezone.utc), token))
        db.commit()
        return cursor.rowcount == 1


def _recent_complete(db) -> list[dict]:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=int(os.environ.get("LOOKBACK_HOURS", "48")))
    return list(db.execute("""SELECT * FROM articles
        WHERE model_status='complete' AND provider IS NOT NULL AND published_at >= %s
        ORDER BY published_at DESC""", (cutoff,)).fetchall())


def digest_for(db, categories) -> list[dict]:
    size = int(os.environ.get("DIGEST_SIZE", "10"))
    return rank_diverse(_recent_complete(db), validate_categories(categories), size)


def dashboard_data() -> dict:
    with connect() as db:
        initialize(db)
        counts = {row["model_status"]: row["count"] for row in db.execute(
            "SELECT model_status,COUNT(*) AS count FROM articles GROUP BY model_status").fetchall()}
        rows = _recent_complete(db)
        digest = rank_diverse(rows, list(DEFAULT_CATEGORIES), int(os.environ.get("DIGEST_SIZE", "10"))) if rows else []
        report_row = db.execute("SELECT report FROM run_reports ORDER BY id DESC LIMIT 1").fetchone()
        deliveries = list(db.execute("""SELECT day,email,status FROM subscription_deliveries
            ORDER BY created_at DESC LIMIT 5""").fetchall())
    return {"counts": counts, "digest": digest, "report": report_row["report"] if report_row else {},
            "deliveries": deliveries}


def _smtp_env() -> dict[str, str]:
    required = ("SMTP_HOST", "SMTP_FROM", "SMTP_USERNAME", "SMTP_PASSWORD")
    if not all(os.environ.get(key) for key in required):
        raise RuntimeError("SMTP sender settings are incomplete")
    return dict(os.environ)


def send_to(email: str, categories, *, require_daily: bool = False) -> str:
    email = validate_email(email)
    with connect() as db:
        initialize(db)
        subscription = db.execute("SELECT * FROM subscriptions WHERE email=%s", (email,)).fetchone()
        if require_daily:
            if not subscription or not subscription["daily_enabled"]:
                raise ValueError("Daily email has not been authorized")
            categories = subscription["preferred_categories"]
        items = digest_for(db, categories)
        if not items:
            raise ValueError("No analyzed articles are available yet")
        day = datetime.now(timezone.utc).date()
        inserted = db.execute("""INSERT INTO subscription_deliveries(day,email,status,detail,created_at)
            VALUES(%s,%s,'sending','SMTP acknowledgement pending',%s)
            ON CONFLICT(day,email) DO NOTHING RETURNING email""", (day, email, datetime.now(timezone.utc))).fetchone()
        if not inserted:
            raise ValueError("Delivery was already attempted for this address today")
        db.commit()
        try:
            body = render_digest(items)
            base_url = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")
            if subscription and base_url:
                body += f"\n\nManage daily delivery: {base_url}/unsubscribe/{subscription['unsubscribe_token']}"
            deliver_email(body, dict(_smtp_env(), SMTP_TO=email, EMAIL_PREVIEW_ONLY="false"), Path("/tmp/email_preview.txt"))
        except Exception as exc:
            db.execute("UPDATE subscription_deliveries SET status='uncertain',detail=%s WHERE day=%s AND email=%s",
                       (type(exc).__name__, day, email))
            db.commit()
            raise
        db.execute("UPDATE subscription_deliveries SET status='accepted',detail=%s WHERE day=%s AND email=%s",
                   ("SMTP server accepted; inbox receipt not verified", day, email))
        db.commit()
    return "accepted"


def run_pipeline() -> dict:
    """Collect six feeds, process bounded pending items, and persist one run report."""
    started = datetime.now(timezone.utc)
    report = {"started_at": started.isoformat(), "feeds": [], "processed": 0, "failed": 0}
    sources = json.loads((ROOT / "sources.json").read_text(encoding="utf-8"))
    if len(sources) != 6 or len({source["url"] for source in sources}) != 6:
        raise ValueError("Exactly six distinct RSS source URLs are required")
    with connect() as db:
        initialize(db)
        for source in sources:
            feed_report = {"source": source["name"], "url": source["url"]}
            try:
                articles = parse_feed(_fetch(source["url"]), source["name"])
                inserted = 0
                for article in articles:
                    row = db.execute("""INSERT INTO articles(id,source,title,url,description,published_at,fetched_at)
                        VALUES(%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(id) DO NOTHING RETURNING id""",
                        (article["id"], article["source"], article["title"], article["url"], article["description"],
                         article["published_at"], datetime.now(timezone.utc))).fetchone()
                    inserted += int(bool(row))
                db.commit()
                feed_report.update(status="ok", parsed=len(articles), inserted=inserted)
            except Exception as exc:
                db.rollback()
                feed_report.update(status="failed", error=type(exc).__name__)
            report["feeds"].append(feed_report)

        key = os.environ.get("GEMINI_API_KEY", "")
        model = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash-lite")
        if not key:
            report["model_stopped"] = "GEMINI_API_KEY missing"
        else:
            client = GeminiClient(key, model, timeout=40, retries=1)
            limit = max(1, min(int(os.environ.get("MAX_ARTICLES_PER_RUN", "3")), 20))
            pending = db.execute("""SELECT * FROM articles WHERE model_status='pending'
                ORDER BY published_at DESC LIMIT %s""", (limit,)).fetchall()
            for article in pending:
                try:
                    result = client.classify(dict(article))
                    db.execute("""UPDATE articles SET category=%s,summary=%s,importance=%s,reason=%s,
                        model_status='complete',model_error=NULL,provider='gemini',model=%s,processed_at=%s WHERE id=%s""",
                        (result["category"], result["summary"], result["importance"], result["reason"],
                         model, datetime.now(timezone.utc), article["id"]))
                    db.commit()
                    report["processed"] += 1
                except Exception as exc:
                    db.rollback()
                    db.execute("UPDATE articles SET model_status='failed',model_error=%s WHERE id=%s",
                               (f"{type(exc).__name__}: {exc}"[:1000], article["id"]))
                    db.commit()
                    report["failed"] += 1
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        db.execute("INSERT INTO run_reports(started_at,finished_at,report) VALUES(%s,%s,%s::jsonb)",
                   (started, datetime.now(timezone.utc), json.dumps(report)))
        db.commit()
    return report


def run_daily() -> dict:
    report = run_pipeline()
    sent = skipped = failed = 0
    with connect() as db:
        initialize(db)
        subscribers = list(db.execute("SELECT email,preferred_categories FROM subscriptions WHERE daily_enabled=TRUE").fetchall())
    for subscriber in subscribers:
        try:
            send_to(subscriber["email"], subscriber["preferred_categories"], require_daily=True)
            sent += 1
        except ValueError as exc:
            if "already attempted" in str(exc):
                skipped += 1
            else:
                failed += 1
        except Exception:
            failed += 1
    report["delivery"] = {"sent": sent, "skipped": skipped, "failed": failed}
    return report
