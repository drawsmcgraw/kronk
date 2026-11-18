# M13 build report: bias Whisper toward household vocabulary (dev path only)

Build agent, 2026-11-18 (UTC), branch `refactor`. Criteria:
`docs/sprint/M13-criteria.md`. Status: built. Commits `ce8375c` (check first), `35f2a65` (prompt
script, `/music/stations`), `de3b40d` (bench, unit copies, change
records), `2d970f7` (dev instance), `9d91118` (HA), `0aadd59`
(regeneration, docs) and the commit that completes this report. Each
host and HA change was recorded here, with its undo, in a commit made
before the change.

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

### Step 5 as done (00:47 to 00:49 UTC)

- A1: config flow `wyoming`, user step `{"host": "127.0.0.1", "port":
  10301}` → `create_entry`, entry **`01M7P1PQG3W0XAPSA5ZXJX5J65`**,
  stored `data {"host": "127.0.0.1", "port": 10301}`, entity
  **`stt.faster_whisper_2`** (friendly name "faster-whisper", from the
  server's info). Retitled "Kronk Whisper STT dev" with
  `config_entries/update`; the entry reloaded and is `loaded`.
- A2: `assist_pipeline/pipeline/update` on `kronk-dev`, `stt_engine
  stt.faster_whisper_2`, every other field copied.
- After, compared with the backups and the "before" read:
  - `core.config_entries`: one entry added; no existing entry changed
    (each entry's JSON compared whole), the production Wyoming entry
    `01KSBFASPA7R5EYA5ZHHX72ATE` included.
  - `assist_pipeline.pipelines`: `kronk`, "Full local assistant" and
    "Home Assistant" identical field by field; `kronk-dev` differs in
    `stt_engine` only; preferred pipeline still `kronk`.
  - Websocket reads before and after: `~/backups/ha/m13-2026-11-17/
    ws_read_before.json`, `ws_read_after.json`.
- No HA restart: `homeassistant` `StartedAt 2026-11-18T00:14:34Z`
  (M12's promotion), `RestartCount 0`. Both STT entities read `unknown`,
  as STT entities do between runs.
- Not done, on purpose: an STT-stage pipeline run to prove the wiring.
  With M12's capture on, HA writes the audio of every run that starts at
  the STT stage to `~/kronk-voice-recordings`, which this item must not
  write to. Zero files there are newer than the prompt file. The first
  real proof is Drew's "Hey Jarvis" (Left for Drew).

Undo, in order (exact):

1. A2: over HA's websocket (from inside the container, `docker exec -i
   -e HA_TOKEN homeassistant python3 -`, aiohttp to
   `ws://localhost:8123/api/websocket`): `assist_pipeline/pipeline/list`,
   take `kronk-dev` (`01m7gvyhhfk2jp7havxm88ecme`), then
   `{"type": "assist_pipeline/pipeline/update", "pipeline_id":
   "01m7gvyhhfk2jp7havxm88ecme", <its twelve fields>, "stt_engine":
   "stt.faster_whisper"}`. The twelve fields are those of the "before"
   read above; only `stt_engine` differs from the current value.
2. A1: `curl -X DELETE -H "Authorization: Bearer $HA_TOKEN"
   http://127.0.0.1:8123/api/config/config_entries/entry/01M7P1PQG3W0XAPSA5ZXJX5J65`
3. Then H2 and H1 of section 4.

## 6. Bench results (synthetic speech; not evidence for production)

Run 2026-11-18 00:36 to 00:58 UTC, one file at a time, on the set of
section 3 (`~/kronk-m13-bench/synthetic/labels.csv`, 754 files).
`prod-before` and `prod-after`: production's port 10300, no prompt, before
the dev instance existed and after the HA change. `dev-prompt`: port
10301 with the prompt as installed (the comma-separated line).
Same model, compute type and beam size on both ports, so 10300 is the
"without the prompt" arm. Prompt-name counts are recounted for every run
against the installed prompt (`compare --prompt-file`).

**Synthetic speech is not evidence for production.** Piper's three voices
are clean, steady and pronounce every name the way its phonemizer reads
the spelling; real household speech, far-field pucks and real kitchens
are none of that. These numbers show the mechanism and its hazards; the
decision waits for the M12 clips (section 8).

### Speech: WER, command-form errors, latency

| Variant | Run | n | WER | Command-form errors | Rows with prompt names added | p50 s | p95 s |
| --- | --- | --- | --- | --- | --- | --- | --- |
| clean | prod-before | 238 | 9.4 % | 26 | 0 | 0.316 | 0.369 |
| clean | dev-prompt | 238 | 9.0 % | 27 | 0 | 0.319 | 0.375 |
| clean | prod-after | 238 | 9.4 % | 26 | 0 | 0.316 | 0.364 |
| noise10 | prod-before | 238 | 11.6 % | 30 | 0 | 0.315 | 0.364 |
| noise10 | dev-prompt | 238 | 9.9 % | 28 | 0 | 0.322 | 0.369 |
| noise10 | prod-after | 238 | 11.6 % | 30 | 0 | 0.316 | 0.364 |
| room | prod-before | 238 | 16.3 % | 39 | 0 | 0.318 | 0.366 |
| room | dev-prompt | 238 | 13.7 % | 34 | 2 | 0.322 | 0.369 |
| room | prod-after | 238 | 16.3 % | 39 | 0 | 0.318 | 0.365 |

All 714 speech rows: WER 12.4 % without the prompt, 10.9 % with it;
command-form errors 95 and 89. Latency (audio-stop to transcript) moves
by 3 to 8 ms at p50 and p95: the prompt's 100 tokens cost almost nothing.

By sentence group (no prompt → prompt):

| Sentences | Variant | n | WER | Command-form errors |
| --- | --- | --- | --- | --- |
| stations in the prompt | clean / noise10 / room | 10 each | 7.9 → 0.0 % / 7.9 → 0.0 % / 21.1 → 5.3 % | 0 → 0 throughout |
| stations not in the prompt | clean / noise10 / room | 40 each | 9.8 → 11.7 % / 16.6 → 16.0 % / 17.8 → 19.0 % | 2 → 2 / 4 → 3 / 4 → 5 |
| artists in the prompt | clean / noise10 / room | 9 each | 31.2 → 25.0 % / 40.6 → 18.8 % / 46.9 → 21.9 % | 2 → 3 / 2 → 2 / 4 → 3 |
| artists not in the prompt | clean / noise10 / room | 16 each | 25.4 → 30.2 % / 31.7 → 31.7 % / 46.0 → 41.3 % | 4 → 4 / 3 → 4 / 5 → 4 |
| rooms | clean / noise10 / room | 5 each | 0.0 → 0.0 % / 0.0 → 0.0 % / 3.7 → 3.7 % | 0 → 0 / 0 → 0 / 1 → 1 |
| corpus sentences | clean / noise10 / room | 158 each | 8.0 → 7.4 % / 9.2 → 8.0 % / 13.5 → 11.6 % | 18 → 18 / 21 → 19 / 25 → 21 |

(The station and artist groups count the sentence words, "Play" and
"some" included. "In the prompt" folds a station's " Radio" as the
prompt does.)

What the prompt does to routing, row by row: it fixes 20 command-form
errors and causes 14. Of the 14:

- 9 are punctuation. The comma-separated prompt pulls Whisper toward
  commas: "Stop. Stop." → "Stop, Stop."; "Pause. Sonos. Move. Dirt." →
  "Pause, Sonos, Move, Dirt."; "Thumbs up! I know but you missed the best
  part." → "Thumbs up, I know but …"; the three-sentence Anime Pop
  transcript became one sentence. The M14 messy gate counts sentences, so
  these route `default` (or `music`) instead of `messy`. Words right,
  route changed.
- 2 are a player name replacing a command verb, the hazard the plan names:
  "Shuffle my YouTube playlist, anime bangers." → "SHIELD, My YouTube
  Playlist, …" (room variant, twice). "SHIELD" is a Music Assistant player
  in the prompt.
- 3 lost the verb: "Play" heard as "Place" (twice) or "Playbook", in
  front of a station or an artist name, so `music` became `default`. The
  prompt holds names and no verbs, and the words after the verb pulled
  toward the prompt's spelling.

### A second format, tried and dropped

One variable: the same 26 names joined by full stops ("Kronk. Basement
Theater. …", also 100 tokens), on the dev instance only (restarted twice:
in and out). WER about the same (8.8 / 9.8 / 14.5 %), but command-form
errors 29 / 34 / 50: it fixed 23 and caused 41, nearly all by splitting
one request into sentences and tripping the messy gate. The comma line
stays. Restored by the regeneration service itself (section 7).

### Non-speech (criterion 11)

Four clips per kind, 2 to 4 s, generated (section 3).

| Kind | Returned text: no prompt (before / after) | Returned text: prompt | Returned a prompt name: no prompt / prompt |
| --- | --- | --- | --- |
| silence (digital zero) | 4 / 4 | 4 | 0 / 0 |
| hiss (-55 dBFS) | 4 / 4 | 4 | 0 / 0 |
| white noise | 4 / 4 | 4 | 0 / 0 |
| pink noise | 4 / 4 | 4 | 0 / 0 |
| hum | 4 / 4 | 4 | 0 / 0 |
| water | 4 / 4 | 4 | 0 / 0 |
| clatter | 4 / 4 | 3 | 0 / 2 ("Kronk", twice) |
| beeps | 4 / 4 | 4 | 0 / 0 |
| music | 4 / 4 | 4 | 0 / 0 |
| reversed speech | 4 / 4 | 4 | 0 / 0 |
| **all 40** | **40 / 40** | **39** | **0 / 2** |

- Without a prompt, production already returns words for every clip,
  silence included: "Thank you." (15 of 40), "You" (9), "so", "Bye.",
  "Beep. Beep. Beep.". That is Whisper's known silence hallucination; no
  VAD is configured on either instance. Kronk sees such a transcript only
  if HA's pipeline passes it on, which it does today for production.
- With the prompt, the only name that came back on non-speech is
  "Kronk", on 2 of 4 clatter clips. No station, artist, room or player
  name appeared on any non-speech clip. No command word either way.
- Prompt names added to real speech: 2 of 714 rows, both the "SHIELD"
  case above.

### Production unchanged (criterion 5)

`prod-before` against `prod-after`: **714 of 714 speech rows give the
same text**; 750 of 754 rows overall. The 4 that differ are non-speech
(3 clatter, 1 reversed speech). Run three more times each on production,
those clips gave 3, 2, 3 and 2 distinct texts: Whisper's temperature
fallback samples on audio it cannot decode, so they vary run to run on an
unchanged instance. A silence clip and a speech clip gave 1 text in 3 runs.

## 7. Regeneration (criterion 8), as done

- H3 at 00:56 UTC: `kronk-stt-prompt.{service,timer}` copied,
  `daemon-reload`, `enable --now kronk-stt-prompt.timer`; next run
  2026-11-18 04:00:40 local.
- Live check, 00:58 UTC, with the full-stop variant still in the file:
  `systemctl --user start kronk-stt-prompt.service` → "written …",
  "try-restart wyoming-whisper-dev.service", dev pid 1187411 → 1202239.
  Started again → "unchanged …", no restart, pid 1202239. Production pid
  2809 throughout. The file is byte-identical to the one benched.
- Unit tests pin the rest with a recorded restart
  (`tests/test_build_stt_prompt.py`): restart on change only; refused for
  `wyoming-whisper.service`, `wyoming-whisper`, any llama unit,
  `homeassistant`, a name with `;`, and anything not `*-dev.service`; exit
  4 with the new file in place when the restart fails.
- After a dev restart the model reloads on the next transcription: the
  first Hey Jarvis after a changed night pays the load (the first clip
  after the start took 0.55 s against about 0.32 s after it; model files
  in page cache).

## 8. Deciding production: the real-clip procedure (criterion 12)

After M12's labelling (about 200 clips with `correct_text` filled in,
`~/kronk-voice-labels/<date>/labels.csv`):

1. Check both instances are up and the prompt is the one to judge:
   `ss -tlnp | grep -E ':1030[01]'`; keep a copy of
   `~/services/wyoming-whisper/dev-prompt.env` beside the results (the
   nightly rebuild may change it).
2. Run the same sheet against both ports, one after the other, in a quiet
   hour (both share the GPU with the household's models):

   ```bash
   S=~/kronk-voice-labels/<date>/labels.csv; O=~/kronk-m13-bench/real-<date>
   cp ~/services/wyoming-whisper/dev-prompt.env $O.prompt.env
   tests/.venv/bin/python scripts/stt_prompt_bench.py run --sheet $S --port 10300 \
       --label no-prompt --prompt-file $O.prompt.env --out $O/no-prompt.json
   tests/.venv/bin/python scripts/stt_prompt_bench.py run --sheet $S --port 10301 \
       --label prompt --prompt-file $O.prompt.env --out $O/prompt.json
   tests/.venv/bin/python scripts/stt_prompt_bench.py compare $O/no-prompt.json $O/prompt.json \
       --prompt-file $O.prompt.env
   ```

   Only labelled rows are scored (non-empty `correct_text`). The results
   hold transcripts of household speech: keep them under
   `~/kronk-m13-bench/` or the labels directory, never in the repo.
3. Pass rule (the roadmap's): on the labelled clips, the prompt run has
   **lower WER and fewer command-form errors, at a p95 latency no worse**
   than the no-prompt run. Look also at the rows the prompt broke (the
   per-row `command_error` flips), since a net gain can hide a new class
   of failure, as the synthetic set's comma punctuation showed.
4. Puck noise: M12's clips do not include non-speech by design (every
   recording is a wake-word run). If the household's false wakes are in
   the set, count `names_added` on them; otherwise the synthetic
   non-speech counts of section 6 stand.
5. **The decision is Drew's.** If it passes and Drew says go, follow
   "Moving the prompt to production" in `docs/features/voice-pipeline.md`.

## 9. Criteria, one line each

| # | Evidence |
| --- | --- |
| 1 | Section 1: `--help` flags quoted; HA wiring table; memory table; one instance cannot serve two prompts (prompt is a process-wide argument, `Transcribe.context` ignored), so the second instance was built. |
| 2 | One line, 100 Whisper tokens (model tokenizer, faster-whisper's own encoding), from HA areas, MA players, MA library Pandora stations, taste-profile artists, plus "Kronk". Command-word test derives its words from the routing patterns; names containing a command word on live data: none. |
| 3 | Loopback reads only (HA template API, `tool_service-dev` GETs, MA through `ma_read`); writes only `--out` (temp file in the same directory, then rename); byte-identical across runs; order independent of play counts (test); every source failing or empty → exit 1, old file untouched, no restart (20 parametrized cases). |
| 4 | Budget rule in section 2 and the script's docstring; pinned by five tests (`candidates` order, turns, rooms first, skip-and-try-next, folded duplicates). |
| 5 | Second unit, `systemd/wyoming-whisper-dev.service`: loopback 10301, same model, language, compute type, device and model directory as production (test compares the two `ExecStart` lines), prompt from `EnvironmentFile`. `wyoming-whisper.service` sha256 `441b8b78…` before and after, pid 2809, `NRestarts=0`, active since 2026-11-04. Port 10300: 714 of 714 speech rows identical before and after; 4 non-speech rows vary run to run on the unchanged instance (section 6). |
| 6 | Memory table, section 4: +2.17 GB GTT, −2.76 GB available; llama servers healthy, gemma and devstral dev requests answered; no OOM or allocation line. Recommendation: section 11. |
| 7 | `kronk-dev` `stt_engine stt.faster_whisper_2` (entry `01M7P1PQG3W0XAPSA5ZXJX5J65`, `127.0.0.1:10301`); `kronk` and the other pipelines identical field by field; existing config entries byte-identical as JSON; no HA restart (`StartedAt 00:14:34Z`). Undo in section 5. |
| 8 | `kronk-stt-prompt.timer` 04:00, after the 03:30 taste rebuild; live: changed → one dev restart, unchanged → none, production pid unchanged; unit tests with a recorded restart refuse production and non-dev units. |
| 9 | `scripts/stt_prompt_bench.py`: Wyoming client, WER, command-form errors by `routing.decide`, files from `--sheet`'s directory, M12 sheet format (test with M12's column set). No sound. |
| 10 | Section 6: corpus and names rendered by Piper (port 10200, files only), clean / noise10 / room, WER, command-form errors, p50/p95, both instances. Synthetic, not evidence for production (said there). |
| 11 | Section 6: 40 generated non-speech clips, 10 kinds: text returned 40/40 without the prompt, 39/40 with it; prompt names returned 0 without, 2 with (both "Kronk", clatter). Nothing downloaded. |
| 12 | Section 8. |
| 13 | `4834 passed, 3 skipped, 53 xfailed` (baseline 4743 + 91 new); new tests under `no_network`, `tmp_path`, in-memory streams. |
| 14 | Replay below: of the M10 baseline's 114 rows, 112 the same, 2 different, both listed flappers; 46 rows added since the baseline: 40 pass, 3 xfail, 3 skip; no fail. |
| 15 | `ss`: `127.0.0.1:10301` (dev), `0.0.0.0:10300` (production, unchanged), no other new listener. Production containers: ids and creation times unchanged since the snapshot before the build; only `kronk-tool_service-dev-1` recreated. MA `StartedAt 2026-11-04`, untouched. Pucks: no select or firmware call. No sound: Piper to files, Whisper from files, no TTS service, no satellite, no device id. Nothing written to `~/kronk-voice-recordings` (newest entry 19:13 local, before the build). |
| 16 | `docs/features/voice-pipeline.md` ("The dev Whisper instance and the vocabulary prompt"), `README.md` service map and "Whisper (STT) units", `ROADMAP.md` voice-accuracy item. |

## 10. Test and replay output (verbatim)

`./scripts/run_tests.sh -q -p no:cacheprovider`, last lines:

```
-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
4834 passed, 3 skipped, 53 xfailed, 6 warnings in 41.04s
```

`python3 scripts/voice_replay.py --label m13-build` (01:00 UTC), summary
lines (148 rows reached; per-row results are in the JSON):

```
  X xpass executed   eval-shuffle-it  []
  s skip  skipped    inc-1017-try-playing-it-ytm
  s skip  skipped    inc-1019-ytm-link
  s skip  skipped    m4b-fetch-and-run-on-the-mirror
voice_replay: STOPPED: media_player state changed before m14-lookalike-am: [{'entity_id': 'media_player.sonos_move_derp', 'before': {'state': 'unavailable', 'media_title': None, 'media_artist': None, 'media_content_id': None}, 'after': {'state': 'idle', 'media_title': None, 'media_artist': None, 'media_content_id': None}}, {'entity_id': 'media_player.sonos_move_derp_2', 'before': {'state': 'off', 'media_title': None, 'media_artist': None, 'media_content_id': None}, 'after': {'state': 'idle', 'media_title': None, 'media_artist': None, 'media_content_id': None}}]
voice_replay: {'pass': 122, 'xfail': 22, 'skip': 3, 'xpass': 1}; executed 69, checked read-only 76; players CHANGED: [{'entity_id': 'media_player.sonos_move_derp', 'before': {'state': 'unavailable', 'media_title': None, 'media_artist': None, 'media_content_id': None}, 'after': {'state': 'idle', 'media_title': None, 'media_artist': None, 'media_content_id': None}}, {'entity_id': 'media_player.sonos_move_derp_2', 'before': {'state': 'off', 'media_title': None, 'media_artist': None, 'media_content_id': None}, 'after': {'state': 'idle', 'media_title': None, 'media_artist': None, 'media_content_id': None}}] (17 checked)
  skipped inc-1017-try-playing-it-ytm: follow-up that needs conversation history; a replay has none
  skipped inc-1019-ytm-link: typed link; a spoken request carries no URL
  skipped m4b-fetch-and-run-on-the-mirror: typed link; a spoken request carries no URL
voice_replay: wrote /home/drew/git-repos/drawsmcgraw/kronk/docs/bench/voice_replay_2026-11-18_010011_m13-build.json
```

The stop: the Sonos Move came online on its own at 01:06:00 UTC
(`unavailable` → `idle`) and started playing at 01:08:06, not by the replay (the dev path is
dry-run and the replay had stopped). After
ten minutes the 12 rows not reached were run once by id (01:16 UTC);
see Departures:

```
voice_replay: pipeline kronk-dev → http://localhost/dev/voice, dry-run on, 12 rows (148 not selected)
  . pass  executed   m14-lookalike-am
  s skip  skipped    m14-lookalike-decimal
  s skip  skipped    m14-lookalike-time
  . pass  checked    m11-jazz-music-style
  . pass  checked    m11-the-song-jazz-music
  . pass  checked    m11-burning-down-the-house
  . pass  checked    m11-girl-with-the-sun-in-the-office
  . pass  checked    m11-toys-in-the-attic-in-the-kitchen
  . pass  checked    m11-handcuffed-in-mississippi-radio
  . pass  checked    m11-some-jazz-on-the-basement-player
  . pass  checked    m11-play-a-game-with-me
  . pass  checked    m11-play-the-movie-inception
voice_replay: {'pass': 10, 'skip': 2}; executed 1, checked read-only 9; players unchanged (17 checked)
  skipped m14-lookalike-decimal: fuzzy local match (HassTimerStatus) while a player is active; not executed
  skipped m14-lookalike-time: fuzzy local match (HassLightSet) while a player is active; not executed
voice_replay: wrote /home/drew/git-repos/drawsmcgraw/kronk/docs/bench/voice_replay_2026-11-18_011618_m13-build-rest.json
```

Files: `docs/bench/voice_replay_2026-11-18_010011_m13-build.json`,
`docs/bench/voice_replay_2026-11-18_011618_m13-build-rest.json`.
Compared with `voice_replay_2026-11-16_012824_m10-baseline.json` by row
id and status: `inc-0930-search-deep-house-ytm` fail → pass (a "YouTube
Music" search row) and `eval-shuffle-it` xfail → xpass, both on the
flapper list; every other baseline row has the same status.

## 11. Recommendation: leave the dev instance running

Leave it up and enabled. It costs 2.1 GB GTT and 0.7 GB RSS once loaded
(about 2 % of GTT, 5 % of available RAM), it is the only STT behind Hey
Jarvis now, and the M12 comparison needs both instances. Stopping it when
not in use buys 2.8 GB but breaks Hey Jarvis unless `kronk-dev` is
pointed back at production's Whisper first (section 5 undo, step 1). If
memory gets tight, that is the order: pipeline back, then `disable --now`.

## 12. Departures from the criteria, and why

1. **"Kronk" in the prompt.** Not one of criterion 2's four sources. Plan
   M19 step 4 asks for it and M19 left it to M13. One fixed name; on
   non-speech it is the only prompt name that came back (2 of 40 clips).
   Removing it is one line (`FIXED_NAMES`).
2. **A new tool_service route.** Criterion 3 says "the MA reads
   tool_service already makes". The read is one it already makes
   (`music/radios/library_items`, the nightly rebuild's), through
   `ma_read`, but exposing it needed `GET /music/stations`: the taste DB
   cannot tell Pandora stations from others. Only the dev pair runs it
   until the next production deploy.
3. **Station names lose " Radio".** All 50 end in it; dropping it saves a
   token a station. The stations in the prompt went from 21.1 % to 5.3 %
   WER in the room variant without it.
4. **"Without the prompt" is production's instance.** No third instance or
   dev restart without a prompt: 10300 runs the same model and flags, and
   was benched before and after.
5. **A second prompt format was tried** (full stops), on the dev instance
   only, and dropped (section 6). The dev unit was restarted twice for it
   (format in; format out, by the regeneration service).
6. **The replay rerun covered only the rows the first run did not reach**
   (12 by `--id`), not a full rerun: a speaker was in use by the household
   and a full pass would have stopped again on the next track change. Two
   of the 12 were skipped by the replay's own guard (a fuzzy local match
   while a player is active).
7. **No STT-stage pipeline run** to prove the HA wiring: M12 would record
   it into `~/kronk-voice-recordings`.

## 13. For Drew

- **Say "Hey Jarvis, what's the capital of Peru"** on any puck. It is the
  first real use of the dev Whisper through HA; the transcript appears in
  `journalctl --user -u wyoming-whisper-dev`.
- **The comma problem.** On synthetic speech the prompt cuts WER (12.4 →
  10.9 %) and command-form errors (95 → 89), but it also turns sentence
  breaks into commas, which hides multi-sentence captures from the M14
  messy gate (9 of the 14 rows it broke), and once replaced "Shuffle" with
  a player name. The M12 clips will say whether that holds on real speech.
  If it does, options are a gate that counts commas too, or a prompt
  without players. Your call after the data.
- **Whisper talks on silence today.** Production returned words on all 40
  generated non-speech clips, silence included ("Thank you."). The VAD
  filter (`--vad-filter`, already installed) is the roadmap's other
  candidate; it is a separate single-variable test on the dev instance.
- **Production Whisper asks the Hugging Face Hub at each model load**
  (`--local-files-only` is ignored by the installed version). Adding
  `HF_HUB_OFFLINE=1` to `wyoming-whisper.service` would stop it; that is
  a production change, not made.
- **Moving the prompt to production** stays off until the real-clip run in
  section 8 passes and you say go.
