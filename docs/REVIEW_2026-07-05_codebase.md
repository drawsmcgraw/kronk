# Kronk codebase review — 2026-07-05

**Status: recommendations only — nothing here has been implemented.**

Method: four parallel full-read reviews (orchestrator core; tool/health/finance
services; infrastructure + ops; test suite), each judged against the
`CLAUDE.md` engineering tenets and `ROADMAP.md`. Findings consolidated and
deduplicated, ranked by risk-per-effort, and grouped so each group maps to a
roadmap item where one exists. File:line references are as of commit `9678769`
plus the uncommitted docs reorg.

**Verdict:** the codebase is healthy and unusually disciplined for its size —
hash-pinned lockfiles, journald rotation, deliberate de-privileging, sound
single-writer SQLite, a test suite that pins past incidents. The dominant
pattern in the findings: **the discipline of the newest code (`/music`:
pre-check, verify-the-effect, log-the-body, speak-specific-detail) has not
propagated backward** — older routes and handlers trust status codes, swallow
detail, and claim unverified success. Second pattern: **pinning is excellent
at the Python layer and leaks everywhere else** (Docker bases, image tags,
an HF model). Nothing found contradicts the architecture itself; no
microservice splits or framework adoptions recommended (tenet 4).

---

## P0 — correctness bugs (fix before/alongside roadmap Now items)

**P0.1 `litellm/hooks.py:28-44` — `_normalize` destroys `tool_calls` /
`tool_call_id` on every LLM call.** The hook (registered globally,
`config.yaml:87`) rebuilds each message as `{role, content}` only. Every
multi-round agent loop (research plans, coordinator `ask_*` delegation) sends
the model a corrupted transcript: orphaned tool results, no record of the
calls that produced them. Works today only because llama.cpp templates
degrade tolerantly; some templates 400 on it. *Fix:* preserve
`tool_calls`/`tool_call_id` when copying; skip merge/append logic entirely
when any message has `tool_calls` or `role == "tool"`. The planned hooks.py
backfill test must include a round-2 agent-loop message array — the
`call_type` test alone would not catch this. (Tenets 5, 7.)

**P0.2 `health_service/main.py:274-276` — salted `hash()` as a persistent
primary key.** CSV activity rows without an ID get
`abs(hash(f"{date}:{name}")) % 10**9`; Python string hashing is randomized
per process, so re-importing the same CSV after a container restart inserts
duplicates instead of upserting. Silent data corruption in a table the
backup item calls irreplaceable. *Fix:* stable digest, e.g.
`int(hashlib.md5(key.encode()).hexdigest()[:8], 16)`. (Tenets 6, 11.)

**P0.3 `tool_service/main.py:161-201` — weather trusts upstreams without
verification.** (a) `geo_resp.json()` unchecked → Open-Meteo hiccup becomes
a generic 500. (b) The three NWS fetches are never status-checked — an NWS
500 parses as error-JSON, `.get("properties", {})` swallows it, and the
route returns **200 with an empty forecast**; the model then answers from
nothing, and the same silent emptiness flows into the prompt-injected
weather cache. *Fix:* status-check each call; on failure raise 502 naming
which upstream failed + first ~200 bytes of body (the `/music` pattern).
(Tenets 6, 7; roadmap 2.)

**P0.4 `health_service/main.py:104-113` — sync endpoints claim success for
a no-op.** `POST /api/sync` returns `{"status": "sync started"}`, then the
background stub logs "skipped" and discards the result; `sync_log` is never
written, so `/api/sync-status` shows a months-stale "last sync". An agent
will tell the user sync started. *Fix (while stubs exist):* return 503
synchronously with detail naming the ROADMAP secrets-rebuild dependency.
When real sync returns, write start/complete rows so the effect is
verifiable. (Tenet 6.)

**P0.5 `orchestrator/main.py:224-385` — no top-level exception guard in
`_run_pipeline`.** Only `routing.classify` is guarded. Any other raise
(agent loop re-raise at `agents.py:597`, the `weather_context()` call at
main.py:252, a telemetry bug) kills the stream: `/message` ends without
`[DONE]`, the Ollama shim without `done: true`, and HA speaks its generic
"unexpected error" — the tenet-7 anti-pattern verbatim. *Fix:* wrap phases
1–3; on exception yield a specific error token (with rid), log, and pass
the error to `end_pipeline(level="ERROR")`. (Tenet 7; roadmap 2.)

## P1 — the "verbose errors" work package (this IS roadmap item 2)

The audit item 2 calls for is done; these are its findings. Fixing P1.1–P1.3
plus P0.3/P0.5 delivers most of the item.

**P1.1 `orchestrator/main.py:330-333` — specialist errors are laundered
through the coordinator as a "result".** The error string is appended as
`[{agent} specialist result — use this to answer]`; the coordinator is never
told it's a failure, so it paraphrases, apologizes generically, or invents
an answer. This is the single biggest detail-swallowing point on the voice
path. *Fix:* either emit the specific error as a token directly and skip
the coordinator (structural — preferred, tenet 5), or reword the context
block to "the specialist FAILED — report the failure and its cause
verbatim; do not answer the original question."

**P1.2 `orchestrator/tools.py` — six handlers discard status + body:**
`_tool_get_weather:417`, `_tool_web_search:432`, shopping add/remove/view
(:468/:478/:457), health query/search/bloodwork (:525/:505/:547 — status
only, body dropped), `_tool_query_finances:653`. Worst:
`_tool_shopping_list_clear:481-483` ignores the response entirely and
asserts success (tenet 6 violation). `play_music`/`set_timer`/`fetch_url`
show the right pattern. *Fix:* one helper — `_fail(verb, resp)` extracting
`resp.json()["detail"]` or `resp.text[:200]` + status — used by every
handler.

**P1.3 `orchestrator/main.py:376-380` + `telemetry.py:181-193` — failed
turns never mark the Langfuse trace ERROR.** `end_pipeline` accepts
`level`/`status_message`; no caller passes them. "Find the spoken failure
in one step" means filtering traces by level — impossible today. *Fix:*
track `pipeline_error` in `_run_pipeline`, pass it in the `finally`.

**P1.4 `agents.py:84-91` — terminal-speech fallback speaks raw internals.**
An unrecognized result line (e.g. `[Tool play_music error: ReadTimeout(...)]`
from `tools.execute` on timeout) is spoken verbatim, brackets stripped.
*Fix:* part of P2.2 (formatter registry) — every terminal tool gets an
explicit failure phrasing; unknown shapes get a generic-but-clean sentence
plus a log line.

**P1.5 `main.py:269` — router failure asserts an unverified cause.**
"could not reach the language model … Is the server still loading?" fires
for *any* classify exception, including 400s — actively misdirects
troubleshooting. *Fix:* `f"Routing failed: {e}"` + rid; `llm.py`'s
RuntimeError text already carries the specifics.

**P1.6 `tool_service/main.py:279-301` — `/search` detail-free.** Fixed
string "Search service unavailable" regardless of cause; non-JSON 200 →
generic 500; network errors unguarded. *Fix:* mirror `/music`: log
`resp.text[:300]`, return 502 with the SearXNG status.

**P1.7 `tool_service/main.py:18` — logging never configured; INFO silently
dropped.** No `logging.basicConfig(level=logging.INFO)` (health/finance
have it). The `/music` route's "full error body goes to the log" story has
never actually logged at INFO. *Fix:* one line, same as the other services.

**P1.8 `main.py:895-901` — shopping-list page can't distinguish "empty"
from "service down"** (returns `{"items": []}` on error). *Fix:* error
field or 502; page renders "unavailable".

**P1.9 `health_service/main.py:116-128` — `/api/query` (the primary LLM
tool route) turns bad params into generic 500s.** No upper bound on `days`
(overflow), unguarded `date.fromisoformat`. A 4B model *will* send a bad
date (tenet 5). *Fix:* `le=3650`; 422 with "end_date must be YYYY-MM-DD".

**P1.10 `health_service/main.py:291-292,682-683` — import row-loops count
exceptions as `skipped` with zero diagnostics** (`{"inserted": 0, "skipped":
400}` and nothing anywhere says why). *Fix:* log first N with row context;
`sample_errors` list in the response.

**P1.11 `health_service` vector-store failures swallowed** (:549-554, :593,
:628, :686, :901): `upsert_chunks` wrapped in log-and-continue; responses
claim full success (bloodwork returns `"chunks": N` even when zero stored).
*Fix:* `"vector_store": {"ok": false, "error": …}` in the response.

## P2 — roadmap-readiness refactors (do before the feature they unblock)

**P2.1 One context-injection seam — prerequisite for the context cache
(item 5).** Weather context is injected in two independent paths today:
`main.py:252` (coordinator `extra_parts`) and a hard-coded
`if agent.name == "home"` inside `run_stream` (`agents.py:480-483`) — a
home request fetches it twice, and the special case hides agent behavior
outside the `AGENTS` dict (tenet 8). Also: `weather_context()` +
`_load_state()` run before routing on every request, inside `_llm_lock`,
even when unused (tenet 12). *Fix:* declare `context_keys` on
`AgentConfig`, resolve them in one place in `run_stream`, give the
coordinator the same mechanism, build `extra_parts` lazily at phase 3.
That declaration point is where the item-5 cache plugs in. Related:
`file_contexts` (`main.py:55-58` — unbounded, quirky clearing) should ride
the same path when item 5 lands. tool_service side: the weather cache
(global + loop + file + route hardcoded to one key) becomes the registered
fetcher pattern — keyed dict `{key: {fetched_at, ttl, data}}`, one loop,
one `GET /cache/{key}`; `/weather/cached`'s response contract
(`fetched_at`/`age_s`) is the template to keep.

**P2.2 Generalize terminal-tool speech — prerequisite for MagicMirror
(item 7).** `_terminal_speech` string-matches `"Music playing: "` /
`"Could not play music: "` authored 500 lines away in `tools.py:626,631`
(tenet 8; untested cross-file contract). *Fix:* per-tool speech formatter
registered alongside `_HANDLERS` (or handlers return
`(speech, model_context)` for terminal tools). Also `agents.py:605-611`:
a *duplicate* terminal-tool call takes the non-terminal "[already called]"
path and lets the model keep talking — treat duplicate terminal calls as
terminal (speak the earlier result, end the turn).

**P2.3 Timer decommission (item 3) is nine sites, not four.** Orchestrator:
`tools.py:337-358` (definition), `:597-610` (handler), `:404` (timeout),
`:16` (DEFAULT_TIMER_LABEL), `:671` (dispatch); `agents.py:184-185`
(routing_hint "timer, countdown" — must go or the router keeps stealing
timer utterances), `:193-194` (prompt paragraph), `:206` (tool_names).
tool_service: `/timer` route (:450-498), env comment block (:434-442),
compose `HA_TIMER_ENTITY` (docker-compose.yml:71-75). `HA_URL`/`HA_TOKEN`
are shared with `/music` — keep those. *Fix:* one commit deleting all
sites once item 3's verification passes; until then a
`# ROADMAP item 3: scheduled for decommission` comment on definition +
handler + route.

**P2.4 Metrics module records rows its own dashboard can never show.**
`eval_duration_ns` is hardcoded 0 (`llm.py:187`), so `gen_ms`/
`tokens_per_sec` are always NULL and every `WHERE gen_ms IS NOT NULL`
aggregate on `/performance` (`metrics.py:80,92,105`) is permanently empty;
`ttft_ms` never passed despite `run_stream` knowing it. *Fix:* decide in
the Telemetry v2 requirements pass (item 6) — compute from timings
`run_stream` already has, or delete the dead columns. Related v2 input:
every agent round is currently recorded three times (Langfuse span,
`events.emit`, `metrics.record` — `agents.py:536-560`, duplicated again
at :669-689); v2 should pick one write path per fact.

**P2.5 Terminal-tool turns end their Langfuse span with `output=None`**
(`agents.py:609-612` never sets `last_round_text`) — the flagship voice
feature is invisible in traces. *Fix:* set it to `_terminal_speech(result)`
before the terminal return. (Feeds items 2 and 6.)

**P2.6 Specialist tokens from a failed agent are shown, persisted, and
concatenated with the coordinator's answer** (`main.py:287-299`) —
half-answer + full answer stored as one assistant turn, poisoning future
history windows. *Fix:* buffer until the specialist's done/error event, or
at minimum reset `assistant_reply` on fallback.

**P2.7 Health RAG (Later item) landmines:** CSV importers never index into
the vector store while JSON importers do (`search_health_data` silently
stale until manual `/api/reindex`); `/api/reindex` itself uses the bare
`from db import` pattern the file header banned (main.py:942). *Fix:*
chunk-upsert in CSV paths (or mark staleness in the response); delete the
local import.

**P2.8 ROADMAP stretch entry already stale:** `agents.py:209-221` shows the
`assistant` agent wired with `get_kronk_context` + `generate_diagram` —
the "gap is wiring it to an agent" claim is wrong; the real gap is keeping
`kronk-context.md` from drifting. *Fix:* update the ROADMAP entry.

## P3 — pinning + least privilege (tenets 3, 10)

**P3.1 All four Dockerfiles:** `FROM python:3.12-slim` floats; `RUN pip
install uv` fully unpinned — the tool enforcing the hash pins is itself
version-roulette, and `up -d --build` is a routine step. *Fix:* pin base
by digest, `uv==X.Y.Z`, bump on update day.

**P3.2 `docker-compose.langfuse.yml` header says "PINNED" but `redis:7`
(:153), `postgres:17` (:171), `clickhouse-server:26.3` (:105) float** —
and the header itself warns about schema migrations between versions.
*Fix:* full-version pins.

**P3.3 `health_service/Dockerfile:11`** bakes an unpinned HF embedding
model (`BAAI/bge-small-en-v1.5`) at build time — an upstream revision bump
silently changes embeddings that must match the existing `data/chroma`
vectors. *Fix:* pin the model revision or vendor the ONNX files.

**P3.4 `health_service/requirements.txt:4-10`** uses `>=` ranges
(`chromadb>=0.5` — chroma breaks on-disk formats across majors); any relock
is a mass upgrade. *Fix:* `==` pins like the other services. Also
`docker-compose.yml:28`: `retire_calc` builds from `../retirement-calc` —
unpinned sibling working tree; won't build from a fresh clone. Document or
pin to a git ref. And `tests/requirements.txt` has no hash lock despite
being in the definition-of-done path.

**P3.5 `.env` is mode 664** with the HA admin token + Langfuse keys
(`.env.langfuse` got 600). *Fix:* `chmod 600 .env`; add a perms check to
update day. **One command; do it today.**

**P3.6 One admin-scope HA token, four consumers** (tool_service,
boot_notify, memwatch, perfwatch) — full HA privileges, one leak is total
control, one rotation breaks four things. *Fix:* dedicated non-admin HA
user; separate tokens per consumer class.

**P3.7 Shared `./data` mount exposes every credential to every service**
(withings tokens, garmin JWT, yt-music cookie readable by orchestrator,
finance, etc.). *Fix:* per-service subdirectory mounts when the
secrets-rebuild item lands (`./data/health:/data`).

**P3.8 LAN exposure inventory** (input for the External-access roadmap
item): unauthenticated `DELETE /history`, uploads, list mutation via
nginx :80; LiteLLM on `0.0.0.0:8002`; Whisper `0.0.0.0:10300` and Piper
`10200:10200` are consumed only by same-host HA and could bind loopback
today (free tightening). Langfuse :3000 has auth.

## P4 — backups (roadmap item 4 is bigger than written)

**P4.1 Full state inventory the nightly job must cover** (the ROADMAP
entry names only a subset): named volumes `kronk_ha-config`,
`kronk-ma_ma-config`, `langfuse_postgres_data` + `clickhouse_data` +
`minio_data` (skippable: piper-models, redis, clickhouse_logs); the entire
`./data` bind mount (four SQLite DBs, `chroma/` — derivable via
`/api/reindex`, worth excluding and writing that down — shopping list,
retire/, generated/, plus live credentials: `withings_tokens.json`,
`garmin_jwt_web.txt`, `yt-music-cookie`); gitignored config that exists
nowhere else: `.env`, `.env.langfuse` (losing SALT/ENCRYPTION_KEY makes
the Postgres backup useless), `searxng/settings.yml`, `secrets/garmin.json`;
host state outside the repo: `~/services/wyoming-whisper` (venv +
runtime-libs), llama.cpp builds, the talkie GGUF. SQLite must be
snapshotted with `sqlite3 .backup`/`VACUUM INTO`, not `cp` (both the infra
and services reviews flagged this independently). Test one restore.

**P4.2 `systemd/llama-talkie.service:8`** loads its GGUF from
`~/model-staging/` — a directory whose name invites deletion; possibly
unrecoverable if custom. *Fix:* move to `/opt/models/talkie-lm/`, update
unit + reference copy.

**P4.3 Drift:** `kronk-hottub-monitor.service` is enabled and **active
live** but has no reference copy in `systemd/` — and the feature is
officially parked ([HOTTUB-01]). Stop/disable it or add the copy; make
`diff systemd/ ↔ live` part of update day. Related dead weight: the
`/hottub` route + `query_hottub` tool invite the router to burn a round on
a permanently dead path — unwire or comment-point at HOTTUB-01. Mystery
state in the shared mount: `data/postgres/` (Infisical-era?) and
`data/garmin_login_debug.png` (may show credential material) — verify and
delete.

**P4.4 No Langfuse retention policy** — ClickHouse + MinIO grow unboundedly
on a chatty voice box; also blocks the backup design ("telemetry
disposable? decide"). Decide now; "disposable prototype" is defensible.

## P5 — tests (feeds the Definition-of-done tiers)

**P5.1 Three surfaces have zero coverage, one isn't even importable:**
`litellm/hooks.py` (the repo's `litellm/` dir shadows the pip package as a
namespace path — the backfill test needs a `spec_from_file_location` +
stubbed `CustomLogger` fixture), `orchestrator/llm.py` (`stream()`'s
tool_calls-from-deltas accumulation — the shipped unified-streaming feature
every agent test mocks away; the thing most likely to break on a llama.cpp
upgrade), and both shim endpoints end-to-end (only `_shim_context` is
tested — tier 1 has no test that can fail for the path voice actually
uses). `tool_service /music` is likewise fully untested (player
resolution, unavailable pre-check, poll timeout — each produces a specific
spoken detail item 2 depends on).

**P5.2 Backfill specs for the three 2026-07-03 fixes are written** — see
the test-review transcript for full assertion lists; headline invariants:
(a) routing merge/drop — captured router messages strictly alternate,
duplicated-user-turn incident shape merges, trailing user pops;
(b) terminal tools — `llm.stream` called exactly once, failure variant
speaks "I couldn't play that" with zero model opportunity to contradict,
non-terminal control still loops; (c) hooks — `acompletion` normalizes
(the dead-hook regression), other call_types untouched, **plus the P0.1
tool_calls-preservation case**. Write (a)–(c) as one shared fixture table
run against all three role-repair implementations (routing.py,
sessions.py, hooks.py) so a divergence fails loudly.

**P5.3 Quality fixes:** `test_finance_service._make_minimal_pdf` is dead
code (and PDF ingestion untested despite being a stated success
criterion); `test_cached_endpoint_404_before_population` builds a
TestClient outside the `_fetch_weather` patch and could make a real NWS
call on a Starlette bump; the autouse `reset_history` fixture hits the
real `/data/sessions.db` path outside the DB patch (harmless, sloppy);
`sessions.prune_idle()` (destructive, runs at startup) untested.

**P5.4 Voice smoke test (item 8) design is ready** — battery of 10
utterances (timer→local, blueprint-grammar→MA, fuzzy-music→Kronk terminal
tool, two-turn memory pair that replays the 2026-07-03 400 path, and a
deliberate-failure utterance asserting the response contains *specific*
detail — the standing regression gate for item 2). Tier detection via
`intent-end` event engine + latency bucket; effect verification by polling
entity states (tenet 6); JSON output to `docs/bench/` but **asserting and
exiting non-zero** (a gate, not a measurement — unlike pipeline_bench,
which measures and can never fail). Full design in the test-review
transcript; fold into item 8's plan doc.

## Quick wins (≤30 min each, no design needed)

1. `chmod 600 .env` (P3.5)
2. `logging.basicConfig` in tool_service (P1.7)
3. Stable digest for activity IDs (P0.2)
4. Move talkie GGUF out of model-staging + fix unit (P4.2)
5. Resolve hottub unit drift: disable or add reference copy (P4.3)
6. `_fail()` helper + six handler call sites (P1.2)
7. Pin `uv==` in four Dockerfiles (P3.1, partial)
8. `# scheduled for decommission` comments on timer sites (P2.3, partial)
9. Delete `main.py` `_execute_tool`/`TOOL_DEFINITIONS` compat shims, point
   tests at `tools.py` (orch review #19)
10. Update the stale ROADMAP self-description entry (P2.8)
11. Verify + delete `data/garmin_login_debug.png`, investigate
    `data/postgres/` (P4.3)
12. Add the missing `# Operations:` block to `docker-compose.yml` (infra
    review #14)

## Suggested sequencing against the roadmap

1. **P0 batch first** (five bugs; each small; P0.1 needs its regression
   test in the same change).
2. **Roadmap item 2 = P1 batch** — the audit is done; the item's plan doc
   can be written straight from P1.1–P1.11 + P0.3/P0.5.
3. **Item 3 (timers)** uses the P2.3 nine-site checklist.
4. **Item 4 (backups)** uses the P4.1 inventory — the item as written
   would have missed the keys that decrypt its own Postgres backup.
5. **Item 5 (context cache)** starts with P2.1; **item 7 (MagicMirror)**
   with P2.2; **item 6 (telemetry v2)** ingests P2.4/P2.5 + the
   triple-recording consolidation; **item 8 (smoke test)** ingests P5.4.
6. **P3/P5 remainders** fold into update day (item 9) and the backfill
   chore.

Individual reviewer transcripts (full detail, ~85 raw findings) are in the
session task outputs; this doc is the deduplicated, ranked synthesis.
