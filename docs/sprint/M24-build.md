# M24 build report: reclaim cleanup, and Talkie kept working

Branch `main`. Criteria: `docs/sprint/M24-criteria.md`. Built 2026-11-24 by
the build agent.

| Commit | What |
|---|---|
| main commit | "M24: the retired model servers are gone from the repo and the catalog, and Talkie runs on the CPU and answers when asked for by name" |
| this one | the report |

## Summary

- Four retired reference units deleted from `systemd/`; four retired entries
  deleted from `litellm/config.yaml`. A new test fails when the catalog names
  a port no unit in `systemd/` serves, or when `systemd/` holds any llama unit
  but the three.
- `ROUTER_MODEL` is gone from compose, `servers.py`, `/settings` and
  `.env.dev.example`. `/dev/api/servers` lists three servers, no "Router".
- Talkie answers "ask talkie …" on all four routes on dev, 20 of 20.
- Talkie's replies on the speech mount are capped at 120 tokens
  (`config/voice.yaml`, `max_reply_tokens.talkie`): uncapped, 1 of 16 voice
  requests passed the 25 s deadline and one more finished at 23.46 s. With the
  cap, 20 of 20 finished, slowest 17.86 s. A capped reply loses its
  unfinished last sentence before it is spoken.
- Criterion 7 did not hold on the existing behaviour, so I changed it. A
  Talkie failure went to the coordinator, and on dev the real coordinator
  answered "ask talkie …" itself 2 times in 10 and named the failure in 4. A
  Talkie failure is now the reply, in M2's wording. **This is a pipeline
  change the brief did not ask for; the coordinator should rule on it**
  (below, "Criterion 7").
- Tests: before `7314 passed, 3 skipped, 54 xfailed`; after
  `7356 passed, 3 skipped, 54 xfailed, 6 warnings in 99.67s (0:01:39)`.
- Nothing installed, started, stopped or deployed. The dev pair was rebuilt
  (four times). `llama-talkie.service` still runs as found (NRestarts=0,
  active since 12:16:05 EST). **The installed copy of the Talkie unit now
  differs from the repo's, in its header comment only**; install step below.

## Changes, file by file

| File | Change |
|---|---|
| `systemd/llama-bonsai.service`, `llama-devstral.service`, `llama-mistral-nemo.service`, `llama-gemma3-4b.service` | Deleted (scope 1). |
| `litellm/config.yaml` | Entries `bonsai-8b`, `mistral-nemo`, `devstral-2512`, `gemma-3-4b` deleted. `devstral-2512-q4`, `gemma-4-e4b`, `talkie` unchanged. |
| `systemd/llama-talkie.service` | The coordinator's edit (`--device none`, `-ngl 0`) kept as it was: it meets criterion 3. I rewrote the header to add the measured figures and the fact that the build loads ROCm libraries from `/usr/local/lib/ollama/rocm`, so Ollama's removal must leave that directory (as for Whisper). `ExecStart` is unchanged. |
| `docker-compose.yml` | `ROUTER_MODEL=gemma-3-4b` removed from the orchestrator's environment. |
| `.env.dev.example` | `ROUTER_MODEL=gemma-3-4b` removed. |
| `orchestrator/servers.py` | `agents_by_model()` no longer adds a "Router" label. |
| `orchestrator/settings_view.py` | The `ROUTER_MODEL` row removed from the env allow-list. |
| `orchestrator/config_loader.py` | New `MaxReplyTokens` model (`talkie: int | None = 120`, 16 to 2048, `extra=forbid`), field `Voice.max_reply_tokens`. |
| `config/voice.yaml` | New key `max_reply_tokens: {talkie: 120}`, with the comment giving the measurements and the reason. |
| `orchestrator/llm.py` | `stream()` / `_stream_body()` take `max_tokens` (sent only when set) and yield `{"finish_reason": …}`. |
| `orchestrator/speech_stream.py` | New `unfinished_tail(text)`: the unfinished last sentence of a capped reply, using `split_units`; for an unclosed list or table only its last line; `""` when the text ends on a sentence end or has no complete unit before the tail. |
| `orchestrator/agents.py` | `run_stream` reads the cap when `origin.voice` is set and passes `max_tokens`. When the final round ends with `finish_reason == "length"` it emits `reply_capped` and yields a `retract` for the unfinished tail (the existing retract path: still held on the speech mount, so it is never spoken). New `AgentConfig.coordinator_fallback` (default True); `talkie` sets False. |
| `orchestrator/main.py` | In the routed-specialist error branch: when `coordinator_fallback` is False the failure is rendered with `errors.render("llm", …)` and the request ends; otherwise as before. |
| `tests/test_m24_catalog.py` | New, 6 tests (criteria 1 to 3: catalog ports vs units, the unit set, the Talkie flags, no Router label even with a stale `ROUTER_MODEL` in the environment). |
| `tests/test_m24_talkie.py` | New, 36 tests (the cut, the cap per mount, config bounds, `llm` payload and `finish_reason`, criterion 7 with a fake LiteLLM behind an `httpx.MockTransport`). |
| `tests/test_m21s2_view.py` | The env-row check names `TALKIE_MODEL` in place of `ROUTER_MODEL`. |
| `tests/test_m21_pins.py` | The `_Capture.stream` fake accepts `**kw` (talkie on voice now sends `max_tokens`). No fixture changed; every pin passes. |
| `README.md` | Model table (three rows, Talkie on CPU), the "not running" paragraph replaced by a "Retired 2026-11-24" paragraph, the agents table's Talkie row, the llama.cpp units section (CPU exception, install line enables `llama-talkie`, the ROCm environment is now Talkie's), Model storage. The nginx `/devstral/` sentence now says nothing listens on 11436 (see "Found"). |
| `kronk-context.md` | Pipeline line (no LLM router), the env-var line (`ROUTER_MODEL` out, `TALKIE_MODEL` in), the llama.cpp table (three units, real GGUF names), the units line, the pipeline diagram's Phase 1, the agents table (`devstral-2512-q4`, a Talkie row), "Router prompt" sentence. |
| `docs/features/config-directory.md` | `voice.yaml` row: `max_reply_tokens`. |
| `docs/features/verbose-errors.md` | The specialist-failure bullet and the gotcha: the Talkie exception and why. |

## Criteria and evidence

### 1. `systemd/` holds three llama units; a catalog-port test

`ls systemd/llama-*`: `llama-devstral-q4.service`, `llama-gemma4-e4b.service`,
`llama-props-export.service`, `llama-props-export.timer`,
`llama-talkie.service`.

`tests/test_m24_catalog.py`, run against the tree before this item (a
scratch `git worktree` of `d4c0ff3` with the new test file copied in):
`4 failed, 2 passed`. Failing there: `test_catalog_loads_with_the_three_models`,
`test_systemd_holds_only_the_three_llama_servers_and_the_props_export`,
`test_talkie_unit_runs_on_the_cpu_only` (the committed unit had `-ngl 20`),
`test_no_router_label_even_with_the_old_env_var`.

One thing the criterion did not foresee: **the port check by itself passes
on the old tree**, because the repo then still carried the four retired
units, each serving its catalog port. It catches the orphan case, shown by a
mutation run on the same worktree with only the four units deleted:

```
E       AssertionError: catalog names models no unit in systemd/ serves: ['bonsai-8b (http://127.0.0.1:11437/v1)', 'mistral-nemo (http://127.0.0.1:11435/v1)', 'devstral-2512 (http://127.0.0.1:11436/v1)', 'gemma-3-4b (http://127.0.0.1:11439/v1)']
1 failed, 5 deselected in 0.11s
```

The test that fails on the old tree is the unit-set test. The reverse check
(a unit the catalog does not list) is there too.

### 2. Catalog loads; three servers; no Router; grep

`/dev/api/servers` after the rebuild (dev reads the working tree's catalog):

```
devstral-2512-q4 11440 ['Coding Agent', 'Devops Agent'] True
gemma-4-e4b 11438 ['Coordinator', 'Research Agent', 'Home Agent', 'Assistant Agent'] True
talkie 11441 ['Talkie Agent'] True
```

The dev container still has `ROUTER_MODEL` set, from `.env.dev` (which I may
not edit); the label is gone anyway, as the test pins.
`/dev/api/settings` env rows start `COORDINATOR_MODEL, RESEARCH_AGENT_MODEL,
CODING_AGENT_MODEL, DEVOPS_AGENT_MODEL, TALKIE_MODEL, …`; no `ROUTER_MODEL`.
`grep -rn ROUTER_MODEL orchestrator/ docker-compose.yml config/`: no output,
exit 1.

Production still lists seven servers and the Router label until the deploy:
LiteLLM and the production orchestrator read the release's catalog and code.

### 3. The Talkie unit

- `systemd-analyze --user verify systemd/llama-talkie.service`: no output,
  exit 0.
- `ExecStart` carries `--device none` and `-ngl 0` (pinned by
  `test_talkie_unit_runs_on_the_cpu_only`).
- Header: says why (the ROCm idle spin, memwatch), gives the figures, names
  the `/usr/local/lib/ollama/rocm` dependency.
- **Installed copy: differs from the repo now, in comment lines only**
  (`diff` of the non-comment lines is empty). Before my header edit it was
  identical. Install step below.
- GPU busy with Talkie idle, read every 20 s for 2 minutes:

```
12:51:28 gpu_busy=0
12:51:48 gpu_busy=0
12:52:08 gpu_busy=0
12:52:28 gpu_busy=0
12:52:48 gpu_busy=0
12:53:08 gpu_busy=0
12:53:28 gpu_busy=0
```

### 4. Talkie answers on all four routes

On the final dev build, five sentences per route, one request at a time.
Every row: event `route_shortcut` with `rule: talkie_explicit`, one
`agent_round` with `agent: talkie, model: talkie`, no failure sentence.
**20 of 20** (each route 5 of 5).

| Route | Sentence | s | prompt tok (cached) | reply tok |
|---|---|---|---|---|
| /api/chat | ask talkie what he makes of the wireless | 5.68 | 388 (191) | 26 |
| /api/chat | what does talkie think of the aeroplane | 4.79 | 387 (377) | 36 |
| /api/chat | ask talkie who wrote Hamlet | 2.52 | 385 (377) | 19 |
| /api/chat | talkie, what is the capital of France? | 1.23 | 390 (377) | 8 |
| /api/chat | have talkie tell me about the telephone | 0.93 | 387 (377) | 6 |
| /v1 | ask talkie what he makes of the wireless | 4.39 | 388 (377) | 33 |
| /v1 | what does talkie think of the aeroplane | 3.50 | 387 (377) | 26 |
| /v1 | ask talkie who wrote Hamlet | 3.17 | 385 (377) | 24 |
| /v1 | talkie, what is the capital of France? | 1.22 | 390 (377) | 8 |
| /v1 | have talkie tell me about the telephone | 4.49 | 387 (191) | 5 |
| /message | ask talkie what he makes of the wireless | 3.25 | 388 (377) | 24 |
| /message | what does talkie think of the aeroplane | 3.35 | 433 (374) | 20 |
| /message | ask talkie who wrote Hamlet | 1.24 | 466 (420) | 5 |
| /message | talkie, what is the capital of France? | 1.52 | 476 (381) | 3 |
| /message | have talkie tell me about the telephone | 14.81 | 454 (386) | 108 |
| /voice/api/chat | ask talkie what he makes of the wireless | 11.78 | 388 (374) | 90 |
| /voice/api/chat | what does talkie think of the aeroplane | 5.78 | 387 (191) | 27 |
| /voice/api/chat | ask talkie who wrote Hamlet | 1.77 | 385 (377) | 13 |
| /voice/api/chat | talkie, what is the capital of France? | 1.24 | 390 (377) | 8 |
| /voice/api/chat | have talkie tell me about the telephone | 1.18 | 387 (377) | 8 |

`/message` ran in one session (`m24-build-measure`), so its prompt grows with
history (388 → 476 tokens over five turns).

Reply quality is the model's. "have talkie tell me about the telephone" got a
fragment on three routes: "system in Laurel, MD", "system of communication,",
"service between New York and Chicago." (and 108 good tokens on `/message`).
Talkie is handed the whole sentence, "ask talkie" included, and often
answers about itself in the third person ("Talkie thinks…"). See "Found".

### 5. Ten voice requests inside the deadline

**Measured first, uncapped** (dev build before the cap, 16 requests):

| Question (ask talkie …) | s | reply tok | |
|---|---|---|---|
| what he thinks of the motor car | 3.85 | 11 | |
| who is the greatest poet of the age | 2.46 | 18 | |
| to tell me the whole history of the British Empire in detail | 10.46 | 80 | |
| what he knows about the moon | 13.95 | 108 | |
| to explain step by step how a steam locomotive works | 5.38 | 41 | |
| what he makes of the talking pictures | 3.75 | 28 | |
| to describe everything he knows about the Great War | 22.94 | 160 | |
| what is the best cure for a common cold | 1.96 | 14 | |
| what he thinks of television | 18.33 | 141 | |
| to tell me a long story about a sea voyage | 1.14 | 8 | |
| to explain in full the causes of the French Revolution | 12.38 | 77 | |
| to describe the city of London in great detail | 25.01 | n/a | **deadline sentence** |
| to tell me everything about the Panama Canal | 23.46 | 181 | 1.5 s from the deadline |
| to give me a lecture on the theory of evolution | 6.81 | 34 | |
| to describe the life of Napoleon from start to finish | 16.10 | 124 | |
| to list all the kings and queens of England | 15.98 | 123 | |

15 of 16 inside the deadline. Rate through Kronk: 181 tokens in 23.43 s of
generation, 160 in 22.92 s, 141 in 18.29 s: about 7 to 8 tokens a second,
after 1 to 2 s of prompt when 380 of the ~390 prompt tokens are cached. The
minute in the facts block changes, and then 191 are cached. Uncached
entirely (the first request), 388 tokens took about 4.7 s before the first
token (17:23:08.47 lock → 17:23:13.19 stream start).

**The cap.** 120 tokens: at 7.5 tokens a second that is 16 s of generation.
With an uncached prompt (4.7 s) and routing, the worst case is about 21 s,
4 s inside 25 s. 150 (the criteria's rough figure) leaves under 1 s with an
uncached prompt. Voice only: the deadline exists only there, and typed
routes keep Talkie's full answer. Value on `/settings`:
`/dev/api/settings` shows `voice.yaml` key `max_reply_tokens.talkie`,
`value 120, source "code default", bounds ge 16 le 2048`. The live dev file
does not carry the key, so its comment is not shown there (see "Left for the
coordinator").

**With the cap, the criterion run** (final dev build, ten different
questions, six of them inviting a long answer). **10 of 10** ended with
Talkie's reply, none with the deadline sentence:

| Question (ask talkie …) | s | reply tok | capped | ends |
|---|---|---|---|---|
| to describe the city of London in great detail | 14.61 | 112 | no | "…termini are at Paddington and Euston." |
| to tell me everything about the Panama Canal | 15.61 | 120 | yes, 56 chars dropped | "…tolls amount to $20,000,000." |
| to describe everything he knows about the Great War | 6.71 | 34 | no | "…in November, 1918." |
| to explain in full the causes of the French Revolution | 15.59 | 120 | yes, 129 chars dropped | "…scarcity of the necessaries of life." |
| to describe the life of Napoleon from start to finish | 12.88 | 99 | no | "…banished from public life." |
| to list all the kings and queens of England | 15.57 | 120 | yes, 0 dropped (no sentence end: kept as is) | "…George IV, William IV." |
| what he thinks of television | 3.68 | 28 | no | |
| what he knows about the moon | 17.40 | 117 | no | "…without life of any kind." |
| what he makes of the talking pictures | 3.62 | 27 | no | |
| what is the best cure for a common cold | 3.52 | 26 | no | |

An earlier capped run of the same kind, on a build without the list fix
below and without the criterion 7 change: 10 of 10, slowest 17.86 s, four capped. In it the kings reply kept
only its heading line: an unclosed list is one unit to `split_units`, so the
whole list was dropped (214 chars). Fixed: only the unfinished last line of
a multi-line tail goes (`test_capped_list_keeps_its_complete_items`).

Across both capped runs: 20 of 20 inside the deadline, max 17.86 s, mean
10.2 s.

### 6. A request that does not name Talkie never reaches it

- `tests/test_routing_pin.py`, `tests/test_voice_corpus.py`,
  `tests/voice_corpus.jsonl` and `orchestrator/routing.py` are unchanged
  and pass in the suite.
- 20 ordinary requests on `/dev/api/chat`: `20 requests; 0 reached talkie;
  0 errors`. Every model call was `gemma-4-e4b`. One was a near miss on
  purpose: "the talkies were early sound films, when did they start" (rule
  none, coordinator). The others: weather tomorrow, the time, the shopping
  list, the hot tub (twice), the solar panels (twice), capital of France, a
  joke, feet in a mile, Hamlet, 15 × 23, a refrigerator, hello in Spanish,
  rain this weekend, a haiku, days in a leap year, boiling point, a
  synonym. No music, mirror or devops request.

### 7. Talkie unreachable

**Existing behaviour.** The routed talkie agent fails; `main._run_pipeline`
falls through to the coordinator with `errors.specialist_failed_block`,
which tells it the error and "If you can fully answer the user's question
from your own knowledge, do so." That is M2's design for a specialist
failure, pinned generically (for `home`) by
`tests/test_error_style.py::test_specialist_failure_reaches_the_coordinator_in_the_mounts_style`.
No test named Talkie.

**What the real coordinator said to it.** Run in-process inside the dev
container (`docker exec … python`, the pipeline called directly), with the
talkie agent's model renamed to `talkie-unreachable-m24` so that LiteLLM
answers 400 "Invalid model name". The unit was not touched. The coordinator
receives the same failure block either way; the friendly sentence differs (a
4xx is "couldn't handle that request").

| Style | Request | Coordinator's reply |
|---|---|---|
| debug | ask talkie what he makes of the wireless | "The talkie specialist is currently unreachable due to an error with the model configuration." |
| debug | ask talkie who is the greatest poet of the age | "I do not have a specialist named Talkie, and the Talkie specialist has previously failed to function. I can answer questions from my own knowledge base, but …" |
| debug | ask talkie what he thinks of the motor car | "I do not have a specialist named Talkie, and I cannot access information on what he might think. …" |
| debug | what does talkie think of television | "I do not have a specialist named "talkie" or information on what that entity thinks of television." |
| debug | ask talkie to describe the city of London | answered itself: "London is the capital and largest city of England …" |
| voice | ask talkie what he makes of the wireless | "I cannot fulfill that request because the talkie assistant is unavailable." |
| voice | ask talkie who is the greatest poet of the age | "I don't have a definitive answer to who is the greatest poet of the age, as that is subjective." |
| voice | ask talkie what he thinks of the motor car | "The talkie specialist did not respond to your request." |
| voice | what does talkie think of television | "I am unable to provide the information because the talkie agent failed." |
| voice | ask talkie to describe the city of London | answered itself: "London is the capital of England …" |

Chat: 1 of 5 gave the failure and a cause. Voice: 3 of 5 gave a plain
failure sentence. 2 of 10 answered in Talkie's place. Criterion 7 asks for a
specific failure on chat and a plain sentence on voice, so this fails it.

**What I changed** (tenet 5: change the loop, not the prompt):
`AgentConfig.coordinator_fallback`, False for `talkie` only. A routed
talkie failure is now the reply, rendered by `errors.render("llm", …)`: on
chat `[talkie agent error: LiteLLM returned 500: <LiteLLM's message>]`, on
voice M2's "Sorry — I couldn't reach my language model. Try again in a
moment." (or "…couldn't handle that request." for a 4xx). The trace is
marked ERROR with `talkie specialist: <cause>` as before. Other agents are
unchanged. Escalation (talkie handing a request back) still goes to the
coordinator.

Pinned in `tests/test_m24_talkie.py` with a fake LiteLLM (an
`httpx.MockTransport` in `llm`'s client: 500 with LiteLLM's
`APIConnectionError … Received Model Group=talkie` body for every talkie
call; any other call recorded):
`test_talkie_down_on_chat_is_the_specific_failure` (three chat routes; no
other model is called), `test_talkie_down_on_voice_is_a_plain_sentence`,
`test_litellm_down_too` (LiteLLM itself unreachable, both styles),
`test_only_talkie_skips_the_coordinator_fallback`.

Not shown live: the change itself, because making Talkie unreachable on dev
needs either the unit stopped or `.env.dev` edited. The dev probe above
shows the old behaviour; the tests show the new one.

### 8. Docs

`README.md` no longer says Talkie fails or that gemma-3-4b runs:
`grep -n -i "gemma-3\|bonsai\|nemo" README.md` finds only line 43 (the 2026-08-18
classifier removal, history) and the "Retired 2026-11-24" paragraph. The
same grep on `kronk-context.md` finds only the "retired 2026-11-24" sentence.
Changes listed above.

### 9. Suite

```
-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
7356 passed, 3 skipped, 54 xfailed, 6 warnings in 99.67s (0:01:39)
```

Before: `7314 passed, 3 skipped, 54 xfailed`. +42: the two new files.

### 10. Nothing installed, started, stopped or deployed

- `llama-talkie.service`: `active`, `NRestarts=0`,
  `ActiveEnterTimestamp=Tue 2026-11-24 12:16:05 EST` (the coordinator's
  start). Sent requests one at a time; none to production routes.
- Production containers: same, `Up 3 days` (orchestrator, tool_service,
  litellm, nginx, searxng, voice_tracer); HA `Up 6 days`, MA `Up 2 weeks`.
  Only `orchestrator-dev` and `tool_service-dev` were rebuilt, with the
  documented up line.
- No model loaded on the GPU; Ollama, `/opt/models`, `~/model-staging`
  untouched. No sound; no notification; dev `music_dry_run: True`.

## Not done, and why

- **The voice replay** (CLAUDE.md, Definition of done, tier 4) was not run.
  Before starting it I read the players: `media_player.home_assistant_voice_0ac919_pe_01`
  and `…_0ac919_media_player` were `playing`. The brief says no dev music
  request unless nothing is playing, and the replay sends music rows. The
  change is to the talkie path only (the `llm` stream change adds an event
  every other caller ignores); the voice corpus layer one passes in the
  suite. The test agent or the coordinator can run it when the house is
  quiet: `python3 scripts/voice_replay.py --label m24-post`.
- **A real "Okay Nabu, ask talkie …"** after the deploy is Drew's to give.
  I did not check whether HA's local intents or the music blueprint take any
  "ask talkie" sentence before Kronk sees it.
- Talkie's `kronk:` values, the bench scripts, moving the GGUF, Ollama, the
  `/resources` page: out of scope.

## Found, not foreseen by the criteria

1. **nginx `/devstral/` proxies to 11436**, the retired Q8 server's port
   (`nginx/nginx.conf:40`). Nothing has listened there since `llama-devstral`
   was disabled. I changed only the README sentence to say so. Pointing it at
   11440 would publish the Q4 server on the LAN; that, or removing the route,
   is a decision.
2. **`scripts/upgrade_rules.toml:483`** lists the four removed units under
   the llama.cpp component; the quarterly inventory will print "unit not
   installed" for each. One-line fix, not in scope.
3. **Code defaults name retired models**: `agents.py:207-210`
   (`COORDINATOR_MODEL` default `mistral-nemo:12b`, coding and devops
   `devstral-2512`) and the matching "default in code" text in
   `settings_view.py`. Compose always sets the env vars, so nothing runs on
   them today.
4. **`.env.dev` still sets `ROUTER_MODEL`.** Harmless now (nothing reads
   it); the line can go. I may not edit `.env.dev`.
5. **Talkie gets the whole sentence**, "ask talkie" and "have talkie"
   included. It often answers in the third person, and "have talkie tell me
   about the telephone" got a 5 to 8 token fragment on three routes of four.
   Handing it the request without the address, as the lead-in does for "ask
   Kronk to …", would likely help. That touches the routing decision, which
   this item leaves alone.
6. **Talkie's context is 2048 tokens.** The prompt alone is ~390; `/message`
   adds the last five turns as context (476 after four short turns). Five long
   uncapped chat answers (300+ tokens each) would overflow it. Not reached in
   this build's runs. If it happens, the failure is now spoken as the reply
   with the server's message.
7. **The Ollama shim reports `"model": "gemma-4-e4b"`** for a reply Talkie
   wrote (`/api/chat` response body). Cosmetic; the log and trace carry
   `talkie`.

## Left for the coordinator

1. **Install the Talkie unit's header** (comment lines only, `ExecStart`
   unchanged, so no restart is needed or wanted):

   ```bash
   cp systemd/llama-talkie.service ~/.config/systemd/user/llama-talkie.service
   systemctl --user daemon-reload
   diff systemd/llama-talkie.service ~/.config/systemd/user/llama-talkie.service && echo same
   ```

2. **Deploy** (after the test): tag `item-M24`, then
   `scripts/deploy_tag.sh plan item-M24` and `scripts/deploy_tag.sh deploy item-M24`.
   It carries `litellm/config.yaml` (LiteLLM restarts with the deploy and
   loads three models), `kronk-context.md`, the orchestrator code and the
   compose env change. After it, check: `/api/servers` lists three servers
   and no "Router"; `/api/settings` has no `ROUTER_MODEL` row;
   `curl -s localhost:8002/v1/models` lists three; and the shim paths per
   CLAUDE.md. A production "ask talkie …" request is the coordinator's to
   send.
3. **The live config directories** do not carry `max_reply_tokens`. The value
   is in effect anyway (code default 120, shown on `/settings` as "code
   default"), but its comment is not shown. To have the file say it: add the
   block from `config/voice.yaml` to `../kronk-config/prod/voice.yaml` and
   `../kronk-config/dev/voice.yaml` (through `/settings`, or commit there and
   `scripts/config_reload.sh prod|dev`).
4. **`.env.dev`**: delete the `ROUTER_MODEL=gemma-3-4b` line (cosmetic).
5. **Ollama** (Drew's sudo): blobs and unit can go; `/usr/local/lib/ollama/rocm`
   must stay. Whisper and now Talkie load it.

## Questions

1. Criterion 7: do you accept the `coordinator_fallback=False` change for
   Talkie, or do you want the M2 coordinator fallback kept and the criterion
   ruled on as it stands (1 of 5 specific on chat, 3 of 5 plain on voice,
   2 of 10 answered in Talkie's place)?
