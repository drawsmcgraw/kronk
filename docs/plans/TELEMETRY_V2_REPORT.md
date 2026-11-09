# Telemetry v2 — implementation report (2026-11-08)

Build of `TELEMETRY_V2_PLAN.md`, steps 1–6, all live on `kronk`. Working
tree left uncommitted for review; suggested commit boundaries at the end.

## What was built

| Step | Delivered |
|---|---|
| 1. Stack | `docker-compose.telemetry.yml` (project `kronk-telemetry`): Tempo 3.1.1 monolithic, local disk, 90-day retention, OTLP/HTTP on the shared `kronk_kronk` network, query API on loopback 3200; Grafana 13.2.4 on LAN :3000, provisioned datasource + dashboards, anonymous Viewer, admin login in `.env.telemetry`, phone-home off. `telemetry/tempo.yaml`, `telemetry/grafana/provisioning/*`. |
| 2. Orchestrator | `orchestrator/telemetry.py` rewritten on the OpenTelemetry SDK with the v1 helper surface kept (`start_pipeline` / `root().child_span` / `child_generation` / `first_token` / `end` / `end_pipeline`) plus `.current()` and `current()`; FastAPI + httpx auto-instrumentation (server spans, `traceparent` to tool_service); CLIENT-root sampler; pipeline root attributes (rid, transport from `http.route`, route, origin device/area, error); generation attributes (model, tokens, TTFT, tokens/s, tool calls); `kronk.events` lines carry `trace_id`. Langfuse SDK removed, `requirements.lock` regenerated with hashes. |
| 3. tool_service | `tool_service/telemetry.py` (same pattern, `span()` context manager); spans for `ma.command`, `ha.call_service`, `music.resolve`, `music.pandora_station`, `music.genre_plan`, `music.play_verified`, `ssh.exec`; FastAPI + httpx auto-instrumentation. |
| 4. voice_tracer | New service `voice_tracer/` (kronk stack): read-only HA websocket; pipeline-debug runs → `voice.run` / `voice.stt(.listen/.transcribe)` / `voice.intent` / `voice.tts`; automation traces → `blueprint.run` + step spans; deterministic ids from HA run ids; voice↔Kronk join by `kronk.origin.device_id` + intent window via Tempo search (span link + `kronk.trace_id`); voice↔blueprint join by window; `/health` on 8010; state file in `/data`. |
| 5. Dashboards | `telemetry/grafana/build_dashboards.py` → `overview.json`, `stages.json`, `slowest.json`, `music.json` (44 panels, 42 queries). |
| 6. Retirement + docs | Langfuse containers removed (`down` without `-v`; volumes kept), `docker-compose.langfuse.yml` and `scripts/langfuse_set_password.sh` deleted, `LANGFUSE_*` env gone from `docker-compose.yml`, nginx `/probe/langfuse` → `/probe/grafana` + `/probe/tempo` + `/probe/voice_tracer`, services page cards swapped; `docs/TELEMETRY_GUIDE.md` and `docs/features/telemetry.md` rewritten, README / CLAUDE.md / ROADMAP / BLOG_TOPICS updated, `TELEMETRY_PLAN.md` marked superseded. |

Tests added: `tests/test_telemetry.py` (7), `tests/test_tool_service_telemetry.py`
(4), `tests/test_voice_tracer.py` (14) with recorded HA payloads in
`tests/fixtures/`. Incident doc for the one thing that broke:
`docs/incidents/INVESTIGATION_2026-11-08_tempo_inverted_blocks.md`.

## Versions pinned and why

| Component | Pin | Released | Why |
|---|---|---|---|
| `grafana/tempo` | 3.1.1 | 2026-10-21 | Current minor; 3.x has TraceQL metrics GA in monolithic mode with no `local-blocks` processor and no Kafka. Newest patch older than two weeks at pin time. |
| `grafana/grafana` | 13.2.4 | 2026-10-13 | Newest 13.2 patch older than two weeks (13.2.5 / 13.3.0 are 2026-10-27). |
| `opentelemetry-sdk`, `-api`, `-exporter-otlp-proto-http` | 1.45.1 | 2026-10-06 | Newest SDK older than two weeks (1.45.2 is 2026-10-29). HTTP/protobuf exporter: no grpcio build. |
| `opentelemetry-instrumentation-fastapi`, `-httpx` | 0.66b1 | 2026-10-06 | The instrumentation beta that pairs with SDK 1.45.1. |

All three Python services share the pins; `tests/requirements.txt` lists them
(plus `opentelemetry-exporter-otlp-proto-grpc==1.45.1`, which chromadb
imports — removing the old 1.42.1 copy from the venv broke 13 health tests
until it was put back at the matching version).

## Test results (verbatim)

Final run, `./scripts/run_tests.sh -q`:

```
-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
835 passed, 2 skipped, 14 warnings in 12.66s
```

Before the build (HEAD 0aee46c, same venv): `818 passed, 2 skipped` after
the chromadb gRPC exporter was restored; the intermediate run with it
removed read `2 failed, 788 passed, 3 skipped, 9 warnings, 13 errors`, all
in `tests/test_health_agent.py` with
`ModuleNotFoundError: No module named 'opentelemetry.exporter.otlp.proto.grpc'`.

Dashboard query validation (every target run against Tempo's API, 3 h
window): `42 ok, 0 failed`, 40 of 42 returning data.

## Live verification

All through nginx (`localhost`), orchestrator rebuilt, nginx restarted each
time, HA and MA containers untouched (`Up 4 days` throughout).

| Path | Request | Answer | Trace |
|---|---|---|---|
| `/message` | "Reply with exactly: telemetry check one." | streamed tokens | `40e35609fbf40f8f0c3d347a23fa1025`, 22 spans (first build; ASGI receive/send spans since excluded) |
| `/v1/chat/completions` | "… check two." | `telemetry check two.` | `65bebd4e95fab1fdd1cb0607309d9417` |
| `/api/chat` | "… check three." | `telemetry check three.` | `5a7438b1a7ac977fcfe53079988bb4ac`, 10 spans |
| `/voice/api/chat` + origin stamp | "… check four." | `telemetry check four.` | `859a50661f165cd0326dd2419fd8a524`: `kronk.transport=voice`, `kronk.origin.device_id=1b6bd3b0…`, `kronk.origin.area=Basement Theater` |
| `/api/chat` music | "Play the artist Portishead on the basement voice pe ma" | "Now playing Portishead on the basement voice pe ma speaker." | `bad9680a9c8813c8583817c6eea0ef66`, 21 spans across both services |
| `/api/chat` control | "Pause the basement voice pe ma" | "Paused on the basement voice pe ma speaker." | `458290a75370c9dcc6e7344510c6c37a` |

The music trace, from `GET /api/v2/traces/bad9680a…` (durations, parent
indentation):

```
POST /api/chat [kronk-orchestrator] 24344ms
  pipeline.shim rid=b43cadac transport=ollama route=home 24343ms
    routing.decide rule=music 0ms
    agent.home 24327ms
      llm.gemma-4-e4b round=1 phase=plan_1 in=4013 out=280 tool_calls=[play_music] 4849ms
        POST host.docker.internal:8002/v1/chat/completions 2464ms
      tool.play_music 19454ms
        POST tool_service:8003/music 19448ms
          POST /music [kronk-tool-service] 19446ms
            POST host.docker.internal:8123/api/template 3ms
            music.resolve media_type=artist stage=1 candidates=6 best_score=1.05 outcome=winner 859ms
              ma.command music/search 858ms
            GET …/api/states/media_player.home_assistant_voice_0ab117 1ms
            music.play_verified media_type=artist targets=1 outcome=started 18574ms
              ha.call_service music_assistant.play_media [media_player.home_assistant_voice_0ab117] 17568ms
              GET …/api/states/… 3ms
```

Tempo search by rid, `GET /api/search?q={ span.kronk.rid = "b43cadac" }`
→ `['bad9680a9c8813c8583817c6eea0ef66']`. The `kronk.events` lines for the
same request carry `"trace_id": "bad9680a9c8813c8583817c6eea0ef66"`.

Voice (HA `assist_pipeline/run`, text start stage, `device_id` = basement
satellite; basement player checked idle before, paused after via Kronk):

| Utterance | HA answer | voice_tracer result |
|---|---|---|
| Play the artist Portishead on the basement voice pe ma | 0.3 s, blueprint: "Portishead playing on basement voice pe ma" | `voice.run` `fd0d47ee…` `handled_by=blueprint`, linked to `blueprint.run` `c259029c…` (`trigger_id=artist`, `outcome=played`, 0.32 s) and back |
| How many inches are in a foot | 1.2 s, Kronk: "There are 12 inches in a foot." | `voice.run` `e4d0540b…` `handled_by=kronk`, `kronk.trace_id=2cbbe53460e10a4471b89f1c88fd513f`, span link; that Kronk trace has `kronk.transport=voice`, same device id |
| Put on Portishead on the basement voice pe ma. That is all. | 4.9 s, blueprint catch-all → Kronk → "I couldn't play that. I don't know a speaker or room called 'Basement Theater'…" | `voice.run` `20cf6776…` `handled_by=blueprint_then_kronk`; Kronk trace `82f37a34…` not linked — the join ran after the hand-off decision. Fixed (`resolve_joins` joins the blueprint first; test `test_catchall_handoff_still_looks_up_the_kronk_trace`), redeployed; not re-exercised live. |

Metrics over the voice spans (after the inverted-block fix): `{ name =
"voice.run" } | quantile_over_time(duration, .5, .95)` → p50 0.54 s, p95
2.1 s over the five runs; counts over 3 h: 281 spans (orchestrator 216,
tool_service 44, voice_tracer 18), 25 pipeline roots, 5 voice runs, 3
blueprint runs.

Probes: `/probe/grafana`, `/probe/tempo`, `/probe/voice_tracer` → 200.
Grafana home redirects to the Overview dashboard; datasource health:
"Data source is working. Streaming test succeeded."

## Overhead (R7)

In-process microbenchmark (the span tree a typical request produces — root,
routing, agent, two generations with first-token and usage, one tool span
made current — under the real `BatchSpanProcessor`, 2,000 iterations):

```
instrumentation cost per request: p50 119 us, p95 180 us, max 1296 us
events.emit with trace_id: 3.6 us each
```

Live, telemetry on vs off (containers recreated with `TELEMETRY_ENABLED=false`,
same cached prompt, 8 × `/api/chat`, 6 × `/v1/chat/completions`):

```
on  api_chat  n=8 median 0.095s min 0.090s max 1.260s
on  openai    n=6 median 0.565s min 0.090s max 0.880s
off api_chat  n=8 median 0.495s min 0.090s max 0.650s
off openai    n=6 median 0.450s min 0.090s max 0.570s
```

The live numbers are LLM/prompt-cache noise (the "off" medians are higher);
they show no measurable overhead at 10 ms resolution. The microbenchmark is
the real figure: ~0.12 ms per request against a 5 ms budget. Export runs on
a background thread; a dead Tempo logs one line per failed batch.

## Disk and retention

`docker system df -v` at the end of the session: `kronk-telemetry_tempo-data`
3.4 MB (of which ~0.1 MB is trace blocks: 37 traces / 281 spans ≈ 2.8 KB
per trace, ~370 B per span; the rest is fixed WAL/shard overhead),
`kronk-telemetry_grafana-data` 503 MB (Grafana's own install data, fixed).

Projection: `data/metrics.db` holds 6,175 LLM rows over 212 days, about 29
LLM calls/day, so roughly 20–30 Kronk requests/day plus voice and blueprint
runs ≈ 30–40 traces/day ≈ 500 spans ≈ 200 KB/day. 90 days ≈ 20 MB of
blocks; call it under 200 MB with 10× headroom. Retention is configured at
`2160h` on both the backend scheduler and worker compaction blocks.

Langfuse volumes, still on disk for the operator's decision:
`kronk_langfuse_clickhouse_data` **128.9 GB**, `kronk_langfuse_clickhouse_logs`
2.5 GB, `postgres` 46 MB, `minio` 35 MB, `redis` 1 MB — 131 GB in all, for
1,366 traces.

## Deviations from the plan, and findings

- **Grafana on :3000**: Langfuse was stopped at step 1 (plain `stop`, later
  `down` without `-v`) so Grafana could take the port the services page
  already knew.
- **Tempo reaches the Kronk stack over the `kronk_kronk` network** (external
  in the telemetry compose) rather than a host port: a loopback-published
  port is not reachable from bridge containers, and a LAN port violated the
  plan. Side effect: `docker compose down` on the Kronk stack reports the
  network in use while Tempo is attached (documented).
- **Anonymous Viewer in Grafana** so the dashboards are one click on the
  LAN; the admin login is only for editing. Operator may turn this off
  (`GF_AUTH_ANONYMOUS_ENABLED`).
- **Inverted Tempo blocks**: back-dated spans beyond the 2 m default slack
  produced blocks with `startTime > endTime`, which made every overlapping
  metrics query fail with 500. Fixed by raising the slack to 15 m on all
  three WALs, cutting voice_tracer's backfill to 10 minutes, and removing six
  test-data blocks with Tempo stopped (the 21:11 Anime Pop run among them).
  Incident doc above.
- **Search lag**: trace-by-id is immediate; search/dashboards see a trace
  after the live-store cuts a block (30–90 s). voice_tracer therefore delays
  its emission up to ~60 s for the join; voice traces appear ~2 min after
  the utterance.
- **Fixed coarse `step`** in the per-hour panels dropped edge buckets
  (1 h step over 3 h counted 50 of 281 spans); the dashboards use Tempo's
  dynamic step now and the titles say "over time".
- **Tempo trims leading zeros from trace ids in search results** (a 31-char
  id came back); voice_tracer pads to 32.
- **Catch-all hand-off finding (pre-existing, not changed)**: the blueprint's
  catch-all appends the satellite's area ("… in the Basement Theater") to the
  text it hands Kronk, and `resolve_players` does not know that area, so the
  request failed with "I don't know a speaker or room called 'Basement
  Theater'". The earlier incident test named the player explicitly and
  passed. Worth a look in the music backlog.
- **Two early voice traces carry a wrong label**: runs `01M6YK9C2P…` and
  `01M6YK9CGV…` (22:08) were emitted with `handled_by=blueprint` because the
  first join used a ±0.5 s slack; they are in Tempo as-is (deterministic ids,
  so re-emitting would not replace them). Fixed in `join_blueprint`
  (start must fall inside the intent window; one voice run per blueprint
  run) with tests.
- **Tempo log noise**: in single-binary mode the backend worker logs
  `error calling scheduler … no jobs found` every ~15 s. Harmless; left at
  `log_level: warn`.
- `metrics.db` and `/performance` keep working untouched. Grafana's Stages
  dashboard now duplicates them (TTFT, tokens/s by model) with history
  beyond 50 rows; retiring them is a separate decision.

## Operator to-dos

1. **Real "Okay Nabu" test** (the only path that produces `voice.stt`
   spans; everything above used text runs). Say, at the basement or kitchen
   Voice PE: "Okay Nabu, play the artist Portishead on the basement voice pe
   ma" (blueprint fast path), then "Okay Nabu, how many feet are in a mile"
   (Kronk). About two minutes later, Grafana → Explore → Tempo → Search
   `{ name = "voice.run" }` should show two runs with `voice.stt`,
   `voice.stt.listen`, `voice.stt.transcribe`, `voice.intent`, `voice.tts`
   children, `ha.satellite_id` set, `voice.handled_by` = `blueprint` then
   `kronk`, and on the second a link to the Kronk trace. Then pause.
2. **Grafana login**: `.env.telemetry` holds `GF_ADMIN_USER` / `GF_ADMIN_PASSWORD`
   (generated). Change the password in the UI if you like; the env file only
   seeds the first start.
3. **Langfuse volumes**: 131 GB, five volumes named `kronk_langfuse_*`.
   `docker volume rm` them when you no longer want the v1 traces, or keep
   them; nothing references them. `.env.langfuse` and the `LANGFUSE_*` keys in
   `.env` are untouched and now unused.
4. Optional: turn off anonymous viewing in Grafana; pin the Overview
   dashboard; decide whether `/performance` stays.

## Not verified

- Spoken wake → STT spans (needs a real utterance; see to-do 1).
- The dashboards in a browser: every query was validated against Tempo's
  API and one through Grafana's `/api/ds/query`; panel rendering and the
  trace-ID links in the tables were not clicked.
- The catch-all → Kronk join after the fix (code path tested; not
  re-exercised live).
- Join ambiguity: none seen (every joined run matched exactly one Kronk
  trace); `kronk.join_ambiguous` is set and logged if it ever happens.
- Behaviour when Tempo is down for a long time (export drops and logs; not
  exercised).

## Suggested commit boundaries

1. `Telemetry v2: Tempo + Grafana stack` — `docker-compose.telemetry.yml`,
   `telemetry/tempo.yaml`, `telemetry/grafana/provisioning/`,
   `.env.telemetry.example`, `.gitignore`.
2. `Orchestrator: OpenTelemetry replaces Langfuse` — `orchestrator/telemetry.py`,
   `agents.py`, `main.py`, `events.py`, `requirements.*`, `docker-compose.yml`
   (orchestrator env), `tests/test_telemetry.py`, `tests/requirements.txt`.
3. `tool_service: spans for MA, HA, resolver, verify wait` — `tool_service/*`,
   `docker-compose.yml` (tool_service env), `tests/test_tool_service_telemetry.py`.
4. `voice_tracer: HA voice and blueprint runs as traces` — `voice_tracer/`,
   `docker-compose.yml` (service), `tests/fixtures/`, `tests/test_voice_tracer.py`.
5. `Grafana dashboards` — `telemetry/grafana/build_dashboards.py`,
   `telemetry/grafana/dashboards/`.
6. `Retire Langfuse; telemetry docs` — deletions, `nginx/nginx.conf`,
   `orchestrator/static/services.html`, `scripts/run_tests.sh`, docs, ROADMAP,
   CLAUDE.md, README, incident doc, this report.

`docs/incidents/INVESTIGATION_2026-11-08_resources_page.md` in the tree is
the other agent's and is not part of this work.
