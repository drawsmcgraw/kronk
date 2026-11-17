# Feature: Direct dispatch for regex-pinned commands

**Built:** 2026-11-17, sprint item M7, branch `refactor` (build report
`docs/sprint/M7-build.md`; plan: `Kronk evaluation findings and implementation
plan.md`, section M7, and review item O12). Production once the sprint
coordinator promotes it (`docs/sprint/PROMOTIONS.md`).

## What it does

Five spoken or typed commands run their tool straight away, with no model
call: "pause", "what song is this", "thumbs up", "not that" and "update the
magic mirror", plus the other spellings their regexes accept. Before, the
anchored regex already knew the tool and its arguments, but the request still
went to the `home` agent, where a model spent a round choosing the same tool
and sometimes changed or dropped the arguments (on dev, before M7: "not that"
once came back as a `play_music` call with no query, "That didn't work — I
didn't get enough detail to ask the music service").

On the dev voice mount the four measured commands went from 0.18–4.98 s to
0.01–0.03 s end to end, with tool_service in dry-run (the table in
`docs/sprint/M7-build.md`).

## How it works

### The rules table (O12)

`routing.py` is an ordered table, `routing.RULES`. Each `Rule` has a name, a
route, a matcher and, for five rules, a `direct` builder. `routing.decide(text)`
returns `Decision(route, rule, direct)` for the first rule whose matcher
accepts the text. It is pure (regex only, no event, no span).
`routing.classify()` and `routing._classify_inner()` keep their signatures and
return values and log the decision as before.

Since sprint M19 a `lead_in` rule comes first ("ask Kronk to …", route
`direct`, no direct call): it skips every rule below, so none of the five
direct calls runs for a request that starts with the lead-in
(`docs/features/lead-in.md`). Since sprint M14 the `messy` rule follows
`url` (rule 6 of 17): a request with more than one sentence or more than 20
words goes to the coordinator, so none of the five direct calls runs for it
either; "Update the magic mirror. And play some jazz." reaches the update
only through the model (`docs/features/messy-transcript-gate.md`). The
numbers below are M7's.

| # | Rule | Route | Direct call |
|---|---|---|---|
| 1 | `talkie_explicit` | talkie | — |
| 2 | `direct_override` | direct | — |
| 3 | `music_url` | home | — |
| 4 | `url` | research | — |
| 5 | `search_phrase` | research | — |
| 6 | `weather` | home | — |
| 7 | `playback` | home | `control_music` `{action}` |
| 8 | `now_playing` | home | `now_playing` `{}` |
| 9 | `rate` | home | `rate_music` `{thumb}` |
| 10 | `music_another` | home | `play_music` `{query: "another", kind: "another"[, player]}` |
| 11 | `taste` | home | — |
| 12 | `music` | home | — |
| 13 | `mm_update` | home | `update_magicmirror` `{}` |
| 14 | `mm_ops` | devops | — |
| 15 | `default` | direct (coordinator) | — |

The arguments come from named groups in the regexes, not a second parse:
`_PLAYBACK_RE` has one group per branch (`pause`, `resume`, `next`,
`next_track`, `turn_up`, `louder`, `volume_up`, …) mapped by
`_PLAYBACK_ACTIONS`; `_RATE_RE` likewise through `_RATE_THUMBS`;
`_ANOTHER_RE`'s trailing "in the <room>" is the `room` group, passed as
`player`. Adding the groups did not change what any regex accepts:
`tests/test_routing_pin.py` compares each against a frozen copy of its
pre-M7 pattern over 6415 utterances.

### The pipeline branch

`main._run_pipeline` calls `routing.decide(text)` before any model lock
(since sprint M9 the locks are per model server and taken only inside a
generation, `docs/features/model-locks.md`). A direct call takes no lock, skips the
coordinator's weather, shared-state and file context, routes through
`routing.classify` as usual (same `routing.decide` span, same event), then
`main._direct_dispatch`:

1. narration from `agents._tool_narration`;
2. `tools.execute(name, args)` under a `tool.<name>` span on the pipeline
   root, with `tool_call` and `tool_complete` events (`agent` is the route,
   `direct: true`);
3. the sentence from `agents._terminal_speech(result, error_style, tool=name)`,
   the function the home agent's terminal-tool path uses, so the wording is
   the same on both paths in both error styles;
4. the timing payload (`model: "none (direct call)"`) and `request_complete`.

History, the trace's route and ERROR marking, and the last-resort error guard
are the pipeline's own, shared with every other request.

Because the tool runs through `tools.execute`, every guard inside the
handlers still applies: the satellite that heard the request is the target
(`origin.current`), `MUSIC_DRY_RUN` on dev, and the mirror pin
(`update_magicmirror` runs only when `origin.said`, the request's own last
message, is the pinned sentence).

A failure is spoken and the turn ends. Nothing falls through to a model: a
second attempt could repeat a half-applied action.

## Telemetry

- `route_shortcut` event: `{rule, route, direct: true, tool}` for these five
  rules; unchanged for the others.
- `routing.decide` span metadata: `rule`, plus `direct: <tool>`.
- `tool.<name>` span directly under `pipeline.*` (no `agent.home`, no `llm.*`
  span). The "Tools by name" and "Routes over time" panels count these
  requests as before.
- `tool_call` / `tool_complete` carry `direct: true`; no `agent_complete` or
  `agent_round`. `scripts/voice_replay.py` reads the `direct` flag for corpus
  rows with `expect.direct`.

## Gotchas

- **"stop" is sent as `pause`.** The plan's table and the home prompt say
  so; tool_service runs both as `media_pause` with the same "Paused" word.
  Six corpus rows that expected action `stop` now expect `pause`.
- **"play another one" is not `music_another`.** `_ANOTHER_RE` has no
  "another one" branch, so it is the `music` rule and goes to the model
  (which on dev picked `play_music` kind another, `play_music` with no query,
  or `control_music`, across five runs). Not widened: stopping rule, Drew
  2026-11-11.
- **A named room keeps a command on the model path** ("pause the music in the
  office"): the anchored regexes have no room slot, except `music_another`'s
  trailing "in the <room>", which the music blueprint appends. That room is
  passed as `player`, as the home prompt has the model pass it. Its group is
  a lazy `[\w' -]{1,40}?`, so "not that in the office please" passes
  "office please"; tool_service's substring room match still resolves that to
  the Office speaker. An unknown room ("in the garage please") is answered
  with "I don't know a speaker or room called 'garage please'" and the list
  of rooms (M7 end-to-end test, finding F1).
  This spelling is not in the corpus or the eval set.
- **`mm_update` is not anchored.** The pinned sentence may sit inside a
  longer utterance ("okay, update the magic mirror please"). The rest of
  the utterance is not acted on. That was already true: `update_magicmirror`
  is a terminal tool, so the agent path ended the turn there too.
- **Concurrency.** A direct request writes web UI history without the
  lock. `sessions.append` is synchronous and the pipeline writes the user
  and assistant messages back to back, so an exchange is never split. Two
  overlapping web UI requests are stored in the order they finish.
- **Tests that patch `routing.classify`** still steer the model path. The
  lock decision reads `routing.decide`, so a patched classify on one of the
  five commands still dispatches directly. Patch `routing.decide` to
  force the agent path (`tests/test_direct_dispatch.py` does).
- **The routing pin fixture** (`tests/fixtures/routing_pin.jsonl`) was
  generated once, from the pre-table tree. Regenerating it with
  `tests/fixtures/make_routing_pin.py` proves nothing about a refactor. Do it
  only for a deliberate routing change, and say so in the commit.

## Blog hooks

- The model was the slowest part of "pause": 3 seconds to pick a tool the
  regex had already picked. Taking it out made the command 100x faster.
- Pin, then refactor: 6415 utterances and a frozen copy of every regex,
  committed before the if-chain became a table.
- Same sentence, two paths: routing both through one speech function so the
  fast path can't drift from the slow one.
