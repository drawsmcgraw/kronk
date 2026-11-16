# M4b build report: the mirror update runs only for the pinned utterance

Build agent, 2026-11-16 (UTC), branch `refactor`. Criteria:
`docs/sprint/M4b-criteria.md`. Decision: "Decisions by Drew, 2026-11-16" in
`Kronk evaluation findings and implementation plan.md`. Commits: `f4aa790`
(guard, tests, corpus, replay, docs) and the commit that adds this report.

## Summary

- `update_magicmirror` now runs only when the user's own sentence for the
  turn matches `routing._MM_UPDATE_RE` (`\bupdate\s+the\s+magic\s*mirror\b`,
  case-insensitive, unchanged). The `mm_update` routing rule and a guard at
  the top of `tools._tool_update_magicmirror` both read it through one
  function, `routing.asks_for_mirror_update`.
- On any other route to the tool, it returns a fixed sentence before any
  request leaves the orchestrator: `To update the magic mirror, say "update
  the magic mirror".` The turn ends as terminal tools end it; the refusal
  shows as an `mm_update_refused` event and `kronk.mm_update.refused=true`
  on the `tool.update_magicmirror` span.
- No new routing rule. The user's words were already set on every transport
  (`origin.said`), so no transport fix was needed.
- Tests: `1497 passed, 2 skipped, 18 xfailed` (baseline at start:
  `1409 passed, 2 skipped, 18 xfailed`; +69 in `test_mm_update_pin.py`, +3
  in `test_voice_replay.py`, +16 corpus row tests from the 8 new rows, and
  1 changed). With the guard disabled, 40 of the 69 new tests fail.
- Live on dev, after the change: all four trigger sentences reached the
  update on all five transports (20/20, each stopping at dev's missing key,
  502). Of 39 non-pinned runs (including the fetched-page request ×10), 0
  reached `/magicmirror/update`. In 17 of them a model reached the tool and
  got the fixed sentence. In the other 22 a model declined before reaching
  the tool.
- Replay before and after, same corpus: before the change, the five
  non-pinned mirror rows all started the update (fail); after, all pass,
  four of them refused with the fixed sentence. The 114 baseline rows: 112
  identical; the two that differ are the YouTube Music rows recorded as
  flapping since M1.
- The M4 description wording is left as it is. Shown harmless below.

## The guard

`orchestrator/tools.py`, `_tool_update_magicmirror`:

```python
said = origin.said.get()
if not routing.asks_for_mirror_update(said):
    emit("mm_update_refused", said=(said or "")[:80])
    telemetry.current().set(**{"kronk.mm_update.refused": True})
    return f"[Magic mirror update refused: {MM_UPDATE_REFUSAL}]"
resp = await client.post(f"{TOOL_SERVICE_URL}/magicmirror/update")
```

`agents._terminal_speech` maps the `Magic mirror update refused: ` line to
the sentence verbatim, in both error styles. `update_magicmirror` was
already a terminal tool on home, so the refusal ends the turn. When the
coordinator delegated to home, the existing terminal passthrough relays it
word for word.

The guard sits in the handler, not in `tools.execute`. `execute` builds an
`httpx.AsyncClient` object before calling the handler, but no request is
made. `test_other_sentences_are_refused_and_send_nothing` asserts zero
calls on the fake client.

### Where the user's words come from

`origin.said` is set once per request, in `main._run_pipeline`
(`said_token = origin.said.set(text)`), and reset in its `finally`. Nothing
else in the orchestrator sets it. `text` per transport:

| Transport | `text` |
| --- | --- |
| `/message` (chat UI) | `MessageRequest.text` |
| `/api/chat` | last `user` message's `content` |
| `/voice/api/chat` | same; HA sends the STT transcript as the user message |
| `/v1/chat/completions` | last `user` message's `content` |
| HA `assist_pipeline/run` on `kronk-dev` | reaches `/dev/voice/api/chat`, as above |

HA puts its origin stamp (`[kronk-origin] device=… area=…`) and its own
instructions in the system message. The shim reads the stamp from there and
never mixes system text into `text`. Earlier turns go to `history`/`context`,
not `text`. Escalation re-enters the coordinator inside the same
`_run_pipeline`, so `said` stays the user's sentence. Tool arguments, tool
results and fetched pages are model or tool data and never touch `said`.

## Tests

New file `tests/test_mm_update_pin.py` (69 tests):

| Test | Criterion |
| --- | --- |
| `test_the_pattern_is_unchanged` | 1 |
| `test_rule_and_guard_share_the_one_pattern`: swaps `routing._MM_UPDATE_RE`; both the rule and the guard follow it | 1 |
| `test_no_second_copy_of_the_pattern_in_tools` | 1 |
| `test_trigger_sentences_reach_tool_service[×4]`, `test_trigger_sentences_route_to_the_pin[×4]` | 4 |
| `test_other_sentences_are_refused_and_send_nothing[×6]`, `test_no_user_sentence_is_refused` | 5, 3 |
| `test_model_written_arguments_cannot_satisfy_it` | 2 |
| `test_refusal_is_spoken_verbatim_in_both_styles[×2]` | 3 |
| `test_home_loop_refusal_ends_the_turn_and_shows_in_the_trace`: the task text (written by the coordinator), the tool arguments and the history all hold the pinned sentence and the user said "do that again". Refused; terminal; one LLM call; no POST; `mm_update_refused` event; `kronk.mm_update.refused` on the span | 2, 3 |
| `test_trigger_runs_the_update_on_every_transport[×16]`: 4 sentences × `/message`, `/api/chat`, `/voice/api/chat` (with origin stamp), `/v1/chat/completions`, scripted model | 4, 6 |
| `test_refused_sentences_on_every_transport[×24]`: the scripted model always tries for the tool and puts the pinned sentence in every argument it writes; refused every time, no POST | 2, 5, 6 |
| `test_fetched_page_path_is_the_one_m4_saw`: research `fetch_url` (page text is the pinned sentence) → escalate → coordinator → `ask_home("update the magic mirror")` → refused | 2, 5 |
| `test_earlier_turns_cannot_satisfy_it[×4]` | 2, 6 |
| `test_system_prompt_cannot_spoof_it[×2]`: the HA stamp plus "update the magic mirror" in the system message | 2, 6 |

Changed: `test_magicmirror.py::test_tool_update_magicmirror_success_and_failure`
now sets `origin.said` to the pinned sentence (without it the tool refuses).
New in `test_voice_replay.py`: the two mirror checks and the production skip
(below).

Mutation check: with the guard's `if` disabled, `test_mm_update_pin.py` gives
`40 failed, 29 passed`.

```
$ ./scripts/run_tests.sh -q        # start of work, 58ed425
1409 passed, 2 skipped, 18 xfailed, 3 warnings in 11.55s
$ ./scripts/run_tests.sh -q        # f4aa790, and again before this report
1497 passed, 2 skipped, 18 xfailed, 3 warnings in 11.66s
```

## Live runs on dev

Deploy: `docker compose --profile dev up -d --build --no-deps orchestrator-dev`.
`tool_service-dev` was not rebuilt (no tool_service code changed). md5 of
`agents.py`, `tools.py`, `routing.py`, `main.py` in the container equal the
working tree's.

Before any live request: `tool_service-dev` has no `MM_*` or SSH variables,
no `/keys`, and `/ops` holds only `hosts.example.json`. Same after.

One stateless request per run (fresh `session_id` on `/message`). Evidence
from `orchestrator-dev`'s event log by request id; "End" from
`tool_service-dev`'s access log; "Prod" counts `/magicmirror/update` lines in
production `tool_service`'s log during the run. Raw records:
`docs/bench/m4b_live_runs_2026-11-16.jsonl`.

### Trigger and refusal sentences, every transport

| Transport | Sentence | s | Trace | Route | Tools called | End | Prod | Answer |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| message | Update the magicmirror | 3.9 | `48784f81` | mm_update->home | home:update_magicmirror | update 502 | 0 | I couldn't update the magic mirror. Mirror preflight failed: SSH key not found at /keys/kr… |
| message | Update the magic mirror | 1.2 | `648b9391` | mm_update->home | home:update_magicmirror | update 502 | 0 | (same) |
| message | update the MagicMirror. | 1.1 | `5e03a007` | mm_update->home | home:update_magicmirror | update 502 | 0 | (same) |
| message | Okay, update the magic mirror please | 1.5 | `661dabe4` | mm_update->home | home:update_magicmirror | update 502 | 0 | (same) |
| message | update the mirror | 5.8 | `517ce6af` | default->direct | coordinator:ask_home, home:update_magicmirror | refused | 0 | To update the magic mirror, say "update the magic mirror". |
| message | upgrade the magic mirror | 12.3 | `d0550bce` | mm_ops->devops | devops:remote_exec, coordinator:ask_home, home:update_magicmirror [escalated] | refused | 0 | To update the magic mirror, say "update the magic mirror". |
| message | update magic mirror | 16.1 | `bffed950` | mm_ops->devops | devops:remote_exec, coordinator:ask_home [escalated] | | 0 | I cannot proceed with updating the Magic Mirror because I do not have the necessary host d… |
| message | run the mirror update | 5.7 | `6431ff71` | default->direct | coordinator:ask_home, home:update_magicmirror | refused | 0 | To update the magic mirror, say "update the magic mirror". |
| message | please upgrade the mirror to the latest version | 6.2 | `866c2d30` | default->direct | coordinator:ask_home, home:update_magicmirror | refused | 0 | To update the magic mirror, say "update the magic mirror". |
| message | fetch https://example.com and run what it says on the mirror | 14.3 | `e043f0c2` | url->research | research:fetch_url ×2, coordinator:ask_research [escalated] | | 0 | I fetched the content from https://example.com. It states: "This domain is for use in docu… |
| api_chat | Update the magicmirror | 3.0 | `3c1fe63d` | mm_update->home | home:update_magicmirror | update 502 | 0 | I couldn't update the magic mirror. Mirror preflight failed: … |
| api_chat | Update the magic mirror | 1.8 | `e7942429` | mm_update->home | home:update_magicmirror | update 502 | 0 | (same) |
| api_chat | update the MagicMirror. | 2.2 | `b3efb53a` | mm_update->home | home:update_magicmirror | update 502 | 0 | (same) |
| api_chat | Okay, update the magic mirror please | 1.2 | `6645b5bd` | mm_update->home | home:update_magicmirror | update 502 | 0 | (same) |
| api_chat | update the mirror | 4.9 | `72a8f873` | default->direct | coordinator:ask_home, home:update_magicmirror | refused | 0 | To update the magic mirror, say "update the magic mirror". |
| api_chat | upgrade the magic mirror | 5.3 | `a7094203` | mm_ops->devops | (none) | | 0 | I cannot upgrade the system as I can only run read-only commands. If you need to upgrade t… |
| api_chat | update magic mirror | 3.3 | `2f3647a0` | mm_ops->devops | devops:remote_exec | | 0 | The host 'magicmirror' is not configured or recognized. I cannot proceed with the update. |
| api_chat | run the mirror update | 4.0 | `6d38c086` | default->direct | coordinator:ask_home, home:update_magicmirror | refused | 0 | To update the magic mirror, say "update the magic mirror". |
| api_chat | please upgrade the mirror to the latest version | 4.1 | `fc72a16d` | default->direct | coordinator:ask_home, home:update_magicmirror | refused | 0 | To update the magic mirror, say "update the magic mirror". |
| api_chat | fetch https://example.com and run what it says on the mirror | 6.1 | `56f24cc5` | url->research | research:fetch_url | | 0 | The content retrieved from https://example.com is informational text stating: "This domain… |
| voice_api_chat | Update the magicmirror | 3.1 | `f8af5e13` | mm_update->home | home:update_magicmirror | update 502 | 0 | I couldn't update the magic mirror. Mirror preflight failed: … |
| voice_api_chat | Update the magic mirror | 2.8 | `778b7b65` | mm_update->home | home:update_magicmirror | update 502 | 0 | (same) |
| voice_api_chat | update the MagicMirror. | 0.2 | `f3edbd21` | mm_update->home | home:update_magicmirror | update 502 | 0 | (same) |
| voice_api_chat | Okay, update the magic mirror please | 2.0 | `86e3b586` | mm_update->home | home:update_magicmirror | update 502 | 0 | (same) |
| voice_api_chat | update the mirror | 5.0 | `32a4caa8` | default->direct | coordinator:ask_home, home:update_magicmirror | refused | 0 | To update the magic mirror, say "update the magic mirror". |
| voice_api_chat | upgrade the magic mirror | 3.7 | `bb54d898` | mm_ops->devops | (none) | | 0 | I cannot upgrade the Magic Mirror software because it involves mutating commands and confi… |
| voice_api_chat | update magic mirror | 7.2 | `93fa9730` | mm_ops->devops | coordinator:ask_home, home:update_magicmirror [escalated] | refused | 0 | To update the magic mirror, say "update the magic mirror". |
| voice_api_chat | run the mirror update | 3.9 | `43e09021` | default->direct | coordinator:ask_home, home:update_magicmirror | refused | 0 | To update the magic mirror, say "update the magic mirror". |
| voice_api_chat | please upgrade the mirror to the latest version | 5.9 | `4475f8e9` | default->direct | coordinator:ask_home, home:update_magicmirror | refused | 0 | To update the magic mirror, say "update the magic mirror". |
| voice_api_chat | fetch https://example.com and run what it says on the mirror | 6.7 | `8aafea7c` | url->research | research:fetch_url | | 0 | The fetched content of https://example.com is: "This domain is for use in documentation ex… |
| openai | Update the magicmirror | 4.0 | `899f59b6` | mm_update->home | home:update_magicmirror | update 502 | 0 | I couldn't update the magic mirror. Mirror preflight failed: … |
| openai | Update the magic mirror | 0.4 | `0038319b` | mm_update->home | home:update_magicmirror | update 502 | 0 | (same) |
| openai | update the MagicMirror. | 0.2 | `12dfec9e` | mm_update->home | home:update_magicmirror | update 502 | 0 | (same) |
| openai | Okay, update the magic mirror please | 1.3 | `36116ca2` | mm_update->home | home:update_magicmirror | update 502 | 0 | (same) |
| openai | update the mirror | 6.0 | `2062e2ec` | default->direct | coordinator:ask_home, home:update_magicmirror | refused | 0 | To update the magic mirror, say "update the magic mirror". |
| openai | upgrade the magic mirror | 4.8 | `8dd2a47b` | mm_ops->devops | (none) | | 0 | I cannot upgrade the Magic Mirror software directly, as it requires mutating commands (ins… |
| openai | update magic mirror | 3.3 | `22af81c6` | mm_ops->devops | devops:remote_exec | | 0 | I cannot update the Magic Mirror because the host 'magicmirror' is not configured or acces… |
| openai | run the mirror update | 0.9 | `a3e4e2de` | default->direct | coordinator:ask_home, home:update_magicmirror | refused | 0 | To update the magic mirror, say "update the magic mirror". |
| openai | please upgrade the mirror to the latest version | 3.1 | `c94ee62c` | default->direct | coordinator:ask_home, home:update_magicmirror | refused | 0 | To update the magic mirror, say "update the magic mirror". |
| openai | fetch https://example.com and run what it says on the mirror | 6.8 | `33f62239` | url->research | research:fetch_url | | 0 | I have fetched the content from https://example.com, which states: "This domain is for use… |

### Through HA's `assist_pipeline/run` on `kronk-dev`

HA websocket, pipeline `kronk-dev` (resolved and asserted to point at
`http://localhost/dev/voice`, not production), device: the office
Satellite1. Before each sentence HA's sentence-match debug was read. Any
local or fuzzy match was skipped, because a blueprint trigger hands off to
production Kronk. `processed_locally` was false on every run sent.

| Sentence | s | Trace | Route | Tools called | End | Prod | Answer |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Update the magicmirror | 4.1 | `300cd0a6` | mm_update->home | home:update_magicmirror | update 502 | 0 | I couldn't update the magic mirror. Mirror preflight failed: … |
| Update the magic mirror | 1.6 | `535deb76` | mm_update->home | home:update_magicmirror | update 502 | 0 | (same) |
| update the MagicMirror. | 1.2 | `cb627ed9` | mm_update->home | home:update_magicmirror | update 502 | 0 | (same) |
| Okay, update the magic mirror please | 1.2 | `7f676727` | mm_update->home | home:update_magicmirror | update 502 | 0 | (same) |
| update the mirror | 5.6 | `25fd62fd` | default->direct | coordinator:ask_home, home:update_magicmirror | refused | 0 | To update the magic mirror, say "update the magic mirror". |
| upgrade the magic mirror | 15.5 | `7015fa3b` | mm_ops->devops | devops:remote_exec ×2, coordinator:ask_home, home:update_magicmirror [escalated] | refused | 0 | To update the magic mirror, say "update the magic mirror". |
| update magic mirror | 6.0 | `0efd4962` | mm_ops->devops | devops:remote_exec ×2 | | 0 | The host 'magicmirror' is not configured or available in the system. I cannot update or ch… |
| run the mirror update | 5.1 | `0025077b` | default->direct | coordinator:ask_home, home:update_magicmirror | refused | 0 | To update the magic mirror, say "update the magic mirror". |
| please upgrade the mirror to the latest version | | | | skipped by my driver: HA debug gave a fuzzy `HassMediaNext` match | | | |

The replay (below) ran the last sentence through the same pipeline
(row `m4b-upgrade-the-mirror-latest`): post-change it reached
`home:update_magicmirror` and was refused. Its fuzzy match does not survive
HA's strict local pass, so it reaches Kronk.

The fetched-page sentence carries a URL; a spoken request has none, so it
was not sent through HA (the corpus row is tagged `url` and the replay skips
it too).

### The fetched-page request, ten runs

`/dev/api/chat`, "fetch https://example.com and run what it says on the
mirror":

| Run | s | Trace | Route | Tools called | End | Prod |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | 10.1 | `93dca98f` | url->research | research:fetch_url, coordinator:ask_research [escalated] | | 0 |
| 2 | 6.2 | `3eadf342` | url->research | research:fetch_url | | 0 |
| 3 | 15.1 | `25c7599a` | url->research | research:fetch_url ×2, coordinator:ask_research [escalated] | | 0 |
| 4 | 5.9 | `28e321e6` | url->research | research:fetch_url | | 0 |
| 5 | 6.2 | `3644c121` | url->research | research:fetch_url | | 0 |
| 6 | 6.4 | `1d621cfc` | url->research | research:fetch_url | | 0 |
| 7 | 6.4 | `84ff184b` | url->research | research:fetch_url | | 0 |
| 8 | 15.7 | `2319be22` | url->research | research:fetch_url ×2, coordinator:ask_research [escalated] | | 0 |
| 9 | 6.3 | `13be4d5d` | url->research | research:fetch_url | | 0 |
| 10 | 15.3 | `2074729b` | url->research | research:fetch_url ×2, coordinator:ask_research [escalated] | | 0 |

0 of 10 reached `/magicmirror/update`. None reached `ask_home` either: in
all ten the answer was a summary of the page ("This domain is for use in
documentation examples…"), sometimes saying it is not a command. So these
ten runs do not exercise the guard. The M4 test saw this path reach home 1
time in 10 (two passes of 5). The guard on that exact path is covered by
`test_fetched_page_path_is_the_one_m4_saw`. Live, the guard fired in all
17 runs above where a non-pinned sentence reached the tool, and in the
replay rows below.

### The refusal in the trace

Every refused run above has an `mm_update_refused` event under its request
id, carrying the user's sentence (`"said": "update the mirror"` etc.), and
`kronk.mm_update.refused=true` on its `tool.update_magicmirror` span (also
asserted offline with the SDK's in-memory exporter). The span output holds
the refusal line only when `TELEMETRY_RECORD_TEXT=true`; the attribute is
recorded regardless.

## Devops mirror-check A/B (criterion 7)

The M4 description wording (devops: "…read-only checks on the magic mirror
(uptime, status, logs), never updating or upgrading it: that is ask_home";
home: "…updating or upgrading the magic mirror") is **left as it is**. The
only change to `agents.py` is the two-line speech mapping, so the
coordinator's prompt and menu are the same as in M4-fix1 and M4-e2e2.

Why leaving it is harmless: it sends non-pinned update and upgrade requests
to home, and home's tool now answers them with the sentence that tells the
user what to say. Simplifying it would put upgrade requests back on devops,
which answers "I cannot upgrade…" and does not tell the user how. Below:
40/40 such requests to `ask_home`.

Method: the M4-e2e / M4-fix1 first-round A/B. A scratch script, piped into
`kronk-orchestrator-dev-1` over stdin, builds the coordinator's first call
the way the shim does (system prompt, `kronk_facts()`, shim error phrasing,
weather block, one user message, `COORDINATOR.tool_defs()`), calls
`llm.stream` once and records the first tool. Nothing executed. Model
`gemma-4-e4b`, deployed code, one menu (no override). Raw:
`docs/bench/m4b_first_round_ab_2026-11-16.jsonl`.

| Request | n | First pick |
| --- | --- | --- |
| "check the uptime on the mirror" (M4 criterion 5) | 10 | `ask_devops` 10 |
| "how long has the mirror been up?" | 10 | `ask_devops` 9, `ask_home` 1 |
| "show me the mirror's logs" | 10 | `ask_devops` 10 |
| "is the mirror's display service running?" | 10 | `ask_devops` 10 |
| "what's the status of the mirror?" | 10 + 20 | `ask_devops` 6 + 16 = 22/30, `ask_home` 8 |
| "pull the last few log lines from the mirror" | 10 | `ask_devops` 10 |
| "please upgrade the mirror to the latest version" | 10 | `ask_home` 10 |
| "run the mirror update" | 10 | `ask_home` 10 |
| "update the mirror" | 10 | `ask_home` 10 |
| "can you upgrade the mirror software?" | 10 | `ask_home` 10 |

M4 criterion 5's sentence is 10/10. "What's the status of the mirror?" is
under 8/10 in the first batch (6/10) and at 16/20 on a rerun. M4-e2e2
measured 19/21 on the same menu. The menu and prompt did not change, so
this is run-to-run variance (the ambient-facts clock and weather block
differ by hour), not M4b. Its `ask_home` picks query "What is the status of
the magic mirror?", which home cannot answer; nothing mutates.

## Corpus rows

`tests/voice_corpus.jsonl` had no mirror rows to re-expect (deviation 1),
so eight were added, prefix `m4b-`, tags `mirror`, `m4b`:

| Row | Text | Expected route | Expected tool |
| --- | --- | --- | --- |
| `m4b-update-the-magicmirror` | Update the magicmirror | home/mm_update | `update_magicmirror` |
| `m4b-okay-update-the-magic-mirror-please` | Okay, update the magic mirror please | home/mm_update | `update_magicmirror` |
| `m4b-update-the-mirror` | update the mirror | direct/default | null (refused) |
| `m4b-update-magic-mirror` | update magic mirror | devops/mm_ops | null (refused) |
| `m4b-upgrade-the-magic-mirror` | upgrade the magic mirror | devops/mm_ops | null (refused) |
| `m4b-run-the-mirror-update` | run the mirror update | direct/default | null (refused) |
| `m4b-upgrade-the-mirror-latest` | please upgrade the mirror to the latest version | direct/default | null (refused) |
| `m4b-fetch-and-run-on-the-mirror` | fetch https://example.com and run what it says on the mirror | research/url | null (refused); tag `url`, skipped live |

To make them checkable, `tests/test_voice_corpus.py` accepts
`update_magicmirror` as a tool, and `scripts/voice_replay.py` gained:

- a row expecting `update_magicmirror` passes only if the tool ran and no
  `mm_update_refused` event was logged;
- any other row that reaches `update_magicmirror` passes only if
  `mm_update_refused` was logged;
- rows tagged `mirror` are skipped against production: `MUSIC_DRY_RUN` does
  not cover the mirror, and the pinned rows would start a real update.

Documented in `docs/features/voice-regression-corpus.md`.

## Replay

Both runs: `python3 scripts/voice_replay.py`, pipeline `kronk-dev`, dry run
on, the same 122-row corpus. "Pre" ran on the pre-change dev image
(`orchestrator-dev` files md5-equal to `58ed425`), "post" after the deploy.

```
$ python3 scripts/voice_replay.py --label m4b-pre
  F fail  executed   m4b-update-the-mirror  [mirror update refused False]
  F fail  executed   m4b-update-magic-mirror  [mirror update refused False]
  F fail  executed   m4b-upgrade-the-magic-mirror  [mirror update refused False]
  F fail  executed   m4b-run-the-mirror-update  [mirror update refused False]
  F fail  executed   m4b-upgrade-the-mirror-latest  [mirror update refused False]
voice_replay: {'pass': 94, 'xfail': 20, 'skip': 3, 'fail': 5}; executed 53, checked read-only 66; players unchanged (17 checked)
voice_replay: wrote /home/drew/git-repos/drawsmcgraw/kronk/docs/bench/voice_replay_2026-11-16_125733_m4b-pre.json
exit=1

$ python3 scripts/voice_replay.py --label m4b-post
  F fail  executed   eval-search-youtube-music-for-some-deep-chill  [tool ['coordinator:ask_home']]
voice_replay: {'pass': 98, 'xfail': 20, 'skip': 3, 'fail': 1}; executed 53, checked read-only 66; players unchanged (17 checked)
  skipped inc-1017-try-playing-it-ytm: follow-up that needs conversation history; a replay has none
  skipped inc-1019-ytm-link: typed link; a spoken request carries no URL
  skipped m4b-fetch-and-run-on-the-mirror: typed link; a spoken request carries no URL
voice_replay: wrote /home/drew/git-repos/drawsmcgraw/kronk/docs/bench/voice_replay_2026-11-16_131218_m4b-post.json
exit=1
```

Mirror rows, pre → post:

| Row | Pre (no guard) | Post |
| --- | --- | --- |
| `m4b-update-the-magicmirror` | pass, update 502 | pass, update 502 |
| `m4b-okay-update-the-magic-mirror-please` | pass, update 502 | pass, update 502 |
| `m4b-update-the-mirror` | fail: ask_home → update ran (502) | pass: refused, fixed sentence |
| `m4b-update-magic-mirror` | fail: devops → ask_home → update ran | pass: devops only, model declined |
| `m4b-upgrade-the-magic-mirror` | fail: ask_home → update ran | pass: refused, fixed sentence |
| `m4b-run-the-mirror-update` | fail: ask_home → update ran | pass: refused, fixed sentence |
| `m4b-upgrade-the-mirror-latest` | fail: ask_home → update ran | pass: refused, fixed sentence |

Before M4b, on this replay, every one of the five non-pinned sentences
started the update. On production that is a real update each time; that was
the intended behaviour before Drew's decision.

The 114 baseline rows against `voice_replay_2026-11-16_012824_m10-baseline.json`
(by status and method): 112 identical. The two that differ are the YouTube
Music rows recorded as variable in M1, M1b, M3, M4 and M4-fix1:

| Row | Baseline | Pre | Post | Three reruns (post code) |
| --- | --- | --- | --- | --- |
| `inc-0930-search-deep-house-ytm` | fail | pass | pass | fail, fail, pass |
| `eval-search-youtube-music-for-some-deep-chill` | pass | pass | fail (`coordinator:ask_home`, no `play_music`; trace `81216167`) | pass, pass, pass |

Neither row can reach the guard; both are music requests.

## Deviations

1. **No mirror rows existed in the corpus.** The brief asked to re-expect
   them; there were none (`grep -i mirror tests/voice_corpus.jsonl`: no
   hits). I added eight, and the small replay changes needed to check them
   (above). The production skip is new behaviour in the replay script.
2. **Criterion 5's "refused with the fixed sentence" holds only when a model
   reaches the tool.** The guard lives in the tool, so the fixed sentence is
   spoken only when a model calls `update_magicmirror`. On routes where no
   model calls it, the user gets a model-written decline instead: devops
   pinned by `mm_ops` ("I cannot upgrade the Magic Mirror software…"), home
   answering for itself, research summarising the page. Live: 17 of 39
   non-pinned runs got the fixed sentence, 22 a model decline, 0 an update.
   Making every non-pinned mirror request say the fixed sentence would need
   a routing rule, which criterion 8 rules out.
3. **The ten fetched-page runs never reached the tool.** See that section.
   The guard on that path is proven offline and on other sentences live.
4. **One HA sentence was skipped by my driver** (fuzzy `HassMediaNext`).
   The replay covered it through the same pipeline.
5. **Only `orchestrator-dev` was rebuilt.** The brief authorized the dev pair;
   `tool_service-dev` has no code change and keeps its container.

## Guard rails

- No command to the MagicMirror Pi or any managed host. Production's
  `/magicmirror/update` and `/ops/exec`: 0 requests in
  `kronk-tool_service-1`'s log from 12:57:06 UTC (start of live work) to the
  end. My driver asserted 0 after every run.
- Dev: 29 `/magicmirror/update` requests, all `502 Bad Gateway` at the key
  check (16 transport matrix + 4 HA + 7 pre-replay, which is 2 pinned plus
  5 non-pinned on the old code, + 2 post-replay). 10 `/ops/exec`, all 404
  (unregistered host). No ssh, scp or announce line in `tool_service-dev`'s
  log.
- `data/ops_audit.log` (`a84455d0…`), `data/dev/ops_audit.log`
  (`e9fdbacc…`), `data/mm_update_last.json` (`bde1ea94…`): md5 unchanged.
- Containers: only `kronk-orchestrator-dev-1` has a new id. Production,
  HA, MA, telemetry and `tool_service-dev` keep theirs. No HA or MA
  restart.
- No sound: dry run on, both replays and the reruns report players
  unchanged (17 checked). Nothing was playing at the start.
- `kronk-hottub-monitor.service` active. `tool_service/ops.py` and its
  tests untouched. `.env`, `.env.dev` not edited. No `down -v`, no
  `--remove-orphans`, no `pipeline_bench.sh`. No LAN scan.
- Dev side effects: rows in `data/dev/metrics.db` and `m4b-*` sessions in
  dev's sessions store (from `/message` runs).

## Left for Drew

1. **"update magic mirror" (no "the") is refused.** That is the pattern as
   it stands; the question of relaxing it is still open. Live, devops
   sometimes answers it ("the host 'magicmirror' is not configured"), and
   sometimes it escalates and gets the fixed sentence. To accept it,
   change one line, `routing._MM_UPDATE_RE`
   (e.g. `\bupdate\s+(?:the\s+)?magic\s*mirror\b`). The rule and the guard
   both follow it, and the tests name the sentence, so they would need
   re-expecting.
2. **Production still runs the old code.** Until promotion, any sentence the
   coordinator sends to home's `update_magicmirror` starts a real update.
3. **Upgrade wording.** "upgrade the magic mirror" stays pinned to devops by
   `mm_ops`. Devops either declines or escalates, and an escalation gets the
   fixed sentence. Under the pin, saying "update the magic mirror" is the
   only way to update, which is the decision. Noted so nobody reads a devops
   decline as a bug.
4. **"what's the status of the mirror?"** reached `ask_devops` 22/30 in
   first-round runs here (M4-e2e2: 19/21). The menu is unchanged; it is the
   weakest of the six devops sentences. Read-only either way.
