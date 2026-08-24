# Feature: News brief (cached editions, delivered verbatim)

**Shipped:** 2026-08-24 · **Plan:** `../plans/NEWS_BRIEF_PLAN.md` ·
**Motivating incident:** rid `99ce926c` — the coordinator re-summarized
research's brief down to 754 characters; a sibling attempt confabulated a
"brief" from model memory with no sources at all.

## What it does

"Give me a news brief" delivers a pre-generated ~330-word brief (World /
Tech & AI / Cybersecurity) in under 2 seconds, verbatim, with its edition
and generation time stated up front. Follow-ups by story name ("tell me
more about the Zimbra flaw") ride the normal coordinator → ask_research
path, using the brief in conversation history as context.

"Update the news feed" (added same day) forces a regeneration first: the
`refresh` boolean on the same tool — no second tool for the 4B picker to
confuse — POSTs `/news/refresh`, then delivers the fresh brief. ~8 s
measured live. A failed refresh falls back to the cached brief prefixed
with the specific cause. Calibration 2026-08-24: 3× refresh phrasing and
2× plain phrasing → the flag landed correctly 5/5 (POST count in
tool_service logs matched exactly).

## How it works

- **Editions, not liveness:** `tool_service/news.py` regenerates at
  6am/noon/6pm local (`NEWS_TZ`). A minute-cadence staleness loop (weather
  -cache pattern) compares the cache against the current edition boundary
  — survives restarts, no cron. Before 6am you get yesterday's evening
  edition, labeled as such.
- **Sources:** 8 RSS/Atom feeds — NPR, BBC World, Guardian World (AP's
  public feeds are gone; Guardian substituted 2026-08-24), Ars Technica,
  The Verge, MIT Tech Review, Krebs, BleepingComputer. Parsed with
  `xml.etree` (no new dependency). Override file:
  `/data/news_feeds.json`. Per-feed failures skip and are recorded in
  the edition (`failed_sources`).
- **One summarize call** direct to LiteLLM (`NEWS_MODEL`, default
  gemma-4-e4b) — bypasses `_llm_lock`, so a refresh can contend with a
  live query for ~20 s, three times a day (accepted, plan decision 4).
- **Terminal delivery:** `news_brief` is the coordinator's first service
  tool and it is terminal — the tool's text streams to the user with no
  synthesis round after it. `_terminal_speech` passes it through whole
  (the other terminal tools map structural lines; this one returns
  finished prose, failure included).

## Verified behavior (live, 2026-08-24)

- Stored brief 1,677 chars → delivered 1,722 (header only added); total
  request 1.67 s through the `/api/chat` shim.
- Follow-up "tell me more about the Zimbra flaw" → ask_research →
  CVE-level detail.
- First generation: 30 stories, 0/8 feeds failed.
- 22 tests: parse/selection/edition-math/prompt/storage/endpoints/handler
  formatting + loop-level terminal-verbatim proof.

## Gotchas

- The tool's failure string is SPOKEN (terminal) — it must be finished
  prose with the specific cause, never bracketed model instructions.
- `max_tokens` must leave headroom above the word target: at 600 the
  first editions hit the cap and were stored mid-sentence ("This critical
  flaw"). Now 1000, and `summarize()` refuses `finish_reason=length` —
  a truncated edition is never stored (2026-08-24).
- `tool_service/Dockerfile` enumerates modules — a new module must be
  added to the COPY line or the container crash-loops on import
  (bit us on deploy day).
- The brief is only as fresh as its edition; "check online and…" still
  forces the live research path when the user wants newer-than-edition.
- Full pipeline bench not run for this change (operator session active;
  the battery clears chat history) — touched paths verified individually.

## Blog hooks

- The double-summarization tax: why a supervisor agent compresses your
  briefing, and the terminal-tool escape.
- Editions beat freshness: caching as the thing that makes a *good* brief
  affordable, not just a fast one.
- A news pipeline with zero new dependencies: RSS via xml.etree, one LLM
  call, one JSON file.
