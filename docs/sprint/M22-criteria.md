# M22 pass criteria: the basement hand-off (incident fixes 2 and 3, and its corpus row)

Source: `docs/incidents/INVESTIGATION_2026-11-19_basement_aerosmith.md`,
"Proposed fixes" 2 and 3 and "Corpus row"; Drew's order of 2026-11-21.
Fix 1 (the device's area) is applied. Fixes 4 and 5 stay with the M11
cutover and are out of scope. No new routing rule (the stopping rule).

## Scope

1. **Blueprint `kronk_text`** (`ha/blueprints/mass_assist_kronk.yaml`,
   marked fork change 14): the hand-off text names a target in this order:
   the target the speaker named; else the satellite's own Music Assistant
   player (`on the <friendly name>`); else the satellite's room, only when
   that room holds a Music Assistant player (`in the <room>`); else the
   bare sentence. The wrong comment above it is corrected. The blueprint's
   version string moves on.
2. **tool_service `resolve_players`**: (a) a spoken target that is an area
   Home Assistant has but that holds no Music Assistant player gets its
   own message saying so, instead of "I don't know a speaker or room
   called …"; (b) the own-room rung no longer falls to the default player
   without a word when the origin's area holds no player: it either says
   so in the reply while playing on the default, or refuses with the
   cause. The builder picks one, states why, and both tiers and the docs
   agree with it. Requests that resolve today resolve the same way.
3. **The corpus row** from the incident document joins
   `tests/voice_corpus.jsonl`, with the Music Assistant snapshot and the
   live pin re-recorded as far as the row needs (`scripts/music_snapshot_record.py`,
   reads only), `xfail` kept only for what still fails, with the reason.

## Criteria

1. A test renders `kronk_text` (the real Jinja from the blueprint file, not
   a copy) for each satellite shape: own player and room with a player;
   own player and room without one (the basement before fix 1); no own
   player and a room with one; no own player and a room without one; a
   named target. Each rendered text, resolved through `resolve_players`
   against a matching player list, reaches the right player or the bare
   sentence. The basement shape fails on the old template.
2. A read-only registry check exists (a script or a test helper, run by
   hand against the real HA with reads only) that lists every satellite
   whose ESPHome device and Music Assistant device are not in the same
   area. Its result on the real registry today is in the report.
3. `resolve_players`: tests for 2(a) and 2(b) that fail on the old code;
   the existing "two tiers must agree" tests still pass; no change to what
   any existing passing request resolves to (shown by the existing tests
   and the offline corpus layer).
4. Every failure the user hears names the most specific cause (tenet 7);
   the message is short enough to speak and lists no more than the old one
   did.
5. The corpus row is in, the corpus tests pass, and the report says what
   the row now checks and what it still cannot (the hand-off text is
   checked by criterion 1, not by the replay).
6. Nothing is installed: the blueprint is NOT copied into Home Assistant,
   no automation is reloaded, production is not deployed. The report gives
   the exact install steps for the coordinator (the `docker cp`, the
   reload call, how to read back the installed version, the undo) and
   states that no HA restart is needed, or says why one is.
7. Docs: `docs/features/voice-music-control.md` (the hand-off order, the
   new messages, the registry check under "Adding a satellite"), the
   incident document (dated lines for fixes 2 and 3, the corpus row
   section), and any doc that states the old order.
8. `./scripts/run_tests.sh` passes (before: `5683 passed, 3 skipped, 53
   xfailed`). The dev pair may be rebuilt; with dry run confirmed on dev
   first, the new messages are shown once on the dev `/music` route
   (loopback, text only, no sound).
9. Production containers, units and timers unchanged; HA and MA not
   restarted, not reloaded, not written to; no sound; no notification.

Reports: `docs/sprint/M22-build.md`, `docs/sprint/M22-e2e.md`.
