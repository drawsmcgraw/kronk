# Playback control by voice (Kronk tier) — Plan

Status: **shipped 2026-09-10 (same day).** ROADMAP item 18, part (2) —
operator scoped it to the Kronk tier; the fast-path sentence triggers
and the HA built-in-intent quirk stay open (see
`../incidents/INVESTIGATION_2026-09-10_voice_stop.md`). Verified live
through the Assist pipeline as the basement device: "Stop!" → "Paused
on the basement voice pe ma speaker." (5.2 s, player idle); "resume the
music" → "Resumed…" (6.2 s, playing); "skip this song" → "Skipped…".
Two things learned building it: (a) a bare "Stop!" never reached the
home agent — the coordinator answered "Stop what?" itself — so bare
playback verbs got a deterministic **routing pin** to the home agent
(`_PLAYBACK_RE`, anchored start-to-end; 18 match / 8 reject tests);
(b) MA reports a Sendspin player **idle**, not paused, after a pause
while keeping its queue, and `media_play` resumes it — so resume has no
precondition and the verify poll is the guard. The spoken label names
the player ("basement voice pe ma") because that MA device has no HA
area; setting one makes it "the Basement Theater speaker". 41 tests in
`tests/test_playback_control.py`; suite 518 passed, 2 skipped.

## What ships

- **`control_music(action, player?)`** — a terminal tool on the home
  agent beside `play_music`. Actions: pause, resume, stop, next,
  volume_up, volume_down. **stop == pause** (operator decision): pause
  keeps the queue so "resume" works; a real stop on an MA player clears
  it. The model supplies only the action (and a speaker/room if the
  user named one). The origin (device + room) is attached by the
  dispatcher from the request's origin stamp, never by the model.
- **tool_service `POST /music/control`** — resolves the target exactly
  as `/music` does (named → own device → own room → default), refuses
  cleanly when nothing is playing, calls the matching `media_player`
  service over the websocket helper (HA's own error text comes back),
  and **verifies the effect** before answering: state reaches
  paused/playing, or the volume moved (or was already at a bound).
- **Honesty guard** in the home agent prompt: playback is controlled
  only through this tool; never claim to have paused/stopped otherwise.
  A loop test pins it: "stop" → `control_music(pause)` → the tool's
  sentence, verbatim, through the coordinator passthrough.
- Terminal → the spoken reply ends with a period, so HA does not reopen
  the mic (the "Stop what?" loop came from a question mark).

## Tests

`tests/test_playback_control.py`: route (each action's service, stop→
pause, nothing-playing refusal, HA error message surfaced, unknown
action, volume verification); tool payload carries origin; terminal
speech mapping; home agent registry; coordinator passthrough.

## Verification

Suite; deploy tool_service + orchestrator; nginx restart; shim check;
live through the Assist pipeline as the basement device: "stop" →
player paused, spoken "Paused on the Basement Theater speaker."; "resume"
→ playing; "skip this" → next. pipeline_bench not run: the agent loop is
unchanged (one more tool on a specialist).
