# M24 pass criteria: reclaim cleanup, and Talkie kept working

Source: Drew's order of 2026-11-24 ("we need to reclaim that storage and
memory. delete the ollama blobs and get rid of the old units") and
`ROADMAP.md`, "Reclaim storage and memory".

Handoff line: The retired model servers are gone from the repo and the
catalog, and Talkie runs on the CPU and answers when asked for by name.

## Drew's conditions (2026-11-24)

- gemma-3-4b: "Remove it".
- Talkie: "i like talkie. Keep it and make sure it continues to work. It's
  a novelty model. I should be able to address with "ask talkie...." in a
  prompt".
- Model files: delete "Everything not loaded".

## What the investigation found (coordinator, 2026-11-24)

- Only `llama-gemma4-e4b` and `llama-devstral-q4` served requests.
  `llama-gemma3-4b` was running with 4.3 GB of GPU memory and no caller
  since the LLM router was removed (2026-08-18); its only traffic was
  health probes (prompt eval of 1 token). `llama-bonsai`, `llama-devstral`
  (Q8) and `llama-mistral-nemo` were disabled and inactive.
- Talkie did not work: `llama-talkie.service` was disabled and not
  running, so the `talkie` agent failed. Its unit loaded 20 layers on the
  GPU with a ROCm (HIP) build of a llama.cpp fork, the only build that
  loads the model. An idle ROCm process on the GPU is the idle-spin of
  `docs/incidents/INVESTIGATION_2026-06-09_hangs.md`; memwatch alerts on it.
- A CPU-only trial (`--device none -ngl 0`): healthy 6 s after start,
  8.3 tokens a second (60 tokens in 7.2 s), GPU 0 % while idle, about
  14 GB of RAM as page cache. MemFree fell from 24 GB to 8.7 GB; memwatch
  logged "below warn ... not counted" (pressure 0.00 %) and sent nothing.
- Both Whisper units load ROCm libraries from `/usr/local/lib/ollama/rocm`.
  Ollama's blobs can go; that directory cannot.
- Ollama (`ollama.service`, a system unit) is enabled, running, listening
  on every interface at port 11434, with 16 models and nothing loaded. Its
  only requests since October were status reads.

## Done by the coordinator on the host before this item (not the build's)

- `llama-gemma3-4b` stopped and disabled; GPU memory in use 50.6 → 46.6 GB.
- Unit files removed from `~/.config/systemd/user/`: `llama-bonsai`,
  `llama-devstral`, `llama-mistral-nemo`, `llama-gemma3-4b` (each was
  identical to its copy in `systemd/`).
- 24 GGUF files deleted (212 GB; disk 918 GB → 706 GB used). Left: the
  three files the two running servers load, and Talkie's.
- **Talkie's unit was changed and started by the coordinator**, outside the
  workflow: `systemd/llama-talkie.service` carries an uncommitted edit
  (`--device none`, `-ngl 0`, a header comment), the same file is installed
  in `~/.config/systemd/user/`, and the unit is enabled and running. The
  build checks that edit against these criteria, corrects it if it is
  wrong, and commits it; the test checks it like any other change.
- Not done: Ollama's blobs and unit (needs Drew's sudo).

## Scope

1. **The repo no longer names the retired servers as present.** Reference
   units `systemd/llama-bonsai.service`, `llama-devstral.service`,
   `llama-mistral-nemo.service`, `llama-gemma3-4b.service` are deleted. The
   `litellm/config.yaml` entries `bonsai-8b`, `mistral-nemo`,
   `devstral-2512` and `gemma-3-4b` are deleted. The `talkie`,
   `gemma-4-e4b` and `devstral-2512-q4` entries stay.
2. **No "Router" label.** `ROUTER_MODEL` leaves `docker-compose.yml`, the
   label code in `orchestrator/servers.py`, and the row on `/settings`
   (`orchestrator/settings_view.py`). There has been no LLM router since
   2026-08-18.
3. **Talkie works when asked for by name.** "ask talkie <question>" is
   answered by the Talkie model on `/message`, `/api/chat`,
   `/v1/chat/completions` and `/voice/api/chat`. On the voice mount the
   reply arrives before the 25 s deadline (`config/voice.yaml`,
   `deadline_s`). At about 8 tokens a second that allows roughly 150
   tokens: if replies run longer, the build caps Talkie's reply length in
   config (a value on `/settings`, with a comment) and says what it chose
   and why. Talkie's routing rule (`talkie_explicit`) is not changed.
4. **Docs say what is true.** `README.md` (model table, the "not running"
   paragraph, the agents table's Talkie row, the llama.cpp units section,
   Model storage), `kronk-context.md` (every line that names a removed
   unit or model, or a router model), `systemd/llama-talkie.service`'s
   header.

Out of scope: the `/resources` page itself (M25); Talkie's `kronk:` values
in the catalog (M25 reads them from the server); bench scripts that name
removed models (`scripts/benchmark_models.py`, `scripts/m9_lock_bench.py`,
`scripts/model_bench.py`); moving Talkie's GGUF to `/opt/models`; Ollama;
any new routing rule.

## Criteria

1. `systemd/` holds llama units for `gemma4-e4b`, `devstral-q4` and
   `talkie` only (plus the `llama-props-export` pair). A test fails if
   `litellm/config.yaml` names a model whose port no unit in `systemd/`
   serves, and it fails on the code before this item.
2. `litellm/config.yaml` loads; `/dev/api/servers` lists exactly three
   servers, none with a "Router" agent label. `grep -rn ROUTER_MODEL` over
   `orchestrator/`, `docker-compose.yml` and `config/` finds nothing.
3. `systemd/llama-talkie.service` passes `systemd-analyze --user verify`,
   carries `--device none` and `-ngl 0`, and its header says why and gives
   the measured figures. The installed copy equals it. With Talkie idle
   for two minutes, `/sys/class/drm/card1/device/gpu_busy_percent` reads 0
   at each of at least six readings.
4. On the dev pair, "ask talkie what he makes of the wireless" (and four
   more sentences of the tester's own that match the rule) is answered by
   the `talkie` agent on each of the four routes: the orchestrator log
   shows rule `talkie_explicit` and the model `talkie`, and the reply is
   not a failure sentence. Each route, 5 of 5.
5. On `/dev/voice/api/chat`, 10 of 10 "ask talkie …" requests (ten
   different questions, at least three that invite a long answer) end with
   Talkie's reply and not the deadline sentence. The report gives each
   request's duration and reply length in tokens.
6. A request that does not name Talkie never reaches it: the routing pin
   and corpus tests pass unchanged, and 20 ordinary requests on dev show
   no `talkie` model call.
7. With `llama-talkie` not reachable (shown with a fake model server in a
   test, not by stopping the unit), "ask talkie …" produces a specific
   failure (tenet 7) on chat and a plain spoken sentence on voice, as
   M2 defined them. If that is already pinned by a test, name the test.
8. Docs changed as scope 4 says; `README.md` no longer says Talkie fails or
   that gemma-3-4b runs.
9. `./scripts/run_tests.sh` passes (before: `7314 passed, 3 skipped, 54 xfailed`).
10. Nothing is installed, started, stopped or deployed by the build or the
    test. `llama-talkie.service` is left running as found. Production
    containers unchanged; HA and MA untouched; no sound; no notification.
    Install and deploy steps for the coordinator are in the build report.

## Rulings (coordinator)

- 2026-11-24, on the build report's question 1: the change that makes a
  routed Talkie failure the reply (`AgentConfig.coordinator_fallback`,
  False for `talkie` only) is accepted as the way criterion 7 is met. It
  is a change to the pipeline's failure branch, so it is tested as one:
  every other agent's failure must reach the coordinator exactly as
  before.
- 2026-11-24: the reply cap (`max_reply_tokens.talkie`, voice mount only)
  and the dropping of a capped reply's unfinished last sentence are inside
  scope 3. The `llm.stream` change that carries them (a `max_tokens`
  argument, a `finish_reason` event) reaches every model call, so
  criterion 6 is read widely: no request that does not name Talkie may
  change in what is sent to a model or in what is answered.
- The build report's "Found" items 1 to 7 are not ruled on yet; the test
  may weigh them.

- 2026-11-24, on the first test (`M24-e2e.md`, PASSES, no blocker):
  - Should-fix 1 (the installed Talkie unit differed in its comments):
    closed by the coordinator, 13:40 EST. Copied, `daemon-reload`, no
    restart (`NRestarts=0`, active since 12:16:05); `diff` empty.
  - Should-fix 2 (a capped reply can be dropped whole, spoken as nothing
    or one word) and should-fix 3 (no test pins that `llm.stream` hands
    the cap on, in either lock mode): **fix round 1.** A silent reply with
    no error is a tenet 7 failure on the feature Drew asked to keep.
  - In the same round: `scripts/upgrade_rules.toml` still lists the four
    removed units ("Found" 2), and tests in place of criterion 2's grep
    (mutants M24 and M26: `ROUTER_MODEL` put back in `settings_view.ENV`
    or `docker-compose.yml`).
  - Should-fix 4 (the cap's comment is not on `/settings`): the
    coordinator adds the block to both live config directories at promote
    (production's only after the deploy: the running code rejects a key it
    does not know).
  - Left over, not fixed in this item: notes 1 (the other surviving
    mutants), 2, 3, 5, 6, 7; "Found" 1, 3, 4, 6, 7.
  - For Drew: note 4 ("walkie talkie range in the woods" reaches Talkie
    now that Talkie answers; the rule is unchanged) and "Found" 5 (Talkie
    is handed "ask talkie" with the question; 2 of 50 replies were
    fragments).

- 2026-11-24, on the second test (`M24-e2e2.md`, PASSES, no blocker): no
  further fix round. Its should-fix 1 (a two-word opener such as "Of
  course." can still be spoken alone when the long sentence after it is
  cut) and should-fix 2 (when no complete line before the cut ends on a
  full stop, the cut word is now spoken where the old code dropped it)
  pull opposite ways, were seen in 0 of 63 live Talkie replies, and cost a
  short or ragged spoken reply, not a silent one. Left over, with the
  second test's notes 1 to 4. Promote.

Reports: `docs/sprint/M24-build.md`, `docs/sprint/M24-e2e.md`,
`docs/sprint/M24-fix1.md`, `docs/sprint/M24-e2e2.md`.
