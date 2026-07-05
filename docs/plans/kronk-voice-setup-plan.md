# Kronk Voice Setup — Implementation Plan for Claude Code

**Status:** shipped 2026-05-24 (all phases except the timer-finished
announcement, which was scrapped in favor of HA-native timers — see
`ROADMAP.md` Now item 3). What was actually built, including where it
diverged from this plan, is in `../VOICE_SETUP.md` (build journal) and
`../features/voice-pipeline.md` (distilled).

**Purpose:** This plan instructs Claude Code to add voice input/output capability
to Kronk via a Home Assistant Voice Preview Edition (Voice PE) speaker. Work is
performed on the `kronk` host unless otherwise noted.

**Operator note:** Steps marked `[YOU]` require manual action from Drew before
Claude Code can continue. Steps marked `[CC]` are performed by Claude Code.
Steps marked `[BOTH]` require coordination.

---

## Background & design decisions

- **Home Assistant (HA)** runs as a Docker container with host networking inside
  Kronk's existing `docker-compose.yml`. It acts as a device broker for the
  Voice PE — nothing more. Kronk remains the AI brain.
- **faster-whisper** (the Wyoming STT server) runs directly on the host as a
  user-level systemd service, matching the existing llama.cpp server pattern.
  GPU acceleration (ROCm) is attempted first. CPU fallback keeps the same host
  systemd pattern. Container is a last resort only if host has unexpected issues.
- **Piper TTS** runs in a container using the official `rhasspy/wyoming-piper`
  image. CPU only — no GPU needed.
- **Voice messages** enter Kronk at the same orchestrator endpoint as chat UI
  messages. No separate voice pipeline inside Kronk.
- **Timers** are delegated to HA's native timer service. Kronk's `home` agent
  calls HA's REST API. HA handles the callback and pushes audio to the Voice PE.
- **Build journal:** Claude Code writes `../VOICE_SETUP.md` throughout this
  work. Every action taken, what worked and didn't, decisions made and why, and
  the final system state must be documented there as work progresses — not
  retrofitted at the end.

---

## Phase 0 — Audit & prerequisites

### [CC] 0.1 — Inspect current system state

Before writing a single line of config, gather ground truth. Record all findings
in `../VOICE_SETUP.md` under a "System State at Start" section.

**Check ROCm userspace:**
```bash
rocminfo 2>/dev/null | head -30
rocm-smi 2>/dev/null
ls /opt/rocm/lib/ 2>/dev/null | head -20
dpkg -l | grep -i rocm
```

**Check GPU/render devices:**
```bash
ls -la /dev/dri/
ls -la /dev/kfd 2>/dev/null
groups
```

**Check existing user systemd services (llama.cpp pattern to follow):**
```bash
systemctl --user list-units --type=service
ls ~/.config/systemd/user/
cat ~/.config/systemd/user/*.service 2>/dev/null | head -60
```

**Check existing docker-compose state:**
```bash
cat docker-compose.yml
docker compose ps
```

**Check what port/path the orchestrator exposes for chat:**
```bash
# Inspect orchestrator source to find the chat endpoint
grep -r "POST\|@app\|router\|@router" orchestrator/ --include="*.py" | grep -i "chat\|message\|converse" | head -20
```

**Check Python/uv availability:**
```bash
which uv
uv --version
which python3
python3 --version
```

Record all output. This audit answers:
- Whether ROCm userspace is already installed (affects Phase 2)
- The exact chat endpoint path (needed for Phase 4 HA config)
- The user systemd pattern to replicate for faster-whisper
- Current compose service names and network config

### [YOU] 0.2 — Review audit findings

Claude Code will pause after Phase 0 and present a summary of findings before
proceeding. Confirm or correct before continuing.

---

## Phase 1 — Piper TTS container

Start with Piper because it has no unknowns — official image, CPU only, no GPU
complexity. Getting TTS working first means you can test the audio output path
independently.

### [CC] 1.1 — Add Piper to docker-compose.yml

Add the following service. Voice PE audio quality depends on the voice model
choice — `en_US-lessac-medium` is recommended based on community testing (sounds
natural, low latency).

```yaml
wyoming-piper:
  image: rhasspy/wyoming-piper
  container_name: wyoming-piper
  restart: unless-stopped
  ports:
    - "10200:10200"
  volumes:
    - piper-models:/data
  command: >-
    --voice en_US-lessac-medium
    --uri tcp://0.0.0.0:10200
    --data-dir /data
    --download-dir /data
```

Add to the `volumes` section at the bottom of `docker-compose.yml`:
```yaml
volumes:
  piper-models:
```

### [CC] 1.2 — Start Piper and verify

```bash
docker compose up -d wyoming-piper
docker compose logs wyoming-piper --follow
```

Expected: model downloads on first run, then `Started server on tcp://0.0.0.0:10200`.

**Verify Wyoming is responding:**
```bash
# Wyoming speaks a simple line protocol over TCP
# A successful connection and immediate close means the server is up
nc -zv localhost 10200
```

Document result in `../VOICE_SETUP.md`.

---

## Phase 2 — faster-whisper STT (host systemd, GPU attempt first)

### [CC] 2.1 — Check ROCm state and prepare

Using findings from Phase 0 audit:

**If ROCm userspace is NOT installed**, install it now:
```bash
# Download and install ROCm 6.x userspace tools (no DKMS)
wget https://repo.radeon.com/amdgpu-install/6.4.4/ubuntu/noble/amdgpu-install_6.4.60404-1_all.deb
sudo apt install ./amdgpu-install_6.4.60404-1_all.deb
sudo amdgpu-install --usecase=rocm --no-dkms

# Add user to required groups
sudo usermod -aG render,video $USER
```

**If ROCm userspace IS installed**, verify GPU visibility:
```bash
rocminfo | grep -A3 "Agent 2"
# Expected: gfx1151 (or gfx1150)
```

Document findings. If `rocminfo` shows no GPU agent, note it and proceed
directly to the CPU fallback path (Phase 2.4).

### [CC] 2.2 — Create uv venv and install faster-whisper with ROCm support

```bash
mkdir -p ~/services/wyoming-whisper
cd ~/services/wyoming-whisper

# Create venv with uv
uv venv --python 3.11 .venv
source .venv/bin/activate

# Install CTranslate2 with ROCm support
# The ROCm wheel is separate from the standard CTranslate2 wheel
uv pip install ctranslate2 --extra-index-url https://download.pytorch.org/whl/rocm6.2

# Install wyoming-faster-whisper
uv pip install wyoming-faster-whisper

# Verify CTranslate2 sees ROCm
python3 -c "import ctranslate2; print(ctranslate2.get_supported_compute_types('cuda'))"
# 'cuda' is the CTranslate2 flag for both CUDA and ROCm via HIP
```

If the CTranslate2 import fails or ROCm compute types are empty, document the
error and proceed to Phase 2.4 (CPU fallback).

### [CC] 2.3 — Create user systemd service (GPU attempt)

```bash
mkdir -p ~/.config/systemd/user/
```

Create `~/.config/systemd/user/wyoming-whisper.service`:

```ini
[Unit]
Description=Wyoming faster-whisper STT (GPU)
After=network.target

[Service]
Type=simple
WorkingDirectory=%h/services/wyoming-whisper
ExecStart=%h/services/wyoming-whisper/.venv/bin/python -m wyoming_faster_whisper \
    --model small \
    --language en \
    --uri tcp://0.0.0.0:10300 \
    --data-dir %h/services/wyoming-whisper/models \
    --download-dir %h/services/wyoming-whisper/models \
    --device cuda \
    --compute-type float16
Environment=HSA_OVERRIDE_GFX_VERSION=11.5.1
Environment=ROCR_VISIBLE_DEVICES=0
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
```

Enable and start:
```bash
systemctl --user daemon-reload
systemctl --user enable wyoming-whisper
systemctl --user start wyoming-whisper
sleep 3
systemctl --user status wyoming-whisper
journalctl --user -u wyoming-whisper -n 30
```

**Success indicators:**
- Service is `active (running)`
- Logs show `Loaded model` and `Started server on tcp://0.0.0.0:10300`
- No `RuntimeError` or `CUDA not available` errors

**Failure indicators (move to 2.4):**
- `RuntimeError: CUDA/ROCm not available`
- `ctranslate2.StorageViewError`
- Service enters failed state immediately

Document outcome fully in `../VOICE_SETUP.md` regardless of result.

### [CC] 2.4 — CPU fallback (if GPU attempt failed)

If Phase 2.3 failed, document exactly what failed and why, then proceed.

**Option A: CPU on host (preferred — keeps consistent systemd pattern)**

Update the venv to remove ROCm CTranslate2 and use the standard build:
```bash
cd ~/services/wyoming-whisper
source .venv/bin/activate
uv pip install --upgrade ctranslate2  # standard CPU build
```

Update `~/.config/systemd/user/wyoming-whisper.service` — remove the GPU flags:
```ini
[Unit]
Description=Wyoming faster-whisper STT (CPU)
After=network.target

[Service]
Type=simple
WorkingDirectory=%h/services/wyoming-whisper
ExecStart=%h/services/wyoming-whisper/.venv/bin/python -m wyoming_faster_whisper \
    --model small \
    --language en \
    --uri tcp://0.0.0.0:10300 \
    --data-dir %h/services/wyoming-whisper/models \
    --download-dir %h/services/wyoming-whisper/models \
    --device cpu \
    --compute-type int8
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
```

```bash
systemctl --user daemon-reload
systemctl --user restart wyoming-whisper
systemctl --user status wyoming-whisper
```

**Option B: Container fallback (only if CPU-on-host has unexpected issues)**

If the host systemd approach has an unresolvable problem, add to `docker-compose.yml`:
```yaml
wyoming-whisper:
  image: rhasspy/wyoming-faster-whisper
  container_name: wyoming-whisper
  restart: unless-stopped
  ports:
    - "10300:10300"
  volumes:
    - whisper-models:/data
  command: >-
    --model small
    --language en
    --uri tcp://0.0.0.0:10300
    --data-dir /data
    --download-dir /data
    --device cpu
    --compute-type int8
```

Document which option was taken and why in `../VOICE_SETUP.md`.

### [CC] 2.5 — Verify faster-whisper is responding

```bash
nc -zv localhost 10300
journalctl --user -u wyoming-whisper -n 10
```

---

## Phase 3 — Home Assistant container

### [CC] 3.1 — Add HA to docker-compose.yml

HA uses host networking for mDNS-based Voice PE discovery. This means it shares
the host network stack and can discover the Voice PE on the local network via
Bluetooth/mDNS during initial provisioning.

```yaml
homeassistant:
  image: ghcr.io/home-assistant/home-assistant:stable
  container_name: homeassistant
  restart: unless-stopped
  network_mode: host
  privileged: true
  volumes:
    - ha-config:/config
    - /etc/localtime:/etc/localtime:ro
  environment:
    - TZ=America/New_York
```

Add to `volumes` section:
```yaml
  ha-config:
```

**Note on `privileged: true`:** Required for HA to access host network interfaces
for mDNS/device discovery. This is HA's standard Docker deployment pattern.

**Note on timezone:** Update `America/New_York` to match Kronk's local timezone
if different. Check with `timedatectl`.

### [CC] 3.2 — Start HA and wait for first-run setup

```bash
docker compose up -d homeassistant
docker compose logs homeassistant --follow
```

Wait until logs show `Home Assistant initialized`. First start takes 2-3 minutes.

### [YOU] 3.3 — Complete HA onboarding

Claude Code cannot do this step — it requires browser interaction.

1. Open `http://kronk:8123` in a browser (or `http://<kronk-ip>:8123`)
2. Create your HA admin account (username/password — local only, not Nabu Casa)
3. Skip the "Add devices" step for now
4. Complete onboarding to reach the HA dashboard

Report back when the dashboard is visible.

### [YOU] 3.4 — Generate a Long-Lived Access Token

Claude Code needs this token to configure Kronk's `home` agent to call HA's REST API.

1. In HA, click your profile icon (bottom left)
2. Scroll to the bottom → **Long-Lived Access Tokens** → **Create Token**
3. Name it `kronk-home-agent`
4. Copy the token — it is only shown once

This token will be added to Kronk's `docker-compose.yml` as:
```
HA_TOKEN=<your-token-here>
```

Provide this token to Claude Code to continue.

---

## Phase 4 — Wire HA to Wyoming services and Kronk orchestrator

### [CC] 4.1 — Determine Kronk orchestrator chat endpoint

Using findings from Phase 0 audit, identify the exact endpoint path the chat UI
uses. Expected to be something like `http://localhost:8000/chat` or
`http://localhost:8000/api/chat`. Confirm from the orchestrator source.

Since HA uses host networking, it addresses Kronk's services via `localhost`.
Document the confirmed endpoint in `../VOICE_SETUP.md`.

### [YOU] 4.2 — Add Wyoming STT integration to HA

1. In HA: **Settings → Devices & Services → Add Integration**
2. Search for **Wyoming Protocol**
3. Host: `localhost` / Port: `10300`
4. Name it `Kronk Whisper STT`

### [YOU] 4.3 — Add Wyoming TTS integration to HA

1. **Settings → Devices & Services → Add Integration**
2. Search for **Wyoming Protocol**
3. Host: `localhost` / Port: `10200`
4. Name it `Kronk Piper TTS`

### [CC] 4.4 — Add Ollama/OpenAI conversation agent pointing at Kronk

HA's Ollama integration or OpenAI-compatible integration can point at Kronk's
LiteLLM proxy or orchestrator endpoint as the conversation agent.

Using the endpoint confirmed in Phase 4.1, add the appropriate integration via
HA's UI or configuration YAML. Claude Code will write the exact config once the
endpoint is confirmed from the Phase 0 audit.

The conversation agent should point at Kronk's orchestrator so that voice
messages enter the same pipeline as chat UI messages — router → specialist →
coordinator.

### [YOU] 4.5 — Create the voice pipeline in HA

1. **Settings → Voice Assistants → Add Assistant**
2. Name: `Kronk`
3. Conversation agent: select the Kronk orchestrator agent from step 4.4
4. Speech-to-text: `Kronk Whisper STT`
5. Text-to-speech: `Kronk Piper TTS`
6. Wake word: `OK Nabu` (default) or configure preferred wake word
7. Save

---

## Phase 5 — Home Assistant timer integration

### [CC] 5.1 — Add HA token to environment

Add to `docker-compose.yml` environment section for the `orchestrator` service:
```yaml
environment:
  - HA_TOKEN=${HA_TOKEN}
  - HA_URL=http://localhost:8123
```

Add to `.env` file (create if it doesn't exist, ensure it's in `.gitignore`):
```
HA_TOKEN=<token-from-phase-3.4>
```

**Verify `.gitignore` contains `.env`** — do not commit the token.

### [CC] 5.2 — Add timer tool to tool_service

Add a `set_timer` tool to `tool_service` that calls HA's REST API. When the
timer fires, HA pushes a TTS announcement to the Voice PE media player.

```python
# In tool_service, new tool: set_timer
import os
import httpx

HA_URL = os.environ.get("HA_URL", "http://localhost:8123")
HA_TOKEN = os.environ.get("HA_TOKEN", "")

async def set_timer(duration_minutes: float, label: str = "Timer") -> dict:
    """Set a timer via Home Assistant. HA fires the alert when it expires."""
    headers = {
        "Authorization": f"Bearer {HA_TOKEN}",
        "Content-Type": "application/json",
    }
    # HA timer duration format: HH:MM:SS
    total_seconds = int(duration_minutes * 60)
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    duration_str = f"{hours:02d}:{minutes:02d}:{seconds:02d}"

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{HA_URL}/api/services/timer/start",
            headers=headers,
            json={"duration": duration_str},
        )
        resp.raise_for_status()

    return {"status": "timer_set", "duration_minutes": duration_minutes, "label": label}
```

### [CC] 5.3 — Expose set_timer to the home agent

In `orchestrator/agents.py`, add `set_timer` to the `home` agent's allow-listed
tool set. Claude Code will locate the exact dict structure from the source and
add it following the existing pattern.

### [YOU] 5.4 — Configure HA timer alert automation

When a timer finishes, HA needs to announce it through the Voice PE speaker.
This requires a simple HA automation:

1. In HA: **Settings → Automations → Create Automation**
2. Trigger: **Timer finished** (select the timer entity)
3. Action: **Call service → tts.speak**
   - Entity: your Voice PE media player entity
   - Message: `Your timer is done.`
4. Save

**Note:** The Voice PE media player entity name is assigned during device
adoption (Phase 6). This automation may need to be created or updated after
Phase 6 once the entity name is known.

---

## Phase 6 — Voice PE device adoption

**Prerequisite:** You need the physical Voice PE device and your phone with the
Home Assistant Companion app installed.

### [YOU] 6.1 — Provision the Voice PE onto your WiFi

1. Install the **Home Assistant** app on your phone if not already installed
2. Power on the Voice PE (USB-C cable, included)
3. On your phone, open the HA app → the Voice PE should be discoverable via BLE
4. Follow the in-app provisioning flow to connect it to your WiFi network
5. The device will appear in HA under **Settings → Devices & Services**

### [YOU] 6.2 — Assign the Kronk voice pipeline to the Voice PE

1. In HA: **Settings → Devices & Services → ESPHome → Voice PE device**
2. Find the **Assist** configuration for the device
3. Set pipeline to: `Kronk` (created in Phase 4.5)

### [YOU] 6.3 — Test end-to-end

Say the wake word followed by a test question — something that exercises the
full pipeline:

- "What's the weather?" → should route to `research` or `home` agent
- "Set a timer for 2 minutes" → should call HA timer API, then HA should
  announce when done
- A general question → should route through Kronk's pipeline and speak the response

Report back with what worked and what didn't.

---

## Phase 7 — Build journal completion

### [CC] 7.1 — Finalize ../VOICE_SETUP.md

At the end of all work, ensure `../VOICE_SETUP.md` contains:

- **Timeline** — dated log of each phase, what was done and when
- **System state at start** — output of Phase 0 audit
- **Decisions & reasons** — every fork in the road (GPU vs CPU, host vs
  container, etc.) with the rationale recorded
- **What worked** — with exact commands and confirmed outputs
- **What didn't work** — exact errors, what was tried, why it was abandoned
- **Final system architecture** — a clear description of what is running where,
  on what port, managed how (systemd vs container)
- **Manual steps required** — list of the `[YOU]` steps with notes on any
  issues encountered
- **Open items** — anything deferred or not fully resolved

---

## Reference: ports and service map after this work

| Service              | Port  | Host/Container | Managed by       |
|----------------------|-------|----------------|------------------|
| nginx                | 80    | container      | docker compose   |
| orchestrator         | 8000  | container      | docker compose   |
| litellm              | 8002  | container (host net) | docker compose |
| tool_service         | 8003  | container      | docker compose   |
| health_service       | 8004  | container      | docker compose   |
| finance_service      | 8005  | container      | docker compose   |
| searxng              | 8080  | container      | docker compose   |
| homeassistant        | 8123  | container (host net) | docker compose |
| wyoming-piper        | 10200 | container      | docker compose   |
| wyoming-whisper      | 10300 | host           | user systemd     |
| llama.cpp servers    | various | host         | user systemd     |

---

## Reference: faster-whisper decision tree (for build journal)

```
GPU attempt (Phase 2.2–2.3)
    ├── success → document GPU config, done
    └── failure → document exact error
        └── CPU on host (Phase 2.4 Option A)
            ├── success → document CPU config, done
            └── unexpected failure → document error
                └── CPU in container (Phase 2.4 Option B)
```

---

*Plan version: May 2026. Written for Kronk (Framework Desktop, AMD Ryzen AI+ 395,
Ubuntu 25.04, GFX1151). To be executed by Claude Code on the kronk host.*
