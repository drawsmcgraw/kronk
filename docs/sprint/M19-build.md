# M19 build: a spoken lead-in that goes straight to the model

Build agent, 2026-11-17 (UTC), branch `refactor`. Criteria:
`docs/sprint/M19-criteria.md`. Tree before the build: `cf5f1d4`. No push, no
tag. This report is the last commit.

## Summary

- New first routing rule `lead_in`, route `direct`: "[filler ...] ask Kronk
  [to] <request>" at the start of the utterance skips every other rule and
  every direct dispatch. The coordinator gets `<request>` with its full tool
  menu; `origin.said` becomes `<request>` too. A lead-in with nothing after it
  is not one.
- The words (verb `ask`, name `Kronk`, six fillers) live in
  `config/lead_in.yaml`, loaded by the orchestrator's M21 loader (schema
  `LeadIn`, built-in default equal to the file). No mis-heard spelling of
  "Kronk" was added: captured data shows none.
- HA takes none of 26 lead-in sentences locally (read-only check on all three
  satellites). No HA change is needed; criterion 9's trigger is written up
  and recommended to hold.
- Dev: criterion 6 sentence ends in a `play_music` dry-run line in 10 of 10
  runs (14 of 15 across both batches). Latency medians 3.04 s, 3.04 s,
  7.40 s. Tests `3503 passed, 2 skipped, 23 xfailed`. Replay
  `{'pass': 110, 'xfail': 21, 'skip': 3, 'fail': 1}`, exit 1: one baseline
  row (`eval-skip-treat-me-my-wallet-son`, a `default` row M19 does not
  touch) failed once on model variance and passed 3 of 3 when rerun alone.

## Commits

| Commit | What |
| --- | --- |
| `7ab3221` | Rule, config file and loader, pipeline change, routing pin update, 13 corpus rows, `tests/test_lead_in.py`. |
| `e155c8a` | `xfail.live` on `m19-ask-kronk-what-song-is-this` (measured on dev after the first commit). |
| `f108cb4` | Docs. |
| this one | This report and `docs/bench/voice_replay_2026-11-17_090201_m19-build.json`. |

## What changed

`orchestrator/routing.py` (line numbers at HEAD):

| Line | Change |
| --- | --- |
| `routing.py:15-17` | Docstring points at the lead-in and `docs/features/lead-in.md`. |
| `routing.py:20, 25` | `functools` and `config_loader` imports. |
| `routing.py:268-281` | Comment block, `_LEAD_IN_SEP`, `_EMPTY_REQUEST` (punctuation or a lone "please" is no request). |
| `routing.py:284-289` | `_alternatives()`: one alternation of config words, longest first, each `re.escape`d, `\s+` between words. |
| `routing.py:291-298` | `_lead_in_pattern(verbs, names, fillers)`, `lru_cache(8)`: `[\W_]*(filler\b[\s,.!?;:]*)*(verb)[\s,]+(name)(?=sep|\Z)sep*(to\b\s*)?(?P<request>.*)`, `re.I | re.S`, used with `.match` (anchored). |
| `routing.py:301-305` | `lead_in_pattern()`: built from `config_loader.current().lead_in` on each call, so a reload is followed. |
| `routing.py:308-315` | `lead_in_request(text)`: the stripped request, or None. |
| `routing.py:347-361` | `Rule.request` and `Decision.request` (the model's text when it is not the utterance). |
| `routing.py:381-387` | `Rule("lead_in", "direct", ...)`, first in `RULES`. |
| `routing.py:420-425` | `decide()` fills `Decision.request`. |
| `routing.py:441-444` | `classify()`: span metadata `lead_in_chars`. |

`orchestrator/config_loader.py`: `:15-17` docstring; `:54-56` imports; `:65`
`LEAD_IN_FILE`; `:203-226` `LeadInWords` and `LeadIn` (strict; words of
`[A-Za-z0-9'-]` separated by single spaces, 1-40 chars; 1-10 verbs, 1-20
names, 0-20 fillers; no repeats ignoring case); `:270` `Config.lead_in`;
`:381-384` `_load_lead_in`; `:393` the stray-file scan knows the file;
`:433-441` loaded in `load()` with the same error and empty-file handling as
`tuning.yaml`; `:491-492` reload "changed" lines. `config_files.py` is
untouched (still byte-identical in both services).

`orchestrator/main.py:336-353`: `_run_pipeline` reads `decision.request`
before the lock; `model_text` is the request for a lead-in; `said` becomes the
request when it was the utterance (None stays None). `main.py:433, 523`: the
specialist and coordinator runs get `model_text`. History, trace input and the
`request` event keep the utterance.

`config/lead_in.yaml` (new), every key commented.

Tests: `tests/test_lead_in.py` (new, 122 tests); `tests/test_routing_pin.py`
and `tests/fixtures/routing_pin.jsonl` (below); `tests/test_voice_corpus.py`
(rule table 15 → 16); `tests/test_config_hardening.py` and
`tests/test_config_fix2.py` (three tests that break the YAML parser or the
validator for every file now expect one problem per orchestrator YAML file,
`tuning.yaml` and `lead_in.yaml`; the two-loader differential seeds from
`lead_in.yaml` too). `tests/voice_corpus.jsonl`: 13 rows.

With `main.py` reverted to `cf5f1d4` and everything else in place, 7 of the
new tests fail (the coordinator's text on 4 mounts, `said` on 3).

Docs: `docs/features/lead-in.md` (new), `README.md`,
`docs/features/voice-pipeline.md`, `docs/features/config-directory.md`,
`docs/features/direct-dispatch.md`, `docs/features/agents-as-tools-routing.md`,
`docs/features/voice-regression-corpus.md`.

## The routing pin (deliberate change)

`git diff cf5f1d4 --stat -- tests/test_routing_pin.py tests/fixtures/routing_pin.jsonl`:

```
 tests/fixtures/routing_pin.jsonl | 1281 ++++++++++++++++++++++++++++++++++++++
 tests/test_routing_pin.py        |   74 ++-
 2 files changed, 1347 insertions(+), 8 deletions(-)
```

- Fixture: 1281 lines appended, 0 removed or changed. All 6415 earlier rows
  keep their `(route, rule)`. The new rows are this rule's: the 13 corpus
  rows, 17 overlap lines and the lead-in variants (`default` 772, `lead_in`
  401, `mm_update` 93, `music` 15; the last two are near misses such as "tell
  Kronk to update the magic mirror", decided by the old rules). Appended with
  a scratch script in `make_routing_pin.py`'s order, not regenerated: the 308
  strings recorded from the suite at `7c83f36` are not reproducible.
- `test_routing_pin.py`, the 8 deleted lines: the "15 outcomes" comment (2),
  `ORDER`'s and `ROUTES`' first lines (now start with `lead_in`), the
  frozen-matchers comment, and the count in two test names and one assert
  (`test_fifteen_…` → `test_sixteen_…`, `== 15` → `== 16`). Added:
  `FROZEN_LEAD_IN` (the pattern built from the shipped file) and its matcher,
  17 `OVERLAPS` lines, a lead-in block in `variants()`, the docstring note, and
  `test_lead_in_matches_its_frozen_copy`.

## Criteria

1. **Pass.** `routing.RULES[0]` is `lead_in` (`routing.py:385`).
   `test_lead_in_is_cut_off` (25 forms: casing, "to" or not, "hey", "okay",
   "OK", "please", "can you", "could you", commas, full stops, leading space,
   a newline), `test_coordinator_gets_the_request_without_the_lead_in` (4
   mounts: the coordinator's user message is "pause"),
   `test_coordinator_gets_its_full_tool_menu` (same menu as a `default`
   request). Live: every lead-in request on dev logged `route_shortcut`
   `lead_in` and reached the coordinator.
2. **Pass.** Rule first; `talkie_explicit` and `direct_override` need not stay
   ahead ("ask Kronk to ask talkie …" asks Kronk; `direct_override` goes to the
   coordinator anyway). `test_lead_in_skips_every_shortcut`: each of the 14
   other rules' sample requests is that rule bare and `lead_in` with three
   lead-in forms, no direct call. `test_no_direct_dispatch_with_the_lead_in`:
   no POST to tool_service for four M7 commands behind the lead-in. Pin: 16
   outcomes, every earlier row unchanged. Live `/dev/api/chat`: "ask Kronk to
   pause" → `ask_home` → `control_music` (5.17 s; bare "pause" right after:
   direct, 0.02 s), "ask Kronk to play some jazz" → `play_music`, "ask Kronk
   what song is this" → coordinator (no tool, see 6/Gotchas), "ask Kronk to
   search the web for the tallest building in Ohio" → `ask_research` →
   `web_search`. None hit a shortcut.
3. **Pass.** `config/lead_in.yaml`, `config_loader.LeadIn`.
   `test_shipped_file_equals_the_builtin_default`,
   `test_a_bad_file_is_refused_with_its_line` (9 cases: unknown key, wrong
   type, empty lists, case-insensitive duplicate, punctuation, double space,
   a number, a regex), `test_a_reloaded_name_takes_effect` (`changed:
   ["lead_in.yaml:names: ['Kronk'] -> ['Kronk', 'Crank']"]`, then "ask Crank
   to pause" is a lead-in), `test_a_bad_reload_keeps_the_words_in_effect`,
   `test_a_missing_file_falls_back_to_the_defaults_at_startup`. Dev after the
   rebuild: `config_loader.status()` ok, `lead_in.yaml` digest `b495b0afa87f`,
   no warnings. Mis-heard forms: none found, none added (section below,
   `test_no_invented_mishearing`).
4. **Pass.** It is neither a way around the pin nor a break of it:
   `origin.said` is the request, a suffix of the user's own last message,
   and the lead-in cannot overlap "update the magic mirror", so it holds the
   pinned sentence exactly when the message does. "ask Kronk to update the
   magic mirror" goes to the model, not to `mm_update`'s direct call, and the
   update runs when the model calls the tool. Tests, all four mounts:
   `test_lead_in_mirror_update_runs_when_the_model_calls_it` (2 sentences, one
   POST each), `test_lead_in_does_not_widen_the_mirror_pin` (4 near misses:
   `mm_update_refused`, no POST),
   `test_lead_in_from_an_earlier_turn_cannot_start_the_update` (3 shims),
   `test_said_stays_none_when_the_turn_has_no_words_of_its_own`. Live dev:
   "ask Kronk to update the magic mirror" → `home:update_magicmirror`, not
   refused, 502 at the missing key ("I couldn't update the magic mirror.
   Mirror preflight failed: SSH key not found at /keys/kronk-mm-upd…"); "ask
   Kronk to update the mirror" → tool reached, refused: "To update the magic
   mirror, say "update the magic mirror"."
5. **Pass.** "can you ask Kronk to …" is a lead-in (filler). "what happens if
   I ask Kronk to pause" is not: the pattern is anchored with `.match` at the
   start, so the rest of the table decides (`default`); live it got an
   explanation and no tool call. A lead-in with nothing after it ("Ask
   Kronk.", "ask Kronk to", "ask Kronk to please") is not one: it goes to the
   coordinator as said, never as an empty request. Live: "Ask Kronk." → "How
   can I help you?"; "ask Kronk to" → "Ask Kronk to what? Please complete
   your request." `test_not_a_lead_in` (17 cases),
   `test_a_bare_lead_in_gets_a_plain_answer`, `test_long_input_is_fast`
   (10 kB of fillers, under 1 s).
6. **Pass, 10 of 10.** "ask Kronk to find some deep house music and play
   it", `/dev/voice/api/chat`, Office stamp, `MUSIC_DRY_RUN=1`: every run
   `lead_in` → `ask_home` → `play_music` → a dry-run line (table below). In
   the latency batch it was 4 of 5: run 3 (`79a3b97f9dbadca120eb3b7609a8df7e`)
   asked research first and passed `play_music` a query from the search
   results, which tool_service could not find ("I couldn't play that. I
   couldn't find anything called Deep House Best…"). 14 of 15 overall.
7. **Pass.** `route_shortcut` `{rule: "lead_in", route: "direct"}` (the shape
   of every non-direct shortcut), `routing.decide` span `kronk.rule =
   "lead_in"` and `kronk.lead_in_chars`. `test_classify_logs_the_rule`,
   `test_pipeline_trace_names_the_rule` (event order, no `direct` tool calls,
   span attributes, root `kronk.route = direct`). Live dev logs carry the
   event for every lead-in request; the replay read `rule` from it.
8. **Done (table below).** 26 sentences, sentence-match debug on all three
   satellites, read-only. Same answer on each satellite for every sentence.
   No trigger, no strict built-in intent: HA takes none of them before Kronk.
9. **No HA change made. None needed.** Proposal below, recommended: hold.
10. **Pass.** The replay executed all 13 lead-in rows through `assist_pipeline/run`
    (`start_stage: intent`) on `kronk-dev`. Each reached `orchestrator-dev`
    with an `origin` event (Kitchen or Office, as the row's satellite) and was
    routed `direct/lead_in` (the two negatives `direct/default`). Table below.
11. **Pass.** 13 rows (tag `lead-in`): the two captured transcripts, the
    plan's sentence, pause, turn it up, play some jazz, jazz in the office,
    what song is this, what's playing, web search, mirror, and two negatives.
    No mis-heard rows: none in captured data. Layer one asserts the rule
    (`test_corpus_routing`); replay: 12 pass, 1 xfail
    (`m19-ask-kronk-what-song-is-this`, `xfail.live` with its reason).
12. **Pass (stated, measured).** Slow path by design: the coordinator, plus
    the specialist it delegates to; three hops when it asks research first.
    Table below.
13. **Pass.** `3503 passed, 2 skipped, 23 xfailed` (baseline `3341 passed,
    2 skipped, 23 xfailed`; +162 = 122 in `test_lead_in.py`, 39 from the 13
    corpus rows (schema, routing and direct-call tests each), 1 new pin test
    `test_lead_in_matches_its_frozen_copy`; the 1281 fixture rows run inside
    existing tests).
    New tests use `no_network`, fake tool_service and model, and `tmp_path`.
14. **Fail on the letter, one row; model variance.** Every baseline row has
    its baseline status except `inc-0930-search-deep-house-ytm` (fail → pass,
    a known flapper) and `eval-skip-treat-me-my-wallet-son` (pass → fail: the
    coordinator said "I will skip the music." and called no tool, trace
    `88857423d70501e16d833a4de62eb2bb`). That row routes `default`; M19
    changes neither its route nor its text nor its `said`. It passed in 43
    of 43 earlier replays on file (2 skips), and 3 of 3 when rerun alone with
    `--id` right after. Not marked.
15. **Pass.** Dry-run checked before every live request; all live traffic
    to `127.0.0.1/dev/…` or the `kronk-dev` pipeline; HA's sentence debug and
    pipeline-debug reads only. nginx: 0 POSTs to production chat routes since
    08:40 UTC (125 to `/dev/`). Production `tool_service`: 0 mirror, ops,
    fetch, http_check or reload lines. HA up 33 h and MA up 12 days, as
    before. Only `orchestrator-dev` was recreated (`tool_service-dev` kept
    running: unchanged image). Production runs from
    `kronk-releases/sprint-M7` with its own `config/`, which does not have
    `lead_in.yaml`. Replay: "players unchanged (17 checked)". Nothing sent
    to the Pi.
16. **Pass.** The user rule is in `README.md` (routing step 1),
    `docs/features/voice-pipeline.md` and `docs/features/lead-in.md`. The
    Whisper prompt addition is left to M13 and noted in both feature docs.

## Mis-heard forms of "Kronk": what the data shows

| Source | What was searched | Found |
| --- | --- | --- |
| HA pipeline debug (`assist_pipeline/pipeline_debug/list` + `/get`, every pipeline) | 16 runs held in memory (HA up 32 h; 10 per pipeline): 6 on `kronk`, 10 on `kronk-dev` | All typed text (no `stt-end`); none names Kronk |
| Production orchestrator log (journald, `CONTAINER_NAME=kronk-orchestrator-1`, 2026-09-12 to 2026-11-17) | 521 `request` events; 143 distinct spoken transcripts (Whisper's leading space) | 3 name Kronk, all spelled "Kronk": " Ask Kronk to play Groove Armada Radio on Pandora." (rid `f1365f17`, 2026-11-01, Kitchen), " Ask Kronk to ask the home agent to play French Dinner Radio from Pandora." (`3dc003d6`, 2026-11-07, Kitchen), " Tell Kronk to start an orbital radio to play on YouTube music." (2026-10-04). Searched for crank, cronk, chronic, krong, krunk, kron, conk, clonk and similar: none |
| Dev orchestrator, both tool_service logs, voice_tracer | Container logs since their start | Only replay copies of the "Tell Kronk" row |
| `docs/incidents/`, `docs/VOICE_SETUP.md`, plans, features | Same word list | None ("chronic" only as an English word in the 2026-06-09 hang investigation) |
| `tests/voice_corpus.jsonl`, `docs/bench/routing_eval.jsonl` | Same | Only "Tell Kronk to start an orbital radio…" |

Limit: only requests that reached Kronk are logged. A lead-in mis-heard badly
enough to match an HA trigger would never appear here; M12's transcript and
audio capture is the source that would show it.

## Criterion 8: what HA does with lead-in sentences

`conversation/agent/homeassistant/debug`, `language: en`, each sentence with
the device id of `kitchen_pe`, `office_sat1` and `basement_pe` (the replay's
devices); classified as `scripts/voice_replay.py` does. All three satellites
gave the same result for every sentence. "Taken locally" means a trigger or a
strict built-in match; a fuzzy-only match is skipped by the pipeline's strict
local pass (HA 2026.5.4, `async_handle_intents(..., strict_intents_only=True)`).

| # | Sentence | HA's debug | Taken locally |
| --- | --- | --- | --- |
| 1 | ask Kronk to pause | partial HassMediaPause (match false) | no |
| 2 | Ask Kronk to pause. | partial HassMediaPause (match false) | no |
| 3 | ask Kronk to pause the music | fuzzy HassMediaPause only (strict pass misses) | no |
| 4 | ask Kronk to stop | partial HassFanSetSpeed (match false) | no |
| 5 | ask Kronk to play jazz in the office | fuzzy HassMediaPrevious only (strict pass misses) | no |
| 6 | ask Kronk to play some jazz | fuzzy HassMediaPrevious only (strict pass misses) | no |
| 7 | Ask Kronk to play Groove Armada Radio on Pandora. | partial HassFanSetSpeed (match false) | no |
| 8 | ask Kronk to find some deep house music and play it | partial HassFanSetSpeed (match false) | no |
| 9 | ask Kronk to turn it up | partial HassFanSetSpeed (match false) | no |
| 10 | ask Kronk to turn up the volume | partial HassTurnOn (match false) | no |
| 11 | ask Kronk what's playing | no local match | no |
| 12 | ask Kronk what song is this | no local match | no |
| 13 | ask Kronk to skip this song | fuzzy HassMediaNext only (strict pass misses) | no |
| 14 | ask Kronk to resume | partial HassMediaUnpause (match false) | no |
| 15 | ask Kronk to set a timer for five minutes | partial HassFanSetSpeed (match false) | no |
| 16 | ask Kronk what time it is | fuzzy HassGetCurrentTime only (strict pass misses) | no |
| 17 | ask Kronk what's the weather | fuzzy HassGetWeather only (strict pass misses) | no |
| 18 | ask Kronk to search the web for the best pizza in town | fuzzy HassMediaNext only (strict pass misses) | no |
| 19 | ask Kronk to update the magic mirror | partial HassTurnOn (match false) | no |
| 20 | ask Kronk to turn on the kitchen lights | fuzzy HassTurnOn only (strict pass misses) | no |
| 21 | hey, ask Kronk to pause | fuzzy HassMediaPause only (strict pass misses) | no |
| 22 | can you ask Kronk to play some jazz | fuzzy HassMediaPrevious only (strict pass misses) | no |
| 23 | ask Kronk | no local match | no |
| 24 | Ask Kronk to ask the home agent to play French Dinner Radio from Pandora. | partial HassTurnOn (match false) | no |
| 25 | ask Kronk to shuffle my playlist anime bangers | partial HassFanSetSpeed (match false) | no |
| 26 | ask Kronk to put on some jazz | partial HassFanSetSpeed (match false) | no |

The Kronk music blueprint's triggers (including the catch-all) match none of
them: its sentences start with the request verb. Production agrees: the two
spoken "Ask Kronk to play … Radio" requests in November reached the
orchestrator with no room appended, i.e. through the pipeline's fallback, not
the blueprint's hand-off.

## Criterion 9: proposal for the coordinator

**Recommendation: hold. No HA change is needed today.** The plan's
conversation trigger exists to stop HA's intents or the music blueprint from
taking a lead-in sentence; the table shows they take none, so Kronk already
sees every one, with the origin stamp.

Adding the trigger anyway would make things worse: `conversation.process`
cannot carry `trigger.device_id` (no such field in HA 2026.5.4), so the
request would reach Kronk without the origin stamp and music would go to the
default player instead of the puck that heard it. The blueprint works around
that by appending "in the <area>", which would change the meaning of a
non-music request. It would also hand dev-pipeline requests to production
Kronk (the blueprint gotcha in `docs/features/voice-regression-corpus.md`).

If a later HA upgrade or blueprint change starts taking lead-in sentences
(rerun the table: `conversation/agent/homeassistant/debug` on the 26
sentences above), the change would be:

```yaml
# Settings → Automations → new, YAML mode (no restart; the automation
# editor saves through /api/config/automation/config/<id> and reloads
# automations only).
alias: Kronk lead-in
mode: parallel
triggers:
  - trigger: conversation
    command:
      - "ask kronk [to] {request}"
actions:
  - action: conversation.process
    data:
      agent_id: conversation.ollama_conversation   # production; kronk-dev is conversation.kronk_dev
      text: "ask Kronk to {{ trigger.slots.request }} in the {{ area_name(trigger.device_id) }}"
    response_variable: kronk
  - set_conversation_response: "{{ kronk.response.speech.plain.speech }}"
```

The text keeps the lead-in, so the orchestrator's rule is the "use the
model" flag; the area is the only origin `conversation.process` can carry.
HA prefers the trigger with the most literal words, so "ask kronk" should
win over the blueprint's catch-all; confirm with the corpus (the 13 `lead-in`
rows would become `ha_local`/trigger rows). Undo: delete the automation in
the UI (or `DELETE /api/config/automation/config/<id>`), no restart. Not
applied.

## Latency (criterion 12)

`/dev/voice/api/chat`, Office stamp, non-streaming, one second apart, from
the host. Before: the dev image running before the rebuild
(`routing.lead_in_request` absent; the sentences rode `default`). After:
`orchestrator-dev` recreated at 08:55:47 UTC from `7ab3221`. Client wall
seconds.

| Said | Before (runs 1-5) | Before median | After (runs 1-5) | After median |
| --- | --- | --- | --- | --- |
| ask Kronk to pause | 6.69, 6.4, 6.72, 2.76, 3.32 | 6.40 | 6.5, 3.04, 3.02, 2.55, 3.43 | 3.04 |
| ask Kronk what song is this | 2.59, 1.81, 2.47, 2.58, 2.59 | 2.58 | 3.31, 0.31, 5.13, 3.04, 1.47 | 3.04 |
| ask Kronk to find some deep house music and play it | 7.18, 16.69, 4.05, 3.52, 5.06 | 5.06 | 5.76, 5.29, 17.86, 19.01, 7.4 | 7.40 |

Same path both times: the coordinator, then `ask_home` (or no tool). The
lead-in adds no measurable cost; the spread is the model's. The 16-19 s runs
asked research first. Through HA (replay, `kronk-dev`): the lead-in rows took
1.19-6.69 s. For comparison, bare "pause" is a direct call: 0.02 s.

| Said | Run | Before trace | After trace |
| --- | --- | --- | --- |
| ask Kronk to pause | 1 | `896167613a46c40a027ccd480bc1c562` default, ask_home, control_music | `86f410064887a085d3572d06cb8f8491` lead_in, ask_home, control_music |
| ask Kronk to pause | 2 | `fe6e5ca7bcbe0600543b0fd31d03bf25` default, ask_home, control_music | `11abed8fd03336a20bc23c69e794fb0c` lead_in, ask_home, control_music |
| ask Kronk to pause | 3 | `eb3d46342f46996e3f42f8431cc91279` default, ask_home, control_music | `47618204ecdfc720bf0f7f76dcdf244b` lead_in, ask_home, control_music |
| ask Kronk to pause | 4 | `35af11456314bf44795335cd8594550e` default, ask_home, control_music | `f71b4fd81f304401dd94404c87c7ce1f` lead_in, ask_home, control_music |
| ask Kronk to pause | 5 | `b907b90a39b93bcdd02c4fe903f6e3f1` default, ask_home, control_music | `b6e37011098386c78073bbb6bdbb7a69` lead_in, ask_home, control_music |
| ask Kronk what song is this | 1 | `ca412b7985dc79cdd36c786673481a6f` default, no tool | `19d1a9c44e430c18fb153b016dbf6a9c` lead_in, no tool |
| ask Kronk what song is this | 2 | `eea31ebe51cc902d196381ae1019f541` default, no tool | `de67335ae724d03b7c3e3b9fbe342f36` lead_in, no tool |
| ask Kronk what song is this | 3 | `f7d946887e650af983fe41f4c8798bd2` default, no tool | `c32ddbb45b232ee3614434792c52cc0d` lead_in, no tool |
| ask Kronk what song is this | 4 | `371e9a0c87fcd58ec4d177716359f5db` default, no tool | `d2d44cabd3e39a547be18e67ad02f4f4` lead_in, no tool |
| ask Kronk what song is this | 5 | `acef1ccc3396d352859f99e311e0804d` default, no tool | `2eaf8abb881edc418f627adf9bac72ee` lead_in, no tool |
| ask Kronk to find some deep house music and play it | 1 | `db3b0d30c9390f48fb238508094c4c13` default, ask_home, play_music | `1fefc29f3dcad6c6d3bac36ad4dc2e3b` lead_in, ask_home, play_music |
| ask Kronk to find some deep house music and play it | 2 | `c55206ff3d5d5d36ef8d1abcf6d4fccd` default, ask_research, web_search, ask_home, play_music | `cfe299665848872aae99dff4d86c66b2` lead_in, ask_home, play_music |
| ask Kronk to find some deep house music and play it | 3 | `3033e9d642c44dca99e5d458c36f9ffe` default, ask_home, play_music | `79a3b97f9dbadca120eb3b7609a8df7e` lead_in, ask_research, web_search, ask_home, play_music |
| ask Kronk to find some deep house music and play it | 4 | `633a1272fe5fe5d264bcced55fc38744` default, ask_home, play_music | `63a891d65777ca5f301d485df0b7e31a` lead_in, ask_research, web_search, ask_home, play_music |
| ask Kronk to find some deep house music and play it | 5 | `4fdc425169b88bf3735f76b2738707bd` default, ask_home, play_music | `3291a049579825e19fc79f21dcdb611f` lead_in, ask_home, play_music |

### Criterion 6 runs

| Run | s | Rule | Tools | Dry-run line | Trace |
| --- | --- | --- | --- | --- | --- |
| 1 | 2.18 | lead_in | ask_home, play_music | would play Fantastic Plastic Machine | `bc64f7ab331f96da13079c3b72debfaa` |
| 2 | 3.21 | lead_in | ask_home, play_music | would play Groove Armada | `29848cc4d4a1841640a41c17141ac619` |
| 3 | 4.87 | lead_in | ask_home, play_music | would play Fantastic Plastic Machine | `735c3fa8585c2010c465722840816a4f` |
| 4 | 3.21 | lead_in | ask_home, play_music | would play Groove Armada | `4f4a29ade008aaeb7c783c2c03f86cc1` |
| 5 | 2.39 | lead_in | ask_home, play_music | would play Fantastic Plastic Machine | `b64d3e993b4b1a0d39f14c00d0b5d49b` |
| 6 | 7.62 | lead_in | ask_home, play_music | would play Groove Armada | `ef2253cb1ade12fac9e607bd7c99e100` |
| 7 | 7.05 | lead_in | ask_home, play_music | would play Groove Armada | `07f381758e12798c2c6486100bbb04cc` |
| 8 | 4.69 | lead_in | ask_home, play_music | would play Fantastic Plastic Machine | `a8b1b8bc50d37ea46f3070eaa0a775f7` |
| 9 | 3.77 | lead_in | ask_home, play_music | would play Fantastic Plastic Machine | `df87536ccd1bb175522bd3efae769c5d` |
| 10 | 3.81 | lead_in | ask_home, play_music | would play Fantastic Plastic Machine | `2a52f524664191be04f8dd6d5f308db3` |

Each dry-run line continues "…, for deep house, on the Office speaker.
Nothing was sent to the speaker."

### Criterion 10: lead-in rows through HA (`kronk-dev`)

| Row | Status | s | Route/rule | Tools | Origin area | Dry run | Trace | HA run |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `inc-1101-ask-kronk-groove-armada-radio` | pass | 4.97 | direct/lead_in | ask_home, play_music | Kitchen | play ['media_player.home_assistant_voice_0ac919_pe_01'] | `3e105ab87e11a68798e7a5cf8a92af59` | 01M7MC401QQCGB33ZS0HKKEFKJ |
| `inc-1107-ask-kronk-ask-home-french-dinner` | pass | 4.92 | direct/lead_in | ask_home, play_music | Kitchen | play ['media_player.home_assistant_voice_0ac919_pe_01'] | `bc4b0bf3bc12e9f606d9f2d67317e2c3` | 01M7MC46CJNQPY9YSFPVNG7BY9 |
| `m19-find-deep-house-and-play-it` | pass | 4.67 | direct/lead_in | ask_home, play_music | Office | play ['media_player.satellite1_d8d1bc'] | `1d2ff84339290943ed3217f6dbecb6d8` | 01M7MC4CMT2JCSRYMDWRPMVC6D |
| `m19-ask-kronk-to-pause` | pass | 5.45 | direct/lead_in | ask_home, control_music | Office | — | `2abf6b281321f160c02520ef8232a7c3` | 01M7MC4JQ345JTE6JKS9Y16DKT |
| `m19-ask-kronk-to-play-some-jazz` | pass | 1.19 | direct/lead_in | ask_home, play_music | Office | play ['media_player.satellite1_d8d1bc'] | `4bf18641706a953bc5de9a8496d662ae` | 01M7MC4SFFE9W260X624EAM1GH |
| `m19-ask-kronk-play-jazz-in-the-office` | pass | 6.69 | direct/lead_in | ask_home, play_music | Kitchen | play ['media_player.satellite1_d8d1bc'] | `894725d75b1ce3cad70125d9179a9d50` | 01M7MC4W3KPK0QFH330DS7ZETA |
| `m19-ask-kronk-what-song-is-this` | xfail | 2.46 | direct/lead_in | — | Office | — | `14d4172922db5999d7bddcf129fe3bb1` | 01M7MC543C8GVVKVNFEEDVHH7X |
| `m19-hey-ask-kronk-whats-playing` | pass | 2.38 | direct/lead_in | ask_home, now_playing | Office | — | `f7bb72d6e656b429578a8caa4498aefa` | 01M7MC58192FGCVD8R10ZHCB3N |
| `m19-can-you-ask-kronk-to-turn-it-up` | pass | 4.13 | direct/lead_in | ask_home, control_music | Office | — | `827989953c0cddcf34183c68760bb400` | 01M7MC5BSS5QBSJJ9KGQAJPG1W |
| `m19-ask-kronk-search-the-web` | pass | 5.58 | direct/lead_in | ask_research, web_search | Office | — | `ce0290cfcea891e6da5e9138a5fc2d56` | 01M7MC5HA9C8984AG1JB0RRXCW |
| `m19-ask-kronk-update-the-magic-mirror` | pass | 3.54 | direct/lead_in | ask_home, update_magicmirror | Office | — | `e272f75beb3264a226cae5d881b57ed6` | 01M7MC5R69PT2WQQXCTPQCWBQR |
| `m19-what-happens-if-i-ask-kronk-to-pause` | pass | 2.37 | direct/default | — | Office | — | `a9dd6cfcef40c172110299e0a71b4fda` | 01M7MC5X62F2Z7D660652FEHN0 |
| `m19-ask-kronk-alone` | pass | 1.36 | direct/default | — | Office | — | `47f3d1848727c5e7dcb4c90b12497b9b` | 01M7MC6116GJPFDYMW4FH8QZGX |

`control_music` and `now_playing` log no dry-run line when nothing is playing
(tool_service answers from state: "already paused", "Nothing is playing").
The replay checked the HA-local gates first: 3 of the 13 were fuzzy-only
matches (run because no player was active), the rest partial or no match.

## Tests and replay, verbatim

```
$ ./scripts/run_tests.sh -q --no-header -p no:cacheprovider
3503 passed, 2 skipped, 23 xfailed, 6 warnings in 24.76s
```

```
$ python3 scripts/voice_replay.py --label m19-build
voice_replay: pipeline kronk-dev → http://localhost/dev/voice, dry-run on, 135 rows (0 not selected)
…
  F fail  executed   eval-skip-treat-me-my-wallet-son  [tool []]
…
voice_replay: {'pass': 110, 'xfail': 21, 'skip': 3, 'fail': 1}; executed 66, checked read-only 66; players unchanged (17 checked)
  skipped inc-1017-try-playing-it-ytm: follow-up that needs conversation history; a replay has none
  skipped inc-1019-ytm-link: typed link; a spoken request carries no URL
  skipped m4b-fetch-and-run-on-the-mirror: typed link; a spoken request carries no URL
voice_replay: wrote /home/drew/git-repos/drawsmcgraw/kronk/docs/bench/voice_replay_2026-11-17_090201_m19-build.json
exit=1
```

Against `docs/bench/voice_replay_2026-11-16_012824_m10-baseline.json`: 114
baseline rows, 2 status changes (`inc-0930-search-deep-house-ytm` fail →
pass, known flapper; `eval-skip-treat-me-my-wallet-son` pass → fail, see
criterion 14), 21 rows new since the baseline (8 from M4b: 7 pass, 1 skip;
13 from M19: 12 pass, 1 xfail). The failed row, rerun alone three times:

```
$ python3 scripts/voice_replay.py --id eval-skip-treat-me-my-wallet-son --out …/rerun_wallet_N.json   (N = 1, 2, 3)
  . pass  executed   eval-skip-treat-me-my-wallet-son
voice_replay: {'pass': 1}; executed 1, checked read-only 0; players unchanged (17 checked)
```
(identical for each of the three runs)

## Departures from the criteria

1. **Criterion 14, one row.** Reported above, not marked. The full replay was
   not rerun: the rerun allowance covers a busy player, and the row-level
   reruns answer the question.
2. **`origin.said` is stripped.** The criteria allow either reading; the
   stripped request is chosen so tool_service resolves the sentence as it
   would without the lead-in (`radio_name()` anchors on the verb). Shown live:
   `query='French Dinner Radio' media_type='radio'` and the Pandora station
   dry-run line for the captured French Dinner transcript.
3. **"clear my history" keeps its transport intercept.** It runs before
   routing, so "ask Kronk to clear my history" still clears. It is not a
   routing rule and no model has a tool for it
   (`test_clear_history_is_still_the_transport_intercept`).
4. **Fillers.** "hey", "okay" and the comma as the criteria list; "ok",
   "please", "can you" (criterion 5), "could you". "so", "um" and wake words
   ("Okay Nabu") are not fillers; none is in captured data in front of a
   lead-in.
5. **ROADMAP not touched.** It carries no sprint items (M7 added none
   either); the sprint plan is the source. The coordinator may add a Shipped
   line at promotion.

## Findings for the coordinator

- **The coordinator does not delegate "what song is this".** 0 of 11 dev runs
  through the lead-in, 0 of 5 before M19 through `default`; it answers "I
  cannot identify a song". One run (`c32ddbb45b232ee3614434792c52cc0d`)
  spoke its own reasoning ("what song is this", I need to inform the user…).
  "What's playing?" behind the lead-in was delegated. Pre-existing coordinator
  behaviour, now visible because the lead-in sends the request there. The
  corpus row carries `xfail.live`.
- **Research first.** For "find some deep house music and play it" the
  coordinator asked research before home in 2 of 16 lead-in runs on dev
  (1 of 5 before M19), at 16-19 s; one of the two passed a search-result
  title to `play_music` that did not resolve.

## What the coordinator must do at promotion

1. Production runs from `kronk-releases/sprint-M7` with its own `config/`
   copy. The release for M19 must include `config/lead_in.yaml`: without it,
   the new orchestrator reports `config/lead_in.yaml: file is missing` and
   runs on built-in defaults for all of `config/` (the words are the same,
   but the load is refused as a whole).
2. Rebuild the orchestrator only (`tool_service` is unchanged), restart
   nginx, verify through `/api/chat` and `/voice/api/chat` with sentences HA
   does not handle and that make no sound: "ask Kronk what time it is" or
   "ask Kronk to search the web for …" (no music; the mirror sentence starts
   a real update in production).
3. No HA change (criterion 9: hold).
4. Ask Drew for a real "Okay Nabu, ask Kronk to …" on one puck when the
   house is free.
5. M13 owns adding "Kronk" to the Whisper prompt; M12's capture is where a
   mis-heard "Kronk" would first show. Any spelling found goes into
   `config/lead_in.yaml` `names` with a corpus row.

Dev pair left running and healthy (`orchestrator-dev` on `7ab3221`,
`tool_service-dev` unchanged, dry-run on). `config/` is as committed.
