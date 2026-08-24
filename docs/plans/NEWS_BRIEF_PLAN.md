# News brief — Plan

Status: **shipped 2026-08-24** (same day). Distilled into
`docs/features/news-brief.md` — read that first. Verified: 1,677-char
brief delivered as 1,722 (header only) in 1.67 s; follow-ups delegate to
research. One as-built note: AP has no public feeds — Guardian World
carries the third general slot. Motivating incident: rid
`99ce926c` — "Check online and provide an updated news brief" produced a
754-character answer after the coordinator re-summarized research's
already-summarized brief. A prior attempt in the same session confabulated
a "brief" from model memory without consulting any source at all.

## Operator decisions (2026-08-24)

1. **Terminal `news_brief` tool** on the coordinator — the brief's text
   streams to the user verbatim; no synthesis round can compress it, and
   no delegation path can confabulate it.
2. **Editions at 6am / noon / 6pm** local (morning / midday / evening),
   pre-generated and cached; the live request path is a cache read.
3. **Sources:** NPR, AP, BBC as the general-news base, plus tech/AI and
   cybersecurity feeds (operator's standing interests): Ars Technica,
   The Verge, MIT Technology Review, Krebs on Security, BleepingComputer.
   Feed URLs verified at build time; operator-editable override file.
4. **Summarization runs in tool_service, calling LiteLLM directly**
   (accepted tradeoff: bypasses the orchestrator's `_llm_lock`, so a
   refresh can contend with a live query for ~20 s three times a day).
5. **Medium length** (~300–350 words spoken), with follow-ups: the brief
   lands in conversation history, so "tell me more about X" rides the
   normal coordinator → ask_research path. A stored per-story link list
   supports a `news_detail` tool later if live follow-ups disappoint (V2).

## Design

All new machinery in `tool_service/news.py`, following the weather-cache
and solar-poll patterns:

- **Fetch:** RSS/Atom via httpx + `xml.etree` (no new dependencies; a
  feed-parsing library is not worth a pin for two formats). Per-feed
  failures are logged and skipped — a brief from 7/8 sources beats no
  brief; failed sources are named in the stored record (tenet 7).
- **Select:** items from the last 24 h, capped per feed and overall, so
  the summarization context stays bounded.
- **Summarize:** one chat call to LiteLLM (`NEWS_LLM_URL`, default
  `http://host.docker.internal:8002/v1`; `NEWS_MODEL`, default the
  coordinator model). Prompt asks for a spoken-friendly brief in three
  sections (world / tech & AI / cybersecurity), ~300–350 words, no
  preamble, clear story names so follow-ups have handles.
- **Store:** `/data/news_brief.json` — edition label, generated_at, brief
  text, per-story {title, source, link, category}, and which feeds
  failed. One file; history is not a requirement (right-size).
- **Refresh loop:** asyncio task (weather-loop pattern): on startup and
  once a minute, compare the stored `generated_at` against the current
  edition boundary (6:00/12:00/18:00, `NEWS_TZ` default
  America/New_York); regenerate when the cache predates the boundary.
  Boot with a stale cache → regenerate immediately.
- **Endpoints:** `GET /news/brief` (the tool's source of truth — brief +
  edition + age; a specific error if generation has never succeeded),
  `POST /news/refresh` (force regeneration; used by deploy verification
  and the operator).

Orchestrator side:

- `news_brief` tool def + handler in `tools.py`; registered on the
  **coordinator** with `terminal_tools={"news_brief"}` — the first
  terminal tool on the coordinator. `_terminal_speech` gets a passthrough
  case: the news handler returns display-ready text, not a structural
  line.
- Coordinator prompt: news-brief requests call `news_brief`; research
  remains the path for *specific* news questions, not briefings.
- Freshness is labeled in the delivered text ("Midday brief, generated
  12:04") — never implied-live (the confabulation lesson).

## Build steps + tests (each lands green)

1. Plan doc (this file).
2. `news.py` pure parts + tests: RSS and Atom parse fixtures, selection
   window/caps, edition boundary math (incl. the before-6am wrap to
   yesterday-evening), prompt build, storage round-trip.
3. Endpoints + refresh loop + mocked-LLM tests.
4. Orchestrator tool + coordinator wiring + tests: handler formatting,
   terminal passthrough in `_terminal_speech`, and a `run_stream` test
   proving a coordinator `news_brief` call streams the text verbatim and
   ends the turn.
5. Deploy both services; verify feeds fetch live; force a refresh; run
   the brief through `/api/chat`; confirm the delivered text length ≈ the
   stored brief (the whole point); follow-up question live test.
6. Feature doc + ROADMAP Shipped entry.

## Latency budget

Request path: route (deterministic) → coordinator plan round (~2 s) →
cache read (<100 ms) → text streams. ≈2–3 s to first word — voice-tier.
Refresh path: ~15–30 s of GPU three times a day, invisible unless it
collides with a live query (accepted, decision 4). A "news brief" command
pin (method-pin class) can remove the coordinator round later if 2–3 s
proves annoying by voice; not in V1.
