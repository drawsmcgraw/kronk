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
  End-to-end test deferred until operator creates the `timer.voice_timer`
  helper in HA (1 click). The `timer.finished` → TTS-announce automation
  (Phase 5.4) is deferred until Phase 6 (Voice PE adoption) so the
  media_player entity exists to target.

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

*(to be filled in at Phase 7)*

---

## Manual operator steps required

*(to be filled in as `[YOU]` steps complete)*

---

## Open items

- Confirm with operator: proceed to Phase 1 (Piper) now?
- Confirm with operator at Phase 2.1: install ROCm 6.4 userspace, or skip
  straight to CPU faster-whisper given the existing vendored ROCm 7 setup?
- `.env` must be added to `.gitignore` before writing `HA_TOKEN` (Phase 5.1).
