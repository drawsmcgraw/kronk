# M24 fix round 1

Branch `main`. Ordered by the coordinator's last ruling in
`docs/sprint/M24-criteria.md` (on the first test, `M24-e2e.md`): should-fix
2 and 3, "Found" 2, and tests in place of criterion 2's grep (mutants M24 and
M26). Fixed 2026-11-24 by the fix agent.

| Commit | What |
|---|---|
| main commit | "M24 fix 1: a capped reply is never spoken as nothing or a heading alone; tests pin the cap in both lock modes, the upgrade rules' unit list and ROUTER_MODEL's absence" |
| this report | in the same commit |

## Changes, file by file

| File | Change |
|---|---|
| `orchestrator/speech_stream.py` | `unfinished_tail` keeps its two old reasons to drop nothing (the text ends on a sentence end; no complete unit before the tail) and adds a third: what would be left, cut into units with `split_units(…, final=True)`, must say something on its own (`_says_something`). That means a list item, a table data row (a header row and its `|---|` line are not one; `_TABLE_RULE`), or complete sentences ending on `.`, `?` or `!` of two or more words in all. Blank lines, headings, lead-in lines ("Certainly:", "Kings of England") and one word alone ("Certainly!") do not count. If nothing is left that counts, nothing is dropped. The new check only ever drops less than before (below). |
| `scripts/upgrade_rules.toml` | The llama.cpp `units` list is `["llama-gemma4-e4b", "llama-devstral-q4", "llama-talkie"]`: the four retired units (`llama-gemma3-4b`, `llama-devstral`, `llama-mistral-nemo`, `llama-bonsai`) are gone. Order of the rest unchanged. |
| `tests/test_m24_talkie.py` | 37 new tests: cut cases (5), `NOTHING_TO_STAND_ON` (10 texts, unit level), the report's four texts through the app on the speech mount with `VOICE_STREAM` off and on (10), `llm.stream` itself in each lock mode with and without a cap (4), and end to end through the app on all four mounts in each lock mode (8). |
| `tests/test_m24_catalog.py` | 4 new tests: the upgrade rules' unit list against `systemd/`; `ROUTER_MODEL` not in `settings_view.ENV`; not in `docker-compose.yml` (no service's environment, and not in the file's text); not named anywhere under `orchestrator/` or `config/` (criterion 2's grep as a test). |

## Job 1: a capped reply is never spoken as nothing or a heading alone

The test report's texts, fixed code (`unfinished_tail` → what is dropped):

| Text | Before | After |
|---|---|---|
| `"\nThe kings were William I, William II, Henry I, Stephen, Henry II"` | the whole sentence (spoken `""`) | `""` (kept whole) |
| `"Certainly:\nThe kings were William I, William II"` | the sentence (spoken `"Certainly:"`) | `""` |
| `"Kings of England\n\nWilliam I, William II, Henry I, Steph"` | the line (spoken `"Kings of England."`) | `""` |
| `"The kings were William I, William II"` | `""` | `""` (unchanged) |

Added cases of my own, all kept whole now: three blank lines first; a `#`
heading; `##` heading then "Certainly:"; "Certainly! …" (one word); a lead-in
then a list whose only item is the cut one; a lead-in then a table whose only
data row is the cut one; whitespace-only lines with a tab.

Cases that worked before and still do (`test_unfinished_tail`, unchanged
rows pass on both trees): `CUT` loses "The total cost am"; "One. Two? Three!
Four and fi" loses "Four and fi" (three one-word sentences are three words in
all); "Dr. Watson was there. And Mr. Holm" loses "And Mr. Holm"; the unclosed
`-` and `1.` lists lose only their last line. New rows: leading blank lines
before a real sentence still drop the tail (`"\n\nThe canal is long. It was
op"` → `"It was op"`); "Certainly:" followed by list items drops only the cut
item; a lead-in then a table drops only the cut row; a reply that is one
unclosed list or table with nothing before it is kept whole, as before.

Evidence:

- Through the app (`test_capped_reply_is_never_spoken_as_nothing_or_a_heading`,
  speech mount, model faked, `finish_reason: length`, `VOICE_STREAM` off and
  on): the spoken text holds "William I, William II" and `reply_capped`
  reports `dropped_chars: 0` for the report's first three texts and a
  three-blank-line variant; the fourth text as before.
- The new tests against the old `unfinished_tail` (HEAD's
  `speech_stream.py` copied in, then restored):
  `18 failed, 136 passed, 3 warnings in 1.49s` over `tests/test_m24_talkie.py`
  and `tests/test_speech_stream.py`. The 18: the 10 `NOTHING_TO_STAND_ON`
  texts and 8 app runs (4 texts × 2 modes). Every `test_unfinished_tail` row
  passes on the old code, so no old behaviour changed in them. With the fix:
  `154 passed, 3 warnings in 1.21s`.
- Only ever drops less: 200,000 random texts built from sentence, heading,
  list, table, quote, emphasis, link and whitespace fragments (seed 7, old
  and new functions side by side, script in the session scratchpad, not
  committed). In every one the new result equals the old one or is `""`.
  It is `""` where the old was not in 22,182 of them. So a reply the old
  code spoke whole is still spoken whole, and the change cannot make any
  reply shorter.

## Job 2: the cap reaches the model in each lock mode

`test_llm_stream_hands_the_cap_on_in_each_lock_mode` calls `llm.stream` (not
`_stream_body`) with `MODEL_LOCK=server` and `MODEL_LOCK=pipeline`, cap 120
and None, LiteLLM faked by an `httpx.MockTransport` (no socket). It checks the
request body has `max_tokens: 120`, or no `max_tokens` key, and that
`finish_reason` comes back. `test_the_cap_reaches_litellm_on_the_speech_mount_only`
does the same through the app, `_route("talkie")`, real `agents` and `llm`,
on all four mounts in each mode: `max_tokens: 120` on `/voice/api/chat` only,
no key on the other three.

Mutants (applied in place with `PYTHONDONTWRITEBYTECODE=1`, the file run,
then `git checkout -- orchestrator/llm.py`):

| Mutant | Result on `tests/test_m24_talkie.py` |
|---|---|
| M03: pipeline branch calls `_stream_body(messages, model, tools)` | `2 failed, 69 passed`: `…each_lock_mode[120-pipeline]`, `…speech_mount_only[/voice/api/chat-pipeline]` |
| M04: server-lock branch calls `_stream_body(messages, model, tools)` | `2 failed, 69 passed`: `…each_lock_mode[120-server]`, `…speech_mount_only[/voice/api/chat-server]` |

Both survived the first test; both are killed now.

## Job 3: the upgrade rules' unit list

The list now names the three `llama-*.service` files in `systemd/` whose text
runs `llama-server`. `llama-props-export.service` is not a server and is not
listed, as before. `test_upgrade_rules_name_exactly_the_llama_server_units_in_systemd`
pins the list to those files and checks that the component's `file` exists.
Mutants run against `tests/test_m24_catalog.py` and `tests/test_m18_rules.py`:
`llama-bonsai` put back → `1 failed, 68 passed`; `llama-talkie` removed →
`1 failed, 68 passed`. `tests/test_m18_rules.py` passes with the new list.
`scripts/upgrades.py` was read, not run (it reads `units` at line 3234 in
`running_version`).

## Job 4: ROUTER_MODEL, tests in place of the grep

| Mutant | Result on `tests/test_m24_catalog.py` + `tests/test_m18_rules.py` |
|---|---|
| M24: the `ROUTER_MODEL` row put back in `settings_view.ENV` (the line removed in 8bc0248) | `2 failed, 67 passed`: `test_router_model_is_not_a_settings_env_row`, `test_router_model_is_named_nowhere_in_orchestrator_or_config` |
| M26: `- ROUTER_MODEL=gemma-3-4b` put back in the orchestrator's environment in `docker-compose.yml` | `1 failed, 68 passed`: `test_router_model_is_not_in_docker_compose` |

## Job 5: the suite

`./scripts/run_tests.sh`, after all changes:

```
-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
===== 7397 passed, 3 skipped, 54 xfailed, 6 warnings in 101.00s (0:01:41) ======
```

Before: 7356 passed, 3 skipped, 54 xfailed. The 41 more are the 37 + 4 new
tests above.

## What I did not do

- No live check. The cut is a pure function, and the app tests drive it
  through the real speech mount on both stream paths. The dev pair was not
  rebuilt and Talkie was sent no request. The voice replay (layer two) was
  not run. It needs a dev rebuild, and a corpus row only meets the new code
  when Talkie's reply hits its cap and the old code would have dropped down
  to a heading, a lead-in or nothing. The test report saw that 0 times in 50
  live Talkie replies.
- Nothing else from the test report: notes 1 (M12, M13, M18, M31 still
  survive), 2, 3, 5, 6 (including "… Then Mr." and the one-character "…"
  tail, unchanged) and 7; "Found" 1, 3, 4, 5, 6, 7. Should-fix 1 and 4 are
  the coordinator's.
- No change to the routing rule, to what Talkie is handed, to `llm.py` or
  `agents.py`. A request that does not name Talkie sends and answers what it
  did: `unfinished_tail` is called only when a capped agent's reply stopped
  at its cap, and Talkie is the only capped agent.
- No push, no tag, no deploy.

## Questions

None.
