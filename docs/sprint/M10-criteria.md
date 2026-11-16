# M10 pass criteria: voice regression corpus

Written by the sprint coordinator on 2026-11-15, before the build, from
`Kronk evaluation findings and implementation plan.md` (section "M10. Voice
regression corpus", the M10 line of "Verified on Kronk, 2026-11-15") and the
M17 end-to-end report (`docs/sprint/M17-e2e.md`, observation O1). The tester
checks against this file, not against the build report.

## Context the plan did not have

- The music dry-run switch already exists (`MUSIC_DRY_RUN`, built in M17, on
  for `tool_service-dev`). M10 uses it; it does not build a second one.
- The M11 parser does not exist yet. Layer one asserts routing today and
  carries the expected-call fields so M11 can assert against them without
  changing the corpus format.
- On any pipeline with `prefer_local_intents` on (both `kronk` and
  `kronk-dev`), HA's own sentence triggers (the Kronk music blueprint, HA
  built-ins such as pause and next) handle some utterances before Kronk is
  asked, and `MUSIC_DRY_RUN` cannot silence those. A live replay must
  therefore never execute an utterance that HA would handle locally.

## Criteria

### A. The corpus

1. `tests/voice_corpus.jsonl` exists. One JSON object per line with: the
   text as transcribed; the origin area (and device where it matters); the
   expected outcome (tier: HA-local or Kronk; routing rule; tool; media
   type; name; target player, where each applies); a source field naming
   the incident doc or bench file the row came from; and an id that stays
   stable when rows are added.
2. It holds every utterance the plan names, verbatim: "Play Anime Pop Radio.
   That's probably about it. Everything is pretty wet.", "Like massive
   attack radio", "Start an orbital radio station on YouTube Music", "play
   The Girl with the Sun in Her Head by Orbital", "shuffle my YouTube
   playlist anime bangers", "put on some jazz", "Stop!", the "ninety's two
   thousands" station utterance as recorded in its incident doc, and the
   mishearings "Pasta" (for pause) and "What's up?" (for stop).
3. It holds the music and playback rows from `docs/bench/routing_eval.jsonl`,
   and further utterances from the music incident docs in `docs/incidents/`
   where the doc records what was actually said.
4. Expected outcomes record what the system should do. Where today's
   behaviour is a known failure (the mishearings; anything an incident doc
   left unfixed), the row says so with an explicit expected-failure marker
   and a reason, instead of pinning the wrong behaviour as correct or
   being left out. No new special-case routing rule is added to make a row
   pass (the stopping rule, Drew, 2026-11-11).
5. The file format is documented (a short section in a `docs/features/` or
   `tests/` README-level doc), including the rule: every new incident adds
   its utterance in the same commit as the fix. `CLAUDE.md`'s incident
   ground rule or definition-of-done mentions the corpus so the rule is
   found.

### B. Layer one, offline

6. A pytest in `tests/` runs every corpus row through
   `routing._classify_inner` and asserts the expected rule and route. It
   runs as part of `./scripts/run_tests.sh`, needs no network, and adds
   under five seconds.
7. Expected-failure rows are reported as such (xfail or equivalent), are
   counted in the output, and turn into a visible signal when they start
   passing.
8. The test fails with a message naming the row id and utterance when a
   row's routing changes. Demonstrate by temporarily breaking one
   expectation; revert it.
9. There is a clear, documented place where M11's parser assertion plugs
   in (expected tool, media type, name, player are already in the rows).

### C. Layer two, live replay

10. `scripts/voice_replay.py` sends corpus rows to HA's
    `assist_pipeline/run` over the websocket with `start_stage: intent`,
    as a chosen satellite, and compares the result with the row's expected
    outcome. It defaults to the `kronk-dev` pipeline.
11. It is silent and safe by construction:
    - before sending anything it confirms that the `tool_service` behind
      the target pipeline has dry-run on, and refuses to run otherwise;
    - it refuses the production pipeline unless given an explicit
      override flag, and the override still requires dry-run;
    - rows HA would handle locally are checked without being executed
      (HA's sentence-match debug, or an equivalent read-only check), and
      the report says which rows were checked that way.
12. A full replay changes no `media_player` state: every player's state
    and media title are the same before and after, checked by the script
    itself and reported. It never pauses or replaces something already
    playing.
13. Each replay writes a result file under `docs/bench/` (or a path given
    on the command line) with, per row: pass or fail, expected against
    actual, latency, and the trace id or pipeline run id. Exit status is
    non-zero when a row that should pass fails.
14. It can run a subset (by id or tag), and reports rows it skipped and
    why.
15. A full replay on the dev path completes, and its result file is
    committed as the baseline. Rows that fail on today's code are either
    marked expected-failure with a reason or listed in the build report as
    real findings. They are not silently dropped.
16. The replay does not write to production chat history, the production
    ops audit log, or the taste file and DB.

### D. Dry-run

17. `MUSIC_DRY_RUN` behaviour from M17 is unchanged for production (unset,
    off) and dev (on). If M10 adds a per-request dry-run field to `/music`
    or `/music/voice`, it can only turn dry-run on, never off, and has
    unit tests.

### E. Regression

18. `./scripts/run_tests.sh` passes: at least `868 passed, 2 skipped`
    plus the new tests. Expected-failure rows are counted separately and
    explained.
19. Production is untouched by the build: no production container
    recreated or restarted, no HA or Music Assistant restart, no HA
    configuration change.
20. Dev traffic from the replay is excluded by the M17 TraceQL filter
    (`{ trace:rootService !~ ".*-dev" }`), so it does not pollute the
    household dashboards or the future M12 accuracy data.

## Reports

Build: `docs/sprint/M10-build.md`. End to end: `docs/sprint/M10-e2e.md`, one
line per criterion with pass, fail or not testable, and the evidence.
