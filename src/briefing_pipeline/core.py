"""RSS-to-digest pipeline; external services are optional and injectable."""
from __future__ import annotations

import email.utils
import hashlib
import json
import logging
import os
import re
import smtplib
import ssl
import math
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
import sqlite3
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from email.message import EmailMessage
from html import unescape
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

LOG = logging.getLogger("briefing_pipeline")
CATEGORIES = ("world", "politics", "technology", "science", "business", "health", "environment", "culture", "other")
MODEL_SCHEMA = {
    "type": "object", "required": ["category", "summary", "importance", "reason"],
    "additionalProperties": False,
    "properties": {
        "category": {"type": "string", "enum": list(CATEGORIES)},
        "summary": {"type": "string", "minLength": 1, "maxLength": 500},
        "importance": {"type": "integer", "minimum": 1, "maximum": 5},
        "reason": {"type": "string", "minLength": 1, "maxLength": 500},
    },
}


def validate_result(value: Any) -> Dict[str, Any]:
    """Validate every keyword used by MODEL_SCHEMA, without external packages."""
    schema = MODEL_SCHEMA
    if not isinstance(value, dict):
        raise ValueError("response must be a JSON object")
    required = set(schema["required"])
    if not required.issubset(value):
        raise ValueError("missing required response fields")
    if schema.get("additionalProperties") is False and set(value) != required:
        raise ValueError("unexpected response fields")
    for name, rule in schema["properties"].items():
        item = value[name]
        expected = rule["type"]
        if expected == "string" and type(item) is not str:
            raise ValueError(name + " must be a string")
        if expected == "integer" and type(item) is not int:
            raise ValueError(name + " must be an integer")
        if "enum" in rule and item not in rule["enum"]:
            raise ValueError(name + " is outside the allowed enum")
        if expected == "string" and not rule.get("minLength", 0) <= len(item) <= rule.get("maxLength", 10**9):
            raise ValueError(name + " has invalid length")
        if expected == "integer" and not rule.get("minimum", -10**9) <= item <= rule.get("maximum", 10**9):
            raise ValueError(name + " is out of range")
    return value


def normalize_date(raw: Optional[str]) -> str:
    if raw:
        try:
            parsed = email.utils.parsedate_to_datetime(raw)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc).isoformat()
        except (TypeError, ValueError, OverflowError):
            try:
                parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                return parsed.astimezone(timezone.utc).isoformat()
            except ValueError:
                pass
    return datetime.now(timezone.utc).isoformat()


def _text(node: Optional[ET.Element]) -> str:
    if node is None:
        return ""
    return " ".join("".join(node.itertext()).split())


def canonical_url(url):
    parts = urlsplit(url.strip())
    if parts.scheme.lower() not in ('http', 'https') or not parts.hostname or parts.username or any(c.isspace() for c in url):
        raise ValueError('invalid article URL')
    query = [(k,v) for k,v in parse_qsl(parts.query, keep_blank_values=True)
             if not k.lower().startswith('utm_') and k.lower() not in ('fbclid','gclid')]
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path or '/', urlencode(sorted(query)), ''))


def parse_feed(xml_bytes: bytes, source: str) -> List[Dict[str, str]]:
    """Parse RSS 2/Atom, tolerate absent fields, and discard unusable links."""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        raise ValueError("invalid feed XML") from exc
    if root.tag.split("}")[-1] not in ("rss", "feed", "RDF"):
        raise ValueError("not an RSS or Atom document")
    result = []
    for item in root.findall(".//item") + root.findall(".//{http://www.w3.org/2005/Atom}entry"):
        title = _text(item.find("title")) or _text(item.find("{http://www.w3.org/2005/Atom}title"))
        link = _text(item.find("link"))
        atom_link = item.find("{http://www.w3.org/2005/Atom}link")
        for atom_link in item.findall("{http://www.w3.org/2005/Atom}link"):
            if atom_link.attrib.get("rel", "alternate") == "alternate":
                link = atom_link.attrib.get("href", link)
                break
        if not re.match(r"^https?://[^\s]+$", link, re.I):
            LOG.warning("skip item with invalid/missing link source=%s title=%r", source, title)
            continue
        desc = _text(item.find("description")) or _text(item.find("{http://www.w3.org/2005/Atom}summary"))
        desc = unescape(re.sub(r"<[^>]+>", " ", desc))[:4000]
        date = _text(item.find("pubDate")) or _text(item.find("{http://www.w3.org/2005/Atom}updated")) or _text(item.find("{http://www.w3.org/2005/Atom}published"))
        if not title:
            title = link
        try:
            link = canonical_url(link)
        except ValueError:
            continue
        key = hashlib.sha256(link.encode("utf-8")).hexdigest()
        result.append({"id": key, "source": source, "title": title[:500], "url": link, "description": desc, "published_at": normalize_date(date)})
    return result


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path))
        self.db.row_factory = sqlite3.Row
        self.db.execute("""CREATE TABLE IF NOT EXISTS articles (
            id TEXT PRIMARY KEY, source TEXT NOT NULL, title TEXT NOT NULL, url TEXT NOT NULL,
            description TEXT NOT NULL, published_at TEXT NOT NULL, fetched_at TEXT NOT NULL,
            category TEXT, summary TEXT, importance INTEGER, reason TEXT, model_status TEXT NOT NULL DEFAULT 'pending',
            model_error TEXT)""")
        self.db.commit()

    def add(self, article: Dict[str, str]) -> bool:
        cur = self.db.execute("INSERT OR IGNORE INTO articles(id,source,title,url,description,published_at,fetched_at) VALUES(?,?,?,?,?,?,?)",
            (article["id"], article["source"], article["title"], article["url"], article["description"], article["published_at"], datetime.now(timezone.utc).isoformat()))
        self.db.commit()
        if cur.rowcount:
            LOG.info("sqlite inserted article id=%s source=%s", article["id"][:12], article["source"])
            return True
        LOG.info("sqlite deduplicated article id=%s", article["id"][:12])
        return False

    def pending(self) -> List[sqlite3.Row]:
        return list(self.db.execute("SELECT * FROM articles WHERE model_status='pending' ORDER BY published_at DESC"))

    def save_model(self, article_id: str, result: Dict[str, Any]) -> None:
        self.db.execute("UPDATE articles SET category=?,summary=?,importance=?,reason=?,model_status='complete',model_error=NULL WHERE id=?",
            (result["category"], result["summary"], result["importance"], result["reason"], article_id))
        self.db.commit()
        LOG.info("sqlite saved model result id=%s", article_id[:12])

    def fail_model(self, article_id: str, error: str) -> None:
        self.db.execute("UPDATE articles SET model_status='failed',model_error=? WHERE id=?", (error[:1000], article_id))
        self.db.commit()
        LOG.error("article isolated id=%s error=%s", article_id[:12], error)

    def rows(self) -> List[sqlite3.Row]:
        return list(self.db.execute("SELECT * FROM articles WHERE model_status='complete' ORDER BY published_at DESC"))

    def close(self) -> None:
        self.db.close()


class QwenClient:
    def __init__(self, api_key: str, model: str, endpoint: str, timeout: int = 30, retries: int = 2,
                 transport: Optional[Callable[[bytes, Dict[str, str], int], bytes]] = None):
        self.api_key, self.model, self.endpoint = api_key, model, endpoint
        self.timeout, self.retries, self.transport = timeout, retries, transport

    def _request(self, payload: bytes, headers: Dict[str, str]) -> bytes:
        if self.transport:
            return self.transport(payload, headers, self.timeout)
        req = urllib.request.Request(self.endpoint, data=payload, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=self.timeout) as response:
            return response.read()

    def classify(self, article: Dict[str, str]) -> Dict[str, Any]:
        instruction = "Classify the article. Return only JSON with category (one of %s), summary (max 500 chars), importance integer 1-5, and reason (max 500 chars). Importance: 1 minor, 3 meaningful, 5 major broad impact." % ", ".join(CATEGORIES)
        payload = json.dumps({"model": self.model, "temperature": 0, "response_format": {"type": "json_object"}, "messages": [
            {"role": "system", "content": instruction},
            {"role": "user", "content": json.dumps({"title": article["title"], "description": article["description"][:3000]}, ensure_ascii=False)}]}, ensure_ascii=False).encode()
        headers = {"Authorization": "Bearer " + self.api_key, "Content-Type": "application/json"}
        last_error = "unknown error"
        for attempt in range(self.retries + 1):
            try:
                LOG.info("qwen request id=%s attempt=%d/%d", article["id"][:12], attempt + 1, self.retries + 1)
                outer = json.loads(self._request(payload, headers).decode("utf-8"))
                content = outer["choices"][0]["message"]["content"]
                return validate_result(json.loads(content))
            except Exception as exc:  # isolate transport, envelope, JSON, and schema failures
                last_error = "%s: %s" % (type(exc).__name__, exc)
                LOG.warning("qwen attempt failed id=%s attempt=%d error=%s", article["id"][:12], attempt + 1, last_error)
                if attempt < self.retries:
                    time.sleep(min(0.25 * (2 ** attempt), 2))
        raise RuntimeError(last_error)


def score_interest(row: Dict[str, Any], preferred: Sequence[str]) -> float:
    return float(row["importance"]) + (1.0 if row["category"] in preferred else 0.0)


def rank_interest(rows: Sequence[Dict[str, Any]], preferred: Sequence[str], size: int) -> List[Dict[str, Any]]:
    return sorted(rows, key=lambda r: (score_interest(r, preferred), r["published_at"]), reverse=True)[:size]


def rank_diverse(rows: Sequence[Dict[str, Any]], preferred: Sequence[str], size: int, outside_share: float = 0.2) -> List[Dict[str, Any]]:
    """Guarantee ceil(size*outside_share) outside-interest items when available."""
    if size < 1 or not 0 <= outside_share <= 1:
        raise ValueError("invalid digest size or quota")
    size = min(size, len(rows))
    ordered = rank_interest(rows, preferred, len(rows))
    outside = [r for r in ordered if r["category"] not in preferred]
    required = min(len(outside), math.ceil(size * outside_share))
    chosen = ordered[:size]
    selected = {r['id'] for r in chosen}
    for candidate in outside:
        if sum(r['category'] not in preferred for r in chosen) >= required:
            break
        if candidate['id'] in selected:
            continue
        for index in range(len(chosen)-1, -1, -1):
            if chosen[index]['category'] in preferred:
                selected.discard(chosen[index]['id'])
                chosen[index] = candidate
                selected.add(candidate['id'])
                break
    return sorted(chosen, key=lambda r: (score_interest(r, preferred), r["published_at"]), reverse=True)


def render_digest(rows: Sequence[Dict[str, Any]], title: str = "Daily Briefing") -> str:
    lines = ["# " + title, ""]
    for index, row in enumerate(rows, 1):
        lines += ["## %d. %s" % (index, row["title"]), "", "**%s · importance %s/5 · %s**" % (row["category"], row["importance"], row["source"]), "", row["summary"], "", "Reason: " + row["reason"], "", row["url"], ""]
    return "\n".join(lines)


def write_email_preview(path: Path, recipient: str, subject: str, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("To: %s\nSubject: %s\n\n%s" % (recipient or "(not configured)", subject, body), encoding="utf-8")
    LOG.info("email preview written path=%s", path)


def deliver_email(body: str, env: Dict[str, str], preview_path: Path) -> str:
    recipient = env.get("SMTP_TO", "")
    subject = "Anti-Filter-Bubble Daily Briefing"
    if env.get("EMAIL_PREVIEW_ONLY", "true").lower() != "false" or not all(env.get(k) for k in ("SMTP_HOST", "SMTP_FROM", "SMTP_TO")):
        write_email_preview(preview_path, recipient, subject, body)
        return "preview"
    message = EmailMessage()
    message["From"], message["To"], message["Subject"] = env["SMTP_FROM"], recipient, subject
    message.set_content(body)
    use_ssl = env.get('SMTP_SSL', 'false').lower() == 'true'
    if not use_ssl and env.get('SMTP_STARTTLS', 'true').lower() != 'true':
        raise ValueError('SMTP requires TLS')
    factory = smtplib.SMTP_SSL if use_ssl else smtplib.SMTP
    options = {'context': ssl.create_default_context()} if use_ssl else {}
    with factory(env["SMTP_HOST"], int(env.get("SMTP_PORT", "465" if use_ssl else "587")), timeout=20, **options) as smtp:
        if not use_ssl:
            smtp.starttls(context=ssl.create_default_context())
        if env.get("SMTP_USERNAME"):
            smtp.login(env["SMTP_USERNAME"], env.get("SMTP_PASSWORD", ""))
        refused = smtp.send_message(message)
        if refused:
            raise RuntimeError("SMTP refused recipients")
    return "sent"


def load_env(path: Path) -> Dict[str, str]:
    values = dict(os.environ)
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values.setdefault(key.strip(), value.strip().strip("\"'"))
    return values
