# M36 pass criteria: a follow-up can refer to the turn before

Source: Drew, 2026-11-26: "kronk isn't honoring the chat history. Check
the section where I just asked for 'pulse' by pink floyd. Kronk said it
found it. I said 'play that' and kronk didn't know what I was asking
for. Investigate and fix."

Handoff line: after "play the album Pulse by Pink Floyd", "play that
album" plays it, on the chat page and by voice, because a specialist
agent is given the earlier turns as turns.

Plan doc: this file, with
`docs/incidents/INVESTIGATION_2026-11-26_play_that_album.md` (the
cause, reproduced, and the measurement: history in the system text 0 of
6, as real chat turns 6 of 6). Tier 3 (the agent loop): a design
review, build, one test, a fresh narrow test after any fix.

## Plumbing (coordinator's decisions, open to the review)

1. A specialist run from `_run_pipeline` (`orchestrator/main.py:470`)
   gets the recent history as chat messages (`history_messages`), not
   as the `context` block in the system text. The same number of
   messages as today (five), cut so the window starts on a user turn.
2. The review says what else passes `context`: `run_delegated` (a
   specialist asked by the coordinator through `ask_*`) and the shims'
   path (Home Assistant resends the conversation), and for each whether
   it changes here, with a measurement like the incident's.
3. The review checks what real turns cost and risk: the prompt cache
   (the system text no longer changes per turn, the turns follow it),
   the voice deadline, a stale turn steering a new request (a request
   that names its own subject must not be pulled to the one before:
   "play Abbey Road" after the Pulse exchange plays Abbey Road), an
   assistant turn that was a failure message, and terminal tools.
4. The resolver's reading of "Pink Floyd - Pulse" (the first fault of
   the incident) is not this item: one variable. It is recorded as a
   decision for Drew with the music weaknesses already listed.

## Criteria

1. Unit: the messages built for a pinned specialist hold the system
   text with no "[Recent conversation context]" block, then the earlier
   turns in order with their roles, then the request.
2. On dev, music in dry-run, ten times each through the chat route and
   through `/dev/voice/api/chat` with the conversation resent: the
   incident's exchange (failure reply, then "play that album, in the
   kitchen") calls `play_music` for Pulse; quoted counts. At least nine
   of ten each.
3. Not pulled by the turn before, ten times each: after the Pulse
   exchange, "play Abbey Road in the kitchen" asks for Abbey Road; "what
   is the weather" does not call `play_music`; "stop the music" does
   what it did before.
4. The same follow-up shape for two other specialists' tools (the
   review names them, for instance a list item then "remove that", a
   light then "turn it back off"), read-only or dry-run only.
5. The voice corpus and the silent replay before and after: no row
   changes route or tool. Two new rows for the incident's utterances.
   A multi-turn check is added where the review says it belongs (the
   corpus is single-turn today).
6. `pipeline_bench.sh` on dev before and after (the coordinator's):
   every prompt answers; medians within noise; the home route's
   cached-token count reported.
7. The suite passes; last lines quoted.
8. By the coordinator at promotion, on production with nothing
   playing or by Drew: the two turns, typed.

## Out of scope

- The resolver's threshold and the "Artist - Album" form.
- Longer memory than the five messages; summaries; per-device voice
  history.

## Rulings
