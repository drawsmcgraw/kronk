# M19 pass criteria: a spoken lead-in that goes straight to the model

Written by the sprint coordinator on 2026-11-17 (UTC), before the build, from
`Kronk evaluation findings and implementation plan.md` (section "M19. A
spoken lead-in that goes straight to the model") and the items promoted
since (M7's rules table and direct dispatch, M4b's mirror pin, M2's voice
signal, M21's `config/`). The tester checks against this file, not against
the build report.

## What M19 is

Drew wants one reliable way to skip every shortcut and hand a request to
the model: start the sentence with "ask Kronk to" (confirmed by Drew,
2026-11-11; it applies to every kind of request, not only music). This is
a routing rule Drew decided himself, so the stopping rule does not bar it.

## Criteria

### A. The orchestrator rule

1. A first rule in the routing table recognises the lead-in at the start
   of the utterance: "ask Kronk to …", with or without "to", any casing,
   optional leading filler HA or Whisper leave in ("hey", "okay", a
   comma). It strips the lead-in and sends the rest to the coordinator
   with its full tool menu.
2. A lead-in request skips every regex shortcut and every direct
   dispatch: "ask Kronk to pause", "ask Kronk to play some jazz", "ask
   Kronk what song is this", "ask Kronk to search the web for …" all go
   to the coordinator, none to a shortcut rule. The rule order pin from
   M7 is updated to 16 outcomes with this rule first (or immediately
   after `talkie_explicit` / `direct_override` if the report shows why
   those must stay ahead); every other utterance in the M7 pin keeps its
   `(route, rule)`.
3. The lead-in phrases live in `config/` (M21 names "lead-in phrases" as
   a config file), validated by the loader, with the built-in default
   equal to the shipped file. Mis-heard forms of "Kronk" are added only
   where captured data shows them (HA's pipeline debug history, logs,
   the corpus, incident docs); the report lists what was found and
   where. None are invented.
4. What the coordinator receives is the request without the lead-in; the
   user's own words for the turn (`origin.said`) are handled so that
   M4b's pin still holds: "ask Kronk to update the magic mirror" must
   not be a way around or a way to break the pin. The report says which
   it is (the pinned sentence is inside the user's words, so the update
   is allowed to run when the model calls the tool) and a test pins it.
   Nothing other than the pinned sentence in the user's own last message
   can start the update.
5. A sentence that merely contains the phrase later on ("can you ask
   Kronk to …" is a lead-in; "what happens if I ask Kronk to pause" is
   judged by the anchored pattern and the report says how), and a
   lead-in with nothing after it, behave sensibly: no crash, no empty
   request to the model, a plain answer.
6. For music reached this way the model decides and calls `play_music`;
   `tool_service` resolves as today. With dry-run on, "ask Kronk to find
   some deep house music and play it" on dev ends in a `play_music`
   dry-run line in at least 8 of 10 runs; the report gives the count and
   what happened in the others.
7. The route is visible: a `route_shortcut`-style event and span
   attribute naming the lead-in rule, so its use and outcomes can be
   counted.

### B. Home Assistant

8. The build determines, read-only (HA's sentence-match debug, as the
   replay script uses), what HA does today with lead-in sentences on a
   pipeline with local intents on: whether any HA trigger or built-in
   intent takes "ask Kronk to pause", "ask Kronk to play jazz in the
   office", "ask Kronk to turn it up", "ask Kronk what's playing" before
   Kronk is asked. The report tabulates at least 15 sentences.
9. No change is made to Home Assistant by the build agent. If the table
   in criterion 8 shows HA intercepting lead-in sentences, the report
   says exactly what HA change is needed (the plan's conversation
   trigger, forwarding the request and `trigger.device_id`), how to
   apply it without an HA restart, and how to undo it. The coordinator
   applies it or holds it.
10. On the `kronk-dev` pipeline, lead-in sentences that HA does not take
    locally reach `orchestrator-dev` with the origin stamp and are routed
    by the lead-in rule (`assist_pipeline/run`, `start_stage: intent`).

### C. Corpus, regression, safety

11. Lead-in rows are added to `tests/voice_corpus.jsonl` (plain forms,
    each request kind, the data-backed mis-heard forms if any), layer
    one asserts the rule, and the replay runs them on dev.
12. Latency is stated up front (tenet 12): this is the slow path by
    design, two model hops at most; measured on dev for three sentences,
    five runs each.
13. `./scripts/run_tests.sh` passes: at least `3341 passed, 2 skipped,
    23 xfailed` plus the new tests. New tests do not reach the network.
14. The voice replay on the dev path matches the M10 baseline row by row
    apart from the known flapping rows.
15. No sound; dev only; production untouched; no HA or Music Assistant
    restart or configuration change; no command to the MagicMirror Pi.
16. Docs: the rule for users ("say it plainly for the fast path; start
    with 'ask Kronk to' and the model handles it") is in
    `docs/features/voice-pipeline.md` or the routing doc and in README.
    The Whisper prompt addition (plan step 4) is left to M13 and noted.

## Reports

Build: `docs/sprint/M19-build.md`. End to end: `docs/sprint/M19-e2e.md`, one
line per criterion with pass, fail or not testable, and the evidence.
