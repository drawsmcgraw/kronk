# Kronk Telemetry — Operator's Guide

One page on how to actually use the Langfuse deployment. Written 2026-06-12
after operator feedback that the dashboards alone don't show what one
pipeline run looks like.

Langfuse UI: **http://kronk:3000** (login: see `.env.langfuse`; password is
whatever you last set with `scripts/langfuse_set_password.sh`).

---

## The #1 thing: seeing the stages of ONE pipeline run

That view is **Tracing**, not Dashboards.

1. Left sidebar → **Tracing**. Every row is one pipeline run (a "trace").
   Columns show the input, total latency, and timestamp.
2. Click any row → the **waterfall/tree view**: every stage of that run as
   a nested bar, sized by duration. Click a bar to see its full input/output
   (prompts, completions, tool args/results) and token counts.
3. Sort the list by **latency** to find your slowest runs; that's the
   "why was that one slow?" workflow in two clicks.

## What the span names mean

| Span | What it is |
|---|---|
| `pipeline.message` | One whole run from the web UI chat |
| `pipeline.shim`    | One whole run from voice/HA or any OpenAI/Ollama client |
| `routing.decide`   | Phase-1 route choice (metadata shows which rule fired: regex shortcut or `llm`) |
| `llm.router`       | The router model call inside routing (gemma-3-4b) |
| `agent.<name>`     | A specialist agent's whole tool-calling loop (home, research, …) |
| `llm.<model>`      | One LLM round inside an agent — metadata shows round number and phase (`plan_N` = deciding/calling tools, `synthesis` = writing the answer) |
| `tool.<name>`      | One tool execution (get_weather, web_search, …) |
| `llm.coordinator`  | Direct-answer path (no agent) or fallback synthesis |

Reading a typical weather run, before the 2026-06 optimizations:
`pipeline.message → routing.decide(+llm.router) → agent.home →
llm.gemma-4-e4b (plan) → tool.get_weather → llm.gemma-4-e4b (synthesis)`.
After weather-context injection the `tool.get_weather` and second LLM round
disappear — that's the speedup, visible in the waterfall.

## Generations: the LLM-specific numbers

Click any `llm.*` span:
- **Time to first token** — derived from `completion_start_time`; this is
  prompt-processing + queueing. (Tool-call-only rounds have no TTFT — the
  model emits no visible tokens.)
- **Usage** — input/output token counts. Output tokens × ~1/50 s is a good
  mental model for generation time on this box.
- **Metadata** — round number, phase, route.

## Dashboards (the aggregate views)

Left sidebar → **Dashboards**:
- **Kronk — Pipeline Bottlenecks**: stage-level p95/avg latency (which stage
  to optimize next), TTFT by model, tokens/sec, token usage, call volume.
  Stage charts exclude the `pipeline.*` rows where filtered — totals would
  otherwise dominate every chart.
- **Kronk — Pipeline Health**: volume over time, error levels (anything not
  `DEFAULT` deserves a look), overall latency trend.

Time-range selector is top-right; widgets are editable (pencil icon) if you
want different aggregations.

## Escape hatch: raw SQL

Everything the UI shows lives in ClickHouse:

```bash
# password: CLICKHOUSE_PASSWORD in .env.langfuse
docker exec -it langfuse-clickhouse clickhouse-client --user clickhouse --password '<pw>'
```

```sql
-- p95 latency by stage, last 24 h
SELECT name,
       count() AS calls,
       round(quantile(0.95)(date_diff('millisecond', start_time, end_time))) AS p95_ms
FROM observations
WHERE start_time > now() - INTERVAL 1 DAY AND end_time IS NOT NULL
GROUP BY name ORDER BY p95_ms DESC;

-- slowest traces, last 24 h
SELECT id, name, date_diff('millisecond', timestamp, now()) AS age_ms
FROM traces WHERE timestamp > now() - INTERVAL 1 DAY
ORDER BY id DESC LIMIT 20;
```

Tables: `observations` (spans/generations — has `usage_details`,
`provided_model_name`, `completion_start_time`) and `traces`, in database
`default`.

## Operational notes

- Stack: `docker-compose.langfuse.yml` (ops recipes in its header comment).
  Never `down -v` — the volumes are the data.
- The orchestrator's instrumentation is `orchestrator/telemetry.py`;
  `LANGFUSE_ENABLED=false` in the compose env turns it all into no-ops.
- Traces appear ~5-10 s after a request (batched ingestion).
