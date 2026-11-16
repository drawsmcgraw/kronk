# M1 pass criteria: remove the health and finance features

Written by the sprint coordinator on 2026-11-15, before the build, from
`Kronk evaluation findings and implementation plan.md` (section "M1. Remove
the health and finance features", rows C7 and C8 and the M1 line of
"Verified on Kronk, 2026-11-15", and "Drew's review"). The tester checks
against this file, not against the build report.

## Scope

In: the `health` and `finance` agents and their tools; `health_service`,
`finance_service` and `retire_calc`; the `/health`, `/finances` and `/retire/`
pages and their nginx routes; the upload forwarding to `health_service`;
tests, benches and docs that reference them; a deletion manifest for the
stored data.

Out: the hot tub feature (M1b, next item); `data/postgres` (owner unknown,
never touched); the sibling repo `../retirement-calc` (never touched); any
other agent or tool.

**Split between build and promotion.** `./data` is shared by dev and
production, and production still runs the three services until this item is
promoted. So the build agent removes code and proves it on the dev path, and
writes the deletion manifest. The data deletion and the removal of the three
production containers are done by the coordinator at promotion, after the
end-to-end test passes. Neither the build agent nor the test agent deletes
anything under `./data` or stops a production container.

## Criteria

### A. Orchestrator

1. `AGENTS` has no `health` or `finance` entry; `HEALTH_AGENT_MODEL` and
   `FINANCE_AGENT_MODEL` are gone; `agent_tool_defs()` offers no
   `ask_health` or `ask_finance`. The coordinator prompt no longer names
   health or finances as something it can delegate.
2. `tools.py` has no `query_health`, `query_finances`, `query_bloodwork` or
   `search_health_data` (definitions, handlers, dispatch table, timeouts,
   narration lines).
3. `main.py` has no `/finances` page route, no `/health` page route, no
   `_forward_to_health` and no Garmin, zip or LabCorp upload detectors. An
   upload of a file type that used to be forwarded now gets a clear
   refusal or the normal upload path, not a 500.
4. `grep -rniE 'health_service|finance_service|retire_calc|query_health|query_finances|query_bloodwork|search_health_data|ask_health|ask_finance'`
   over `orchestrator/`, `tool_service/`, `nginx/`, `docker-compose.yml`,
   `scripts/`, `tests/`, `litellm/` and `kronk-context.md` returns nothing,
   or only lines the build report lists and justifies (history notes,
   negative tests).

### B. Compose, nginx, UI

5. `docker-compose.yml` has no `health_service`, `finance_service` or
   `retire_calc` service, no `depends_on` or env var pointing at them, in
   production or dev services. `docker compose config` is valid with and
   without `--profile dev`.
6. `nginx.conf` has no `/api/health/`, `/api/finance/`, `/probe/health`,
   `/probe/finance`, `/retire/` or `/probe/retire` location. On the dev
   path those URLs under `/dev/` do not exist either. `nginx -t` passes
   against the new file.
7. The static pages `health.html` and `finances.html` are gone;
   `services.html` and `agents.html` show no health, finance or retirement
   entry; nothing in `static/` links to a removed page.
8. The chat UI's status dot polls `/api/status` (C7), not `/health`. Shown
   on the dev path: the request the page makes returns 200.
9. The health service's own liveness endpoints for containers and probes
   that survive (orchestrator `/health` JSON if compose healthchecks use
   it) still work. The build report says exactly what `GET /health` returns
   now and what depends on it.

### C. Tests, benches, corpus

10. The eight health and finance test files are deleted
    (`test_bloodwork_parser`, `test_health_agent`, `test_health_rag`,
    `test_finance_service`, `test_positions_api`, `test_positions_importer`,
    `test_statement_pdf`, `test_extractor`). The generic agent-loop tests
    that used the health agent as a fixture (`tests/test_agentic_loop.py`
    and any others) are ported to a surviving agent or a neutral fixture,
    not deleted: the count of agent-loop test cases does not drop. The
    build report gives test counts before and after and accounts for every
    removed test.
11. `./scripts/run_tests.sh` passes. Baseline before M1:
    `1111 passed, 2 skipped, 18 xfailed`. Skips and xfails are unchanged or
    explained.
12. Health and finance rows are dropped from `docs/bench/routing_eval.jsonl`
    and the bench scripts' probes (`scripts/coordinator_model_bench.py`,
    `scripts/routing_bench.py`). The voice corpus and its offline test still
    pass.
13. New coordinator bench probes exist: "how did I sleep last night" and
    "what's my net worth". Run on the dev path, several times each, the
    answer is a plain statement that Kronk does not have that information.
    No invented figure, no claim to have looked it up, no delegation to an
    unrelated agent that then makes something up. The report records each
    answer verbatim.
14. The coordinator bench, run against dev before and after, shows
    delegation accuracy for the surviving agents is not worse. Numbers for
    both runs are in the report.
15. The M10 live replay on dev (`scripts/voice_replay.py`) matches the
    committed baseline apart from known run-to-run variance; every
    differing row is listed and explained.

### D. Docs

16. README inventory, `kronk-context.md`, `TECH_DEBT.md` (the Garmin and
    Withings stub entry) and `CLAUDE.md` no longer describe health, finance
    or retirement features as present. `ROADMAP.md` no longer carries
    "Financial expert, phase 2" or "Health RAG completion". Plan and
    feature docs for the removed features are moved to history
    (`docs/` history location, with a status header saying removed and
    when), not deleted.
17. `scripts/deploy_tag.sh` no longer links `../retirement-calc` into the
    releases directory.

### E. Data and containers (prepared by the build, executed at promotion)

18. The build report holds a deletion manifest: every path to delete, its
    size and file count as measured, and what it is. It covers
    `data/health.db`, `data/chroma/`, `data/finance/`, `data/retire/`,
    `data/withings_tokens.json`, `data/garmin_jwt_web.txt`,
    `data/garmin_login_debug.png`, and anything else the removed code wrote
    that the agent found (named, with evidence that only removed code used
    it). It lists Docker named volumes and images belonging to the three
    services. It states plainly that `data/postgres` and
    `../retirement-calc` are excluded.
19. Nothing under `./data` was deleted during build or test, and no
    production container was stopped, removed or recreated. Checked by the
    tester from file listings and container ids.
20. The build report gives the exact promotion commands: deploy from the
    tag, remove the three stale production containers by name (no
    `--remove-orphans`, which could take the dev pair), delete the data,
    and the checks that prove production is healthy afterwards.
21. No secret that only the removed services used remains wired:
    `.env` and `.env.dev` keys used only by health, finance or retirement
    code are listed in the report for Drew to remove (the agents do not
    edit `.env`).

### F. Dev path end to end

22. On the dev path with the dev pair rebuilt from the branch: `/dev/api/chat`
    and `/dev/voice/api/chat` answer a general question, a weather or
    search question and a dry-run music request as before.
23. `/dev/` requests for the removed pages and APIs return 404, not a 502
    or a stack trace.

## Reports

Build: `docs/sprint/M1-build.md`. End to end: `docs/sprint/M1-e2e.md`, one
line per criterion with pass, fail or not testable, and the evidence.
