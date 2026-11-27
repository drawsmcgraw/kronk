# M38 pass criteria: the status probe stops running a completion on every model server

Source: decision #16 (M25, 2026-11-25): "/services' orchestrator dot and
deploy_tag.sh's check run a completion on every model server through
/api/status (Talkie's on the CPU included). A candidate for its own
item". Drew, 2026-11-26: "Item: cheap probe".

Handoff line: `/api/status` says whether the orchestrator, LiteLLM,
tool_service and each model server are up without asking any model for
a completion, so a page left open no longer empties Gemma's prompt
cache every 30 seconds.

Plan doc: this file. Tier 2 (a route on production, not the request
pipeline). No design review.

## What the investigation found (coordinator, 2026-11-26)

- `orchestrator/main.py` `health_probe` (`GET /api/status`) calls
  LiteLLM's `GET /health` and tool_service's `/health`. LiteLLM's
  `/health` sends a 5-token completion (`max_tokens: 5`, no cache) to
  every model in `litellm/config.yaml`: `gemma-4-e4b` and `talkie`.
  The call takes about 0.4 s and goes past the orchestrator's model
  locks.
- Callers: the chat page's liveness dot (`index.html`, every 30 s while
  open), `/services` through nginx's `/probe/orchestrator` (every 30 s
  while open), `deploy_tag.sh`'s check, `voice_replay.py` (before every
  request; it reads `tool_service.music_dry_run`), `m9_lock_bench.py`.
- Measured on dev, home route, three requests in one minute and then
  one `/api/status` call: the first round cached 4,172 of 4,173 tokens
  (1.46 s), then after the one probe 0 of 4,173 (4.68 s). A first try
  with six probes gave the same: 4,172 cached, then 0. The probe's
  completion takes the slot that held the home prompt. With the chat
  page open, the next request pays a cold prompt at most 30 s after any
  probe, whatever M37 does.
- `deploy_tag.sh` already runs a real completion through the `/api/chat`
  shim after `/api/status`, so the deploy check does not need the probe
  to run one.

## Plumbing (coordinator's decisions)

1. LiteLLM: `/health/liveliness` (the proxy answers) in place of
   `/health`.
2. Model servers: up or down from what the host exporters already write
   (`servers.py`, `llama_props_source()`: each server's port, read on a
   timer), with the file's age; a server missing from the file, or a
   file older than its stated limit, is reported down or unknown, by
   name. No request to a model server from the orchestrator.
3. The answer keeps every key a caller reads today:
   `status`, `tool_service` (with `music_dry_run`), and an
   `llm_service` object; what changes inside `llm_service` is
   documented in `docs/features/`. The status code stays 200 when all
   are up and 503 when one is down, as the chat page's dot reads it.
4. Nothing else in the route's behaviour changes; no new route.

## Criteria

1. Unit: the route makes no request to LiteLLM's `/health` and none to a
   model server; a server down in the exporter's file, or a stale file,
   gives 503 with its name.
2. On dev, home route, three requests in one minute, then ten
   `/dev/api/status` calls, then a fourth request in the same minute:
   the fourth caches at least 4,000 tokens; five runs, quoted. Before
   the fix the same run gave 0.
3. `llama-talkie`'s and `llama-gemma4-e4b`'s journals show no request
   from ten status calls.
4. The callers still work: the chat page's dot on dev, `/dev/services`'
   chat card, `voice_replay.py --check-only` (reads `music_dry_run`),
   `m9_lock_bench.py`'s status read (by reading, if running it is not
   wanted).
5. The suite passes; last lines quoted.
6. By the coordinator at promotion: `deploy_tag.sh` passes its check;
   the measurement of criterion 2 once on production with nothing
   playing.

## Out of scope

- `/services`' other probes, and their intervals.
- The model locks.

## Rulings

- 2026-11-27, coordinator, on `M38-build.md` points 1 to 3: a model
  server down, or the exporters' file stale past its limit, answers 503
  with the server named, as plumbing 3 says. Before, LiteLLM's `/health`
  answered 200 with a dead server listed as unhealthy, so the old route
  hid it. With Talkie stopped the chat dot goes red and `deploy_tag.sh`'s
  check fails: that is the intended reading (tenet 7). A model meant to
  be stopped leaves the catalog (`tests/test_m24_catalog.py`). Health
  lags the server by up to the props timer's 60 s; accepted.
