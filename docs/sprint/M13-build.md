# M13 build report: bias Whisper toward household vocabulary (dev path only)

Build agent, 2026-11-18 (UTC), branch `refactor`. Criteria:
`docs/sprint/M13-criteria.md`. Status: in progress. This file is written
step by step; each host and HA change is recorded here, with its undo,
in a commit made before the change.

## 1. Check first (criterion 1), read-only

Taken 2026-11-18 00:15 to 00:25 UTC. Nothing was changed in this step.
Context: M12 was promoted at 00:13 UTC, so `homeassistant` and the six
production Kronk containers had restarted about four minutes before the
check started. Not this item's doing.

### Installed Whisper

`~/services/wyoming-whisper/.venv`: `wyoming-faster-whisper 3.1.0`,
`wyoming 1.9.0`, `faster-whisper 1.2.1`, `ctranslate2 4.7.2`.

`python -m wyoming_faster_whisper --help` (run with the unit's three
environment variables, since `libctranslate2.so.4` is found only through the
unit's `LD_LIBRARY_PATH`; help only, no model load, no listener):

```
  --beam-size BEAM_SIZE
                        Size of beam during decoding (0 for auto)
  --initial-prompt INITIAL_PROMPT
                        Optional text to provide as a prompt for the first
                        window (faster-whisper only)
  --vad-filter          Enable Silero VAD to filter out non-speech which can
                        reduce hallucinations (default: false, faster-whisper
                        only)
  --vad-threshold VAD_THRESHOLD
                        VAD speech probability threshold (default: 0.5,
                        faster-whisper only)
  --vad-min-speech-ms VAD_MIN_SPEECH_MS
                        VAD minimum speech duration in ms (default: 250,
                        faster-whisper only)
  --vad-min-silence-ms VAD_MIN_SILENCE_MS
                        VAD minimum silence duration in ms to split (default:
                        2000, faster-whisper only)
  --local-files-only    Don't check HuggingFace hub for updates every time
```

Production (`systemd/wyoming-whisper.service`, byte-identical to
`~/.config/systemd/user/wyoming-whisper.service`, sha256 `441b8b78…a797b1`)
passes none of them: beam size is the default (5 on x86), no prompt, no VAD.

### Can one instance serve two prompts?

No, not with the installed build.

- `dispatch_handler.py` passes `initial_prompt=self._loader.initial_prompt`
  to every transcription. The loader takes it once, from the command line
  (`__main__.py:212`). It is process-wide.
- The Wyoming `Transcribe` event carries `name`, `language` and `context`
  (`wyoming/asr.py:53`). The handler reads `language` only; `name` and
  `context` are ignored. No per-request or per-connection prompt.
- One process has one `--uri`, so one listener.
- Serving both from one model load would need a wrapper process that holds
  one model and runs two servers. That process would replace production's,
  which this item may not touch. So the second instance is built.

Two more facts from the source that matter for the dev unit:

- The model loads lazily, on the first audio chunk of the first request
  (`dispatch_handler.py`, `load_transcriber` task), not at start. A new
  instance holds no model memory until it transcribes once.
- `FasterWhisperTranscriber` does not pass `local_files_only` to
  `faster_whisper.WhisperModel`, so on each model load
  `huggingface_hub.snapshot_download` asks the Hub for updates, whatever
  `--local-files-only` says. Production does this today. The dev unit sets
  `HF_HUB_OFFLINE=1` so it never reaches the network; the snapshot it needs
  (`models--mobiuslabsgmbh--faster-whisper-large-v3-turbo`) is already on
  disk.

### How HA reaches Whisper

From `/config/.storage/` (read with `docker exec homeassistant cat`;
copies in `~/backups/ha/m13-2026-11-17/`, see section 4):

| Item | Value |
| --- | --- |
| Wyoming entry `01KSBFASPA7R5EYA5ZHHX72ATE` | title "Kronk Whisper STT", `host localhost`, `port 10300`, entity `stt.faster_whisper` |
| Wyoming entry `01KSBFC6S9WGJN5V6QHB5MGKVF` | title "Kronk Piper TTS", `localhost:10200`, entity `tts.piper` |
| Pipeline `kronk` (`01ksc2pw5bkhtk37dh2d6zwcxp`, preferred) | `stt_engine stt.faster_whisper`, `stt_language en` |
| Pipeline `kronk-dev` (`01m7gvyhhfk2jp7havxm88ecme`) | `stt_engine stt.faster_whisper`, `stt_language en` |
| Pipeline "Full local assistant" (`01m1q5ay5xzawwp756pffm9gs8`) | `stt_engine stt.faster_whisper` (not used by any satellite slot; left alone) |
| Pipeline "Home Assistant" | no STT |

All three STT-using pipelines share the one production instance.

### Memory before any change (00:18 to 00:20 UTC)

Method: system memory from `free -m` and `/proc/pressure/memory`; GPU
memory from `/sys/class/drm/card1/device/mem_info_*` (totals) and per
process from DRM fdinfo (`scripts/gpu_mem_export.py --once`, which prints
and writes nothing). On this iGPU model memory is GTT carved out of system
RAM; RSS does not show it (`docs/features/resources-live-memory.md`).

| Measure | Value |
| --- | --- |
| RAM total / used / free / buff-cache / available | 125,784 / 72,286 / 14,916 / 40,555 / 53,497 MiB |
| Swap used | 0 of 8,191 MiB |
| Memory PSI some avg60 | 0.00 |
| GTT used / total | 48.43 / 109.05 GB |
| VRAM carve-out used / total | 0.528 / 0.537 GB |
| `gpu_busy_percent` | 0 |
| `llama-devstral-q4` (pid 2826, :11440) | GTT 36.52 GB, RSS 9.12 GB |
| `llama-gemma3-4b` (pid 2827, :11439) | GTT 3.96 GB, VRAM 0.35 GB, RSS 0.71 GB |
| `llama-gemma4-e4b` (pid 2828, :11438) | GTT 5.48 GB, RSS 11.15 GB |
| `wyoming-whisper` (pid 2809, :10300, up since 2026-11-04) | GTT 2.07 GB, RSS 0.69 GB; cgroup memory peak at its last two stops 3.3 GB |

Expected cost of a second instance: the same as the running one, about
2.1 GB GTT plus 0.7 GB RSS, rising for the length of a transcription (its
cgroup peak of 3.3 GB). After it: GTT about 50.5 of 109 GB, available RAM
about 50.7 GB.

Alarms that could fire, and why they should not:

- `kronk-memwatch` pushes a phone alert when GTT exceeds 90 GB for 5 polls
  (about 40 GB away), or when `mem_free` drops below 12 GB **and** memory
  PSI is above 0.5 %, or below 2 GB regardless. `mem_free` already dips to
  6 GB with PSI 0 (its own log, 18:28 local), which it does not count.
  2.8 GB less free with no pressure stays below both rules.
- Its GPU-busy alert (wedge: busy ≥ 95 % with no engine time, 5 polls) is
  for ROCm idle-spin. Production Whisper is a HIP build and has not tripped
  it; `gpu_busy_percent` is checked after the dev load all the same.

No out-of-memory or allocation-failure line in the last day of the
`llama-*` journals or in `wyoming-whisper`'s journal.

### Listeners

`ss -tlnp`: `0.0.0.0:10300` (pid 2809), `0.0.0.0:10200` and `[::]:10200`
(Piper). 10301 is free; the dev instance takes `127.0.0.1:10301`.

### Prompt sources, as found

- HA areas: HA's template API (`{{ areas() }}`), read-only.
- Music Assistant players: the `media_player` entities of HA's
  `music_assistant` integration, the membership `tool_service` itself uses
  (`_PLAYERS_TEMPLATE`, `tool_service/main.py`). Friendly names.
- Pandora stations in the MA library: the taste DB's `stations` table holds
  all 50 library radios, but its `provider` column reads `library` for
  every row (MA's library provider), so it cannot tell a Pandora station
  from another. The provider is in MA's `provider_mappings`. The build adds
  a read-only `GET /music/stations` to `tool_service` that lists library
  radios with a `pandora` mapping, through `ma_read`
  (`music/radios/library_items`, already in `MA_READ_COMMANDS` and the read
  the nightly rebuild makes). Only `tool_service-dev` is rebuilt; production
  gets the route at its next deploy and nothing calls it there.
- Top artists: `GET /music/taste/profile` (1,531 artists; ordered by
  score, which is play history plus feedback). Read from
  `tool_service-dev` (127.0.0.1:8103), which serves production's taste DB
  read-only; production `tool_service`'s music routes are not called.
- Token count: the Whisper model's own tokenizer
  (`tokenizer.json` in the model snapshot), through the `tokenizers`
  package (0.23.1 in both `tests/.venv` and the Whisper venv).
