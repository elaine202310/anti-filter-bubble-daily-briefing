# Anti-Filter-Bubble Daily Briefing

A production-style Python project that turns six RSS feeds into a personalized daily news briefing. It uses Gemini for structured classification, summaries and importance scoring, preserves topic diversity, and supports explicit email subscriptions.

[![Tests](https://github.com/elaine202310/anti-filter-bubble-daily-briefing/actions/workflows/tests.yml/badge.svg)](https://github.com/elaine202310/anti-filter-bubble-daily-briefing/actions/workflows/tests.yml)

## What it demonstrates

- RSS and Atom ingestion from exactly six configurable sources.
- Canonical-URL SHA-256 deduplication in SQLite locally and PostgreSQL in the Vercel deployment path.
- Gemini structured JSON output validated against a strict local schema: `category`, `summary`, `importance` (1–5), and `reason`.
- Bounded retries, timeouts, failure isolation, and run reports. A failed feed or model request does not stop the remaining daily run.
- Two ranking strategies: interest-only ranking and a diversity-aware ranking that reserves at least 20% of the digest for topics outside a subscriber's preferences when enough candidates exist.
- Explicit daily email consent, one delivery attempt per address per UTC day, and an unsubscribe link in cloud-delivered emails.
- A reproducible 30-item human-label evaluation workflow for exact agreement and scores within ±1 point.

## Architecture

```text
Six RSS / Atom feeds
        ↓
URL normalization + deduplication
        ↓
Gemini structured JSON + schema validation
        ↓
Interest ranking + 20% diversity quota
        ↓
Digest page / Gmail SMTP delivery
```

Two execution paths share the same parsing, schema validation, ranking and email-rendering logic:

| Path | Storage and scheduling | Use case |
| --- | --- | --- |
| Local CLI + dashboard | SQLite + local process | Repeatable development and evidence collection |
| Vercel Flask app | PostgreSQL + Vercel Cron | Public portfolio deployment and per-email subscriptions |

## Local demo

Python 3.9+ is sufficient for the local path.

```sh
cp .env.example .env
chmod 600 .env
PYTHONPATH=src python3 -m briefing_pipeline.cli dashboard
```

Open `http://127.0.0.1:8765/`. The local dashboard supports category choices, daily-email consent, a manual send action, an RSS/Gemini refresh action, and a digest preview.

Useful commands:

```sh
PYTHONPATH=src python3 -m briefing_pipeline.cli collect
PYTHONPATH=src python3 -m briefing_pipeline.cli run
PYTHONPATH=src python3 -m briefing_pipeline.cli send
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

`collect` ingests and deduplicates articles. `run` ingests feeds, analyzes a bounded number of new items, creates a digest, and writes an email preview. `send` submits the current real-model digest to SMTP only after valid SMTP settings are present.

## Configuration

Set `GEMINI_API_KEY` in `.env`; it is never committed. The configured model is `gemini-3.5-flash-lite`. The pipeline only sends public RSS titles and summaries to Gemini.

For Gmail SMTP, configure `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_FROM`, and `SMTP_STARTTLS`. Keep `EMAIL_PREVIEW_ONLY=true` until a real send is intended. SMTP acceptance proves the server accepted the message; it does not prove inbox receipt.

`.env`, SQLite databases, logs, and generated outputs are excluded through `.gitignore`.

## Vercel deployment

The cloud deployment replaces the local database, HTTP server and macOS scheduler:

```text
Vercel Flask app → PostgreSQL → Gemini API → Gmail SMTP
                    ↑
          Vercel Cron: /api/cron
```

1. Create a free PostgreSQL database through the Vercel Marketplace, such as Neon, and connect it to the Vercel project. This provides `DATABASE_URL`.
2. Import this GitHub repository into Vercel.
3. Add `GEMINI_API_KEY`, SMTP settings, `DATABASE_URL`, `CRON_SECRET`, `ADMIN_SECRET`, and `PUBLIC_BASE_URL` in Vercel Project Settings → Environment Variables.
4. Deploy. `app.py` creates the database tables on first use. Check `/api/health`, then call `POST /api/run` with `Authorization: Bearer <ADMIN_SECRET>` to create the first cloud digest.

`vercel.json` calls `/api/cron` at `21:00 UTC` every day: 07:00 Sydney time during standard time and 08:00 during daylight saving. Vercel Hobby Cron may start at any time during that UTC hour.

The deployed site stores an email address, selected categories, daily consent, and delivery state in PostgreSQL. It is intentionally a portfolio implementation: email verification and rate limiting are not implemented, so it should not be used as a public mass-email product.

## Ranking and diversity quota

Interest score = `importance + 1` for a preferred category, otherwise `importance`. Interest-only ranking selects the highest scores. The diversity-aware ranking then replaces lower-ranked preferred-topic items until at least `ceil(digest_size × 20%)` items fall outside the user's preferences, when enough such articles exist.

The system records the actual baseline and diversity shares in `output/ranking_comparison.json`; it does not hard-code a 10% → 20% result.

## Evaluation

Importance uses a five-point scale: 1 is a minor single-topic update, 3 has clear impact on a relevant group, and 5 has broad, major impact. To evaluate model scores, first produce 30 real Gemini predictions, then export and label them independently:

```sh
PYTHONPATH=src python3 -m briefing_pipeline.cli export-labels
# Fill in human_importance for all 30 rows in output/human_labels.csv
PYTHONPATH=src python3 -m briefing_pipeline.cli evaluate --csv output/human_labels.csv
```

The evaluator joins labels to model outputs through a stable article ID and reports exact agreement and agreement within ±1. No accuracy claim is reported until all 30 real human labels exist.

## Verification status

- Local Gemini processing, SQLite deduplication, email delivery through Gmail SMTP, dashboard behavior, and 28 automated tests have been verified.
- The Vercel runtime is implemented and tested locally with Flask/psycopg dependencies, but has not yet been deployed or connected to a cloud database. Cloud Cron and cloud email delivery therefore remain unverified.
- Full evidence and resume-claim checks are recorded in [docs/verification.md](docs/verification.md).

## Limitations

The pipeline uses RSS titles and summaries rather than full article text. Deduplication identifies the same normalized URL, not semantic duplicates published under different URLs. Gemini free-tier limits can change. The local macOS scheduler is not used for the cloud path.
