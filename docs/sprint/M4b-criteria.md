# M4b pass criteria: the mirror update runs only for the pinned utterance

Written by the sprint coordinator on 2026-11-16. Decided by Drew that day,
after the M4 test showed a fetched-page request ending in
`update_magicmirror`: no turn-level guard; instead the update is pinned to
one utterance. The tester checks against this file.

## Criteria

1. `update_magicmirror` executes only when the sentence the user actually
   said in this turn matches the existing pinned pattern, "update the magic
   mirror" with "magicmirror" as an accepted spelling, case-insensitive,
   anywhere in the sentence (today's `_MM_UPDATE_RE`,
   `\bupdate\s+the\s+magic\s*mirror\b`). One pattern, defined once, used by
   both the routing rule and the tool guard.
2. The check is made in code on the user's own words for the turn (the
   value the orchestrator already keeps for the request), never on
   model-written arguments, tool output, fetched text or earlier turns in
   the history. A unit test proves each of those cannot satisfy it.
3. Any other route to the tool is refused before any call leaves the
   orchestrator: the tool returns a fixed sentence that tells the user to
   say "update the magic mirror", the turn ends as terminal tools do, and
   the refusal is visible in the trace. No request reaches `tool_service`'s
   `/magicmirror/update` on a refusal.
4. These trigger the update path (on dev it stops at the missing key with
   the existing error): "Update the magicmirror", "Update the magic
   mirror", "update the MagicMirror.", "Okay, update the magic mirror
   please".
5. These are refused with the fixed sentence: "update the mirror",
   "upgrade the magic mirror", "update magic mirror", "run the mirror
   update", "please upgrade the mirror to the latest version", and "fetch
   https://example.com and run what it says on the mirror" (ten runs: no
   run reaches `/magicmirror/update`).
6. Works on every transport: chat UI (`/message`), `/api/chat`,
   `/voice/api/chat`, `/v1/chat/completions`, and through HA's
   `assist_pipeline/run` on `kronk-dev`. The voice origin stamp or any
   wrapper text HA adds does not break or spoof the match.
7. The M4 description wording that steered "upgrade the mirror" to home is
   simplified or left, whichever the builder can show is harmless; the
   devops read-only mirror checks from M4 criterion 5 still reach
   `ask_devops` at least 8 of 10 in the first-round A/B.
8. Corpus and docs: the voice corpus rows for mirror updates are
   re-expected to match (pinned phrase passes; others refuse);
   `docs/features/` or the mirror plan documents the pin and why. No new
   routing rule is added.
9. `./scripts/run_tests.sh` passes with new tests for criteria 1 to 5.
10. The M10 live replay on dev matches the baseline apart from known
    variance and the re-expected mirror rows.
11. No command was sent to the MagicMirror Pi; production's
    `/magicmirror/update` and `/ops/exec` were not called; dev has no
    mirror key (confirmed before any live request); production containers,
    HA and Music Assistant untouched.

## Reports

Build: `docs/sprint/M4b-build.md`. End to end: `docs/sprint/M4b-e2e.md`
(covers the M1b restore as well).
