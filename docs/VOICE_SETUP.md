# Kronk Voice Setup — Build Journal

Live journal for the Voice PE / HA / Wyoming integration work driven by
`kronk-voice-setup-plan.md`. Updated as work progresses.

---

## Timeline

- **2026-05-23** — Phase 0 audit completed. Findings recorded below; paused for
  operator review before proceeding to Phase 1.
- **2026-05-23** — Phase 1 (Piper TTS) completed. Container up, voice model
  downloaded, port 10200 responding.
- **2026-05-23** — Phase 2 (faster-whisper STT) completed. Built CTranslate2
  v4.7.2 with `WITH_HIP=ON` for gfx1151, vendored runtime libs, installed as
  user systemd unit. GPU detected, port 10300 responding, `small` model loaded
  on the Radeon 8060S.
- **2026-05-23** — Phase 3 (Home Assistant) container up. Pulled `stable`,
  host networking + privileged, port 8123 serving the onboarding page. Paused
  for operator to complete `[YOU] 3.3` onboarding and `[YOU] 3.4` LLAT.
- **2026-05-23** — Hardening side-quest while waiting on operator:
  (1) Switched Docker daemon log driver to `journald` via `/etc/docker/daemon.json`
  so container logs use journald's existing 4GB rotation cap instead of the
  unbounded `json-file` default. (2) Dropped `privileged: true` from the HA
  service — we don't use USB/Zigbee/Z-Wave/Bluetooth on this stack, and host
  networking already gives us mDNS. All containers force-recreated; HA still
  serving HTTP 302 onboarding after the change.
- **2026-05-23** — Phase 4 partial: operator completed HA onboarding and
  produced a Long-Lived Access Token. Token stored at `.env` (`.env` added to
  `.gitignore` first). Built an OpenAI-compatible shim at
  `orchestrator/main.py:/v1/chat/completions` + `/v1/models` so HA's OpenAI
  Conversation integration can drive Kronk's full router→agent→coordinator
  pipeline.
- **2026-05-23** — HA 2026.5.4 ships without an "OpenAI Conversation"
  integration card (or it's been renamed beyond what we can find in the
  picker). Operator confirmed Ollama is present though, so extended the shim
  to also speak Ollama API: `/api/version`, `/api/tags`, `/api/show`,
  `/api/chat`. Refactored both shims behind a single transport-agnostic
  core generator (`_kronk_pipeline_tokens`) — pays down the duplication tech
  debt called out earlier. Smoke tests all green via nginx at
  `http://localhost`.
- **2026-05-24** — Phase 4 end-to-end verified through the HA voice pipeline
  builder's text/mic test pane. Timing breakdown of two real queries done
  from orchestrator logs:
  weather (`home` agent, 1 tool) → ~15.8s Kronk-side;
  AVGO stock (`research` agent, 3 plan rounds + forced synthesis) → ~41.5s.
  Tool latency is negligible (~0.2–1.1s per call); the model is the
  bottleneck. Added a follow-up to README roadmap for short-TTL tool-result
  caching (weather/news).
- **2026-05-24** — Phase 5 code shipped. Added `POST /timer` to
  `tool_service/main.py` (proxies to HA's `timer/start` REST service, with
  a pre-check against the entity's state so missing-entity returns a clean
  503 instead of a misleading 200). Added the `set_timer` tool definition
  + dispatcher in `orchestrator/tools.py` and wired it into the `home`
  agent's `tool_names` in `orchestrator/agents.py`. Added `HA_URL` /
  `HA_TOKEN` / `HA_TIMER_ENTITY` env vars to `tool_service` in compose
  (reaching HA via `host.docker.internal:8123` since HA is on host net).
- **2026-05-24** — Operator created the `timer.voice_timer` helper in HA
  (Settings → Helpers). Required a default duration (HA enforces) — set to
  1 minute as a never-used fallback since the tool always overrides. End-to-end
  set_timer worked: voice command → home agent → tool_service /timer → HA
  REST → timer entity counted down in HA UI. Phase 5 functionally complete
  *except* the timer-finished TTS announce.
- **2026-05-24** — Ambient-context refactor: added `kronk_facts()` helper
  in `orchestrator/agents.py` so the `LOCATION` env var (and any future
  ambient facts: timezone, occupants, etc.) reaches every code path in one
  place. Prepended at both the agent system-prompt construction in
  `run_stream()` and the coordinator/shim system message in `main.py`.
  Resolves the "what location are you interested in?" reflex from gemma-4-e4b
  on bare-weather queries — confirmed via direct API test.
- **2026-05-24** — Phase 6: Voice PE physical adoption complete. Device
  provisioned via the HA Companion app on the operator's phone, joined WiFi,
  registered with HA via mDNS as `home_assistant_voice_0ac919`. Confirmed
  one weather query end-to-end (wake word → STT → home agent → TTS → speaker).
- **2026-05-24** — Discovered: the Voice PE exposes **two assistant slots**
  (`assistant` + `assistant_2`) for two simultaneous wake words / the
  tap-to-talk button. Only slot 1 was bound to `kronk`; slot 2 was set to
  `preferred` (HA's local Assist), which silently intercepted button-pushed
  queries — explaining a "weak response" the operator hit on a cellulitis
  query. Flipped slot 2 to `kronk` via REST and both paths now route to
  Kronk regardless of how the device is invoked.
- **2026-05-24** — Stack hardening: split HA into its own compose project
  (`docker-compose.ha.yml`, project `kronk-ha`) so the main kronk stack's
  rebuilds no longer cycle HA. The `kronk_ha-config` volume stays shared
  via `external: true`. Took a tarball backup
  (`~/ha-config-backup-2026-05-24.tar.gz`, 4.9 MB, 914 entries) before any
  destructive op. README operations runbook updated with new commands and
  an explicit "never `down -v`" warning. Also enabled the previously-dormant
  `llama-talkie.service` user systemd unit; smoke-tested via LiteLLM and
  marked its `/home/drew/model-staging/...` GGUF path as documented tech debt.
- **2026-05-24/05-28** — STT quality iteration. Operator reported the Voice
  PE getting weak / mis-routed responses to harder questions. Whisper logs
  showed transcriptions like "Oh" and "Thank you for watching" from
  multi-second audio — the classic faster-whisper-on-silence hallucination
  with the `small` model. Swapped systemd unit to `medium`; hallucinations
  stopped but ~57% of queries came back with empty transcription (Whisper
  rejecting borderline audio rather than guessing). Switched to
  `large-v3-turbo` — accuracy noticeably better, latency still acceptable.
  Single-variable changes throughout, per operator preference.
- **2026-05-28** — Phase 5.4 attempted and **deferred**. The
  `timer.finished` → announce automation was created and stored in
  `/config/automations.yaml`, and the entity loads cleanly. But the audio
  side has a real bug: direct calls to the `tts.speak` service silently
  return 200 without producing any Piper synthesis (no log activity, no
  new file in `/config/tts/`). The `assist_satellite.announce` service is
  the correct path (it actually triggers Piper and produces a cache file)
  but it wasn't what we used in the automation. The right fix is probably
  to rewrite the automation action to `assist_satellite.announce` targeting
  the satellite entity rather than `tts.speak` targeting the media_player
  — but the operator asked to pause and revisit with a fresh approach,
  so this is tracked as Open Item rather than fixed in this pass.

- **2026-07-03** — **Voice music control shipped (Option C, two-tier).**
  Tier 1: imported MA's local-assist blueprint (`local-assist-blueprint/
  mass_assist_blueprint_en.yaml`), default player = kitchen Voice PE MA
  entity. Strict grammar ("play the artist X [on Y]") plays in ~2 s, fully
  local. NOTE: sentences need a media-type keyword — "play music by X"
  does NOT match the blueprint. Tier 2: `play_music` tool — `tool_service`
  `POST /music` → HA `music_assistant.play_media`, wired into the `home`
  agent as a **terminal tool** (result spoken verbatim, turn ends; gemma-4-e4b
  otherwise re-called the tool or claimed success after failures). The route
  pre-checks player availability and polls for `playing` before reporting
  success (MA queues async; HA 200 ≠ playing). Player map + default via
  `MUSIC_PLAYERS` / `MUSIC_DEFAULT_PLAYER` env on `tool_service` — entries
  must be the **MA** entities (`_2` suffixed), not native Sonos/Cast ones.
- **2026-07-03** — **Fixed the voice-path router 400** (pre-existing, hit
  every spoken query that matched a built-in HA intent without a backing
  entity, e.g. "what is the weather" → `no_valid_targets` → fallback to
  Kronk with a *duplicated user turn* in the chat log → non-alternating
  messages → Gemma template exception). Two fixes: (1) `litellm/hooks.py`
  was dead — LiteLLM passes `call_type="acompletion"`, hook matched only
  `"completion"`; (2) `routing.py` history builder now merges consecutive
  same-role turns and drops a trailing user turn. Discovered along the way:
  `prefer_local_intents` was already ON for the kronk pipeline, and nginx
  needs a restart after orchestrator rebuilds (stale upstream IP → HA's
  ollama client gets an HTML error page → "Unexpected error during intent
  recognition").

---

## System state at start (Phase 0 audit, 2026-05-23)

### Host

- **OS time / TZ:** `Sat 2026-05-23 14:38:49 EDT` — `America/New_York` (matches
  the Phase 3 compose default, no change needed).
- **Python:** `/usr/bin/python3` → 3.13.7.
- **uv:** `/home/drew/.local/bin/uv` → 0.11.3.
- **User groups:** `drew adm cdrom sudo dip plugdev users lpadmin ollama docker render`
  — `render` and `docker` both present (good).

### GPU / ROCm

- **`/dev/kfd`** present (`crw-rw---- root:render`).
- **`/dev/dri/card1`, `renderD128`** present.
- **`rocminfo`** → not installed (no output).
- **`rocm-smi`** → not installed.
- **`/opt/rocm`** → does not exist.
- **`dpkg -l | grep rocm`** → no packages.

**Interpretation:** there is **no host ROCm userspace installed**. The
llama.cpp servers run because they use **vendored ROCm 7.x libs**:

- `/usr/local/lib/ollama/rocm/` → `libamdhip64.so.7.2.70200`, `libamd_comgr.so.3`,
  `libdrm*`, etc.
- `/home/drew/pai/pai_workspace/llama-cpp/llama-gfx1151-rocm7/` → custom
  llama-server binary + shared libs built for gfx1151 inside a ROCm 7.2
  container.

The systemd units set `LD_LIBRARY_PATH` to these two paths and
`HSA_OVERRIDE_GFX_VERSION=11.5.1`. **This means Phase 2's "install ROCm 6.4
userspace" step is a real install, not a no-op.** It would coexist with the
vendored ROCm 7 libs llama.cpp uses; that's worth flagging before doing it.

### Existing user systemd units (pattern to follow)

`~/.config/systemd/user/`:
```
llama-bonsai.service
llama-devstral-q4.service
llama-devstral.service          # (no matching active service — Q8 not running)
llama-gemma3-4b.service
llama-gemma4-e4b.service
llama-mistral-nemo.service
llama-talkie.service            # (not in active list at audit time)
```
Plus `kronk-hottub-monitor.service` (active).

### docker-compose state

8 services up and healthy: `nginx` (0.0.0.0:80), `orchestrator`, `litellm`,
`tool_service`, `health_service`, `finance_service`, `searxng`, `retire_calc`.
No port collisions with the targets: **10200, 10300, 8123 are all free.**

### Orchestrator chat endpoint

- **Path:** `POST /message` — `orchestrator/main.py:169`.
- **Request body:** `{"text": str, "model": str | None}`.
- **Response:** SSE stream (`data: {...}\n\n` … `data: [DONE]\n\n`).

This is what HA's conversation agent will need to hit. Since HA uses host
networking, it addresses the orchestrator at `http://localhost:8000/message`
(the orchestrator container publishes on `8000` internally; the nginx proxy at
`80` also fronts it).

### `.gitignore` state

**`.env` is NOT in `.gitignore`.** Phase 5.1 requires this before writing the
HA token. Will add it as part of Phase 5.

---

## Decisions & reasons

### 2026-05-23 — Pivot from earlier voice plan recorded in memory

Prior memory (`project_voice_interface.md`) described wiring the Voice PE
**directly** to Kronk via custom ESPHome firmware and a new `voice_service`
container, bypassing Home Assistant entirely. The new plan reverses that: HA is
in the loop as a device broker, faster-whisper + Piper sit behind Wyoming, and
no `voice_service` is built.

Reason for the pivot is in the new plan's "Background & design decisions"
section — HA handles device adoption, timer callbacks, and TTS routing to the
Voice PE for free; Kronk stays focused on being the AI brain.

Memory file will be updated once Phase 6 is complete and the architecture is
real (not just planned).

### 2026-05-23 — ROCm 6.4 userspace install for Phase 2 is a non-trivial fork

The plan installs ROCm 6.4 userspace from `repo.radeon.com` and uses a
CTranslate2 wheel built against ROCm 6.2. The host currently has **no** ROCm
userspace — llama.cpp works only because of vendored ROCm 7.x libs in
`/usr/local/lib/ollama/rocm` and `pai_workspace/llama-cpp/`.

Will surface this to operator before installing — adding ROCm 6.4 system-wide
alongside vendored ROCm 7 is a meaningful change that deserves a confirm.

---

## What worked

### Phase 1 — Piper TTS (2026-05-23)

- Added `wyoming-piper` service to `docker-compose.yml` (image
  `rhasspy/wyoming-piper`, voice `en_US-lessac-medium`, port 10200, named volume
  `piper-models`). Put it on the `kronk` bridge network so other compose
  services can reach it by name; published 10200 to the host for HA (which is
  on host net).
- `docker compose up -d wyoming-piper` pulled the image, created the volume,
  started clean. First-run voice download took ~30s.
- Logs: `INFO:__main__:Ready`. `nc -zv localhost 10200` → connection succeeded.
- Container state: `Up`, ports `0.0.0.0:10200->10200/tcp` plus the image's
  internal 10400.

---

### Phase 2 — faster-whisper STT (2026-05-23)

**Approach chosen:** built CTranslate2 v4.7.2 from source with `WITH_HIP=ON`
inside the `rocm/dev-ubuntu-24.04:7.2-complete` container. Used `amdclang` /
`amdclang++` as the C/C++ compilers (mirrored from upstream's
`docker/Dockerfile_rocm`), targeted only `gfx1151`, dropped MKL/DNNL/MPI in
favor of OpenBLAS (smaller dep tree, plenty fast for STT).

Build artifacts:
- C++ install tree at `~/services/wyoming-whisper/build/ctranslate2-install/`.
- Wheel at `~/services/wyoming-whisper/build/dist/ctranslate2-4.7.2-cp312-cp312-linux_x86_64.whl`.

Runtime libs vendored to `~/services/wyoming-whisper/runtime-libs/` (the
extracted ROCm 7.2 libs the wheel depends on that aren't in
`/usr/local/lib/ollama/rocm/`):
- `libctranslate2.so.4`
- `libhiprand.so.1` (huge — ~700MB; all archs baked in)
- `librocrand.so.1`
- `libomp.so` (from ROCm's own llvm)
- `libopenblas.so.0` (from Ubuntu 24.04 `libopenblas0` package)

Host venv at `~/services/wyoming-whisper/.venv` (Python 3.12.13 via uv). Pulled
the wheel + `wyoming-faster-whisper` (3.1.0). Test import succeeded:
`ctranslate2.get_cuda_device_count()` → `1`,
`get_supported_compute_types('cuda')` → `{float16, bfloat16, int8, int8_float16, int8_bfloat16, float32, int8_float32}`.

systemd unit at `~/.config/systemd/user/wyoming-whisper.service` — model
`small`, `--device cuda` (HIP via CTranslate2's CUDA-flag aliasing),
`--compute-type float16`, port 10300. Sets `HSA_OVERRIDE_GFX_VERSION=11.5.1`,
`ROCR_VISIBLE_DEVICES=0`, and `LD_LIBRARY_PATH` covering both vendored
locations (`~/services/wyoming-whisper/runtime-libs` and
`/usr/local/lib/ollama/rocm`). Active and listening on `0.0.0.0:10300`. First
start downloaded the Whisper `small` model from HuggingFace to
`~/services/wyoming-whisper/models/`.

---

## What didn't work

### Phase 2 — first build attempt with g++ (2026-05-23)

First build script used the container's default `c++` (g++) and CMake set
`enable_language(HIP)` with the regular GCC toolchain. Compile blew up with
`c++: error: language hip not recognized` on the `.cc` files that CTranslate2
reassigns to HIP language. Fix: switch to AMD's `amdclang` / `amdclang++`
matching upstream's `docker/Dockerfile_rocm`.

Also: piped the docker output through `tee` on the host without
`set -o pipefail`, so the failed build returned exit 0 to the wrapper. Added
explicit `${PIPESTATUS[0]}` check for the second run.

### Phase 2 — wheel build hit PEP 668 (2026-05-23)

Second build cleared the C++ compile but the wheel build `pip install -r
install_requirements.txt` fell over with `error: externally-managed-environment`
— Ubuntu 24.04's PEP 668 lock on the system Python. Worked around by creating
a throwaway venv inside the container for the wheel build step. Made the
script idempotent so the C++ install dir is reused across re-runs.

### Phase 2 — initial runtime ImportError chain (2026-05-23)

First test import failed on `librocrand.so.1: cannot open shared object file`.
The `/usr/local/lib/ollama/rocm/` directory has `libamdhip64`, `libhipblas`,
`libhipblaslt` but not the rand libs. Extracted `librocrand.so.1` (~700MB) from
`/opt/rocm/lib/` in the container into the vendored runtime-libs dir.

---

## Final system architecture

```
                                                       ┌─────────────────────────────────────┐
                                                       │  Voice PE (kitchen)                 │
                                                       │  on-device wake word: "OK Nabu"     │
                                                       │  mic + speaker + tap-to-talk        │
                                                       └────────────┬────────────────────────┘
                                                                    │  WiFi (mDNS-discovered)
                                                                    ▼
┌───────────────────────────────────────────────────────────────────────────────────┐
│  Home Assistant — `docker-compose.ha.yml` (project: kronk-ha)                     │
│  • container: homeassistant, network_mode: host, NOT privileged                   │
│  • volume:    kronk_ha-config (external — shared with kronk stack history)        │
│  • integrations: Wyoming STT, Wyoming TTS, Ollama (→ Kronk shim)                  │
│  • voice pipeline "Kronk" assigned to both Voice PE assistant slots               │
│  • timer.voice_timer helper, assist_satellite, automations.yaml                   │
└───────────┬───────────────────────────┬───────────────────────────┬───────────────┘
            │ Wyoming                   │ Ollama-compat HTTP        │ Wyoming
            │ tcp://localhost:10300     │ http://localhost:80/api/* │ tcp://localhost:10200
            ▼                           ▼                           ▼
┌─────────────────────┐   ┌──────────────────────────────────┐   ┌─────────────────────┐
│  wyoming-whisper    │   │  Kronk stack — docker-compose.yml │   │  wyoming-piper       │
│  (host systemd:     │   │  project: kronk, bridge network  │   │  (docker container,  │
│   user unit)        │   │                                  │   │   project: kronk)    │
│  faster-whisper     │   │  ┌─ nginx :80 ─┐                 │   │  en_US-lessac-medium │
│  large-v3-turbo     │   │  │ /api/* /v1/* │ → orchestrator │   │  port 10200          │
│  GPU: HIP gfx1151   │   │  │  /probe/*    │ → tools/health │   └─────────────────────┘
│  port 10300         │   │  └──────────────┘                │
└─────────────────────┘   │   orchestrator :8000              │
                          │   ├─ /message       (chat UI)     │
                          │   ├─ /v1/chat/completions (OpenAI)│
                          │   ├─ /api/chat /tags (Ollama)     │   ┌──────────────────────┐
                          │   └─ router→agent→coord pipeline ─┼──▶│ LiteLLM :8002        │
                          │   tool_service :8003              │   │ network_mode: host   │
                          │   ├─ /weather /search /fetch …    │   │ proxies to →         │
                          │   └─ /timer ──────┐               │   └──┬───────────────────┘
                          │   health_service :8004            │      │ HTTP 127.0.0.1:114xx
                          │   finance_service :8005           │      ▼
                          │   searxng :8080                   │   ┌─────────────────────┐
                          └──────────┬────────────────────────┘   │  llama.cpp servers  │
                                     │ HA REST                    │  (host systemd —    │
                                     ▼ host.docker.internal:8123  │   user units)       │
                          ┌──────────────────────┐                │  gemma-3-4b 11439   │
                          │ HA  /api/services/   │                │  gemma-4-e4b 11438  │
                          │ timer/start          │                │  devstral-q4 11440  │
                          └──────────────────────┘                │  mistral-nemo 11435 │
                                                                  │  bonsai-8b   11437  │
                                                                  │  talkie      11441  │
                                                                  └─────────────────────┘
```

### Where each piece lives, in one table

| Component | Managed by | Lifecycle |
|---|---|---|
| llama.cpp model servers (×6) | user systemd (`~/.config/systemd/user/llama-*.service`) | host-level, survives container churn |
| `wyoming-whisper` (STT, GPU) | user systemd (`~/.config/systemd/user/wyoming-whisper.service`) | host-level, survives container churn |
| `wyoming-piper` (TTS) | docker compose — project `kronk` | restarted with kronk stack rebuilds |
| `homeassistant` | docker compose — project `kronk-ha`, separate file | independent of kronk lifecycle |
| Everything else (orchestrator, nginx, tool/health/finance services, searxng, litellm, retire_calc) | docker compose — project `kronk` | bundled |

### Key wire decisions

- **HA on host net** so it can reach all kronk services via `localhost:<port>` *and* expose mDNS for the Voice PE.
- **Wyoming STT on the host** (not in a container) so the GPU passthrough story is just `/dev/kfd` + user group membership rather than container device-mapping.
- **Kronk pipeline reached via Ollama shim**, not OpenAI shim — HA 2026.5 was missing an OpenAI Conversation card so we extended the shim to dual-speak both APIs. Either works going forward.
- **Tool calls bridged into HA via REST + bearer token** (`HA_TOKEN` in `.env`, scoped to `tool_service` only) — same `host.docker.internal:8123` indirection LiteLLM uses for the llama servers.

---

## Manual operator steps required

Consolidated from `[YOU]` steps in `kronk-voice-setup-plan.md` plus
additional ones we discovered. Listed in order to reproduce on a fresh host.

1. Bring up the kronk stack: `docker compose up -d --build`.
2. Install/enable the llama.cpp + wyoming-whisper systemd units
   (`systemctl --user enable --now …`).
3. Bring up HA: `docker compose -f docker-compose.ha.yml up -d`.
4. **In HA UI:** complete onboarding (admin account, no Nabu Casa).
5. **In HA UI:** profile → Security → Create Long-Lived Access Token named
   `kronk-home-agent`. Copy it into `.env` as `HA_TOKEN=…` (verify `.env`
   is gitignored).
6. **In HA UI:** Settings → Devices & Services → Add Integration →
   **Wyoming Protocol** twice — host `localhost`, ports `10300` (rename
   "Kronk Whisper STT") and `10200` (rename "Kronk Piper TTS").
7. **In HA UI:** Add Integration → **Ollama** → URL `http://localhost`
   → model `kronk:latest`. Rename "Kronk Orchestrator". Leave any "control
   Home Assistant" / function-calling toggles **off**.
8. **In HA UI:** Settings → Voice Assistants → Add Assistant "Kronk" →
   conversation agent = Kronk Orchestrator, STT = Kronk Whisper, TTS =
   Kronk Piper. **Disable "Prefer handling commands locally."**
9. **In HA UI:** Helpers → Create Helper → Timer named `voice_timer`.
   Any default duration (it's overridden on every call).
10. Power on Voice PE → provision via HA Companion app on phone (BLE
    handshake + WiFi credentials). Device appears in HA via mDNS.
11. **In HA UI:** Voice PE device page → Assist config → set both
    `Assistant` and `Assistant 2` to `kronk`. Wake word `Okay Nabu`.
12. (Deferred) Configure timer-finished announcement — see Open Items.

---

## Open items

- **Timer-finished announcement / native HA timers (2026-05-28 decision).**
  After researching the failure, decided to **scrap our `set_timer` tool +
  `timer.voice_timer` helper + broken announce automation entirely** and let
  HA handle timers natively. Reasoning: HA's local Assist has had first-class
  multi-named-timer support since ~2024.4 via the `HA.Timer.Start/Cancel/Get`
  intents, including the `assist_satellite.announce`-based completion
  announcement (which we verified works in isolation; that's the right
  service to push audio to a Voice PE outside the conversation flow). To
  switch over: re-enable **Prefer handling commands locally** in the Kronk
  pipeline so HA matches timer intents before falling through to Kronk for
  everything else. Modern HA's intent matcher is *entity-aware* — only
  intercepts intents that have backing entities — so the old 2022-era
  "weather intent eats my query" risk is largely gone. Plan: (1) operator
  flips toggle in HA UI; (2) operator voice-tests weather/news/AVGO/health
  to confirm nothing gets stolen from Kronk; (3) once confirmed, delete the
  broken announce automation, decommission Kronk's `set_timer` tool +
  `/timer` route + `home` agent wiring + `timer.voice_timer` helper. Step
  (3) is reversible since the code lands as a deletion, not a rewrite —
  worst case we restore from git.
- **Whisper hallucination filter / VAD-filter.** Even with `large-v3-turbo`
  there's an occasional empty transcription on borderline audio. Enabling
  `--vad-filter` and/or relaxing the Voice PE's `finished_speaking_detection`
  from `default` to `relaxed` is the cheap next iteration if accuracy slips.
- **Voxtral STT — deferred (2026-05-31 evaluation).** Mistral's
  `Voxtral-Mini-4B-Realtime-2602` is genuinely interesting for our voice path:
  Apache-2.0 weights, 4B params (3.4B LM + 970M audio encoder), **causal
  streaming architecture with <500ms latency** (vs. our current batch-mode
  Whisper). The streaming win alone would shave 1-3 s off perceived voice
  latency on every query.
  - **Why we're not doing it yet:** the integration stack is in a different
    ecosystem than our existing setup. Our text LLMs run on llama.cpp (C++
    binary we compiled for gfx1151) which doesn't yet support Voxtral's
    audio-encoder architecture. Voxtral's official runtime is **vLLM**
    (officially supports gfx1151 per the AMD support matrix) — but **no
    prebuilt vLLM or PyTorch ROCm wheels exist for gfx1151 yet** because
    Strix Halo is too new. Getting Voxtral running would require building
    PyTorch from source for gfx1151 (~6-8 h compile, real failure risk),
    then vLLM, then writing a `wyoming-voxtral` server because rhasspy
    hasn't shipped one. Realistic ~5-day project.
  - **What changes the math:**
    1. AMD ships prebuilt PyTorch ROCm wheels with `gfx1151` in
       `PYTORCH_ROCM_ARCH` (likely 3-6 months). Project becomes ~1 day.
    2. The rhasspy community ships a `wyoming-voxtral` wrapper.
    3. llama.cpp adds Voxtral audio-encoder support — then it's a normal
       GGUF download.
  - **Cheaper voice-pipeline wins to try first:** enable Whisper
    `--vad-filter` flag (already in this list), switch Voice PE
    `finished_speaking_detection` to `relaxed`, both ~30 min and low-risk.
  - **Revisit trigger:** when any of the three "what changes the math"
    items is true. Check quarterly. Notes from the original research
    conversation captured in this entry; no need to re-derive.
- **`llama-talkie.service` GGUF path** is still under
  `/home/drew/model-staging/…` (documented tech debt in the README runbook).
- **Container BT errors from HA** — harmless `habluetooth.scanner` noise
  because we don't pass `/var/run/dbus` through. Can be silenced by deleting
  the Bluetooth integration in HA, or wired properly if BT-managed devices
  are added later.
- **HA OpenAI Conversation card absent in HA 2026.5.4** — unclear if
  renamed/moved/removed; Ollama works fine for now, so not blocking.
