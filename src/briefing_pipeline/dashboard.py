"""Local dashboard for viewing results and setting topic preferences."""
from __future__ import annotations

import html
import json
import sqlite3
import fcntl
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .core import CATEGORIES, Store, load_env
from .preferences import load_preferences, save_preferences
from .subscription import load_subscription, save_subscription, cancel_subscription, send_subscription


def _text(value):
    return html.escape(str(value if value is not None else ""), quote=True)


def _json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _link(url: str, title: str):
    parsed = urlparse(url or "")
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return _text(title)
    return f'<a href="{_text(url)}" target="_blank" rel="noopener noreferrer">{_text(title)}</a>'


def render_dashboard(root: Path, notice: str = "", error: str = "") -> str:
    output = root / "output"
    report = _json(output / "run_report.json")
    comparison = _json(output / "ranking_comparison.json")
    evaluation = _json(output / "evaluation.json")
    items = _json(output / "digest_items.json")
    if not isinstance(items, list):
        items = []
    articles = []
    deliveries = []
    subscription_deliveries = []
    counts = {}
    database = root / "data" / "live.sqlite3"
    if database.exists():
        connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        try:
            counts = {r[0]: r[1] for r in connection.execute("SELECT model_status, COUNT(*) FROM articles GROUP BY model_status")}
            articles = [dict(r) for r in connection.execute("SELECT title,source,category,importance,model_status,published_at,url FROM articles ORDER BY fetched_at DESC LIMIT 12")]
            deliveries = [dict(r) for r in connection.execute("SELECT day,status,detail FROM deliveries ORDER BY day DESC LIMIT 3")]
            if connection.execute("SELECT 1 FROM sqlite_master WHERE name='subscription_deliveries'").fetchone():
                subscription_deliveries = [dict(r) for r in connection.execute("SELECT day,email,status FROM subscription_deliveries ORDER BY day DESC LIMIT 3")]
        finally:
            connection.close()

    total = sum(counts.values())
    selected_categories = load_preferences(root)
    subscription = load_subscription(root)
    category_labels = {category: category.title() for category in CATEGORIES}
    category_choices = "".join(
        f'<label class="topic"><input type="checkbox" name="category" value="{_text(category)}" {"checked" if category in selected_categories else ""}><span>{_text(category_labels[category])}<small>{_text(category)}</small></span></label>'
        for category in CATEGORIES
    )
    notice_html = f'<p class="notice" role="status">{_text(notice)}</p>' if notice else ""
    error_html = f'<p class="warning" role="alert">{_text(error)}</p>' if error else ""
    baseline = comparison.get("baseline", {})
    diverse = comparison.get("diverse", {})

    def share(part):
        value = part.get("outside_share")
        return "—" if value is None else f"{value:.0%}"

    cards = [
        ("Collected", str(total), "Articles deduplicated in SQLite"),
        ("Analyzed", str(counts.get("complete", 0)), "Completed model analyses"),
        ("Current digest", str(len(items)), "Articles selected"),
        ("Outside interests", f"{share(baseline)} → {share(diverse)}", "Interest ranking → diversity quota"),
    ]
    cards_html = "".join(f'<div class="card"><span>{_text(label)}</span><strong>{_text(value)}</strong><small>{_text(note)}</small></div>' for label, value, note in cards)
    delivery = subscription_deliveries[0] if subscription_deliveries else (deliveries[0] if deliveries else None)
    delivery_text = f"{delivery['day']} · {delivery['status']}" if delivery else "No delivery attempts yet"
    if delivery and delivery.get("email"):
        delivery_text += f" · {delivery['email']}"
    subscription_status = "Daily delivery selected" if subscription["daily_enabled"] else "Daily delivery not selected"
    unsubscribe_html = '<form method="post" action="/unsubscribe"><button type="submit">Cancel daily subscription</button></form>' if subscription["daily_enabled"] else ""
    run_text = report.get("finished_at", "No runs yet")
    if run_text != "No runs yet":
        try:
            run_text = datetime.fromisoformat(run_text).astimezone().strftime("%Y-%m-%d %H:%M %Z")
        except ValueError:
            pass
    feed_rows = "".join(f'<tr><td>{_text(f.get("source"))}</td><td>{_text(f.get("status"))}</td><td>{_text(f.get("parsed", "—"))}</td><td>{_text(f.get("inserted", "—"))}</td></tr>' for f in report.get("feeds", [])) or '<tr><td colspan="4">No feed runs yet</td></tr>'
    digest_html = "".join(
        f'<article class="story"><div class="eyebrow">{i:02d} · {_text(item.get("source"))} · {_text(item.get("category"))} · {_text(item.get("importance"))}/5</div>'
        f'<h3>{_link(item.get("url", ""), item.get("title", "Untitled"))}</h3>'
        f'<p>{_text(item.get("summary"))}</p><details><summary>Why this score?</summary><p>{_text(item.get("reason"))}</p></details></article>'
        for i, item in enumerate(items, 1)
    ) or '<p class="empty">No digest yet. Select “Generate today’s digest” above.</p>'
    recent_html = "".join(
        f'<tr><td>{_link(a["url"], a["title"])}</td><td>{_text(a["source"])}</td><td><span class="pill">{_text(a["model_status"])}</span></td><td>{_text(a["category"] or "—")}</td></tr>'
        for a in articles
    ) or '<tr><td colspan="4">No articles yet</td></tr>'
    if evaluation and evaluation.get("n"):
        evaluation_text = f'{_text(evaluation.get("n"))} articles · exact {evaluation["exact_agreement"]:.0%} · within ±1 {evaluation["within_1_point"]:.0%}'
    else:
        evaluation_text = "The 30 human labels are incomplete; agreement cannot be reported yet."
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Anti-Filter-Bubble · Daily Briefing</title><style>
:root{{--ink:#18332e;--muted:#60746e;--line:#dfe8df;--paper:#f6f8f2;--accent:#165e4d;--light:#e7f1e7}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--paper);color:var(--ink);font:15px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}}a{{color:var(--accent);text-decoration:none}}a:hover{{text-decoration:underline}}
header{{background:#173b33;color:white;padding:34px max(24px,calc((100vw - 1140px)/2)) 30px}}header p{{color:#bfd5ca;margin:6px 0 0}}h1{{font-size:32px;line-height:1.2;margin:0}}main{{max-width:1188px;margin:auto;padding:26px 24px 70px}}.top{{display:flex;align-items:end;justify-content:space-between;gap:20px;flex-wrap:wrap}}.top p{{color:var(--muted);margin:5px 0 0}}
.cards{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:22px 0}}.card,.panel{{background:white;border:1px solid var(--line);border-radius:14px;box-shadow:0 3px 15px #1b352508}}.card{{padding:18px}}.card span,.card small{{display:block;color:var(--muted)}}.card strong{{display:block;font-size:27px;margin:4px 0;line-height:1.25}}.layout{{display:grid;grid-template-columns:minmax(0,2fr) minmax(280px,1fr);gap:18px}}.panel{{padding:22px;margin-bottom:18px}}h2{{font-size:21px;margin:0 0 12px}}h3{{font-size:18px;line-height:1.35;margin:4px 0}}.story{{border-top:1px solid var(--line);padding:17px 0}}.story:first-of-type{{border-top:0}}.story p{{margin:7px 0;color:#40554e}}.eyebrow{{font-size:12px;font-weight:700;letter-spacing:.05em;text-transform:uppercase;color:var(--accent)}}details{{font-size:13px;color:var(--muted)}}summary{{cursor:pointer}}table{{width:100%;border-collapse:collapse;font-size:13px}}td,th{{text-align:left;border-bottom:1px solid var(--line);padding:9px 5px;vertical-align:top}}th{{color:var(--muted);font-weight:600}}.pill{{background:var(--light);padding:3px 7px;border-radius:6px}}.meta{{font-size:13px;color:var(--muted)}}.empty{{color:var(--muted)}}button{{border:0;background:#e3efe6;color:var(--accent);border-radius:7px;padding:8px 12px;cursor:pointer}}button:focus-visible,input:focus-visible{{outline:3px solid #d59d3a;outline-offset:2px}}.topics{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px;margin:14px 0}}.topic{{display:flex;align-items:center;gap:8px;padding:9px;border:1px solid var(--line);border-radius:9px;cursor:pointer}}.topic:has(input:checked){{background:var(--light);border-color:var(--accent)}}.topic input{{accent-color:var(--accent)}}.topic small{{display:block;color:var(--muted);font-size:11px;line-height:1.1}}.topic span{{font-weight:600;line-height:1.2}}.notice{{padding:10px 12px;background:var(--light);border-radius:8px;color:var(--accent)}}.warning{{padding:10px 12px;background:#fff2dd;border:1px solid #e7bc6b;border-radius:8px;color:#684317}}.form-action{{background:var(--accent);color:white;font-weight:700}}.subscription-form{{display:flex;gap:14px;flex-wrap:wrap;align-items:end}}.field{{display:grid;gap:5px;flex:1 1 250px;font-weight:600}}.field input{{font:inherit;border:1px solid #a9bdb2;border-radius:8px;padding:9px 11px;width:100%}}.consent{{display:flex;align-items:start;gap:8px;margin:14px 0}}.consent input{{margin-top:5px;accent-color:var(--accent)}}@media(max-width:850px){{.cards{{grid-template-columns:repeat(2,1fr)}}.layout{{grid-template-columns:1fr}}}}@media(max-width:480px){{.cards{{grid-template-columns:1fr}}.topics{{grid-template-columns:repeat(2,minmax(0,1fr))}}h1{{font-size:26px}}main{{padding:20px 14px}}}}
</style></head><body><header><h1>Anti-Filter-Bubble</h1><p>Daily Briefing · Six RSS feeds · Gemini analysis · Diversity quota</p></header><main>
<div class="top"><div><h2>Today at a glance</h2><p>Last run: {_text(run_text)}</p></div><div class="subscription-form"><form method="post" action="/refresh"><button type="submit">Generate today’s digest (RSS + Gemini)</button></form><button onclick="location.reload()">Refresh page</button></div></div>{notice_html}{error_html}
<div class="panel"><h2>Choose your topics</h2><p class="meta">Which topics interest you most? The digest ranks them first while aiming for at least 20% outside your interests when enough articles are available.</p><form method="post" action="/preferences"><div class="topics">{category_choices}</div><button class="form-action" type="submit">Save preferences and update preview</button></form><p class="meta">Saving only reorders analyzed articles. It does not call the model or resend an email.</p></div>
<div class="panel"><h2>Email subscription</h2><p class="meta">Enter your email address. Choose separately whether you want a daily briefing.</p><form method="post" action="/subscribe"><label class="field">Email address<input type="email" name="email" required maxlength="254" autocomplete="email" value="{_text(subscription['email'])}" placeholder="name@example.com"></label><label class="consent"><input type="checkbox" name="daily" value="yes" {"checked" if subscription['daily_enabled'] else ""}><span>I agree to receive a daily briefing</span></label><div class="subscription-form"><button type="submit" name="submit_action" value="save">Save subscription settings</button><button class="form-action" type="submit" name="submit_action" value="send">Send current digest</button></div></form><p class="meta">Status: {_text(subscription_status)}. Send uses the current digest. Delivery is attempted only once per address each day.</p>{unsubscribe_html}</div>
<div class="cards">{cards_html}</div><div class="layout"><section><div class="panel"><h2>Latest digest</h2><p class="meta">Preferred topics: {_text(', '.join(comparison.get('preferred_categories', [])) or 'None selected')} · Quota: at least 20% outside your interests when enough articles are available</p>{digest_html}</div>
<div class="panel"><h2>Recently collected articles</h2><table><thead><tr><th>Title</th><th>Source</th><th>Status</th><th>Category</th></tr></thead><tbody>{recent_html}</tbody></table></div></section>
<aside><div class="panel"><h2>Delivery status</h2><p>{_text(delivery_text)}</p><p class="meta">“accepted” means the SMTP server accepted the message. Check your inbox to confirm receipt. Preference changes do not alter emails already sent.</p></div>
<div class="panel"><h2>Six feeds</h2><table><thead><tr><th>Source</th><th>Status</th><th>Parsed</th><th>Inserted</th></tr></thead><tbody>{feed_rows}</tbody></table></div>
<div class="panel"><h2>Evaluation</h2><p>{evaluation_text}</p><p class="meta">Agreement is reported only after human labels are compared with model scores.</p></div></aside></div></main></body></html>'''


def serve(root: Path, port: int = 8765):
    if not 1 <= port <= 65535:
        raise ValueError("Port must be 1..65535")

    class Handler(BaseHTTPRequestHandler):
        def _local_request(self):
            return self.headers.get("Host") in (f"127.0.0.1:{port}", f"localhost:{port}")

        def do_GET(self):
            if not self._local_request():
                self.send_error(403)
                return
            parsed = urlparse(self.path)
            if parsed.path not in ("/", "/index.html"):
                self.send_error(404)
                return
            query = parse_qs(parsed.query)
            notice = "Preferences saved and the digest preview updated. Emails already sent today are unchanged." if query.get("saved") == ["1"] else ""
            if query.get("subscription") == ["saved"]:
                notice = "Subscription settings saved."
            elif query.get("subscription") == ["sent"]:
                notice = "Digest submitted to the SMTP server. Check your inbox to confirm receipt."
            elif query.get("subscription") == ["cancelled"]:
                notice = "Daily subscription cancelled."
            elif query.get("refresh") == ["ready"]:
                notice = "Today’s digest is ready. Review it and choose whether to send it."
            body = render_dashboard(root, notice).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            if not self._local_request() or self.headers.get("Origin") not in (f"http://127.0.0.1:{port}", f"http://localhost:{port}"):
                self.send_error(403, "Only this local dashboard may change preferences")
                return
            if self.path not in ("/preferences", "/subscribe", "/unsubscribe", "/refresh") or self.headers.get("Content-Type", "").split(";", 1)[0] != "application/x-www-form-urlencoded":
                self.send_error(404)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 <= length <= 2048 or (self.path in ("/preferences", "/subscribe") and length == 0):
                    raise ValueError("Invalid form size")
                fields = parse_qs(self.rfile.read(length).decode("utf-8"), keep_blank_values=True)
                destination = "/?saved=1"
                if self.path == "/refresh":
                    from .pipeline import run
                    if run(root) != 0:
                        raise ValueError("No sendable digest was generated. Check the run report.")
                    destination = "/?refresh=ready"
                else:
                    lock_path = root / "data" / "run.lock"
                    lock_path.parent.mkdir(parents=True, exist_ok=True)
                    with lock_path.open("w") as lock:
                        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        if self.path == "/preferences":
                            from .preferences import validate_categories
                            selected = validate_categories(fields.get("category", []))
                            save_preferences(root, selected)
                            if (root / "data" / "live.sqlite3").exists():
                                from .pipeline import create_digest, initialize
                                store = Store(root / "data" / "live.sqlite3")
                                try:
                                    initialize(store)
                                    create_digest(root, store, load_env(root / ".env"), {})
                                finally:
                                    store.close()
                        elif self.path == "/unsubscribe":
                            cancel_subscription(root)
                            destination = "/?subscription=cancelled"
                        else:
                            email_values = fields.get("email", [])
                            actions = fields.get("submit_action", [])
                            if len(email_values) != 1 or actions not in (["save"], ["send"]) or fields.get("daily", []) not in ([], ["yes"]):
                                raise ValueError("Invalid subscription form.")
                            daily = fields.get("daily") == ["yes"]
                            if actions == ["send"]:
                                send_subscription(root, email_values[0])
                                destination = "/?subscription=sent"
                            else:
                                destination = "/?subscription=saved"
                            save_subscription(root, email_values[0], daily)
            except (ValueError, UnicodeError) as exc:
                body = render_dashboard(root, error=str(exc)).encode("utf-8")
                self.send_response(400)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            except BlockingIOError:
                self.send_error(409, "Daily run is in progress; retry after it finishes")
                return
            except Exception:
                message = ("Digest generation failed. Check RSS and Gemini settings and logs/pipeline.log." if self.path == "/refresh"
                           else "Email submission failed. Check SMTP settings and logs. No new daily subscription preference was saved.")
                body = render_dashboard(root, error=message).encode("utf-8")
                self.send_response(502)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            self.send_response(303)
            self.send_header("Location", destination)
            self.send_header("Content-Length", "0")
            self.end_headers()

    with ThreadingHTTPServer(("127.0.0.1", port), Handler) as server:
        print(f"Local dashboard: http://127.0.0.1:{port}/", flush=True)
        server.serve_forever()
