# M7 pass criteria: direct dispatch for regex-pinned commands

Written by the sprint coordinator on 2026-11-17 (UTC), before the build, from
`Kronk evaluation findings and implementation plan.md` (section "M7. Direct
dispatch for regex-pinned commands", row C4 and the M7 line of "Verified on
Kronk, 2026-11-15") and the items promoted since (M2's voice signal and
failure flag, M4b's mirror pin, M21's `config/`). The tester checks against
this file, not against the build report.

## What M7 is

For five routing rules an anchored regex over the whole utterance already
determines the tool and its arguments, yet the request still goes to the
`home` agent, where a model spends a round choosing the same tool and
sometimes changes the arguments. M7 calls the tool directly for those
rules, with no model call. It starts with review item O12: the routing
if-chain becomes an ordered rules table.

## Criteria

### A. O12: the rules table, with no routing change

1. Before any refactor, a test pins the 15 routing outcomes and their
   order (`talkie_explicit`, `direct_override`, `music_url`, `url`,
   `search_phrase`, `weather`, `playback`, `now_playing`, `rate`,
   `music_another`, `taste`, `music`, `mm_update`, `mm_ops`, `default`)
   and, over a broad utterance set (the voice corpus, `routing_eval.jsonl`,
   the existing shortcut tests), the `(route, rule)` each utterance gets.
   The pin is committed before the first change to `routing.py`.
2. `routing._classify_inner` (or its replacement) is an ordered table of
   rules, each with a name, a matcher and a route. `(route, rule)` is
   identical before and after for every pinned utterance. No rule is
   added, removed, reordered or widened (stopping rule, Drew 2026-11-11).

### B. Direct dispatch

3. For exactly these rules the classifier also returns a direct call
   `(tool_name, args)`, taken from named groups of the match, not from a
   second parse:
   - `playback` → `control_music`: stop, pause, shut up → `pause`;
     resume, unpause → `resume`; skip, next → `next`; louder, turn it up,
     volume up → `volume_up`; quieter, turn it down, volume down →
     `volume_down`. The mapping is the plan's table; where today's regex
     accepts a word the table does not list, the report says what it maps
     to and why.
   - `now_playing` → `now_playing`, no arguments.
   - `rate` → `rate_music` with `thumb` `up` (like, love, thumbs up,
     upvote, more like this) or `down` (hate, dislike, don't like, never
     play this again, thumbs down, downvote, less like this).
   - `music_another` → `play_music` with `kind: another`; the trailing
     "in the <room>" this rule accepts is handled as it is today.
   - `mm_update` → `update_magicmirror`, no arguments.
   `music`, `taste`, `weather`, `search_phrase`, `url` and every other
   rule return no direct call and behave as before.
4. In the pipeline, a direct call runs `tools.execute(name, args)`, renders
   the result through the same terminal-speech function the agent path
   uses, yields it, and returns. No model call is made: a test with the
   model client patched to fail shows each of the five rules still
   succeeds.
5. What the user hears for a direct call is the same sentence the agent
   path gives for the same tool result, in both error styles and on every
   mount (M2: friendly on voice, debug elsewhere; music failure sentences
   unchanged).
6. The speaker that heard the request is still the target: the handlers
   read `origin.current` as before, on HA voice requests with the origin
   stamp and on requests without one.
7. The M4b pin holds on the direct path: `update_magicmirror` runs only
   when the request's last message is the user's own and matches the
   pinned pattern; the odd request shapes from `M4b-e2e.md` are refused
   with the fixed sentence; nothing reaches `/magicmirror/update` any
   other way.
8. If the tool fails, the failure is spoken and the request ends. It does
   not fall through to the model (a second attempt could repeat a
   half-applied action).
9. The direct branch runs outside `_llm_lock`: a direct request issued
   while another request holds the lock completes without waiting for it.
   A test shows this, and a live check on dev shows it.
10. Telemetry keeps its shape: a tool span under the pipeline root, and a
    `route_shortcut` event carrying `direct=true`, so the dashboards still
    count these. Chat history, session handling and the request-complete
    event are written as for any other request.
11. Negatives stay off the direct path and behave as before: at least
    "stop the timer", "pause my subscription", "what is the next holiday",
    and requests naming a room ("pause the music in the office").
12. The corpus is updated where it pins a path: rows for the five rules
    expect the direct call; no row is weakened.

### C. Latency, regression, safety

13. Stated up front (tenet 12): a direct request on the dev voice path
    answers in about one second or less, excluding what the tool itself
    takes. Measured on dev before and after for "pause", "what song is
    this", "thumbs up" and "play another one", at least five runs each,
    with trace ids. Dry-run is on for dev, so the tool's own time is not
    the real one; say so.
14. `./scripts/run_tests.sh` passes: at least `3003 passed, 2 skipped,
    18 xfailed` plus the new tests. Unit tests do not reach the network.
15. The voice replay on the dev path matches the M10 baseline row by row
    apart from the known flapping rows; rows that were `xfail` only
    because the model changed or dropped arguments on these five rules
    may now pass, and are listed.
16. No sound. Every live check is on dev with dry-run on or is read-only;
    on the `kronk-dev` HA pipeline, utterances HA would handle locally
    are not executed (the replay's rule). Production untouched; no HA or
    Music Assistant restart; no command to the MagicMirror Pi.
17. Docs: `docs/features/agents-as-tools-routing.md` (or the routing doc)
    describes the table and direct dispatch; README's routing description
    is correct.

## Reports

Build: `docs/sprint/M7-build.md`. End to end: `docs/sprint/M7-e2e.md`, one
line per criterion with pass, fail or not testable, and the evidence.
