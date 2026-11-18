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

## 2. The prompt (criteria 2 to 4)

`scripts/build_stt_prompt.py` (commit `35f2a65`).

- Sources, read-only, loopback only: HA's template API for area names
  (`AREAS_TEMPLATE`) and the friendly names of the `music_assistant`
  `media_player` entities (`PLAYERS_TEMPLATE`); `tool_service-dev`
  `GET /music/stations?provider=pandora` and
  `GET /music/taste/profile?limit=200`. HA_TOKEN from the environment, else
  the `HA_TOKEN=` line of the repo's `.env` (as `scripts/lib/notify.sh`).
- New route `GET /music/stations` (`tool_service/main.py:2418`): MA's
  `music/radios/library_items` through `ma_read` (refuses anything outside
  `MA_READ_COMMANDS`), filtered on `provider_mappings`, play counts joined
  from the taste DB opened with `taste.db_readonly()`. Only
  `tool_service-dev` was rebuilt (`up -d --build --no-deps
  tool_service-dev`; every other container's id and creation time
  unchanged, compared before and after).
- Output: one line in an EnvironmentFile, default
  `~/services/wyoming-whisper/dev-prompt.env` (new file), key
  `WHISPER_INITIAL_PROMPT`, with a comment header carrying the token and
  name counts and no timestamp. Written only when the content changes,
  through a temporary file in the same directory and a rename
  (`write_if_changed`, `scripts/build_stt_prompt.py:274`).
- Names only. The script adds nothing but `", "` between names and a
  final full stop. Characters systemd treats specially inside double
  quotes (`" \ $ backtick`) are dropped from names, control characters
  become spaces, and a comma inside a name becomes a space.
- `Kronk` is added as a fixed name (plan, M19 step 4, left to M13 by
  M19). Not one of criterion 2's four sources; see Departures.
- `COMMAND_WORDS` (`scripts/build_stt_prompt.py:85`): the words of the
  command forms. A test extracts every literal word from
  `command_forms.PLAYBACK_RE`, `RATE_RE`, `ANOTHER_RE` and
  `routing._MUSIC_RE` and fails if one is neither in `COMMAND_WORDS` nor
  in its explicit list of nouns and function words. A kept name that
  contains a command word is listed in the summary (`command_word_names`).
  On the live sources: **none**.
- Token count: `len(tokenizer.encode(" " + prompt.strip(),
  add_special_tokens=False))` with the model's own `tokenizer.json`, the
  exact call faster-whisper 1.2.1 makes (`transcribe.py:1143-1147`).
  faster-whisper keeps at most the last 223 prompt tokens; the default
  budget is 100.
- Budget rule (criterion 4; `candidates` and `build`,
  `scripts/build_stt_prompt.py:214-226`): Kronk and every room first, in
  full; then stations (most played first), artists (highest taste score
  first) and players take turns, one name each, until all three lists are
  spent. A name is kept when the whole line with it still fits; one that
  does not fit is skipped and the next tried. Folded duplicates are
  skipped (an artist whose station is already in is not added again). A
  station's trailing " Radio" is dropped: all 50 library stations end in
  it, and the distinctive words are the ones before it. Output order:
  category (Kronk, rooms, stations, artists, players), alphabetical inside
  each, so play counts reordering the sources does not change the text;
  only a change in the kept names does. A first version put players before
  stations and artists in strict order; on the live sources that spent the
  budget on 8 player names and kept 7 of 50 stations and 1 artist, so the
  rule was changed before anything used it.
- Live result (`--print`, 2026-11-18 00:30 UTC; the line itself is not
  committed): **100 tokens, 26 names**: Kronk; 5 of 5 rooms; 6 of 46
  stations (50 less 4 folded duplicates); 8 of 198 artists offered; 6 of 8
  players. Examples: rooms "Basement Theater", "Office"; stations "Anime
  Pop", "Mushroom Jazz"; artists "Air", "Orbital"; players "SHIELD". Two
  runs were byte-identical.
- Exit codes: 0 written or unchanged, 1 a source failed or was empty (old
  file untouched, no restart), 2 usage or a refused unit, 3 tokenizer
  missing, 4 restart failed.
- Tests: `tests/test_build_stt_prompt.py` (52) and
  `tests/test_music_stations.py` (7); stubbed sources, `no_network`,
  `tmp_path`, a stand-in token counter, a recorded restart.

## 3. The bench (criteria 9 to 11)

`scripts/stt_prompt_bench.py`, run with `tests/.venv/bin/python`.

- `render`: corpus sentences (`tests/voice_corpus.jsonl`, 158 distinct
  texts) and carrier sentences for household names ("Play <station>.",
  "Play some <artist>.", "Play music in the <room>.") from a names file,
  rendered by Piper over its Wyoming port 10200 straight to WAV files,
  resampled to 16 kHz mono 16-bit (the pucks' format). Voices rotate over
  three already installed in the Piper volume (`en_US-lessac-medium`,
  `en_US-hfc_male-medium`, `en_US-amy-medium`); the render reads Piper's
  `describe` answer and refuses any voice not installed, so nothing is
  downloaded. No HA, no speaker, no pipeline.
- Each sentence in three variants: `clean`; `noise10` (white noise at
  10 dB SNR); `room` (synthetic reverberant room, an exponential-decay
  impulse of about 0.5 s, pink noise at 15 dB SNR, 12 dB quieter).
- Non-speech (criterion 11), generated, 4 clips each of 2 to 4 s:
  `silence` (digital zero), `hiss` (-55 dBFS), `white` and `pink` noise
  (-20 dBFS), `hum` (50/60 Hz and harmonics), `water` (modulated bright
  noise), `clatter` (random decaying knocks), `beeps` (three appliance
  tones), `music` (synthesized chords), `reversed` (a Piper sentence
  played backwards: speech-like, no words). Nothing downloaded.
- `run`: one Wyoming `transcribe` / `audio-start` / chunks / `audio-stop`
  exchange per file, as HA sends it; latency is from `audio-stop` to the
  `transcript` event. Loopback hosts only (`check_host`). Scores: WER over
  normalized words; a command-form error when `routing.decide()` gives the
  transcript a different (rule, direct call) than the reference text; for
  speech, rows where the transcript gained a prompt name the reference did
  not have; for non-speech, whether any text came back and whether it held
  a prompt name. HA's local tier (intents, the music blueprint) is not
  modelled: it cannot be asked without running a pipeline.
- Reads the M12 labelling sheet (`labels.csv` of
  `scripts/voice_label_sheet.py`): `sample_id`, `audio_file`,
  `correct_text`; unlabelled rows skipped. The render writes the same
  columns plus `variant` and `origin`.
- Files: `~/kronk-m13-bench/` (new directory, outside the repo):
  `names.tsv`, `synthetic/labels.csv`, `synthetic/audio/*.wav` (754
  files, 63 MB), `results/*.json` (per-row text; not committed).
- Tests: `tests/test_stt_prompt_bench.py` (31): framing on in-memory
  streams, loopback guard, scoring, an M12-format sheet, generators.

## 4. Host changes (recorded before they were made)

Each is new; no existing file under `~/services/wyoming-whisper/` or
`~/.config/systemd/user/` is edited. `wyoming-whisper.service` is not
edited, restarted, reloaded or signalled.

| # | Change | Exact undo |
| --- | --- | --- |
| H1 | New file `~/services/wyoming-whisper/dev-prompt.env`, written by `scripts/build_stt_prompt.py` | `rm -- "$HOME/services/wyoming-whisper/dev-prompt.env"` (after H2's undo) |
| H2 | New unit `~/.config/systemd/user/wyoming-whisper-dev.service`, a copy of `systemd/wyoming-whisper-dev.service`; `daemon-reload`; `start` (enable only after the memory check) | `systemctl --user disable --now wyoming-whisper-dev.service; rm -- "$HOME/.config/systemd/user/wyoming-whisper-dev.service"; systemctl --user daemon-reload` |
| H3 | New units `~/.config/systemd/user/kronk-stt-prompt.{service,timer}`, copies of `systemd/`; timer enabled (step 7) | `systemctl --user disable --now kronk-stt-prompt.timer; rm -- "$HOME/.config/systemd/user/kronk-stt-prompt.service" "$HOME/.config/systemd/user/kronk-stt-prompt.timer"; systemctl --user daemon-reload` |
| H4 | New directory `~/kronk-m13-bench/` (bench audio and results) | `rm -r -- "$HOME/kronk-m13-bench"` |
| H5 | New directory `~/backups/ha/m13-2026-11-17/` (copies of four HA `.storage` files, taken 00:19 UTC) | Keep; `rm -r` only once M13's HA change is settled |

Stop rule for H2, from the brief: if any `llama-*` unit, LiteLLM or
production Whisper logs an allocation error, or a dev chat request on
gemma or devstral fails after the load, `systemctl --user stop
wyoming-whisper-dev.service` at once, then disable it, and report. No
llama unit is restarted to make room.

## 5. Home Assistant changes (recorded before they were made)

No HA restart, no core config reload. Backups taken before:
`~/backups/ha/m13-2026-11-17/` (`core.config_entries` sha256
`dade50dc…0c03`, `assist_pipeline.pipelines` `1ccc96e5…2214`,
`core.entity_registry`, `core.device_registry`).

| # | Change | How | Exact undo |
| --- | --- | --- | --- |
| A1 | New Wyoming config entry for `127.0.0.1:10301`, retitled "Kronk Whisper STT dev"; HA creates one STT entity for it | REST config flow `POST /api/config/config_entries/flow {"handler": "wyoming"}`, then the user step `{"host": "127.0.0.1", "port": 10301}`; title by websocket `config_entries/update` | After A2's undo: `DELETE /api/config/config_entries/entry/<new entry id>` (or Settings → Devices & services → Wyoming → "Kronk Whisper STT dev" → Delete). Removes its entity too |
| A2 | `kronk-dev` pipeline (`01m7gvyhhfk2jp7havxm88ecme`): `stt_engine` `stt.faster_whisper` → the new entity. Every other field copied from its current value | websocket `assist_pipeline/pipeline/update` | The same call with `stt_engine: "stt.faster_whisper"` (fields as in the "before" read below) |

Not touched: the `kronk` pipeline, "Full local assistant", "Home
Assistant", the production Wyoming entries, the pucks' selects, every
other entry.

"Before" read (websocket, 00:38 UTC), the fields A2 copies:
`kronk-dev`: `conversation_engine conversation.kronk_dev`,
`conversation_language *`, `language en`, `stt_engine stt.faster_whisper`,
`stt_language en`, `tts_engine tts.piper`, `tts_language en_US`,
`tts_voice en_US-hfc_male-medium`, `wake_word_entity null`,
`wake_word_id null`, `prefer_local_intents true`. `kronk`: the same
except `conversation_engine conversation.ollama_conversation`. Preferred
pipeline `01ksc2pw5bkhtk37dh2d6zwcxp` (`kronk`). Wyoming entries: the two
above, both `loaded`. STT entities: `stt.faster_whisper` only.

### Step 4 as done (00:41 to 00:46 UTC)

H1: `dev-prompt.env` written (sha256 `df732f30…a827`, 100 tokens, 26
names, identical to the `--print` preview). H2: unit copied,
`daemon-reload`, started once at 00:41:30 UTC (pid 1155070), listening on
`127.0.0.1:10301` only (`ss -tlnp`). `/proc/<pid>/cmdline` ends
`--local-files-only --initial-prompt <the line>`: the prompt arrives as
one argument. The model loaded on the first transcription (one synthetic
clip, 0.55 s including the load). Enabled at 00:46 UTC after the checks
below.

Memory (method as in section 1):

| Measure | Before (00:41:20) | After the first load (00:41:45) | Change |
| --- | --- | --- | --- |
| RAM used / free / available (MiB) | 72,439 / 14,568 / 53,344 | 75,201 / 11,422 / 50,582 | +2,762 / −3,146 / −2,762 |
| GTT used (GB of 109.05) | 48.558 | 50.724 | +2.166 |
| VRAM carve-out used (GB of 0.537) | 0.5276 | 0.5309 | +0.003 |
| Memory PSI some avg60 | 0.00 | 0.00 | |
| `wyoming-whisper-dev` GTT / RSS (GB) | not running | 2.067 / 0.674 | |
| `wyoming-whisper` (production) GTT / RSS | 2.097 / 0.717 | 2.097 / 0.717 | 0 |
| `llama-devstral-q4` / `llama-gemma3-4b` / `llama-gemma4-e4b` GTT | 36.518 / 3.963 / 5.475 | same | 0 |
| `gpu_busy_percent` at idle | 0 | 0, 0, 0 (00:45) | |

Health after the load:

- No out-of-memory, allocation, `hipError` or error line in the journals of
  the three `llama-*` units, `wyoming-whisper`, `wyoming-whisper-dev` or
  `kronk-memwatch` since 00:41 UTC; none in `kronk-litellm-1`'s log.
- `/health` on 11438, 11439, 11440: `{"status":"ok"}`.
- `/dev/api/chat` "What is the capital of Portugal?": 200 in 1.85 s,
  "Lisbon" (coordinator, `gemma-4-e4b`).
- `/dev/api/chat` "Write a Python function that returns the reverse of a
  string. Use the coding agent.": 200 in 12.4 s, a correct function;
  `orchestrator-dev` logged `ask_coding`, agent `coding`, model
  `devstral-2512-q4`.
- Production Whisper: same pid 2809, `NRestarts=0`, active since
  2026-11-04; unit file sha256 unchanged (`441b8b78…a797b1`).

Headroom: GTT 50.7 of 109 GB (memwatch alerts at 90); 50.6 GB available.
`mem_free` 11.4 GB is under memwatch's 12 GB warning line, which counts
only with memory pressure (PSI 0.00 here; `mem_free` already dipped to
6 GB at PSI 0 before this item). The 2 GB unconditional floor is far off.
The cost is the one predicted in section 1.
