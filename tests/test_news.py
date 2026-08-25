"""News brief tests (docs/plans/NEWS_BRIEF_PLAN.md).

Covers the tool_service news module (feed parsing, item selection, edition
boundary math, prompt build, summarize contract, storage), the /news
endpoints, and the orchestrator tool handler's verbatim formatting. The
terminal behavior on the coordinator lives in test_agentic_loop.py with
the other run_stream tests.
"""
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

import tool_service.main as ts
import tool_service.news as news

TZ = ZoneInfo("America/New_York")

RSS_FIXTURE = b"""<?xml version="1.0"?>
<rss version="2.0"><channel><title>Feed</title>
<item>
  <title>Grid upgrade passes &amp; funding secured</title>
  <description><![CDATA[<p>Lawmakers <b>approved</b> the grid bill.</p>]]></description>
  <link>https://example.com/grid</link>
  <pubDate>Mon, 24 Aug 2026 09:00:00 GMT</pubDate>
</item>
<item>
  <title>Old story</title>
  <description>ancient</description>
  <link>https://example.com/old</link>
  <pubDate>Mon, 17 Aug 2026 09:00:00 GMT</pubDate>
</item>
</channel></rss>"""

ATOM_FIXTURE = b"""<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom"><title>AFeed</title>
<entry>
  <title>Model release roundup</title>
  <summary>New weights shipped.</summary>
  <link href="https://example.com/models"/>
  <published>2026-08-24T10:30:00Z</published>
</entry>
</feed>"""


# ── parsing ──────────────────────────────────────────────────────────────────

def test_parse_rss_cleans_html_and_dates():
    items = news.parse_feed(RSS_FIXTURE, "NPR", "world")
    assert len(items) == 2
    first = items[0]
    assert first["title"] == "Grid upgrade passes & funding secured"
    assert first["summary"] == "Lawmakers approved the grid bill."   # tags stripped
    assert first["source"] == "NPR" and first["category"] == "world"
    assert first["published"].tzinfo is not None


def test_parse_atom():
    items = news.parse_feed(ATOM_FIXTURE, "Ars", "tech")
    assert len(items) == 1
    assert items[0]["title"] == "Model release roundup"
    assert items[0]["link"] == "https://example.com/models"
    assert items[0]["published"].year == 2026


def test_parse_garbage_raises():
    with pytest.raises(Exception):
        news.parse_feed(b"<html>not a feed", "X", "world")


# ── selection ────────────────────────────────────────────────────────────────

def test_select_items_window_caps_and_undated():
    now = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)
    fresh = now - timedelta(hours=2)
    stale = now - timedelta(hours=40)
    items = (
        [{"title": f"t{i}", "summary": "", "link": "", "source": "A",
          "category": "world", "published": fresh} for i in range(10)]
        + [{"title": "old", "summary": "", "link": "", "source": "A",
            "category": "world", "published": stale}]
        + [{"title": "undated", "summary": "", "link": "", "source": "B",
            "category": "tech", "published": None}]
    )
    selected = news.select_items(items, now=now)
    titles = [i["title"] for i in selected]
    assert "old" not in titles                       # outside the 24h window
    assert "undated" in titles                       # dateless feeds still count
    assert sum(1 for i in selected if i["source"] == "A") == news.NEWS_PER_FEED


# ── edition boundaries ───────────────────────────────────────────────────────

@pytest.mark.parametrize("hour,minute,expect_label,expect_day_offset", [
    (5, 59, "evening", -1),   # pre-dawn → yesterday's evening edition
    (6, 0,  "morning", 0),
    (11, 59, "morning", 0),
    (12, 0, "midday", 0),
    (17, 59, "midday", 0),
    (18, 0, "evening", 0),
    (23, 30, "evening", 0),
])
def test_edition_boundary(hour, minute, expect_label, expect_day_offset):
    now = datetime(2026, 8, 24, hour, minute, tzinfo=TZ)
    boundary, label = news.edition_boundary(now)
    assert label == expect_label
    assert boundary.day == (now + timedelta(days=expect_day_offset)).day
    assert boundary <= now


def test_is_stale_across_boundary():
    gen_at = datetime(2026, 8, 24, 11, 50, tzinfo=TZ)   # generated pre-noon
    rec = {"generated_ts": gen_at.timestamp()}
    assert not news.is_stale(rec, now_local=datetime(2026, 8, 24, 11, 55, tzinfo=TZ))
    assert news.is_stale(rec, now_local=datetime(2026, 8, 24, 12, 1, tzinfo=TZ))
    assert news.is_stale(None)


# ── prompt + summarize contract ──────────────────────────────────────────────

def test_build_prompt_has_sections_and_items():
    items = news.parse_feed(RSS_FIXTURE, "NPR", "world")
    p = news.build_prompt(items, "midday", datetime(2026, 8, 24, 12, 5, tzinfo=TZ))
    assert "midday news brief" in p
    assert "World, Tech & AI, and" in p
    assert "(NPR) Grid upgrade passes" in p


@pytest.mark.asyncio
async def test_summarize_empty_content_is_an_error():
    client = AsyncMock()
    client.post.return_value.status_code = 200
    client.post.return_value.raise_for_status = lambda: None
    client.post.return_value.json = lambda: {
        "choices": [{"message": {"content": "  "}, "finish_reason": "stop"}]}
    with pytest.raises(news.NewsError):
        await news.summarize("prompt", client)


@pytest.mark.asyncio
async def test_summarize_refuses_truncated_brief():
    """finish_reason=length means the text ends mid-sentence — never store
    it (2026-08-24: an edition shipped ending 'This critical flaw')."""
    client = AsyncMock()
    client.post.return_value.status_code = 200
    client.post.return_value.raise_for_status = lambda: None
    client.post.return_value.json = lambda: {
        "choices": [{"message": {"content": "World\nA fine brief that got cut"},
                     "finish_reason": "length"}]}
    with pytest.raises(news.NewsError, match="truncated"):
        await news.summarize("prompt", client)


def test_normalize_markdown_blank_line_after_bold_headline():
    """Web UI: a single \\n after a bold headline is a markdown soft break —
    headline and story fuse into one paragraph. The normalizer guarantees
    the blank line; idempotent; inline bold untouched."""
    src = "### World\n**Big Story**\nThe text.\n\n**Spaced Story**\n\nMore text."
    out = news._normalize_markdown(src)
    assert "**Big Story**\n\nThe text." in out
    assert "**Spaced Story**\n\nMore text." in out          # already-spaced unchanged
    assert news._normalize_markdown(out) == out             # idempotent
    inline = "It was **bold inline** mid-sentence.\nNext line."
    assert news._normalize_markdown(inline) == inline       # not a headline


@pytest.mark.asyncio
async def test_summarize_accepts_complete_brief():
    client = AsyncMock()
    client.post.return_value.status_code = 200
    client.post.return_value.raise_for_status = lambda: None
    client.post.return_value.json = lambda: {
        "choices": [{"message": {"content": "World\nAll done."},
                     "finish_reason": "stop"}]}
    assert await news.summarize("prompt", client) == "World\nAll done."


# ── storage + endpoints ──────────────────────────────────────────────────────

def _seed_record(tmp_path, monkeypatch, generated_ts):
    rec = {
        "edition": "midday",
        "generated_ts": generated_ts,
        "generated_at_local": "Mon 12:04 PM",
        "brief": "World\nThings happened.\n\nTech & AI\nModels shipped.",
        "stories": [{"title": "t", "source": "NPR", "link": "l", "category": "world"}],
        "failed_sources": [],
    }
    path = tmp_path / "news_brief.json"
    path.write_text(json.dumps(rec))
    monkeypatch.setattr(news, "NEWS_FILE", path)
    return rec


def test_news_brief_endpoint_serves_cache_with_age(tmp_path, monkeypatch):
    import time as _time
    rec = _seed_record(tmp_path, monkeypatch, _time.time() - 600)
    client = TestClient(ts.app)
    resp = client.get("/news/brief")
    assert resp.status_code == 200
    body = resp.json()
    assert body["brief"] == rec["brief"]
    assert 9 <= body["age_min"] <= 11


def test_news_brief_endpoint_503_when_never_generated(tmp_path, monkeypatch):
    monkeypatch.setattr(news, "NEWS_FILE", tmp_path / "missing.json")
    resp = TestClient(ts.app).get("/news/brief")
    assert resp.status_code == 503
    assert "no news brief" in resp.json()["detail"]


def test_save_and_load_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(news, "NEWS_FILE", tmp_path / "news_brief.json")
    news.save_record({"edition": "morning", "generated_ts": 1.0, "brief": "x"})
    assert news.load_record()["edition"] == "morning"


# ── orchestrator handler formatting ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_tool_news_brief_passes_text_through():
    import tools as orch_tools
    client = AsyncMock()
    client.get.return_value.status_code = 200
    client.get.return_value.json = lambda: {
        "edition": "midday", "generated_at_local": "Mon 12:04 PM",
        "brief": "World\nA long brief body that must survive verbatim.",
        "age_min": 9,
    }
    out = await orch_tools._tool_news_brief(client, {})
    assert out.startswith("Midday news brief, generated Mon 12:04 PM:")
    assert "survive verbatim" in out
    client.post.assert_not_called()          # no flag → no regeneration


@pytest.mark.asyncio
async def test_tool_news_brief_refresh_posts_then_delivers():
    import tools as orch_tools
    client = AsyncMock()
    client.post.return_value.status_code = 200
    client.get.return_value.status_code = 200
    client.get.return_value.json = lambda: {
        "edition": "midday", "generated_at_local": "Mon 01:15 PM",
        "brief": "Fresh content.", "age_min": 0,
    }
    out = await orch_tools._tool_news_brief(client, {"refresh": True})
    client.post.assert_called_once()
    assert "/news/refresh" in client.post.call_args[0][0]
    assert out.startswith("Midday news brief, generated Mon 01:15 PM:")
    assert "couldn't fetch" not in out


@pytest.mark.asyncio
async def test_tool_news_brief_refresh_failure_falls_back_with_caveat():
    """A failed regeneration still delivers the cached brief, prefixed with
    the specific cause — spoken verbatim, so it must read as prose."""
    import tools as orch_tools
    client = AsyncMock()
    client.post.return_value.status_code = 502
    client.post.return_value.json = lambda: {"detail": "no usable items from any feed"}
    client.get.return_value.status_code = 200
    client.get.return_value.json = lambda: {
        "edition": "morning", "generated_at_local": "Mon 09:41 AM",
        "brief": "Cached content.", "age_min": 200,
    }
    out = await orch_tools._tool_news_brief(client, {"refresh": True})
    assert out.startswith("I couldn't fetch fresh news (no usable items from any feed)")
    assert "Morning news brief, generated Mon 09:41 AM:" in out
    assert "Cached content." in out


@pytest.mark.asyncio
async def test_tool_news_brief_failure_is_user_facing_prose():
    """The tool is terminal — a failure string is SPOKEN, so it must be a
    finished sentence with the specific cause, not bracketed instructions."""
    import tools as orch_tools
    client = AsyncMock()
    client.get.return_value.status_code = 503
    client.get.return_value.json = lambda: {"detail": "no news brief has been generated yet"}
    out = await orch_tools._tool_news_brief(client, {})
    assert "isn't available right now" in out
    assert "no news brief has been generated yet" in out
    assert not out.startswith("[")
