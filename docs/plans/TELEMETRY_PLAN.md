# Telemetry — Plan (Langfuse self-hosted)

Adding real telemetry for the Kronk orchestrator pipeline so we can find and
fix latency bottlenecks stage-by-stage, and so the work doubles as exposure
to a current piece of the LLM-observability ecosystem.

Status: **Phases 1-2 executed 2026-06-10** (deploy + instrumentation +
end-to-end verification). Operator explicitly accepted deploying inside the
post-Vulkan-fix watch window ("kronk is low risk, I accept the risk") —
the stability gate below is recorded as waived, not satisfied.
Phases 3-4 (validation queries, dashboards) remain.

Implementation notes vs. this plan:
- Langfuse v3.181.0 server — 6 containers (web, worker, ClickHouse,
  Postgres, Redis, MinIO), pinned tags, ~1.7 GB idle actual. Stack file:
  `docker-compose.langfuse.yml`, secrets in `.env.langfuse` (gitignored).
- ClickHouse HTTP remapped to host port 8124 (HA owns 8123 on host net).
- Python SDK `langfuse==4.7.1`; all SDK contact isolated in
  `orchestrator/telemetry.py`; spans are explicit parent→child objects
  (no ContextVar propagation — pipeline runs are serialised by _llm_lock).
- `LANGFUSE_ENABLED=false` on the orchestrator turns every helper into a
  no-op (verified live: a missing SDK during the build produced one log
  line and zero pipeline impact).

---

## Goal, in one paragraph

Capture per-stage timing for every pipeline run (route decision → tool calls
→ llama.cpp inference → tool exec → response assembly), persist it to a
queryable store, and surface it in a UI that answers "which stage is slow,
how is its p95 trending, and what does a slow trace look like end-to-end."
Primary use case is **profiling** — we are optimizing the pipeline and need
data to choose what to work on next. Secondary use is operational
observability (error rates, throughput).

---

## Why Langfuse self-hosted (recap)

Decision made in the 2026-06-09 conversation. Options considered:

- **Hand-built SQLite + small UI** — minimal weight, full schema control,
  zero new containers. Rejected as the primary path because Kronk is also a
  vehicle for AI-ecosystem exposure, and rolling our own duplicates work
  that purpose-built tooling already does well. May still be the right
  answer eventually if Langfuse proves too heavy in practice — schema can
  be migrated later because the data model is similar.
- **LangSmith (SaaS)** — best UI for nested timing, but: prompts/completions
  leave the box (privacy), free tier ceiling (~150-300 runs/day), needs
  internet (Kronk's stability is currently in question). Self-host is
  enterprise-tier only. Rejected.
- **OpenTelemetry + Tempo/Prometheus/Loki + Grafana** — most portable, but
  4-5 containers and ~3-4 GB idle is overkill for one operator, and the
  query DSLs (TraceQL/PromQL/LogQL) are less ergonomic for ad-hoc
  bottleneck hunting than SQL. Rejected for now.
- **Langfuse self-hosted** — purpose-built for LLM apps, OTel-compatible
  underneath, ~3 containers / ~1.5 GB idle, direct Postgres + ClickHouse
  access if we want to drop to SQL, prompts/completions stay on the box.
  **Chosen.**

Practical mental model: Langfuse is a hierarchical span store with an
LLM-aware UI on top. A *trace* is one pipeline run; *observations* (spans /
generations) are the stages. The UI gives waterfall views, latency
dashboards, percentile tracking, and trace-comparison out of the box.

---

## Architecture

```
┌──────────────────────────┐
│ orchestrator (FastAPI)   │
│   @observe-instrumented  │──── OTLP/HTTP ───► langfuse-web :3000
│   stages emit spans      │                       │
└──────────────────────────┘                       │ reads/writes
                                                   ▼
                              ┌──────────────────────────────┐
                              │ langfuse-postgres            │ ← metadata
                              │ langfuse-clickhouse          │ ← traces/events
                              │ langfuse-worker (background) │
                              └──────────────────────────────┘
```

Three (technically four) containers:

- **langfuse-web** — Next.js UI + API. Port 3000. This is what we look at.
- **langfuse-worker** — background ingestion / async eval workers.
- **langfuse-postgres** — metadata (users, projects, dashboards, prompts).
- **langfuse-clickhouse** — high-volume events (traces, observations,
  scores). This is where the bottleneck-hunting SQL would live if/when we
  drop below the UI.

All wired together on an internal Docker network. UI port published on
loopback only (`127.0.0.1:3000`), reachable via the same nginx pattern we
use for the other services if we want HTTPS / kronk.local routing.

---

## Why a separate compose stack

Same reasoning as `kronk-ha` (2026-05-24) and `kronk-ma` (2026-05-31):

- Langfuse's lifecycle is independent of orchestrator rebuilds. We do not
  want a `docker compose up` on the main stack to restart Postgres or
  ClickHouse.
- Langfuse stores **state we cannot afford to lose** (Postgres + ClickHouse
  volumes). Isolating it means no accidental `down -v` cross-fire.
- Pulls big optional deps. Rebuilds on the main stack should not touch it.

File: `docker-compose.langfuse.yml`. Follows the standing Operations
comment block convention (up/restart/logs/down recipes at the top of the
file).

---

## Phases

### Phase 1 — Deploy infrastructure (no orchestrator changes yet)

Goal: get Langfuse running and reachable, prove the volumes survive a
reboot, prove the UI is sane. **No instrumentation in this phase** — we
want to validate the bottom of the stack before tying our orchestrator to
it.

Steps:

1. Write `docker-compose.langfuse.yml` based on the upstream "self-hosting
   Docker Compose" recipe. Pin image tags (don't use `:latest` — Langfuse
   has schema migrations between versions and we want reproducible
   restarts).
2. Add `langfuse-postgres-data` and `langfuse-clickhouse-data` named
   volumes. **Document explicitly** in the file header that these contain
   irreplaceable state.
3. Generate the secrets Langfuse needs (`NEXTAUTH_SECRET`, `SALT`,
   `ENCRYPTION_KEY`, ClickHouse password, S3 secret if using built-in MinIO
   for blob storage). Store in `.env.langfuse` (gitignored), not committed.
4. `docker compose -f docker-compose.langfuse.yml up -d`.
5. Verify: hit `http://localhost:3000`, create an admin user, create a
   project, generate a project API keypair (public + secret) for the
   orchestrator. Save these in `.env` for the orchestrator to read.
6. Reboot test: `sudo reboot`, confirm Langfuse comes back up healthy and
   the project + keys persist. (Note: containers need `restart: unless-stopped`.)
7. **Stop here for a day.** Let it idle and confirm it doesn't add memory
   pressure or perfwatch alerts.

Exit criteria for Phase 1: Langfuse running, surviving reboots, no impact
on system stability.

### Phase 2 — Instrument the orchestrator, one stage at a time

Goal: capture timing for every pipeline stage, with the right tag/metadata
shape to slice by model, route, tool, and request shape.

The orchestrator's current module split makes this easy — each module is a
natural span boundary:

| Module / function | Span name | Span type | Captures |
|---|---|---|---|
| pipeline entrypoint (in `main.py`) | `pipeline.run` | trace root | total latency, user input excerpt, final response |
| `routing.choose_route(...)` | `routing.decide` | span | which route was picked, why |
| `agents.run_agent(...)` | `agent.<name>` | span | tools called, hops |
| llama.cpp HTTP call (in `llm.py` / `servers.py`) | `llm.<model>` | **generation** | model, prompt, completion, prompt_tokens, completion_tokens, TTFT, total |
| `tools.<tool>` calls | `tool.<name>` | span | tool args, tool result size |
| any retrieval / search step | `retrieve.<source>` | span | query, hit count |

Implementation pattern:

1. Add `langfuse` to `orchestrator/requirements.txt`. Pin the version.
2. Initialize the client once at orchestrator startup (FastAPI lifespan in
   `main.py`).
3. Use the `@observe` decorator on the existing stage functions where
   possible — minimal code churn.
4. For LLM HTTP calls, use **manual generation spans** wrapped around the
   `httpx` call, so we can capture TTFT separately from total. The decorator
   only sees function entry/exit; the interesting LLM number (TTFT) is in
   the middle of the call.
5. Pass `metadata={"route": ..., "model": ..., "user": ...}` and `tags=[...]`
   on each observation so the UI's slice/filter actually works.
6. Add a `request_id` link between Langfuse's `trace_id` and the existing
   `events.current_request_id`. Means we can cross-reference Langfuse traces
   with the orchestrator's log lines.

Roll out incrementally — one module per change. Verify each module's spans
show up correctly in the UI before moving to the next. Do **not** ship a
single mega-PR that instruments everything; the iteration is faster
small-batch.

Exit criteria for Phase 2: every pipeline run produces a trace with a clean
parent/child hierarchy, generation spans carry token counts + TTFT, and
filter-by-tag works in the UI.

### Phase 3 — Validate the data answers the bottleneck questions

Goal: prove the telemetry is useful for its actual purpose before we invest
in dashboards.

Manually run each of these queries against a week of real data:

- "Show the 20 slowest traces in the last 24h, ranked by total latency."
- "For each stage, p50 / p95 / p99 over the last 24h."
- "Which stage dominates the p95 of total latency?"
- "Compare today's p95 of `llm.gemma3-4b` to last week's."
- "Show all traces where `tool.<name>` errored."
- "For traces that used the `bonsai` route, what's the median `routing.decide`
  latency?"

If any of these is awkward in the UI, the answer is one of:

- Add a tag/metadata field we forgot to capture (cheap — change in
  orchestrator, redeploy, get going).
- Drop to SQL via direct ClickHouse access (Langfuse uses `traces` and
  `observations` tables in its `default` database — both are queryable).

Exit criteria for Phase 3: each of the questions above has a known
one-click or one-query answer.

### Phase 4 — Dashboards

Goal: make the recurring views one click away.

Langfuse has a built-in dashboards feature. Build:

1. **Pipeline health** — total trace count, error rate, p95 total latency,
   last 24h.
2. **Stage timing** — bar/line per stage of median + p95 latency, last 7d.
3. **Model usage** — call count + median latency per model, last 24h.
4. **Tool usage** — call count + error rate per tool, last 7d.

If Langfuse's dashboarding turns out to be limited (it is opinionated), the
escape hatch is Grafana with a ClickHouse data source pointed at the same
Langfuse ClickHouse instance, read-only.

---

## Open questions to revisit at each phase boundary

- **Sampling.** Do we need it? At one operator's volume, almost certainly
  no — keep 100% capture. Revisit if storage growth bites.
- **PII handling.** Voice transcriptions and finance/retirement data flow
  through the orchestrator. Decide before Phase 2: capture full prompts/
  completions, or redact at the boundary? Capturing them makes debugging
  easier; redacting is more conservative. **Tentative**: capture in full
  for now — data stays on the box, single operator, no shared access.
  Revisit if we ever expose Kronk beyond the LAN.
- **Retention.** ClickHouse will grow. Default Langfuse retention is
  forever. Pick a number (90 days?) before Phase 4 and configure a TTL on
  the ClickHouse `observations` table.
- **Resource cap.** Set `MemoryHigh` / `MemoryMax` on each Langfuse
  container in the compose file so they can't run away. Same pattern as
  the user systemd services.
- **Backups.** Postgres has the project config + dashboards — losing it
  means re-creating from scratch but not losing trace data (that's in
  ClickHouse). Decide: nightly `pg_dump` to a host volume? Probably yes,
  small file, cheap insurance.

---

## Stability gate (do not skip)

This plan does **not** ship until the silent-hang issue is resolved.
Concretely:

- Bluetooth integration removed (done 2026-06-09).
- Hardware watchdog armed (done 2026-06-09).
- perfwatch + memwatch + bootnotify all active (done 2026-06-09).
- **At least one boot must clear +24h uptime with zero `perfwatch` alerts.**
  If the BT theory is right, we should see this within a few days. If we
  see another silent hang or a `perf interrupt latency rising` alert
  before +12h, the root cause is not BT and adding 1.5 GB of telemetry
  containers during an active stability incident is the wrong order of
  operations.

Adding Langfuse before stability is restored would just confuse the
investigation — its containers themselves would become suspects for any
new hang.

---

## What ships when we execute this plan

- `docker-compose.langfuse.yml` (with the Operations comment block).
- `.env.langfuse` (gitignored) — Langfuse-internal secrets.
- Additions to `.env` — orchestrator's Langfuse project API keys.
- `orchestrator/requirements.txt` — `langfuse==X.Y.Z`.
- Edits across `orchestrator/main.py`, `agents.py`, `llm.py`, `routing.py`,
  `tools.py` — `@observe` decorators + manual generation spans on llama.cpp
  calls.
- Possibly a new `orchestrator/telemetry.py` for shared instrumentation
  helpers (TTFT timing context manager, generation-span boilerplate) so the
  pattern doesn't get copy-pasted.
- This doc, updated with "executed YYYY-MM-DD" status when done.
