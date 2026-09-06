# Real errors reach the speaker — Plan

Status: **shipped 2026-09-05 (same day).** Folded two ROADMAP chores
(speak HA's message; verify before failing on a 5xx) into one change.
Verified live through the Assist pipeline as the office satellite:
"put on the radio station Zorblax Fnordwave Nonexistent" →
"I couldn't play that. Playback failed: Could not resolve Zorblax
Fnordwave Nonexistent to playable media item." — MA's own sentence,
relayed by the coordinator with no synthesis round (`terminal_passthrough`
event, rid `cb0501ce`). 13 tests in `tests/test_error_surfacing.py`;
suite 476. New pinned dependency in tool_service: `websockets==16.0`.
One polish found live: MA renders the unresolved query as a Python list
(`['…']`), which TTS would read bracket by bracket — unwrapped, nothing
else touched.

## Trigger

"Play Portishead Radio" (rid `3c84f79b`): Pandora answered MA with HTTP
429; MA failed the queue with "Playback failed for Portishead Radio - no
more tracks available"; HA raised that; Kronk told the operator it
"couldn't play music right now". Tenet 7 says the most specific cause
available must reach the user. It was available and was dropped three
times.

## Where the cause is lost (verified 2026-09-05)

1. **HA's REST API.** `APIDomainServicesView.post` catches JSON,
   service-not-found and validation errors, but a `HomeAssistantError`
   raised inside the service propagates to aiohttp → bare
   "500 Internal Server Error" body. The message lives only in HA's log.
   HA's **websocket** `call_service` returns it
   (`send_error(id, "home_assistant_error", str(err))`).
2. **tool_service** speaks "Music Assistant rejected the request
   (HTTP 500)" regardless of body.
3. **The coordinator relay.** `play_music` is terminal for the home
   agent (spoken verbatim, turn ends), but the coordinator receives the
   home agent's text as an ordinary `ask_home` result and synthesizes —
   observed rewording twice ("can't play music right now", "internal
   server error and could not play jazz"). `_terminal_speech` itself
   keeps the detail in both error styles.

Limit: the deepest cause (Pandora 429) never leaves MA's log. HA's
message is the most specific thing obtainable with HA as the broker.

## Design

- **tool_service `ha_call_service()`** — HA websocket: connect, auth
  with the same token, `call_service`, return `(ok, message)`. Used for
  `music_assistant.play_media` and `assist_satellite.announce`; REST
  stays for state reads. New pinned dependency: `websockets`.
- **/music**: on a failed call, still poll the targets for `playing`
  for the verify window (MA has failed the call and played anyway); if
  nothing plays, 502 with detail = HA's message verbatim, prefixed
  "Playback failed: …" only when the message doesn't already say so.
  Logs keep the raw error.
- **Coordinator passthrough (structural, tenet 5)**: a delegated
  specialist run reports whether it ended on a terminal tool
  (`run_stream` `done` event gains `terminal`); the coordinator's loop,
  on an `ask_*` result flagged terminal, ends its own turn with that
  text verbatim — the news_brief rule generalized. Success sentences
  ("Now playing …") pass through too. Depth cap unchanged.
- **Telemetry**: `tool_error` carries HA's message; span status too.

## Tests

tool_service: fake websocket → error message in detail; error then
`playing` → success; success unchanged; websocket unreachable → clear
502. orchestrator: coordinator + fake home specialist ending terminal
(failure and success) → output verbatim; non-terminal delegate still
synthesized; existing loop tests unchanged.

## Verification

Suite; `pipeline_bench.sh errsurf-pre/-post` (agent loop touched);
deploy tool_service + orchestrator, nginx restart, shim check; live:
"play the radio station <nonexistent>" through the pipeline as a
satellite → MA's own sentence spoken; the Portishead station when
Pandora next throttles.
