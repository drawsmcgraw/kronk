"""News brief: scheduled editions, RSS → one summarize call → cached prose.

Design (docs/plans/NEWS_BRIEF_PLAN.md): the brief is pre-generated at
edition boundaries (6am/noon/6pm local) and served VERBATIM by a terminal
tool on the coordinator — the live request path is a cache read, so the
brief's length survives (motivating incident: rid 99ce926c, a brief
re-summarized down to 754 chars) and its freshness is real, never implied
(the same session confabulated a "brief" from model memory).

Summarization calls LiteLLM directly (operator decision 2026-08-24). That
bypasses the orchestrator's _llm_lock, so a refresh can contend with a
live query for ~20 s, three times a day — accepted.

Feeds are RSS/Atom parsed with xml.etree — two well-known formats are not
worth a new pinned dependency. Per-feed failures skip, are logged, and are
named in the stored record: a brief from 7/8 sources beats no brief, but
the record says so.
"""
import asyncio
import json
import logging
import os
import re
import time
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from html import unescape
from pathlib import Path
from xml.etree import ElementTree as ET
from zoneinfo import ZoneInfo

import httpx

logger = logging.getLogger(__name__)

NEWS_FILE       = Path(os.getenv("NEWS_FILE", "/data/news_brief.json"))
NEWS_FEEDS_FILE = Path(os.getenv("NEWS_FEEDS_FILE", "/data/news_feeds.json"))
NEWS_TZ         = os.getenv("NEWS_TZ", "America/New_York")
NEWS_LLM_URL    = os.getenv("NEWS_LLM_URL", "http://host.docker.internal:8002/v1")
NEWS_MODEL      = os.getenv("NEWS_MODEL", "gemma-4-e4b")
NEWS_WINDOW_H   = int(os.getenv("NEWS_WINDOW_H", "24"))
NEWS_PER_FEED   = int(os.getenv("NEWS_PER_FEED", "6"))
NEWS_MAX_ITEMS  = int(os.getenv("NEWS_MAX_ITEMS", "36"))
NEWS_CHECK_S    = int(os.getenv("NEWS_CHECK_S", "60"))

# Verified working 2026-08-24 (AP's public feeds are gone — 401/HTML — so
# the Guardian carries the third general-news slot; operator can veto via
# the override file below).
DEFAULT_FEEDS = [
    {"name": "NPR",             "category": "world", "url": "https://feeds.npr.org/1001/rss.xml"},
    {"name": "BBC World",       "category": "world", "url": "https://feeds.bbci.co.uk/news/world/rss.xml"},
    {"name": "Guardian World",  "category": "world", "url": "https://www.theguardian.com/world/rss"},
    {"name": "Ars Technica",    "category": "tech",  "url": "https://feeds.arstechnica.com/arstechnica/index"},
    {"name": "The Verge",       "category": "tech",  "url": "https://www.theverge.com/rss/index.xml"},
    {"name": "MIT Tech Review", "category": "tech",  "url": "https://www.technologyreview.com/feed/"},
    {"name": "Krebs",           "category": "cyber", "url": "https://krebsonsecurity.com/feed/"},
    {"name": "BleepingComputer","category": "cyber", "url": "https://www.bleepingcomputer.com/feed/"},
]

EDITION_HOURS  = (6, 12, 18)
EDITION_LABELS = {6: "morning", 12: "midday", 18: "evening"}

_ATOM = "{http://www.w3.org/2005/Atom}"
_TAG_RE = re.compile(r"<[^>]+>")


class NewsError(Exception):
    """Generation failed for a specific, reportable reason (tenet 7)."""


def load_feeds() -> list[dict]:
    """Operator override file wins wholesale; otherwise the defaults."""
    try:
        feeds = json.loads(NEWS_FEEDS_FILE.read_text())
        if isinstance(feeds, list) and feeds:
            return feeds
    except (OSError, ValueError):
        pass
    return DEFAULT_FEEDS


def _clean(text: str | None) -> str:
    if not text:
        return ""
    return re.sub(r"\s+", " ", _TAG_RE.sub(" ", unescape(text))).strip()


def _parse_date(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return parsedate_to_datetime(raw)          # RFC 822 (RSS)
    except (TypeError, ValueError):
        pass
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))  # Atom
    except ValueError:
        return None


def parse_feed(content: bytes, source: str, category: str) -> list[dict]:
    """RSS 2.0 or Atom → normalized items. Raises on unparseable XML."""
    root = ET.fromstring(content)
    items = []
    for node in root.findall(".//item"):           # RSS
        items.append({
            "title":     _clean(node.findtext("title")),
            "summary":   _clean(node.findtext("description"))[:400],
            "link":      (node.findtext("link") or "").strip(),
            "published": _parse_date(node.findtext("pubDate")),
        })
    for node in root.findall(f".//{_ATOM}entry"):  # Atom
        link = node.find(f"{_ATOM}link")
        items.append({
            "title":     _clean(node.findtext(f"{_ATOM}title")),
            "summary":   _clean(node.findtext(f"{_ATOM}summary")
                                or node.findtext(f"{_ATOM}content"))[:400],
            "link":      link.get("href", "") if link is not None else "",
            "published": _parse_date(node.findtext(f"{_ATOM}published")
                                     or node.findtext(f"{_ATOM}updated")),
        })
    for it in items:
        it["source"] = source
        it["category"] = category
    return [it for it in items if it["title"]]


async def fetch_all(client: httpx.AsyncClient) -> tuple[list[dict], list[str]]:
    items: list[dict] = []
    failed: list[str] = []
    for feed in load_feeds():
        try:
            resp = await client.get(
                feed["url"], timeout=15, follow_redirects=True,
                headers={"User-Agent": "kronk-news/1.0"},
            )
            resp.raise_for_status()
            items.extend(parse_feed(resp.content, feed["name"], feed["category"]))
        except Exception as e:
            failed.append(feed["name"])
            logger.warning("news feed %s failed: %s", feed["name"], str(e)[:150])
    return items, failed


def select_items(items: list[dict], now: datetime | None = None) -> list[dict]:
    """Window to the last NEWS_WINDOW_H hours, cap per feed and overall.

    Undated items are kept (some feeds omit dates) — the window exists to
    drop yesterday's news, not to demand metadata perfection.
    """
    now = now or datetime.now(ZoneInfo("UTC"))
    cutoff = now - timedelta(hours=NEWS_WINDOW_H)
    fresh = [it for it in items
             if it["published"] is None or it["published"] >= cutoff]
    by_feed: dict[str, list[dict]] = {}
    for it in fresh:
        by_feed.setdefault(it["source"], []).append(it)
    selected: list[dict] = []
    for feed_items in by_feed.values():
        feed_items.sort(key=lambda i: i["published"] or now, reverse=True)
        selected.extend(feed_items[:NEWS_PER_FEED])
    return selected[:NEWS_MAX_ITEMS]


def edition_boundary(now_local: datetime) -> tuple[datetime, str]:
    """Most recent edition boundary at or before now (local time).

    Before 6am the current edition is *yesterday's evening* brief — the
    stale-check compares against this, so a 5am boot serves yesterday's
    evening edition rather than burning a generation nobody asked for.
    """
    candidates = [now_local.replace(hour=h, minute=0, second=0, microsecond=0)
                  for h in EDITION_HOURS if now_local.hour >= h]
    if candidates:
        boundary = candidates[-1]
    else:
        boundary = (now_local - timedelta(days=1)).replace(
            hour=EDITION_HOURS[-1], minute=0, second=0, microsecond=0)
    return boundary, EDITION_LABELS[boundary.hour]


def build_prompt(items: list[dict], label: str, now_local: datetime) -> str:
    lines = [
        f"Write Kronk's {label} news brief for {now_local.strftime('%A, %B %d')}.",
        "Summarize the items below into a spoken-friendly brief of 300 to 350",
        "words with exactly three sections titled World, Tech & AI, and",
        "Cybersecurity. Lead each section with its most important story and",
        "use clear story names — the listener asks follow-ups by name.",
        "No preamble, no closing remarks, no URLs, no markdown beyond the",
        "three section titles.",
        "",
        "Items:",
    ]
    for it in items:
        lines.append(f"- [{it['category']}] ({it['source']}) {it['title']} — {it['summary'][:250]}")
    return "\n".join(lines)


async def summarize(prompt: str, client: httpx.AsyncClient) -> str:
    resp = await client.post(
        f"{NEWS_LLM_URL}/chat/completions",
        json={
            "model": NEWS_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            # Headroom well above the ~350-word ask — the prompt's word
            # budget is the real limiter. 600 was too tight: editions hit
            # the cap and were stored mid-sentence (found 2026-08-24).
            "max_tokens": 1000,
            "temperature": 0.4,
        },
        timeout=120,
    )
    resp.raise_for_status()
    choice = (resp.json().get("choices") or [{}])[0]
    content = choice.get("message", {}).get("content", "")
    # Never store a truncated edition: a length-stop means the model ran
    # through the cap and the text ends mid-sentence (tenet 6 — verify the
    # effect). Fail loudly; the refresh loop retries in a minute.
    if choice.get("finish_reason") == "length":
        raise NewsError("brief hit the token cap (finish_reason=length) — "
                        "refusing to store a truncated edition")
    if not content.strip():
        raise NewsError("LLM returned an empty brief")
    return content.strip()


def load_record() -> dict | None:
    try:
        return json.loads(NEWS_FILE.read_text())
    except (OSError, ValueError):
        return None


def save_record(rec: dict) -> None:
    NEWS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = NEWS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(rec))
    os.replace(tmp, NEWS_FILE)


def is_stale(rec: dict | None, now_local: datetime | None = None) -> bool:
    if not rec or "generated_ts" not in rec:
        return True
    now_local = now_local or datetime.now(ZoneInfo(NEWS_TZ))
    boundary, _ = edition_boundary(now_local)
    return rec["generated_ts"] < boundary.timestamp()


async def generate(client: httpx.AsyncClient | None = None) -> dict:
    """Fetch → select → summarize → store. Raises NewsError with the most
    specific cause available; never stores a partial/empty record."""
    own_client = client is None
    client = client or httpx.AsyncClient()
    try:
        items, failed = await fetch_all(client)
        selected = select_items(items)
        if not selected:
            raise NewsError(
                f"no usable items from any feed (failed: {', '.join(failed) or 'none'})")
        now_local = datetime.now(ZoneInfo(NEWS_TZ))
        _, label = edition_boundary(now_local)
        try:
            brief = await summarize(build_prompt(selected, label, now_local), client)
        except NewsError:
            raise
        except Exception as e:
            raise NewsError(f"summarization failed: {str(e)[:200]}") from e
        rec = {
            "edition": label,
            "generated_ts": time.time(),
            "generated_at_local": now_local.strftime("%a %I:%M %p"),
            "brief": brief,
            "stories": [{k: it[k] for k in ("title", "source", "link", "category")}
                        for it in selected],
            "failed_sources": failed,
        }
        save_record(rec)
        logger.info("news brief generated: %s edition, %d items, %d feeds failed",
                    label, len(selected), len(failed))
        return rec
    finally:
        if own_client:
            await client.aclose()


async def refresh_loop() -> None:
    """Regenerate whenever the cache predates the current edition boundary.

    A minute-cadence staleness check instead of scheduled timers: survives
    restarts, catches a stale cache at boot, and needs no cron precision —
    the same shape as the weather cache loop.
    """
    while True:
        try:
            if is_stale(load_record()):
                await generate()
        except NewsError as e:
            logger.error("news brief generation failed: %s", e)
        except Exception:
            logger.exception("news refresh loop error")
        await asyncio.sleep(NEWS_CHECK_S)
