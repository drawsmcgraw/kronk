# M4 build report: separate web tools from remote exec

Build agent, 2026-11-16 (UTC), branch `refactor`. Criteria:
`docs/sprint/M4-criteria.md`. Commits: `527a74c` (devops tools, loop
rejection, tests), `bca9d08` (devops description, its test, docs), and the
commit that adds this report.

## Summary

- `devops` holds only `remote_exec`. The web-lookup sentence is gone from its
  prompt. `research` and `coding` are unchanged.
- `run_stream` rejects any tool call the run did not offer the model, without
  running it. Not specific to devops: it closes the general gap (before M4 the
  loop ran any tool name the model emitted) and also enforces the depth-2
  delegation cap structurally.
- `tests/test_agent_tool_separation.py`: 16 tests. Seven of them fail against
  the pre-M4 `agents.py`.
- Dev finding: "check the uptime on the mirror" went to `ask_home` in 5 of 5
  runs. That was there before M4: the coordinator's prompt and tool menu hash
  the same before and after the first commit. Fixed by one edit to the devops
  `description` (what the coordinator reads as `ask_devops`). After it: 5/5
  to `ask_devops`.
- The chain works on dev: 5/5 runs of a docs-plus-mirror request did a web
  lookup in `research` or `coding`, then `ask_devops` → `remote_exec`. No run
  called a web tool from inside devops.
- Deviation: the criterion's exact wording ("look up what …") is pinned to
  `research` by the existing `search_phrase` shortcut, so it never reaches
  the coordinator. Not changed (stopping rule). See "Deviations".
- Tests: `1412 passed, 2 skipped, 18 xfailed` (baseline 1396 + 16 new).
- Replay: same counts as the baseline (91/20/1/2). Two rows swapped
  pass/fail. Both flap on reruns: known variance.

## Files changed

| File | Change |
| --- | --- |
| `orchestrator/agents.py` | devops `tool_names` → `["remote_exec"]`; web-lookup prompt sentence removed; devops `description` adds read-only mirror checks and "no web access: ask research first"; `run_stream` builds the offered set and rejects calls outside it (`tool_rejected` event, ERROR span `tool.<name>`, warning log, tool message `[TOOL: <name> — REJECTED: not available to the <agent> agent. It was not run. …]`) |
| `tests/test_agent_tool_separation.py` | new, 16 tests (below) |
| `README.md` | agent table: devops tools `remote_exec` only; a paragraph on the separation and the loop check |
| `kronk-context.md` | devops row said `web_search`, `fetch_url` (wrong before M4 too, it never listed `remote_exec`); now `remote_exec` |
| `docs/plans/MAGICMIRROR_PLAN.md` | status header: M4 update, and the residual risk carried into phase B |
| `docs/bench/m4_chain_runs_2026-11-16.jsonl` | raw records of all 30 dev runs below |
| `docs/bench/voice_replay_2026-11-16_*_m4-*.json` | replay pre, post, three reruns |
| `docs/sprint/M4-build.md` | this report |

No other feature doc lists the devops tools (`grep` of `docs/features/`,
`docs/*.md`). `tool_service/ops.py` and its tests not touched.

### The tests

| Test | Criterion |
| --- | --- |
| `test_devops_holds_only_remote_exec` (names and defs sent to the LLM) | 1 |
| `test_devops_prompt_does_not_offer_web_lookups` | 1 |
| `test_other_agents_web_tools_unchanged` (research, coding keep both; coding has no `remote_exec`) | 1 |
| `test_no_agent_holds_web_tools_and_remote_exec[<agent>]`, every `AGENTS` entry plus the coordinator (7) | 2 |
| `test_devops_loop_rejects_web_tool[web_search]`, `[fetch_url]`: tool never executed, REJECTED tool message, `tool_rejected` event, no narration, turn ends `done` | 3 |
| `test_devops_loop_still_runs_remote_exec_next_to_a_rejected_call` | 3 |
| `test_specialist_cannot_delegate_with_an_ask_tool` | 3 (same check, depth cap) |
| `test_coordinator_offers_both_halves_of_the_chain` | 4 |
| `test_ask_devops_description_covers_the_mirror_and_the_chain` | 4, 5 |

Against the pre-M4 `agents.py` (stashed, `e674aa8`): 7 failed, 8 passed (the
description test did not exist yet). The 8 that pass guard agents M4 did not
change.

## Dev path runs (criteria 4 and 5)

All through the Ollama shim on dev, `POST http://localhost/dev/api/chat`,
stateless, one user message per run. Tools from `orchestrator-dev`'s event
log (`tool_call` / `tool_rejected`), grouped as `agent:tool`. Raw records,
answers included: `docs/bench/m4_chain_runs_2026-11-16.jsonl`.

"Pre-fix" means dev on `527a74c` (M4 tools and loop, old description).
"Post-fix" means dev on the description edit in `bca9d08`.

Prompts:

- **A**, the criterion's wording: "look up what the pm2 flag for
  non-streaming logs is, then tell me how you would check the mirror's logs"
- **B**, same request reworded so no shortcut pins it: "check the pm2 docs
  for the flag that prints logs without streaming, then tell me how you
  would check the mirror's logs"
- **Plain**: "check the uptime on the mirror"

Router check in the dev container (`routing.classify`): A → `research`
(rule `search_phrase`, the `look up` alternative), B → `direct`, Plain →
`direct`.

### Post-fix (the result)

| Set | Run | s | Trace | Tools called |
| --- | --- | --- | --- | --- |
| B | 1 | 20.6 | `f2d1160a…` | coordinator:ask_coding, coding:web_search, coordinator:ask_devops, devops:remote_exec |
| B | 2 | 27.0 | `96d8832b…` | coordinator:ask_research, research:web_search ×2, coordinator:ask_devops, devops:remote_exec |
| B | 3 | 24.3 | `96bedd89…` | coordinator:ask_research, research:web_search ×2, coordinator:ask_devops, devops:remote_exec |
| B | 4 | 22.9 | `9eabb85b…` | coordinator:ask_research, research:web_search, coordinator:ask_devops, devops:remote_exec |
| B | 5 | 24.3 | `d039a82b…` | coordinator:ask_coding, coding:web_search, coordinator:ask_devops, devops:remote_exec |
| Plain | 1 | 6.8 | `a805eb09…` | coordinator:ask_devops, devops:remote_exec |
| Plain | 2 | 3.9 | `8f995b55…` | coordinator:ask_devops, devops:remote_exec |
| Plain | 3 | 5.5 | `a34f1f3f…` | coordinator:ask_devops, devops:remote_exec |
| Plain | 4 | 5.2 | `d7165ee8…` | coordinator:ask_devops, devops:remote_exec |
| Plain | 5 | 4.9 | `1ebb17ad…` | coordinator:ask_devops, devops:remote_exec |
| A | 1 | 8.3 | `e53b4244…` | research:web_search |
| A | 2 | 11.8 | `265bff10…` | research:web_search ×2 |
| A | 3 | 10.8 | `a9099830…` | research:web_search ×2 |
| A | 4 | 8.0 | `aeb4cc97…` | research:web_search |
| A | 5 | 13.7 | `ea18e0af…` | research:web_search ×2 |

B: 5/5 chained a web lookup, then devops. 3 used `ask_research`, 2 used
`ask_coding` (coding also holds the web tools; it is still a separate loop
from devops). Every chain ended on dev's expected refusal, e.g. run 1:
"Regarding the mirror's logs, the host 'magicmirror' is not configured in the
system." Plain: 5/5 `ask_devops`, answer "The host 'magicmirror' is not
configured." A: no devops hop, see "Deviations".

Across all 30 runs: 0 `tool_rejected` events, 0 web tool calls by `devops`.
Every `devops` call was `remote_exec` (11). All 11 dev `/ops/exec` requests
got `404 Not Found` from `tool_service-dev`: no host registry (`/ops` holds
only `hosts.example.json`) and no `MM_SSH_*` env, so it refuses before any
SSH.

### Pre-fix (how the description edit was found)

| Set | Tools called, runs 1–5 |
| --- | --- |
| A | research:web_search (×2, ×1, ×1, ×2, ×1); traces `8bcfe8fa`, `1ea1f798`, `ec5d7ddc`, `7f19d8ea`, `7a666f75` |
| B | 1 `ask_research`→`ask_assistant`; 2, 4, 5 `ask_research`→`ask_home`; 3 `ask_research`→`ask_devops`→`remote_exec` |
| Plain | `ask_home` 5/5 ("I can only update the MagicMirror, not check its uptime.") |

The coordinator chose `ask_home` for the mirror because that is the only
description that names it: home's ends "and updating the magic mirror";
devops's listed SSH, Docker, systemd and so on, and no mirror. M4 did not
cause it. The coordinator's system prompt plus its JSON tool menu hash to
`9cede530ddcba408` for both `e674aa8` and `527a74c`, so its first round was
byte-identical before and after. It is still criterion 5, so I fixed it the
way criterion 4 allows: one edit to the devops `description`.

## Bench script (criterion 6)

`scripts/devops_model_bench.py` is unaffected. Evidence: it imports only
`json`, `re`, `sys`, `time`, `datetime`, `pathlib` and `httpx`, nothing from
`orchestrator/`. It carries its own `SYSTEM` prompt and `TOOLS` list
(`magicmirror_ops` plus a `web_search` decoy for tool-selection scoring). It
scores emitted tool calls and never executes one. Its only HTTP calls go to
llama-server bench ports 127.0.0.1:11495–11498. Untouched in this item.

Not re-run. It would send nothing to a managed host, but all four bench
ports are down (`/health` fails on each), and running it means hand-starting
an isolated devstral llama-server (~14 GB of GPU memory) on the shared box.
That is outside this brief. The result could not differ anyway, because the
bench reads nothing M4 changed.

## Test output (criterion 8)

`./scripts/run_tests.sh`, final tree:

```
=========== 1412 passed, 2 skipped, 18 xfailed, 3 warnings in 11.77s ===========
```

First run after adding the test file:

```
FAILED tests/test_now_playing.py::test_tool_carries_origin_and_speaks_verbatim
FAILED tests/test_playback_control.py::test_tool_payload_carries_action_and_origin
====== 2 failed, 1409 passed, 2 skipped, 18 xfailed, 3 warnings in 11.70s ======
```

Caused by the new file importing `agents` (and so `tools`) at collection time,
before `test_agentic_loop.py` sets `TOOL_SERVICE_URL=http://fake-tools:8003`.
`tools` latched onto localhost, and two later files assert the fake host. The
new file now sets the same env defaults before importing. No code change.

## Voice replay (criterion 9)

`python3 scripts/voice_replay.py --label m4-pre` before the dev rebuild (dev
`orchestrator` image from before M4), `--label m4-post` after the final dev
rebuild (`bca9d08` code). Pipeline `kronk-dev`, dry run on.

| Run | File | pass | xfail | fail | skip |
| --- | --- | --- | --- | --- | --- |
| committed baseline | `voice_replay_2026-11-16_012824_m10-baseline.json` | 91 | 20 | 1 | 2 |
| before | `voice_replay_2026-11-16_054856_m4-pre.json` | 91 | 20 | 1 | 2 |
| after | `voice_replay_2026-11-16_060314_m4-post.json` | 91 | 20 | 1 | 2 |

All runs: 46 executed, 66 checked read-only, 17 players unchanged. The script
exits 1 whenever a row fails, which it did in each.

Before matches the baseline row for row. After differs on two rows, which
swapped:

| Row | Baseline | After |
| --- | --- | --- |
| `inc-0930-search-deep-house-ytm` ("Search for some deep house chill music on YouTube Music") | fail (`ask_research`) | pass (`ask_home`→`play_music`) |
| `eval-search-youtube-music-for-some-deep-chill` ("Search YouTube Music for some deep, chill house music and play that.") | pass | fail: no tool call, 2.88 s, trace `7692115428f10b4492d83bf2dd768c15` |

The after-fail has the same signature M3 recorded for this row on pre-M3 code
(the coordinator answers that it can't search YouTube Music). The
description edit does change the coordinator's tool menu, so I reran both
rows three times instead of assuming:

| Rerun | `inc-0930-…` | `eval-search-youtube-music-…` |
| --- | --- | --- |
| `m4-rerun1` | pass | pass |
| `m4-rerun2` | fail (`ask_research`→`web_search`) | pass |
| `m4-rerun3` | pass | pass |

Both rows go both ways from run to run. This is the known variance on the
"search … YouTube Music" phrasing (M1, M1b, M3), not a regression.

## Deviations

1. **Criterion 4's exact wording never reaches the coordinator.** "look up
   …" matches `_SEARCH_PHRASES` in `orchestrator/routing.py` (rule
   `search_phrase`), which pins the request to `research` before the
   coordinator runs. Research answers the pm2 half and either asks what "the
   mirror" is or gives a generic `pm2 logs` line. It holds `escalate` on a
   pinned run and did not use it in any of 10 runs. The rule predates M4 and
   M4 does not change it. Changing it would be a new special-case routing
   rule, which the stopping rule bars, so I left it alone. I showed the
   chain with wording B, which the router sends to the coordinator. The
   safety property holds for A too: devops was never reached, so no web
   call came from inside it. Drew or the coordinator decides whether B
   satisfies criterion 4.
2. **The devops description edit** is a coordinator-facing prompt change,
   which criterion 4 allows ("in one sentence if that is needed"). I added
   two clauses in one edit: the mirror checks (needed for criterion 5,
   0/5 → 5/5) and the research-first sentence. I didn't measure them
   separately, so I can't say whether B's 1/5 → 5/5 chaining is due to the
   mirror clause, the research sentence, or both.
3. **nginx not restarted** after the dev rebuild (the ground rules say to
   restart it after an orchestrator rebuild). Production nginx is outside
   this brief, and it resolves `orchestrator-dev` per request
   (`set $orchestrator_dev`), so every `/dev/` call above reached the new
   container.
4. **Only `orchestrator-dev` rebuilt** (`--no-deps`). No tool_service code
   changed, so `tool_service-dev` stayed as M3 left it.

## Guard rails (criterion 10)

- No command to the MagicMirror Pi or any managed host. Production
  `/ops/exec` not called. All 11 `/ops/exec` calls went to `tool_service-dev`
  and got 404 for an unregistered host.
- `data/ops_audit.log` md5 `a84455d0a6427ecc0a7e57a5ace8f635`, unchanged.
- Production containers, HA and Music Assistant untouched. Ids and start
  times of all 12 non-dev containers identical before and after.
- `.env` and `.env.dev` not edited. No `down -v`, no `--remove-orphans`, no
  `pipeline_bench.sh`, no sound (dry run on, players unchanged in every
  replay).

## Left for Drew

- Criterion 4, deviation 1: accept wording B as the shown chain, or ask for
  another approach. One option that adds no routing rule is to let a pinned
  `research` run escalate more readily, but that is a prompt change on
  research and outside M4.
- M4 is promoted only after M3 passes, or on Drew's word (criteria note).
- Residual risk, recorded in `docs/plans/MAGICMIRROR_PLAN.md`: command output
  is untrusted text, and the devops loop still reads it before its next
  command. The phase-B confirmation gate stays a requirement.
