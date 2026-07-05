# Feature: Telemetry v1 (Langfuse) — prototype

**Shipped:** 2026-06-10 (deploy + instrumentation), dashboards + guide 2026-06-12 · **Plan:** `../plans/TELEMETRY_PLAN.md` · **Operator's guide:** `../TELEMETRY_GUIDE.md`

> **Status: prototype.** It works and is in daily use, but ROADMAP item 6
> (Telemetry v2) puts a requirements-first redesign on the table — up to and
> including replacing Langfuse. Treat this doc as "what v1 does," not "what
> telemetry will be."

## What it does

Every pipeline run — chat UI (`pipeline.message`) or voice/shim
(`pipeline.shim`) — becomes a Langfuse trace with a span waterfall: route
decision, each tool call, each llama.cpp inference, response assembly, with
full prompts/completions/token counts per span. UI at `http://kronk:3000`.
The "why was that one slow / what did the router actually see" workflow is
two clicks (Tracing → sort by latency → open the waterfall).

## How it works

- **Server:** self-hosted Langfuse v3 — 6 containers
  (`docker-compose.langfuse.yml`; web, worker, ClickHouse, Postgres, Redis,
  MinIO), ~1.7 GB idle. ClickHouse HTTP remapped to :8124 (HA owns :8123).
  Secrets in `.env.langfuse` (gitignored).
- **Instrumentation:** all SDK contact isolated in
  `orchestrator/telemetry.py`; spans are explicit parent→child objects (no
  ContextVar magic — runs are serialized by `_llm_lock` anyway).
  `LANGFUSE_ENABLED=false` turns every helper into a no-op; a missing SDK
  produces one log line and zero pipeline impact.
- **Dashboards:** seeded programmatically via Langfuse's Postgres
  (`scripts/`), because there is no public dashboards API.

## Gotchas

- Dashboards answer "how is the system trending"; **Tracing** answers "what
  happened on that one run" — operators reach for the wrong one first
  (that's why the guide exists).
- Traces were the deciding evidence for the 2026-07-03 voice-path router 400
  (the duplicated-user-turn bug) — the troubleshooting loop works, but
  finding *the trace for a spoken error* still takes manual timestamp
  correlation. That gap is a named requirement for v2 (ROADMAP items 2 + 6).

## Blog hooks

- Instrumenting a home assistant with self-hosted Langfuse (already tracked
  in `../BLOG_TOPICS.md`) — including the undocumented dashboard-seeding.
- The SDK-isolation pattern: instrumentation that can never break the app.
