# M1b pass criteria: remove the hot tub feature

Written by the sprint coordinator on 2026-11-15, before the build, from
`Kronk evaluation findings and implementation plan.md` (section "M1b. Remove
the hot tub feature" and the M1b line of "Verified on Kronk, 2026-11-15").
The tester checks against this file, not against the build report.

## Scope and split

In: the `query_hottub` tool and every mention of the hot tub in the `home`
agent; the `/hottub` route in `tool_service`; `hottub_monitor/`; docs, bench
rows and probes; a manifest for the host unit and `data/hottub/`.

Corrections from the verification pass: there is no narration line for
`query_hottub`, no `geckolib` in `tool_service` (no lockfile change), and no
hot tub entry in `kronk-context.md` or `services.html`. Check, do not assume.

As with M1: the build removes code and proves it on the dev path. Stopping,
disabling and deleting the host unit `kronk-hottub-monitor.service`, and
deleting `data/hottub/`, are the coordinator's steps at promotion. Neither
agent stops the unit or deletes under `./data`.

## Criteria

1. `orchestrator/tools.py` has no `query_hottub` (definition, handler,
   dispatch entry, timeout). The `home` agent's `tool_names`, prompt,
   description and routing hint do not mention a hot tub or spa.
2. `tool_service` has no `/hottub` route and no `HOTTUB_*` setting; compose
   passes no hot tub env var to any service, production or dev.
3. `hottub_monitor/` is gone from the repo. Nothing in the repo imports or
   launches it.
4. `grep -rniE 'hot ?tub|hottub|gecko'` over `orchestrator/`,
   `tool_service/`, `nginx/`, `docker-compose.yml`, `scripts/`, `tests/`,
   `static/`, `systemd/` and `kronk-context.md` returns nothing, or only
   lines the build report lists and justifies (negative tests, the probe).
5. Docs: README and `TECH_DEBT.md` (HOTTUB-01) no longer describe the
   feature as present; any plan or feature doc for it is moved to history
   with a removed-on status header, not deleted. No roadmap entry remains.
6. Hot tub rows are removed from `docs/bench/routing_eval.jsonl` and the
   bench scripts, including the hot tub composite probe in the coordinator
   bench. The voice corpus and its offline test still pass; any corpus row
   about the hot tub is re-expected to the plain refusal, not deleted.
7. A probe exists and passes on the dev path: "is the hot tub online", and
   two or more paraphrases, asked at least 10 times in total across
   `/dev/api/chat` and `/dev/voice/api/chat`, get a plain statement that
   Kronk cannot check that. No invented status or temperature, no tool
   call that pretends to check. Answers recorded verbatim. This is done
   without a new routing rule (stopping rule).
8. Things the `home` agent can still do are not refused: at least six
   checks (music dry-run, weather, solar, shopping list or similar) behave
   as before.
9. `./scripts/run_tests.sh` passes. Baseline before M1b:
   `989 passed, 2 skipped, 18 xfailed`. Every removed test is accounted
   for; skips and xfails unchanged or explained.
10. The M10 live replay on dev matches the committed baseline apart from
    known run-to-run variance; differing rows are listed and explained.
11. The build report holds the manifest and exact promotion commands: the
    unit file path and its `ExecStart`, whether it is enabled and running,
    the commands to stop, disable and remove it and reload systemd, any
    virtualenv or files it owns outside the repo (listed for Drew, not
    deleted unless inside `hottub_monitor/`), `data/hottub/` with size and
    file count, and post-promotion checks. Order: stop and disable the unit
    before the directory it runs from disappears.
12. During build and test: the unit was not stopped, nothing under `./data`
    was deleted, no production container was stopped, removed or
    recreated, and HA and Music Assistant were not restarted.

## Reports

Build: `docs/sprint/M1b-build.md`. End to end: `docs/sprint/M1b-e2e.md`, one
line per criterion with pass, fail or not testable, and the evidence.
