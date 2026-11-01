"""Vercel Flask entry point for the public briefing and subscription UI."""
from __future__ import annotations

import html
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from flask import Flask, abort, redirect, render_template_string, request, url_for

from briefing_pipeline.cloud import dashboard_data, run_daily, run_pipeline, save_subscription, send_to, unsubscribe
from briefing_pipeline.core import CATEGORIES
from briefing_pipeline.preferences import DEFAULT_CATEGORIES

app = Flask(__name__)

PAGE = r'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Anti-Filter-Bubble · Daily Briefing</title><style>
:root{--ink:#18332e;--muted:#60746e;--line:#dfe8df;--paper:#f6f8f2;--accent:#165e4d;--light:#e7f1e7}*{box-sizing:border-box}
body{margin:0;background:var(--paper);color:var(--ink);font:15px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}
header{background:#173b33;color:white;padding:34px max(24px,calc((100vw - 1140px)/2)) 30px}header p{color:#bfd5ca;margin:6px 0 0}h1{font-size:32px;line-height:1.2;margin:0}main{max-width:1188px;margin:auto;padding:26px 24px 70px}
.hero{display:flex;align-items:end;justify-content:space-between;gap:20px;flex-wrap:wrap}.hero p{color:var(--muted);max-width:680px}.cards{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin:22px 0}.card,.panel{background:white;border:1px solid var(--line);border-radius:14px;box-shadow:0 3px 15px #1b352508}.card{padding:18px}.card span,.card small{display:block;color:var(--muted)}.card strong{display:block;font-size:27px;margin:4px 0;line-height:1.25}.layout{display:grid;grid-template-columns:minmax(0,2fr) minmax(300px,1fr);gap:18px}.panel{padding:22px;margin-bottom:18px}h2{font-size:21px;margin:0 0 12px}h3{font-size:18px;line-height:1.35;margin:4px 0}.story{border-top:1px solid var(--line);padding:17px 0}.story:first-of-type{border-top:0}.story p{margin:7px 0;color:#40554e}.eyebrow{font-size:12px;font-weight:700;letter-spacing:.05em;text-transform:uppercase;color:var(--accent)}details{font-size:13px;color:var(--muted)}summary{cursor:pointer}.meta{font-size:13px;color:var(--muted)}
.topics{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px;margin:14px 0}.topic{display:flex;align-items:center;gap:8px;padding:9px;border:1px solid var(--line);border-radius:9px;cursor:pointer}.topic:has(input:checked){background:var(--light);border-color:var(--accent)}.topic input,.consent input{accent-color:var(--accent)}.notice,.warning{padding:10px 12px;border-radius:8px}.notice{background:var(--light);color:var(--accent)}.warning{background:#fff2dd;border:1px solid #e7bc6b;color:#684317}.field{display:grid;gap:5px;font-weight:600}.field input{font:inherit;border:1px solid #a9bdb2;border-radius:8px;padding:9px 11px;width:100%}.consent{display:flex;align-items:start;gap:8px;margin:14px 0}.consent input{margin-top:5px}.actions{display:flex;gap:10px;flex-wrap:wrap}button{border:0;background:#e3efe6;color:var(--accent);border-radius:7px;padding:9px 13px;cursor:pointer}button.primary{background:var(--accent);color:white;font-weight:700}button:focus-visible,input:focus-visible{outline:3px solid #d59d3a;outline-offset:2px}.empty{color:var(--muted)}
@media(max-width:850px){.cards{grid-template-columns:1fr}.layout{grid-template-columns:1fr}}@media(max-width:480px){.topics{grid-template-columns:1fr}h1{font-size:26px}main{padding:20px 14px}}
</style></head><body><header><h1>Anti-Filter-Bubble</h1><p>Daily Briefing · Six RSS feeds · Gemini analysis · Diversity quota</p></header><main>
<div class="hero"><div><h2>A broader view, delivered daily</h2><p>Choose what you care about. Your briefing prioritizes those topics while reserving at least 20% for useful stories outside your usual interests.</p></div></div>
{% if notice %}<p class="notice" role="status">{{ notice }}</p>{% endif %}{% if error %}<p class="warning" role="alert">{{ error }}</p>{% endif %}
<div class="cards"><div class="card"><span>Collected</span><strong>{{ total }}</strong><small>Deduplicated articles</small></div><div class="card"><span>Analyzed</span><strong>{{ complete }}</strong><small>Structured Gemini results</small></div><div class="card"><span>Current digest</span><strong>{{ digest|length }}</strong><small>Default topic mix</small></div></div>
<div class="layout"><section><div class="panel"><h2>Latest digest</h2>{% for item in digest %}<article class="story"><div class="eyebrow">{{ "%02d"|format(loop.index) }} · {{ item.source }} · {{ item.category }} · {{ item.importance }}/5</div><h3><a href="{{ item.url }}" target="_blank" rel="noopener noreferrer">{{ item.title }}</a></h3><p>{{ item.summary }}</p><details><summary>Why this score?</summary><p>{{ item.reason }}</p></details></article>{% else %}<p class="empty">The first cloud digest will appear after the scheduled pipeline runs.</p>{% endfor %}</div></section>
<aside><div class="panel"><h2>Create your briefing</h2><p class="meta">Your email and preferences are stored in PostgreSQL. Daily delivery requires explicit consent.</p><form method="post" action="/subscribe"><label class="field">Email address<input type="email" name="email" required maxlength="254" autocomplete="email" placeholder="name@example.com"></label><fieldset style="border:0;padding:0;margin:16px 0"><legend style="font-weight:700">Preferred topics</legend><div class="topics">{% for category in categories %}<label class="topic"><input type="checkbox" name="category" value="{{ category }}" {% if category in defaults %}checked{% endif %}><span>{{ category|title }}</span></label>{% endfor %}</div></fieldset><label class="consent"><input type="checkbox" name="daily" value="yes"><span>I agree to receive this briefing every day</span></label><div class="actions"><button type="submit" name="action" value="save">Save preferences</button><button class="primary" type="submit" name="action" value="send">Save and send today’s briefing</button></div></form><p class="meta">One delivery attempt per address per UTC day. Every daily email includes a cancellation link.</p></div>
<div class="panel"><h2>Pipeline status</h2><p>{{ last_run }}</p><p class="meta">Daily collection and delivery run through a protected Vercel Cron endpoint.</p></div></aside></div></main></body></html>'''


def _same_origin() -> bool:
    origin = request.headers.get("Origin", "")
    return origin in (f"https://{request.host}", f"http://{request.host}")


@app.get("/")
def index():
    notice = request.args.get("notice", "")[:180]
    error = request.args.get("error", "")[:180]
    try:
        data = dashboard_data()
        counts = data["counts"]
        finished = data["report"].get("finished_at", "No cloud runs yet")
        return render_template_string(PAGE, notice=notice, error=error, total=sum(counts.values()),
                                      complete=counts.get("complete", 0), digest=data["digest"],
                                      last_run=finished, categories=CATEGORIES, defaults=DEFAULT_CATEGORIES)
    except RuntimeError as exc:
        return render_template_string(PAGE, notice=notice, error=str(exc), total=0, complete=0, digest=[],
                                      last_run="Cloud database setup required", categories=CATEGORIES,
                                      defaults=DEFAULT_CATEGORIES), 503


@app.post("/subscribe")
def subscribe():
    if not _same_origin():
        abort(403)
    action = request.form.get("action", "")
    if action not in ("save", "send"):
        abort(400)
    email = request.form.get("email", "")
    categories = request.form.getlist("category")
    daily = request.form.get("daily") == "yes"
    try:
        save_subscription(email, categories, daily)
        if action == "send":
            send_to(email, categories)
            notice = "Preferences saved and today’s briefing submitted to the mail server."
        else:
            notice = "Preferences and delivery choice saved."
        return redirect(url_for("index", notice=notice), code=303)
    except (ValueError, RuntimeError) as exc:
        return redirect(url_for("index", error=str(exc)), code=303)


@app.route("/unsubscribe/<token>", methods=["GET", "POST"])
def cancel(token):
    if request.method == "GET":
        return render_template_string('''<!doctype html><html lang="en"><meta name="viewport" content="width=device-width"><title>Cancel daily briefing</title><body style="font:16px/1.6 system-ui;max-width:560px;margin:60px auto;padding:20px"><h1>Cancel daily briefing</h1><p>This stops future daily emails. It does not delete past delivery records.</p><form method="post"><button style="padding:10px 14px">Confirm cancellation</button></form></body></html>''')
    if unsubscribe(token):
        return redirect(url_for("index", notice="Daily delivery cancelled."), code=303)
    return redirect(url_for("index", error="This cancellation link is invalid."), code=303)


@app.get("/api/cron")
def cron():
    secret = os.environ.get("CRON_SECRET", "")
    if not secret or request.headers.get("Authorization") != f"Bearer {secret}":
        abort(401)
    return run_daily()


@app.post("/api/run")
def manual_run():
    secret = os.environ.get("ADMIN_SECRET", "")
    if not secret or request.headers.get("Authorization") != f"Bearer {secret}":
        abort(401)
    return run_pipeline()


@app.get("/api/health")
def health():
    return {"status": "ok", "database_configured": bool(os.environ.get("DATABASE_URL"))}
