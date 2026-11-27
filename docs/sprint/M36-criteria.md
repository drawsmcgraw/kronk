# M36 pass criteria: a follow-up can refer to the turn before

Source: Drew, 2026-11-26: "kronk isn't honoring the chat history. Check
the section where I just asked for 'pulse' by pink floyd. Kronk said it
found it. I said 'play that' and kronk didn't know what I was asking
for. Investigate and fix."

Handoff line: after "play the album Pulse by Pink Floyd", "play that
album" plays it on the chat page, and through the shims when the client
resends the conversation, because a specialist agent is given the
earlier turns as turns. A spoken "play that" is not covered: the music
blueprint hands a spoken "play …" to Kronk as a new conversation.

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
- 2026-11-26, coordinator, on the design review (`M36-review.md`: build
  with these changes). All taken; where the text above differs, this
  holds.
  - **The window** is whole exchanges from the end, user then
    assistant, two at most (four messages), stopping at the first break
    in alternation, each assistant turn cut at `ASSISTANT_PROMPT_CHARS`;
    one function, used at `main.py:470` for every entry point. Failure
    replies are kept. No tool call or result is rebuilt into it. (A
    five-message window that starts on an assistant turn gave the wrong
    album 2 of 18 times and a claimed play with no tool call once.)
  - The `[Recent conversation context]` branch of `run_stream` goes:
    one form exists. `run_delegated` and the coordinator path do not
    change.
  - **Voice is not promised** (handoff line corrected). What speech
    gains is a follow-up Home Assistant passes straight to Kronk within
    its five minutes, in practice weather.
  - **Criterion 2** as the review rewords it: the incident's exact
    exchange through `/dev/api/chat` and `/dev/voice/api/chat` with the
    conversation resent, ten each, at least nine; on the chat route the
    natural two turns ("play the album Abbey Road in the kitchen", then
    "play that again"), ten times, counts quoted, at least seven (the
    review measured 9 of 12).
  - **Criterion 3**: the Abbey Road and weather rows are the test;
    "stop the music" is a smoke check only (no model runs).
  - **Criterion 4**: the home agent's weather follow-up (the Boston
    exchange, then "what's the weather there tonight": 0 of 6 today, 6
    of 6 as turns) and, as a no-regression check, the research agent's
    "look up when he was born".
  - **Added to criteria 2 to 4**: the count of replies that say an
    action was done with no tool call. It must be zero; if it is not,
    stop and report.
  - **Criterion 5**: rows in `tests/voice_corpus.jsonl` with an
    optional `history` field; layer one ignores it;
    `scripts/voice_replay.py` gains a mode that posts such rows to
    `/dev/api/chat` and `/dev/voice/api/chat` after its dry-run check.
    Three rows: the incident's follow-up, the weather follow-up, and
    `inc-1017-try-playing-it-ytm` (tagged `needs-context`, skipped
    today).
  - **Criterion 6** reports cached tokens for a follow-up in the same
    minute and for one a minute later.
  - Not this item, recorded for Drew: the coordinator path (which has
    real turns already) answered "I removed oat milk…" with no tool
    call 2 of 6 times on "remove that from the shopping list"; the
    clock at token 893 of the prompt empties the prompt cache every
    minute.
  - Testing, by Drew's question of the same morning ("are you wasting a
    lot of time with testing corner cases?"): one test, on ordinary
    use. One fix round at most without a reason given to Drew.
- 2026-11-26, Drew, on the test (`M36-e2e.md`):
  - Finding 1 (one reply of 222, "Abbey Road is now playing again in
    the basement." with no tool call, on "play that again" after a
    success): "1 reply out of 222 is a failure rate I'm okay with.
    Promote it and move on." The zero rule of criteria 2 to 4 is met by
    the counted rows (0 of 202); the supplementary row's claim is
    accepted.
  - Criterion 2's natural leg is judged on the live-form earlier reply
    ("Now playing …", what production sends): 8 of 10 with the room
    named, at or above 7. Pass. On dev's dry-run reply it was 1 of 10;
    the model reads "again" as resume. Left over.
- 2026-11-26, coordinator: findings 3 (a follow-up with no room goes to
  the default speaker, 4 of 12) and 4 (the earlier turn's artist joins a
  new album query, 8 of 20, one unresolved) are left over. Criterion 5's
  silent replay and criterion 6's bench are run by the coordinator before
  the tag.
