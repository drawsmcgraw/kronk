# Feature: Voice pipeline

**Shipped:** 2026-05-24 (hardware end-to-end) · **Plan:** `../plans/kronk-voice-setup-plan.md` · **Build journal:** `../VOICE_SETUP.md`

## What it does

"Okay Nabu, what's the weather?" spoken at the kitchen Voice PE gets a spoken
answer from Kronk's full router → specialist-agent → coordinator pipeline.
Fully local: wake word on-device, STT/TTS on the kronk host, no cloud.

## How it works

```
Voice PE (wake word, mic/speaker)
  → Home Assistant (device broker; Assist pipeline "kronk")
    → Wyoming faster-whisper STT (host systemd, GPU, large-v3-turbo, :10300)
    → local intent matcher (prefer_local_intents ON — timers, MA blueprint)
    → fallback: Ollama shim  → Kronk pipeline (orchestrator /api/chat via nginx)
    → Wyoming Piper TTS (container, en_US-lessac-medium, :10200)
  → audio back to the Voice PE
```

Key design decisions (full rationale in the build journal):

- **HA is a device broker, nothing more.** Kronk stays the brain; HA talks to
  it through an Ollama-compatible shim (`orchestrator/main.py:/api/chat`)
  because HA 2026.5 shipped without an OpenAI Conversation card. Both shims
  share one transport-agnostic core generator.
- **STT on the host, not a container** — GPU passthrough is just `/dev/kfd` +
  group membership. Built CTranslate2 from source with `WITH_HIP=ON` for
  gfx1151.
- **HA in its own compose project** (`docker-compose.ha.yml`, `kronk-ha`) so
  orchestrator rebuilds never cycle HA. Volume `kronk_ha-config` holds all
  integrations — never `down -v`.
- **Ambient context** (`kronk_facts()` in `agents.py`) injects LOCATION etc.
  into every agent path — fixed the "what location?" reflex on bare weather
  queries.

## Gotchas (learned the hard way)

- The Voice PE has **two assistant slots**; slot 2 (tap-to-talk) silently
  defaulted to HA's local Assist. Both must be bound to `kronk`.
- Whisper `small` **hallucinates on silence** ("Thank you for watching");
  `medium` rejects borderline audio (empty transcriptions). `large-v3-turbo`
  is the working point.
- **nginx must be restarted after orchestrator rebuilds** — a stale upstream
  IP feeds HA's ollama client an HTML error page → "Unexpected error during
  intent recognition".
- HA local-intent fallback can leave a **duplicated user turn** in the chat
  log (fixed 2026-07-03 in `routing.py` + `litellm/hooks.py` — see the
  build-journal timeline).
- `tts.speak` silently no-ops for announcements; `assist_satellite.announce`
  is the correct service (matters for timers, ROADMAP item 3).

## Testing utterances without speaking (2026-07-03 method)

Until the automated voice smoke test ships (ROADMAP item 8), exercise the
pipeline from the shell:

- **Full Assist pipeline** (local intents + blueprint + Kronk fallback —
  what the Voice PE actually runs, minus audio): HA's websocket API.
  From inside the container: `docker exec -i homeassistant python3 -` with
  a script that auths against `ws://localhost:8123/api/websocket` using
  `HA_TOKEN` (from `.env`) and sends
  `{"type": "assist_pipeline/run", "start_stage": "intent",
  "end_stage": "intent", "input": {"text": "<utterance>"}}` (add
  `"pipeline": "<id>"` for a non-default pipeline; the kronk pipeline id is
  in HA's pipeline list). The `-i` flag is load-bearing — without it stdin
  closes and `python3 -` runs an empty script, exiting 0 as if it passed.
- **Local agent only** (skips the Kronk fallback): REST
  `POST /api/conversation/process` with `{"text": "<utterance>"}` and the
  bearer token.
- **Which tier answered?** A ~2 s response with MA-flavored wording = the
  blueprint; a built-in-intent error ("not aware of any device…") = HA's
  local agent; a `pipeline.shim` trace appearing in Langfuse = it fell
  through to Kronk.

## Blog hooks

- Building a fully-local voice assistant on one AMD Strix Halo box —
  the CTranslate2-on-gfx1151 build saga.
- The Whisper model-size ladder: hallucinations vs. empty transcriptions.
- The two-assistant-slot Voice PE trap.
- Shim archaeology: speaking Ollama's API because the OpenAI card vanished.
