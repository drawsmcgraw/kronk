# M39 pass criteria: the music canary says why a provider is down

Source: `docs/incidents/INCIDENT_2026-11-27_ytmusic_challenge_solver.md`,
"What would have caught it sooner". Drew, 2026-11-27: "add the incorrect
canary message to the work queue. it shouldn't take much to update the
error message. be sure to only test what's necessary, not a full
regression test."

Handoff line: when `scripts/music_provider_canary.sh` finds a provider
unavailable, its log line and its push carry Music Assistant's own error
for that provider, not a guessed cause.

Plan doc: this file. Tier 1 (tooling; no request to production runs
through the canary). No design review.

## What the investigation found (coordinator, 2026-11-27)

- The canary's failure message is fixed text:
  `Not available: <providers> (first seen <date>). YouTube: paste a fresh
  cookie (docs/features/voice-music-control.md).` From 2026-11-25 to
  2026-11-27 ytmusic was down because yt-dlp could not solve YouTube's
  player challenges; the cookie was fine. The push sent Drew to the wrong
  fix.
- The canary already captures the reload's error: the probe prints
  `{"reload_error": "..."}` to stderr, the script folds stderr into
  `out` (`2>&1`), then keeps only the last line starting with `{`
  (`grep -E '^\{' | tail -1`), which is the availability line. The error
  is thrown away.
- MA (2.11.0b11, running) reports each provider's load error as
  `last_error` in `config/providers/get` (`instance_id=...`); it is
  `None` for a healthy provider. Set in `music_assistant/mass.py` when a
  provider fails to load.
- No test in `tests/` covers the canary.

## Plumbing (coordinator's decisions)

1. The probe reads `last_error` for each provider it reports unavailable
   and the reload error it already gets, and prints them in its JSON.
2. The message names each failing provider with MA's error text, cut to a
   length a phone notification shows (the builder states the limit). The
   cookie hint stays only when the error text is about the cookie or
   login (MA's `cookies_expired` / `LoginFailed` text); otherwise it is
   dropped.
3. When MA gives no error text, the message says so ("MA gave no reason")
   rather than guessing.
4. Nothing else changes: pass/fail/skip exit codes, the 24 h cooldown,
   the state file's keys (a new key for the error text is allowed), the
   timer.

## Testing (Drew's condition, 2026-11-27)

Only what the change needs. No test agent, no pipeline bench, no voice
replay, no live playback. The full suite is not this item's gate: the
change touches one shell script and nothing the suite imports; the
builder runs only the new test file, with `tests/.venv/bin/python -m
pytest tests/<new file>` (not bare `pytest`, not `run_tests.sh`, which
always runs all of `tests/`).

## Criteria

1. A new test file drives the script with the MA probe stubbed (no
   Docker, no HA, no push sent): a provider down with an error text gives
   a FAIL line and a push body containing that text; a cookie error keeps
   the cookie hint; a non-cookie error does not; no error text gives "MA
   gave no reason"; all providers up gives PASS and exit 0. Output
   quoted.
2. One live run of `scripts/music_provider_canary.sh` on production with
   every provider up: `PASS`, exit 0, line quoted. (Nothing plays on
   YouTube Music first; the script skips itself if something does.)

## Out of scope

- Which providers the canary checks, its schedule, the cooldown.
- The SearXNG canary.

## Rulings
