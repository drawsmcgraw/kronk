# Feature: Voice regression corpus

**Built:** 2026-11-16 (sprint item M10) · **Plan:** `Kronk evaluation findings and implementation plan.md`, "M10. Voice regression corpus" · **Build report:** `../sprint/M10-build.md`

## What it does

A file of real utterances with what each one should do, and two ways to
check it:

| Layer | What | Runs | Cost |
|---|---|---|---|
| 1, offline | `tests/test_voice_corpus.py`: every row through `routing._classify_inner` | every `./scripts/run_tests.sh` | under 0.2 s, no network |
| 2, live | `scripts/voice_replay.py`: every row through HA's `assist_pipeline/run` on the `kronk-dev` pipeline | by hand, before and after voice or music changes | about 5 minutes, silent |

Every music fix so far was verified by hand. The corpus makes the next one
(M11, one decision-maker for music) checkable, and gives M13 and M14 a fixed
set of sentences to measure against.

## The rule

**Every new voice or music incident adds its utterance to the corpus in the
same commit as the fix.** The text goes in as transcribed (the STT output
from HA's pipeline debug or the incident doc), with what it should have
done. If the fix does not make the row pass, the row goes in with an
`xfail` marker and the reason; it is never left out and never pinned to the
wrong behaviour.

Expected failures are not fixed by adding a special-case routing rule. A new
rule needs the same failure captured several times (Drew's stopping rule,
2026-11-11).

## Row format

`tests/voice_corpus.jsonl`, one JSON object per line:

```json
{"id": "plan-like-massive-attack-radio",
 "text": "Like massive attack radio",
 "origin": {"area": "Kitchen", "device": "kitchen_pe"},
 "expect": {"tier": "kronk", "route": "home", "rule": "music",
            "tool": "play_music", "kind": "named", "media_type": "radio",
            "name": "Massive Attack Radio", "provider": "pandora", "player": "origin"},
 "source": ["Kronk evaluation findings and implementation plan.md#M10",
            "docs/incidents/INVESTIGATION_2026-10-04_radio_failures.md"],
 "tags": ["music", "radio", "mishearing", "stt-mangled", "plan-named", "incident"],
 "xfail": {"routing": "STT mangled the request verb; …",
           "live": "On a routing miss the coordinator can answer without calling a tool. …"},
 "note": "…", "ref": {"trace": "56222d57…", "first": "2026-10-04"}}
```

| Field | Meaning |
|---|---|
| `id` | Stable slug, lowercase and dashes. Never renamed or reused; new rows get new ids. Prefixes: `plan-`, `inc-`, `eval-`, `m4b-`, `m11-`, `m17-` by source. |
| `text` | As transcribed: case, punctuation and STT spacing kept (`" Stop!"` with its leading space is its own row). |
| `origin.area` / `origin.device` | The satellite that heard it: `kitchen_pe` (Kitchen), `office_sat1` (Office), `basement_pe` (Basement Theater). `area: null` when not recorded; the replay then uses the office Satellite1. |
| `expect.tier` | `ha_local` (HA answers before Kronk is asked) or `kronk`. With `ha_local`, `expect.ha` says how: `{"source": "trigger", "trigger": "radio"}` (the Kronk music blueprint; `trigger` is the blueprint trigger id, given only where it matters) or `{"source": "builtin", "intent": "HassMediaPause"}`. |
| `expect.route`, `expect.rule` | Where `routing._classify_inner` should send the text if it reaches Kronk (spoken or typed). One of the 15 rules; the rule must belong to the route. |
| `expect.tool` | The Kronk tool that should end up running: `play_music`, `control_music`, `now_playing`, `rate_music`, `news_brief`, `update_magicmirror`, or `null` when no existing tool can do it (then the live replay checks that no music tool claims it). |
| `expect.action`, `expect.thumb` | `control_music` action (`pause`, `resume`, `next`, `volume_up`, …; "stop" is sent as `pause` since sprint M7) and `rate_music` thumb. |
| `expect.direct` | `true` exactly on rows whose rule is `playback`, `now_playing`, `rate`, `music_another` or `mm_update`: Kronk runs the tool itself with no model call (`docs/features/direct-dispatch.md`). Absent on every other row; the schema test enforces both. |
| `expect.kind`, `media_type`, `name`, `artist`, `provider`, `shuffle` | What `play_music` should be asked for: `kind` `named`/`genre`; `media_type` one of the tool's types or `null` for a style; `provider` `pandora` / `youtube_music` / `nas` only when the sentence implies one. These are what M11's parser asserts. |
| `expect.player` | `origin` (the speaker that heard it), `area:<Area>`, `entity:media_player.<id>`, or `unknown:<words>` for a name no player has. |
| `source` | The incident doc, bench file or plan section the row came from. Paths are checked to exist. |
| `tags` | Free-form; `--tag` selects on them. Three tags change the replay: `needs-context` and `url` rows are skipped live, and `mirror` rows are skipped against production. |
| `xfail` | Per layer: `routing`, `live`, `parser`. Each value is the reason. |
| `note`, `ref` | Free text; the original trace id, first-seen date and count where known. |

What "expected" means, decided once so rows stay consistent:

- A request the pins are built for (a whole-utterance playback command, a
  request verb at the start, a bare "what's playing") expects the pin. A
  miss there is an `xfail.routing`.
- A request outside a pin's scope by design (it names a player or a room
  for playback or now-playing, it is composite, it needs the previous turn)
  expects `direct/default`: the coordinator delegates, one hop, by design.
- A mishearing expects what the speaker meant ("Pasta" expects pause) and
  carries an `xfail` pointing at speech recognition.

## Layer one: offline

`./scripts/run_tests.sh` runs it. Three kinds of test:

- schema: unique, well-formed ids; valid tiers, rules, tools, players;
  sources exist; every `xfail` has a reason;
- the plan's named utterances are present;
- routing: each row through `routing._classify_inner`. Rows with
  `xfail.routing` are strict xfails: they show as `xfailed` in the summary,
  and the day one starts passing the run fails with `XPASS(strict)`. Remove
  the marker in that commit. A changed route fails with the row id and the
  text: `corpus row plan-stop-bang: 'Stop!' routed home/playback, expected
  home/now_playing`.
- direct call (sprint M7): a row with `expect.direct` gets
  `routing.decide`'s direct call for its tool, with its `action`, `thumb`
  and `kind`; every other row gets none. A row that expects the direct path
  and carries `xfail.routing` is a strict xfail here too.

**M11 hook.** The bottom of `tests/test_voice_corpus.py` imports
`tool_service.music_resolve.parse_request` if it exists and then asserts,
for each `play_music` row, every field of `PARSER_FIELDS` the row states
(`xfail.parser` rows as strict xfails). Until the function exists the test is
not collected. M11 changes only the import name if it picks another, and the
call shape `PARSE(text, origin_area) -> dict`.

## Layer two: live replay

```bash
python3 scripts/voice_replay.py --label <name>          # full corpus on kronk-dev
python3 scripts/voice_replay.py --tag playback          # a subset by tag
python3 scripts/voice_replay.py --id plan-stop-bang     # one row (repeatable)
python3 scripts/voice_replay.py --check-only            # read-only checks; runs nothing
```

Result: `docs/bench/voice_replay_<UTC time>_<label>.json` (or `--out`). Per
row: status (`pass`, `fail`, `xfail`, `xpass`, `skip`), the checks with
expected and actual, latency, the Kronk trace id, the HA pipeline run id,
the spoken answer, and the dry-run line tool_service logged. Exit 0 when no
row that should pass failed, 1 when one did, 2 when a safety check refused
to start, 3 when the run was stopped mid-way. The committed baseline is
`docs/bench/voice_replay_2026-11-16_012824_m10-baseline.json`.

**Mirror rows (sprint M4b).** `update_magicmirror` runs only for the pinned
sentence "update the magic mirror" (`docs/plans/MAGICMIRROR_PLAN.md`). A row
expecting it passes only if the call was not refused; any other row that
reaches it passes only if the orchestrator logged `mm_update_refused`. On
dev the pinned rows stop at the missing SSH key (502). Rows tagged `mirror`
are skipped against production, where `MUSIC_DRY_RUN` does not cover the
mirror and the pinned row would start a real update.

A row is executed only when it would reach Kronk. Then the replay checks
tier, rule, tool, for `expect.direct` rows that the orchestrator logged the
tool call with `direct: true` (no model ran), and (from tool_service's
`music dry run:` line) the action and the target speaker. `xfail.routing` or `xfail.live` makes a failed row
`xfail`; a marked row that passes is `xpass` and is reported, not fatal (the
model varies run to run).

### Why it is silent

- **Dry-run gate.** It follows the pipeline to its Kronk (pipeline →
  conversation entity → Ollama config entry URL → `<base>/api/status`) and
  requires `tool_service.music_dry_run: true` there before anything runs and
  again before every executed row.
- **Production guard.** The `kronk` pipeline, HA's preferred pipeline, or any
  pipeline whose Kronk is not under `/dev/` needs `--allow-production`, and
  dry-run is still required.
- **HA-local rows are never executed.** With `prefer_local_intents` on, HA's
  sentence triggers and built-in intents act before Kronk is asked, and
  `MUSIC_DRY_RUN` cannot reach them. Each row is first put through HA's
  sentence-match debug (`conversation/agent/homeassistant/debug`, read-only).
  A trigger or a strict built-in match is compared there and not run
  (`method: checked` in the result). A built-in match that is fuzzy only is
  run, because the pipeline's local pass is strict
  (`async_handle_intents(..., strict_intents_only=True)` returns before the
  fuzzy stage in HA 2026.5.4); it is skipped if any player is playing.
- **Player check.** Every `media_player` state, title, artist and content id
  is read before the run and after every executed row; a change stops the
  run. The result file carries both snapshots.

## Gotchas

- **The blueprint hands off to production Kronk.** Its `conversation.process`
  calls use `conversation.ollama_conversation` whatever pipeline the request
  came in on. A "Hey Jarvis, play …" that matches the catch-all or loose
  trigger reaches production, not dev. Another reason trigger rows are never
  executed.
- **HA runs every matching trigger.** `async_handle_sentence_triggers` fires
  the callback of every trigger whose sentences match (default_agent.py,
  `recognize_all` loop), and the debug call shows only one template. "Play
  orbital radio." reports the catch-all template, not the radio one. The
  blueprint is `mode: single`, so which branch runs is decided by order.
  Trigger ids in the corpus are asserted only where a row states one.
- **HA's pipeline debug keeps 10 runs per pipeline.** A full replay rotates
  out every older `kronk-dev` run.
- **Model variance.** Kronk-tier rows go through gemma-4-e4b. The same row
  can pass one run and fail the next (the coordinator picking `ask_research`
  for a music search; the home agent writing its tool call as text). Read a
  live `fail` with the trace before calling it a regression; run the row
  again with `--id`.
- **Dev traffic stays out of the dashboards' data.** The replay's traces are
  rooted at `kronk-orchestrator-dev` and `kronk-voice-tracer-dev`; the M17
  filter `{ trace:rootService !~ ".*-dev" }` drops all of them.

## Blog hooks

- A regression suite for a voice assistant that never makes a sound: the
  three gates (dry-run behind the pipeline, production refusal, read-only
  sentence matching for anything HA would act on itself).
- "Expected failure" as a first-class result: recording what is broken
  without pinning it, and the strict-xfail tripwire for the day it heals.
- The stopping rule in practice: 18 routing misses kept as data instead of
  18 new regexes.
