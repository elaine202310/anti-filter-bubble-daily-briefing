from __future__ import annotations
import argparse
import json
import logging
import sys
import urllib.request
from pathlib import Path
from typing import Any, Dict
from .core import QwenClient, Store, deliver_email, parse_feed, rank_diverse, rank_interest, render_digest, load_env
from .evaluation import evaluate_labels
from .gemini import GeminiClient

ROOT = Path(__file__).resolve().parents[2]

def setup_logging(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=[logging.StreamHandler(), logging.FileHandler(str(path), encoding="utf-8")], force=True)

def fetch(url: str, timeout: int = 20) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "AntiFilterBubbleBriefing/1.0 (+RSS reader)"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()

def heuristic(article: Dict[str, str]) -> Dict[str, Any]:
    """Offline demo only; deterministic keyword classifier, not model evaluation."""
    text = (article["title"] + " " + article["description"]).lower()
    vocab = {"technology": ("technology", "ai", "software", "cyber", "digital"), "science": ("science", "research", "study", "space"),
        "politics": ("election", "government", "minister", "parliament"), "environment": ("climate", "environment", "wildlife", "emission"),
        "business": ("business", "market", "company", "economy"), "health": ("health", "hospital", "medical", "disease"), "culture": ("film", "music", "art", "culture")}
    category = max(vocab, key=lambda c: sum(word in text for word in vocab[c]))
    if not any(word in text for words in vocab.values() for word in words):
        category = "world"
    return {"category": category, "summary": article["description"][:300] or article["title"], "importance": 3,
        "reason": "Offline demo heuristic assigns a neutral score; use the live Gemini command for real analysis."}

def run_demo(root: Path) -> int:
    setup_logging(root / "logs" / "pipeline.log")
    (root / "output").mkdir(parents=True, exist_ok=True)
    store = Store(root / "data" / "demo.sqlite3")
    try:
        articles = json.loads((root / "samples" / "demo_articles.json").read_text(encoding="utf-8"))
        added = sum(store.add(article) for article in articles)
        demo_ids = [article["id"] for article in articles]
        for row in store.pending():
            try:
                store.save_model(row["id"], heuristic(dict(row)))
            except Exception as exc:
                store.fail_model(row["id"], str(exc))
        rows = [dict(r) for r in store.rows()]
        pref = ["technology", "science", "politics"]
        size = 10
        rows = [r for r in rows if r["id"] in demo_ids]
        baseline, diverse = rank_interest(rows, pref, size), rank_diverse(rows, pref, size, 0.2)
        base_out = sum(r["category"] not in pref for r in baseline)
        div_out = sum(r["category"] not in pref for r in diverse)
        body = render_digest(diverse)
        (root / "output" / "digest.md").write_text(body, encoding="utf-8")
        (root / "output" / "ranking_comparison.json").write_text(json.dumps({"size": len(diverse), "preferred_categories": pref,
            "baseline_outside_count": base_out, "baseline_outside_share": base_out / max(1, len(baseline)),
            "diverse_outside_count": div_out, "diverse_outside_share": div_out / max(1, len(diverse)), "quota_target": 0.2}, indent=2), encoding="utf-8")
        status = deliver_email(body, {"EMAIL_PREVIEW_ONLY": "true"}, root / "output" / "email_preview.txt")
        logging.info("demo complete newly_added=%d total_complete=%d baseline_outside=%d/%d diverse_outside=%d/%d email=%s",
            added, len(rows), base_out, len(baseline), div_out, len(diverse), status)
        print("Demo complete: %d new, %d analyzed; outside-interest share %d%% -> %d%%; email=%s" %
            (added, len(rows), round(100*base_out/max(1,len(baseline))), round(100*div_out/max(1,len(diverse))), status))
        return 0
    finally:
        store.close()

def main() -> int:
    parser = argparse.ArgumentParser(description="RSS daily briefing pipeline")
    parser.add_argument("command", choices=("demo", "run", "collect", "send", "send-subscribed", "export-labels", "evaluate", "dashboard"))
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--csv", type=Path)
    parser.add_argument("--port", type=int, default=8765, help="Local dashboard port (default: 8765)")
    args = parser.parse_args()
    if args.command == "demo":
        return run_demo(args.root)
    if args.command == "dashboard":
        from .dashboard import serve
        serve(args.root, args.port)
        return 0
    from .pipeline import run, send, export_labels
    if args.command in ("run", "collect"):
        return run(args.root, collect_only=args.command == "collect")
    if args.command == "send":
        return send(args.root)
    if args.command == "send-subscribed":
        from .subscription import send_daily_if_subscribed
        print(send_daily_if_subscribed(args.root))
        return 0
    if args.command == "export-labels":
        return export_labels(args.root)
    if not args.csv:
        parser.error("evaluate requires --csv with 30 manually annotated rows")
    result=evaluate_labels(args.csv,args.root / "data/live.sqlite3")
    (args.root/"output/evaluation.json").write_text(json.dumps(result,indent=2))
    print(json.dumps(result, indent=2))
    return 0

if __name__ == "__main__":
    sys.exit(main())
