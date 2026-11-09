# Telemetry v2 — OpenTelemetry, Tempo + Grafana; Langfuse retired

**Status:** shipped 2026-11-08 (approved and built the same day). Report with
test output, live evidence, measurements and open items:
`TELEMETRY_V2_REPORT.md`. Feature doc: `../features/telemetry.md`.
Supersedes `TELEMETRY_PLAN.md` (Langfuse v1). Deviations from the plan as
written are listed in the report ("Deviations from the plan").

## Why

Kronk has three partial timing signals and no complete one:

| Signal | Covers | Missing |
|---|---|---|
| Langfuse v1 (1,366 traces since 2026-06-10) | orchestrator: route, LLM calls, tool calls | everything outside the orchestrator; aggregates need ClickHouse SQL; 6 containers, ~1.7 GB |
| `kronk.events` JSON lines (journald, rid-keyed) | agent rounds, tool call/complete durations | tool internals; voice front half; no query surface |
| `data/metrics.db` | per-LLM-call TTFT, tokens, tok/s | everything else |

Gaps that matter for performance work:

1. **The voice front half is invisible**: wake → end of speech → STT → HA
   intent match → blueprint → TTS. The 2026-11-08 Anime Pop incident spent
   63 s inside HA and MA without Kronk ever seeing the request
   (`docs/incidents/INVESTIGATION_2026-11-08_anime_pop_radio_hang.md`), and
   the blueprint fast path never reaches Kronk at all.
2. **A tool call is one opaque number.** Inside `play_music`: MA name lookup,
   MA search, library-station scoring, HA service call, the playback-verify
   wait (up to 10 s) — none are separately timed.
3. **No shared ID** from an HA voice run to Kronk's rid to tool_service.
4. **No aggregates**: no p50/p95 per stage, no trend over time.

## Requirements (operator, 2026-11-08)

- **R1 — everything.** Every interaction: voice (including the front half
  and the blueprint fast path that never reaches Kronk), chat UI
  (`/message`), OpenAI shim (`/v1/chat/completions`), Ollama shim
  (`/api/chat`, `/voice/api/chat`).
- **R2 — tool-level granularity**, and below: each outbound call a tool makes
  (HA REST/websocket, MA websocket command, SearXNG, NWS, …) and each
  deliberate wait (playback verify).
- **R3 — ongoing monitoring**, not a one-off study: p50/p95 per stage over
  time, slowest requests with their breakdown, retained long enough to see
  trends (target 90 days; size it and report).
- **R4 — industry standard**: OpenTelemetry instrumentation, Grafana Tempo
  storage, Grafana UI.
- **R5 — no HA or MA changes.** No edits to HA or MA config, integrations,
  prompt templates or code; no HA/MA restarts or reloads. Read-only access
  through existing APIs and tokens only. (The Kronk blueprint is ours, but
  this project does not need to edit it either — see "Voice listener".)
- **R6 — local-first** (tenet 1): nothing leaves the box. No vendor
  exporters, no telemetry phone-home in Grafana (disable analytics/update
  checks/gravatar).
- **R7 — telemetry never breaks or slows Kronk** (the v1 rule, kept):
  exporter failures degrade to a log line; batch export off the request path;
  a single env switch turns it all into no-ops. Overhead target: < 5 ms
  added per request, measured.
- **R8 — Langfuse retired** in this project.

## Architecture

```
 Voice PE ──► HA (STT, intents, blueprint) ──► Kronk orchestrator ──► tool_service ──► HA / MA / SearXNG / NWS
                │  read-only websocket              │ OTel SDK            │ OTel SDK
                ▼                                   │ (traceparent ──────►│ via httpx)
        voice_tracer (new) ── OTLP ─────────────────┴──── OTLP ───────────┘
                                                    ▼
                                            Tempo (local disk) ◄── Grafana (dashboards, TraceQL)
```

### 1. Instrumentation — orchestrator and tool_service

- OpenTelemetry Python SDK with OTLP exporter (gRPC or HTTP — pick one, pin
  it), plus the FastAPI and httpx instrumentations. The httpx
  instrumentation propagates W3C `traceparent` from orchestrator to
  tool_service, so a tool call and its internals share one trace for free.
- Keep the v1 isolation pattern: `orchestrator/telemetry.py` stays the only
  module that imports the tracing SDK; it becomes an OTel module with the
  same role (`start_pipeline` / `end_pipeline` / child spans). tool_service
  gets the equivalent small module. Call sites elsewhere use those helpers.
- One env switch per service (e.g. `TELEMETRY_ENABLED=false` → no-ops), and
  standard `OTEL_*` env for endpoint and service name, set in
  `docker-compose.yml`.
- Hand-written spans where auto-instrumentation can't see:
  - pipeline root (one per request; attributes: rid, transport, route,
    agent, origin device id + area, error flag);
  - routing decision; each agent round; each LLM call (attributes: model,
    prompt/completion tokens, time to first token, tok/s — the metrics.db
    fields);
  - each tool call (name, args summary, outcome);
  - tool_service: `ma_command` (websocket, so httpx can't see it — span per
    command with command name), `ha_call_service` (websocket), the
    resolver (`_resolve_named`, candidates, winner score), `_pandora_station`,
    `_play_verified` (the wait, with its outcome);
  - any other websocket or non-httpx outbound call found in tool_service.
- Add `trace_id` to every `kronk.events` JSON line so logs and traces join.
- No prompt or completion text in span attributes by default (privacy and
  size); text previews already in `kronk.events` stay as they are.

### 2. Voice listener — `voice_tracer` (new service, kronk stack)

HA does not emit OpenTelemetry, and R5 forbids changing it. A small service
reads what HA already records and turns it into spans:

- Connects to HA's websocket (`ws://localhost:8123/api/websocket`) with the
  existing `HA_TOKEN` (read-only use). HA runs with host networking; the
  existing tool_service pattern shows how a kronk-stack container reaches it.
- **Assist pipeline runs.** HA keeps recent runs in memory and exposes them
  via `assist_pipeline/pipeline_debug/list` and
  `assist_pipeline/pipeline_debug/get` (the 2026-11-08 investigation used
  these, read-only). Trigger: subscribe to `state_changed` for
  `assist_satellite.*` entities; when a satellite returns to idle, fetch the
  run(s) and emit one trace per run with spans from the event timestamps:
  wake word → STT (vad end, transcript) → intent (which agent handled it,
  `processed_locally`) → TTS → run end. Retention in HA is small, so fetch
  promptly; poll the debug list periodically as a backstop and de-duplicate
  by run id.
- **Blueprint runs.** Subscribe to `automation_triggered` for
  `automation.kronk_music_assistant_voice_device_first`; when the run
  finishes, fetch it with `trace/get` and emit spans for its steps
  (`play_media` call, verify wait, hand-off to Kronk). HA keeps only the last
  5 traces per automation — fetch each one right after it finishes.
  Changing that limit is an HA config change and is out of scope (R5).
- **Joining voice to Kronk.** Without an HA change, HA cannot pass a trace id
  into Kronk. Kronk already receives the satellite's device id
  (`[kronk-origin]` stamp, `orchestrator/origin.py`); put it on the pipeline
  root span. The listener matches a Kronk pipeline root by device id within
  the voice run's intent window (query Tempo's API) and records the join
  (span link, or the Kronk trace id as an attribute on the voice intent
  span — implementer's choice; document it). One satellite talks at a time,
  so device + window is unambiguous in practice; report any ambiguity seen.
- Known gap: wake-word detection happens on the satellite; HA only knows when
  the satellite reported it. State it on the dashboard, don't fake it.

### 3. Storage and UI — Tempo + Grafana (new compose stack)

- New `docker-compose.telemetry.yml` with a top-level `name:` (e.g.
  `kronk-telemetry` — Langfuse's file lacked one and silently joined the
  `kronk` project) and an `# Operations:` comment block like the other
  stacks.
- **Tempo**: single binary, local-disk storage on a named volume, OTLP
  receiver (no separate collector unless needed), retention per R3. Enable
  whatever Tempo needs for **TraceQL metrics** (p50/p95 by span name over
  time — verify the current mechanism and its config in the pinned version's
  docs; do not rely on memory). Bind its ports to loopback/docker network
  only.
- **Grafana**: provisioned from files in the repo (datasource for Tempo,
  dashboards as JSON), so the setup is reproducible. LAN-reachable UI (the
  home LAN is the trust boundary, as for MA on 8095); admin credentials in a
  new gitignored env file, documented in CLAUDE.md's secrets line. Analytics,
  update checks and external lookups off (R6).
- Port: Langfuse holds 3000 today. Either retire Langfuse first and reuse
  3000, or pick another port — choose, and update `/services` and the nginx
  `/probe/*` entry accordingly.
- **Pin every image** (tenet 3) to an exact version at least two weeks old;
  record why each was chosen.

Dashboards to ship (minimum):

1. **Overview**: requests per hour by transport; end-to-end p50/p95 by
   transport (voice measured from wake to TTS end; chat from request to
   last token).
2. **Stages**: p50/p95 over time per stage — STT, intent, blueprint,
   Kronk routing, each agent's LLM rounds (TTFT and generation separately),
   each tool, and each tool-internal span (MA command, HA call, verify wait).
3. **Slowest requests**: table of the slowest traces in the window with a
   link to each waterfall.
4. **Music**: `play_music` breakdown (lookup, resolve, play, verify), the
   blueprint fast path vs the Kronk path.

### 4. Retire Langfuse

- Remove the Langfuse SDK from the orchestrator (`requirements.txt`,
  regenerate `requirements.lock` with hashes — never hand-edit it), the
  `LANGFUSE_*` env in `docker-compose.yml`, the Langfuse references in
  `scripts/run_tests.sh`, `scripts/langfuse_set_password.sh`, and the
  `/services` page card and `/probe/langfuse` nginx route (replace with
  Grafana).
- Stop the stack with plain `down` — **never `down -v`** (tenet 11). The
  volumes stay until the operator decides to delete them; say so in the
  report. Keep `docker-compose.langfuse.yml` in git history only (delete
  the file) and `.env.langfuse` untouched on disk.
- Update `docs/TELEMETRY_GUIDE.md` (rewrite for Grafana/Tempo),
  `docs/features/telemetry.md` (v2), README (service map, stacks table),
  CLAUDE.md (stacks table, telemetry pointer), ROADMAP (move Telemetry v2 to
  Shipped when done), and mark `TELEMETRY_PLAN.md` superseded.
- Out of scope: retiring `metrics.db` and the `/performance` page. Keep them
  working; note whether Grafana now duplicates them.

## Build order (one change, one test — tenet 9)

1. Tempo + Grafana stack up, empty, reachable; datasource provisioned.
2. Orchestrator OTel (replacing Langfuse inside `telemetry.py`); traces
   visible in Grafana for a chat request and a shim request.
3. tool_service OTel; one trace now spans orchestrator → tool_service →
   MA/HA calls.
4. `voice_tracer`; voice runs and blueprint runs appear; join to Kronk
   traces works.
5. Dashboards.
6. Langfuse retirement and docs.

## Testing and definition of done

- `./scripts/run_tests.sh` passes (not bare pytest). New behavior lands with
  tests: span creation with an in-memory exporter, the no-op switch, the
  voice-run → span conversion from a recorded pipeline-debug payload, the
  voice↔Kronk join logic.
- Live, after each `up -d --build`: restart nginx if the orchestrator was
  rebuilt; verify `/message`, `/v1/chat/completions` and `/api/chat` still
  answer; verify the matching traces exist in Tempo via its HTTP API, not
  just in the UI.
- Voice: exercise via HA `conversation/process` and
  `assist_pipeline/run` (text start stage) as in
  `docs/features/voice-pipeline.md` → "Testing utterances". A real spoken
  "Okay Nabu" test (for the STT/wake spans) is the operator's to do; list
  exactly what to say and what should appear.
- Measure the instrumentation overhead (R7) on a few chat and shim requests
  with telemetry on vs off; report the numbers.
- `./scripts/pipeline_bench.sh` **clears chat history**; do not run it
  without the operator's explicit OK.

## Constraints for the implementer

- **No HA or MA changes, restarts or reloads** (R5, CLAUDE.md directive
  2026-09-10). A Kronk-stack `up -d --build` must never touch the
  `homeassistant` or `music-assistant-server` containers.
- **Music playback during tests**: only the basement Voice PE
  (`media_player.home_assistant_voice_0ab117`, player name "basement voice pe
  ma"). Check it is idle first; never pause or replace anything the operator
  started; pause your own playback when done. Name the player explicitly in
  test sentences — an unknown or missing name falls back to the kitchen.
- Never `docker compose down -v` on any stack. No LAN scans. No changes on
  remote hosts.
- Do not `git commit` or push; the operator does. Leave the work as a clean,
  reviewable working tree and suggest commit boundaries in the report.
- Follow CLAUDE.md throughout: tests unprompted, docs lifecycle, incident
  doc if something breaks, plain engineering prose in the repo.

## Report back

What was built, image versions pinned and why, test results quoted
verbatim, the live verification evidence (trace ids, Tempo API output), the
measured overhead, disk usage per day of traces and the projected size at the
chosen retention, what the operator must do by hand (real voice test,
Grafana login, Langfuse volume deletion decision), and anything deferred or
not verified.
