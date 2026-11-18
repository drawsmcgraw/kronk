# M20 build report: TTS trial with a go/no-go (the part that needs no clips)

Status: built 2026-11-18; the bench without clips is done. The listening test and the cloning run wait for Drew's clips.

Builder: Opus 5.5 build agent, branch `refactor`, started 2026-11-18 17:54 UTC.
Criteria: `docs/sprint/M20-criteria.md`. Unit suite on `refactor` before the
build: `5423 passed, 3 skipped, 53 xfailed` (`./scripts/run_tests.sh`).

## 1. Check first (criteria 1 and 2), before anything was installed

Everything in this section was read from PyPI's JSON API, the GitHub API,
the Hugging Face model API, shallow clones of the projects' own
repositories under `~/kronk-tts-trial/src/` (source to read, nothing
installed), the running HA container's own source (read with `docker exec
... sed`, no change) and the host's `/sys`, `/proc` and `ss`. Nothing was
installed before this section was written.

### 1.1 Candidates: version, licence, source, streamed synthesis

"Streamed synthesis" below means what M8 needs from the Wyoming side: the
server takes text as `synthesize-start` / `synthesize-chunk` /
`synthesize-stop` and starts audio for the first sentence before the rest of
the text has arrived or been synthesized. It was established from the code
(file:line in the clone); the runs in section 4 check it.

| Component | Version read 2026-11-18 | Licence | Fetched from | Streamed synthesis on the Wyoming path |
| --- | --- | --- | --- | --- |
| Piper baseline | image `rhasspy/wyoming-piper:2.2.2` (already local; PyPI has 2.5.4, not used) | MIT | local image; voice `en_US-hfc_male-medium` from production's `kronk_piper-models` volume, mounted read-only | Yes, by sentence (the voice-pipeline feature doc, M8 finding); re-read from the image's code in section 4 |
| Pocket TTS engine | `pocket-tts` 4.0.1 (PyPI, 2026-10-21; repo `kyutai-labs/pocket-tts` HEAD `a4a6054`) | code MIT; weights CC-BY-4.0 | PyPI; weights `hf://kyutai/pocket-tts-without-voice-cloning` (ungated). The cloning-capable `kyutai/pocket-tts` is gated (auto-approval, needs a Hugging Face account token) | Engine yields audio frames as it generates (`generate_audio_stream`) |
| Pocket Wyoming server A | `ikidd/pocket-tts-wyoming` HEAD `9ed4112` (no releases; most-starred, 41) | none in the repo (no LICENSE file) | GitHub | Advertises `supports_synthesize_streaming=True` (`wyoming_tts_server.py:451`) but does not stream: chunks are concatenated until `synthesize-stop` (`:111-126`), the whole text is generated, then the audio is sent (`:224-232`, all chunks collected before the first `audio-chunk`). First audio = whole-utterance synthesis time |
| Pocket Wyoming server B | `araa47/wyoming_pocket_tts` 1.4.11, HEAD `11df386` | MIT (pyproject) | GitHub | No text streaming: handles only `synthesize` (`handler.py:328`) and does not advertise streaming, so HA sends the whole reply at once; audio is sent as it is generated (`handler.py:397-438`) |
| Pocket Wyoming server C | `MisterEcks/pocket-tts-streaming` HEAD `b9821d3` (last push 2026-03-03) | MIT | GitHub | Yes by code: text chunks go through `stream2sentence` into a generator thread and audio goes out as generated (`wyoming_server.py:223-297`). Stale against pocket-tts 4.x; to be confirmed by a run |
| Chatterbox | `chatterbox-tts` 0.1.7 (PyPI, 2026-03-26; repo `resemble-ai/chatterbox` HEAD `ab55923`) | MIT (code and weights) | PyPI; weights `ResembleAI/chatterbox` (`from_pretrained` fetches 5 files, about 3.2 GB of the 13.9 GB repo) | Not a Wyoming server itself; see the two rows below |
| Chatterbox-Turbo | same package (`chatterbox/tts_turbo.py`) | MIT | weights `ResembleAI/chatterbox-turbo` (about 4.0 GB) | same |
| OpenAI-compatible server | `devnen/Chatterbox-TTS-Server` v2.0.0, HEAD `915ae28` (2026-05-26) | MIT | GitHub | `/v1/audio/speech` returns the whole file after every sentence of the request is synthesized (`server.py:1395-1500`). It always conditions on a WAV: the `voice` must name a file in `voices/` or `reference_audio/` (`server.py:1405-1412`), so as shipped it cannot use the model's built-in voice. Chosen because it supports both models (`engine.py:62-65`) and ships a Strix Halo recipe. Not chosen: `travisvn/chatterbox-tts-api` (AGPL-3.0, default voice is `voice-sample.mp3`, a recording of a person) |
| `wyoming-openai` | 0.9.0 (PyPI, 2026-11-04; repo `roryeckel/wyoming_openai` tag `v0.9.0`, `4cc87eb`) | Apache-2.0 | PyPI | Yes by code, for models listed in `--tts-streaming-models`: text chunks are cut into sentences with `yasbd` as they arrive (`handler.py:1744-1805`), up to 3 sentence requests run at once and play in order (`handler.py:1245-1290`), each response's audio is read as it arrives (`handler.py:1946-1947`). With devnen's server the unit of streaming is therefore one sentence |
| RVC layer | `xchwarze/wyoming-rvc` HEAD `ae53297` (no releases, 2 stars) | GPL-3.0 | GitHub; images `ghcr.io/xchwarze/wyoming-rvc-rocm` (about 15 GB) | Yes by code (`src/wyoming_rvc/wyoming_server.py:65,115-116`, sentence detector over `synthesize-chunk`) |

PyTorch for the GPU candidates: `download.pytorch.org/whl/rocm7.2`,
`torch-2.11.0+rocm7.2` for CPython 3.13 (6.22 GB wheel) with
`torchaudio-2.11.0+rocm7.2` (the newest torchaudio on that index). The
wyoming-rvc README states the PyTorch ROCm 7.2 wheels ship gfx1151 kernels;
that is a README claim and is checked by a run. TheROCK's gfx1151 nightly
index is the fallback if it fails.

Presets, no cloning:

- Pocket TTS: its preset names (`alba`, `marius`, ...) load precomputed
  voice states (`.safetensors` embeddings) from the ungated
  without-voice-cloning repository (`pocket_tts/utils/utils.py`,
  `get_predefined_voice`). The ungated weights cannot take a new voice from
  audio. No audio file is fetched or supplied.
- Chatterbox and Turbo: each Hugging Face repository ships a built-in voice
  as `conds.pt` (precomputed conditionals), used when `generate` gets no
  `audio_prompt_path` (`tts.py:161-163,222`; `tts_turbo.py:183-186,288`).
  devnen's server needs a scratch-only patch to reach that path (departure,
  section 7). Its `voices/*.wav` files are reference recordings and are not
  used. Every Chatterbox output carries Resemble's Perth watermark
  (`tts_turbo.py:319`), applied locally.
- RVC: the layer's only function is to impose a timbre learned from
  recordings of a person. Its built-in timbres are two singing-voicebank
  characters (Kasane Teto, Hatsune Miku), each trained from a voice
  provider's recordings. Using either is the "reference audio of a person"
  the brief rules out, so the RVC layer is not run (criterion 6, ruling 6).
  It is a candidate again when Drew's clips arrive.

How HA drives a Wyoming TTS (read from HA 2026.5.4's
`homeassistant/components/wyoming/tts.py` inside the running container):
when the server's `describe` says `supports_synthesize_streaming`, HA sends
`synthesize-start`, one `synthesize-chunk` per piece of reply text, a
`synthesize` with the whole text "for backwards compatibility", then
`synthesize-stop`, and reads audio until `synthesize-stopped` (or the
connection closes). Otherwise it sends one `synthesize` and reads until
`audio-stop`. Production's `VOICE_STREAM` is off, so production's reply
reaches the TTS as one chunk. The bench sends exactly these sequences.

### 1.2 Host state before the trial (2026-11-18 18:01 UTC)

From `~/kronk-tts-trial/tools/state.sh` (read-only; output kept in
`~/kronk-tts-trial/state/before.txt`):

| Item | Value |
| --- | --- |
| GPU busy | 0 % (sclk 600 MHz, 40 °C) |
| GPU power (`power1_average`, PPT) | 9.1 W |
| VRAM carve-out | 0.53 of 0.54 GB used |
| GTT (system memory mapped for the GPU) | 50.96 of 109.05 GB used |
| System memory | MemTotal 122.84 GB, MemFree 9.71 GB, MemAvailable 48.99 GB, Cached 37.89 GB, swap free 7.97 GB |
| Memory pressure (PSI some) | avg10/60/300 = 0.00 |
| Disk free on `/` | 851 GB |

GPU processes and what each holds (fdinfo `drm-memory-gtt`, KFD VRAM, RSS):

| Process | GTT | KFD | RSS |
| --- | --- | --- | --- |
| llama-server devstral-24B Q4 (Vulkan, :11440) | 34.01 GB | - | 8.46 GB |
| llama-server gemma-4-E4B (Vulkan, :11438) | 5.10 GB | - | 10.46 GB |
| llama-server gemma-3-4b (Vulkan, :11439) | 3.69 GB | - | 0.66 GB |
| wyoming-whisper production (HIP, :10300) | 1.98 GB | 2.06 GB | 0.73 GB |
| wyoming-whisper-dev (HIP, 127.0.0.1:10301) | 1.93 GB | 2.06 GB | 0.63 GB |
| wyoming-piper production (CPU, :10200) | 0 | - | 0.33 GB |

All three llama `/health` answer `{"status":"ok"}`; none of the five units
has restarted (`NRestarts=0`).

### 1.3 The June hang investigation, restated as what criterion 8 looks for

`docs/incidents/INVESTIGATION_2026-06-09_hangs.md` (and its follow-up
`INCIDENT_2026-08-12.md`): with no requests in flight, a ROCm/HIP process
on this gfx1151 iGPU can hold the GPU at 100 % busy with the shader clock
pinned at 2900 MHz and package power around 35 W (June) or 48 to 62 W
(August), against 0 to 2 % busy, 600 MHz and about 7 to 9 W genuinely idle;
per-process fdinfo shows almost no engine time for the spinning process.
Every silent host hang on record happened while such a spinner was
resident. The llama units moved to Vulkan for that reason. Criterion 8
therefore looks, with each candidate loaded and idle, for
`gpu_busy_percent` above the Piper-only baseline, `sclk` off 600 MHz, and
power above the baseline, sustained.

### 1.4 Constraints found during the check that shape the method

- `kronk-memwatch.service` pushes to Drew's phone (HA `notify`) when (a)
  `gpu_busy_percent` is at least 95 for 5 consecutive 60 s polls while the
  llama/wyoming processes accumulate under 1 % engine time (ROCm work is
  invisible to fdinfo, so a busy ROCm candidate counts), or (b) MemFree is
  under 2 GB for 2 polls, whatever the pressure. The brief forbids
  notifications and forbids stopping that unit. So: GPU work by a ROCm
  candidate runs in bursts of at most 150 s with at least 75 s rest; the
  sampler stops a candidate whose GPU stays at 90 % or more for 180 s while
  idle (before memwatch's fifth poll); and page cache from the trial's own
  downloads is dropped file by file (`posix_fadvise(DONTNEED)`, no root),
  because the kernel's high watermark here is about 340 MB and downloads
  alone would push MemFree under 2 GB.
- Unprivileged `unshare` is blocked (`kernel.apparmor_restrict_unprivileged_userns=1`),
  but `bwrap --unshare-net` works and leaves a process only its own
  loopback (a connect to a public address returns `ENETUNREACH`). That is
  the offline control for criterion 15 (section 4).
- Production's Piper container runs `--voice en_US-lessac-medium` and HA
  asks for `en_US-hfc_male-medium` per request (C9). Its voice files live in
  the `kronk_piper-models` volume.

## 2. What was installed where, and every download

Everything lives under `~/kronk-tts-trial/` (user `drew`); nothing was
installed into an existing environment, no system package, nothing under
`/etc`, no systemd unit. Downloads, all from the sources the brief allows:

| What | From | Size | Landed in |
| --- | --- | --- | --- |
| Source of the six projects in section 1.1 plus `araa47/wyoming_pocket_tts`, `MisterEcks/pocket-tts-streaming`, `travisvn/chatterbox-tts-api` (shallow clones, to read and, for three of them, to run) | GitHub, the projects' own repositories | 69 MB | `src/` |
| `torch 2.11.0+rocm7.2`, `torchaudio 2.11.0+rocm7.2`, `triton-rocm 3.6.0` | `download.pytorch.org/whl/rocm7.2` | torch wheel 6.22 GB (Content-Length); the venv is 15 GB unpacked | `venv/chatterbox`, uv cache `cache/uv` |
| `torch 2.11.0+cpu` | `download.pytorch.org/whl/cpu` | 0.19 GB | `venv/pocket` |
| `pocket-tts 4.0.1`, `wyoming 1.8.0`, `stream2sentence 1.1.3`, `watchdog`, `zeroconf` (imported by server C, never enabled) and their dependencies | PyPI | venv 1.1 GB with CPU torch | `venv/pocket` |
| `chatterbox-tts 0.1.7` (installed `--no-deps`: it pins `torch==2.6.0`, which would replace the ROCm build), `transformers 5.2.0`, `diffusers 0.29.0`, `resemble-perth 1.0.1`, `s3tokenizer 0.3.0`, `librosa 0.11.0`, `safetensors 0.5.3`, `conformer 0.3.2`, `pyloudnorm`, `omegaconf`, `pykakasi 2.3.0`, and devnen's server needs (`fastapi`, `uvicorn`, `soundfile`, `pydub`, `praat-parselmouth 0.4.7`, `scipy`, `pyyaml`, `jinja2`, `python-multipart`, `requests`, `huggingface_hub`) | PyPI | inside the 15 GB above | `venv/chatterbox` |
| `wyoming-openai 0.9.0` (`openai 3.44.0`, `wyoming 1.11.0`) | PyPI | 38 MB | `venv/wyoming-openai` |
| Pocket TTS English weights, tokenizer and the preset voice states | Hugging Face `kyutai/pocket-tts-without-voice-cloning` (ungated; no audio file fetched) | 368 MB | `data/pocket/models` |
| Chatterbox: `ve.safetensors`, `t3_cfg.safetensors`, `s3gen.safetensors`, `tokenizer.json`, `conds.pt` | Hugging Face `ResembleAI/chatterbox` | about 3.2 GB | `hf/` |
| Chatterbox-Turbo: the repository's `*.safetensors`, `*.json`, `*.txt`, `*.pt` (its loader's own pattern) | Hugging Face `ResembleAI/chatterbox-turbo` | about 4.0 GB | `hf/` |

Total fetched: about 15 GB, under the 40 GB cap. No Docker image was pulled
(the baseline uses the local `rhasspy/wyoming-piper:2.2.2`; the compose
file says `pull_policy: never`). Nothing for the RVC layer was fetched.

Python is the system `/usr/bin/python3.13` for every venv (`uv venv
--python /usr/bin/python3.13`), with `UV_CACHE_DIR` inside the trial
directory. Weights were fetched once with the network on, by the
projects' own loaders, with `HOME` and `HF_HOME` inside the trial
directory; every run after that is offline (section 4.6).

## 3. What changed in the repo

Commits on `refactor`: `683b0e2` (section 1, before any install),
`0cc0f7b` (code and docs), and this report. No service code changed; no
container was rebuilt.

- `scripts/tts_bench.py` (new, 1291 lines). Subcommands `run`, `cold`,
  `check`, `llm`, `sample`, `idle`, `relay`, `table`, `blind`. The HA
  event sequences in `synth_once` (`scripts/tts_bench.py:209`); failure
  kinds and the result schema `tts_bench/1` (`:93-97`, `ROW_KEYS`);
  percentiles and per-category summary (`:146`); cold start from
  `/proc/PID/stat` (`:486`); service memory from fdinfo and KFD (`:335`);
  the stand-in check: spelling normalization (`:554`), length, clipping and
  edge sounds (`:592`), port 10300 refused (`:673`); the HA quiet rule
  (`:720`, satellites idle and unchanged for 300 s, read with a GET of
  `/api/states`); bursts capped at 115 s and loopback only (`:96`, `:787`),
  compared on the part after the boost window (`:774`); the sampler's
  idle-spin guard (`:936`) and page-cache drop (`:912`); the loopback relay
  and the in-sandbox bridge (`:1047`, `:1065`); the blind set (`:1144`).
  Framing, transcription and word scoring are reused from
  `scripts/stt_prompt_bench.py` (M13). Run with `tests/.venv/bin/python`.
- `scripts/tts_bench_sentences.json` (new): 20 sentences of my own in six
  kinds (5 confirmations, 5 music with artist, album and station names, 1
  two-sentence weather, 2 three-sentence answers, 1 long answer of about 20 s,
  6 with numbers, dates, times and money).
- `tests/test_tts_bench.py` (new, 31 tests, `no_network`): a fake Wyoming
  service in memory answers what the client writes; timing arithmetic with
  an injected clock, the HA sequences, sentence chunking, the result file's
  schema through `main(["run", ...])`, refused connection, a stream that
  stops, an empty reply, a server error, a timeout, bad format, rests,
  spelling normalization, the audio flags, the dev-Whisper-only rule with a
  stand-in transcriber, the quiet rule, the burst limits, the spin guard,
  the sampler summaries, loopback-only relay and bridge, the blind set.
- `docs/bench/tts_bench_2026-11-18.md` and `.json` (new): the write-up and
  the numbers.
- `docs/incidents/INCIDENT_2026-11-18_m20_memwatch_pushes.md` (new): see
  section 8.
- `ROADMAP.md:43`: a Now entry for the TTS trial.
- `docs/features/voice-pipeline.md:20-21`: the diagram said Piper speaks
  `en_US-lessac-medium`; the pipelines ask for `en_US-hfc_male-medium` (C9),
  so it was wrong and is fixed. `:317`: a short section pointing at the bench.
- `Kronk evaluation findings and implementation plan.md:1019`: the M20 open
  item now says what Drew supplies and where the steps are.

Unit suite: before `5423 passed, 3 skipped, 53 xfailed`; after `5454 passed,
3 skipped, 53 xfailed` (31 new).

## 4. How the trial ran

Order (UTC): Piper cold, run, sentence-chunk run, bursts (18:11-18:17);
Piper-only idle 18:18-18:38; installs and downloads 18:39-18:45; Pocket A
cold, runs, bursts 18:41-18:46, idle hour 18:47-19:47; Pocket C 19:49-19:58;
Chatterbox cold 19:58, spin watch to 20:02, runs in sessions 20:04-20:44,
bursts 20:45; Turbo cold 20:48, spin watch to 20:50, runs 20:51-21:09,
bursts 21:09; dev-Whisper checks 21:12-21:22; stop and the after state 21:20.

4.1 Listeners. Every trial listener was a `tts_bench.py relay` on
127.0.0.1 (`ss -ltnp`: `LISTEN 127.0.0.1:10210 users:(("python",pid=1752604))`,
`LISTEN 127.0.0.1:10211 users:(("python",pid=1776375))`; 10212-10214 per the
relays' own logs, `tts_bench: relay 127.0.0.1:1021x -> unix:...`, and the
relay refuses any non-loopback host by code and test). The engines
themselves listened on Unix sockets or inside their own network namespace
(devnen `127.0.0.1:8004`, MisterEcks `0.0.0.0:10222`, both in a namespace
with nothing but its own loopback). The host's listener list after the
trial is identical to before (section 9).

4.2 Sessions for the GPU candidates. A loaded Chatterbox or Turbo process
keeps the GPU at 100 % (section 6), and memwatch pushes after five
consecutive one-minute polls of that. They were measured with
`~/kronk-tts-trial/tools/session_driver.py` (scratch, not committed): the
bench's own `synth_once`, `MemTrack` and result format, in sessions of at
most 170 s from launch, each followed by 70 s of idle GPU. Chatterbox took
8 sessions (one more failed to start, section 7), Turbo 5. A first-session
warm-up request is not counted. The bursts used
`~/kronk-tts-trial/tools/slowdown_session.sh` (alone, load, warm, with,
unload; 99 s and 89 s loaded).

4.3 Spin guard. `tts_bench.py sample --guard-pid` sent SIGTERM at 140 s of
GPU at 90 % or more with no model-server engine time. It fired twice
(Chatterbox 20:01:5x, Turbo 20:50:44), each time with the GPU back to 0 % /
600 MHz / 9 W within seconds. A shell watchdog killed any Chatterbox server
older than 210 s; it never fired.

4.4 Bursts. Each `llm` burst first ran the HA quiet check (all three
satellites idle and unchanged for five minutes; music on speakers ignored);
all ten passed it. Each burst was 60 s.

4.5 Memory. Before each load MemAvailable was 47-49 GB and GTT 51 of 109 GB;
the largest candidate added 2.7 GB RSS and 4.4 GB GTT, leaving more than
42 GB available for the three llama servers and both Whisper instances,
which kept their memory unchanged.

4.6 Offline (criterion 15). Every candidate and Piper ran with no network
at all, not just offline switches: Piper in `network_mode: none`; Pocket,
Chatterbox, Turbo and `wyoming-openai` under `bwrap --unshare-net`
(`~/kronk-tts-trial/tools/sandbox.sh`), where a connect to a public
address returns `ENETUNREACH` (tested before the trial). The offline
switches were set as well (`HF_HUB_OFFLINE=1`, `TRANSFORMERS_OFFLINE=1`,
`HF_HUB_DISABLE_TELEMETRY=1`, `DO_NOT_TRACK=1`, `BROWSER=/bin/true`,
`DISPLAY` unset: devnen's server opens a browser at start). Evidence that
the sandbox holds: Pocket C's first launch looked in the wrong cache and
failed with `huggingface_hub.errors.OfflineModeIsEnabled: Cannot reach
https://huggingface.co/kyutai/pocket-tts-without-voice-cloning/...` instead
of fetching. No candidate's log shows another outbound attempt. Weights
were fetched once, before, with the network on (section 2).

## 5. Criteria, one line each

1. Check first: section 1.1, written and committed (`683b0e2`) before any
   install: versions, licences, sources, streamed synthesis by code (file:line)
   and then by run (section 6, M8 table).
2. Host state before: section 1.2; the hang symptom restated in 1.3.
3. Bench tool: `scripts/tts_bench.py` `run` (host and port on the command
   line, sentence file of 20 in the six kinds, per-sentence first audio,
   total, audio duration, RTF, failures; p50/p95 over `--reps`; WAVs to
   `--wav-dir`; plays nothing), `cold`, service memory in every result.
4. Tests: `tests/test_tts_bench.py`, 31 tests, no socket (`no_network`);
   suite 5423 → 5454 passed.
5. Baseline first: scratch Piper `en_US-hfc_male-medium`, 100 of 100,
   streaming as HA sends it (`mode: stream`, one chunk).
6. Each candidate: Pocket A, Pocket C, Chatterbox, Turbo, 100 each, loopback
   only (4.1), presets `alba` / built-in `conds.pt`, no reference audio.
7. Model slowdown: alone and with, for Piper and all four (section 6).
8. Idle: Piper-only 19.8 min at 10 s; Pocket one hour at 10 s (covers
   server C, same CPU-only runtime: argued in the bench doc); Chatterbox and
   Turbo spun at once and were unloaded at 140 s, not left an hour
   (departure, section 8).
9. Stability: section 6; no crash, hang, timeout or refused request from any
   engine; the trial's own failed launches listed.
10. Stand-in check: 100 WAVs per engine through the dev Whisper on 10301
    (10300 refused by code), mismatches listed in the bench doc and every
    flagged row in the JSON.
11. Write-up: `docs/bench/tts_bench_2026-11-18.md`: numbers per engine, the
    hard-limit table, latency beside Piper's, M8 streaming, what was not
    stood up, a provisional recommendation that names the failed limits.
12. Listening material: `~/kronk-tts-trial/samples/` (100 WAVs named
    `<sentence>-<letter>.wav`, `rating_sheet.md`, `sentences.txt`,
    `CLONING_RUN.md`), key in `~/kronk-tts-trial/samples_key.json`.
13. Production unchanged: section 9 (container IDs and start times, unit
    PIDs, listeners). No sound: nothing was sent to a speaker, puck or media
    player; no HA call except read-only GETs of `/api/states` and a read of
    HA's source with `docker exec ... sed`. **Not met in one respect:** two
    memwatch pushes reached Drew's phone (section 8).
14. Stopped and back: section 9 (GPU 0 %, 600 MHz, 9.1 W; GTT 50.99 vs
    50.96 GB; MemAvailable 48.97 vs 48.99 GB); what stays: section 10.
15. Downloads: section 2; offline: 4.6.
16. Docs: `ROADMAP.md`, `docs/features/voice-pipeline.md` (a wrong fact
    fixed), the plan's open item; section 3.

## 6. The tables

Full tables and the stand-in rows: `docs/bench/tts_bench_2026-11-18.md`.
The headline:

| | Piper | Pocket A | Pocket C | Chatterbox | Turbo |
| --- | --- | --- | --- | --- | --- |
| Requests / failures | 100 / 0 | 100 / 0 | 100 / 0 | 100 / 0 | 100 / 0 |
| First audio, confirmation p50 / p95 (s) | 0.043 / 0.062 | 0.518 / 0.711 | 0.066 / 0.070 | 3.865 / 5.792 | 3.052 / 3.744 |
| First audio, three sentences p50 / p95 (s) | 0.041 / 0.051 | 1.327 / 2.153 | 0.065 / 0.068 | 7.182 / 12.888 | 6.529 / 8.062 |
| RTF p50 | 0.021 | 0.277 | 0.234 | 1.958 | 1.313 |
| Cold: first audio after start (s) | 1.11 | 3.07 | 2.54 | 27.43 | 12.68 |
| Service memory (RSS / GTT, GB) | 0.40 / 0 | 0.92 / 0 | 1.02 / 0 | 2.35 / 4.4 | 2.73 / 4.0 |
| gemma tok/s alone → with | 85.9 → 60.2 (-29.9 %) | 85.3 → 81.9 (-4.0 %) | 85.4 → 87.1 (+2.0 %) | 85.3 → 63.7 (-25.3 %) | 85.3 → 68.0 (-20.3 %) |
| Idle GPU busy / clock / power | 0 % / 600 MHz / 9.19 W (19.8 min) | 0 % / 600 / 9.35 W (59.8 min) | (same runtime) | 100 % / 2900 / 37.3 W | 100 % / 2900 / 37.5 W |
| Stand-in flags beyond Piper (words; length) | control | 9; 1 | 8; 2 | 13; 0 | 15; 0 |

Stability, every request sent to each engine, and failures: Piper 670
(100 + 12 chunked + 554 in the burst + 4 in two cold measurements), Pocket A
146 (100 + 12 + 32 + 2), Pocket C 147 (100 + 12 + 33 + 2), Chatterbox 137
(2 cold + 14 in the driver's lost first attempt + 7 session warm-ups + 100 +
5 warm + 9 in the burst), Turbo 123 (2 cold + 5 warm-ups + 100 + 5 warm + 11
in the burst). Failures: 0 for every engine (no `FAIL` in either driver log).

## 7. What could not be stood up, and the trial's own failures

- RVC layer: not run, by the brief (its built-in timbres are trained on
  recordings of people). Details in the bench doc.
- `araa47/wyoming_pocket_tts`: read, not run (a third server for the same
  engine; bounded effort).
- The trial's own failed starts, none an engine fault: Pocket C's first
  launch exited because `huggingface_hub` read `HF_HOME` before the server
  set it, so it looked in the empty cache and the sandbox denied the Hub
  (relaunched with `HF_HOME` set); the Chatterbox driver's first attempt
  lost its 13 rows (written only after the idle wait, which waited for a
  memwatch journal line that memwatch writes only while MemFree is under
  12 GB) and its rerun's session 1 refused to reuse that attempt's socket
  directory (`mkdir` fails, by design: no `rm`); the driver's relative WAV
  paths were made absolute in the two result files, and `run` now resolves
  `--wav-dir` (`scripts/tts_bench.py:461`, with a test).
- SIGTERM to the Chatterbox sandbox left two Python children running every
  time (`stray [...]` in the driver log); the driver killed them.

## 8. Departures from the criteria, and the incident

- **Two notifications reached Drew's phone**, against the brief:
  20:49:32 UTC "Kronk: CRITICAL free RAM" and 20:53:33 UTC "Kronk: GPU
  idle-spin (ROCm wedge?)", from memwatch, caused by this trial. The first:
  Turbo's load took MemFree to 1.47 GB (MemAvailable 42.8 GB, PSI near 0)
  after my page-cache guard had expired. The second: I started the Turbo
  driver seconds after unloading the spinning cold-start process, so no
  memwatch poll saw an idle GPU in between and five consecutive polls
  counted across the two loads. Not retried, no follow-up push.
  `docs/incidents/INCIDENT_2026-11-18_m20_memwatch_pushes.md`; the scratch
  driver now waits for an idle poll before its first session too.
- Idle hour (criterion 8) for Chatterbox and Turbo: 2.6 and 2.3 minutes, not
  an hour. Both spin at 100 % the moment they are idle; an hour would have
  meant memwatch pushes and the ROCm spin that preceded every silent hang on
  record. The limit fails either way.
- Chatterbox and Turbo were measured in sessions of at most 170 s (4.2), not
  one continuous run, with the first request after each load treated as a
  warm-up; the rests the criteria allow (150 s / 75 s) do not help when the
  process spins between requests.
- Pocket C's idle hour is covered by Pocket A's (same runtime, no GPU
  handle), as criterion 8 allows when argued.
- devnen's server got a two-line scratch patch to use the built-in voice
  (`~/kronk-tts-trial/state/devnen-builtin-voice.patch`); as shipped it
  always conditions on a reference WAV. Generation defaults set to the
  model's own (exaggeration 0.5, cfg 0.5, temperature 0.8) instead of the
  server's sample config (exaggeration 1.3).
- Sentence-chunk (M8) runs were done for Piper and Pocket only; for
  Chatterbox and Turbo the answer is by code.
- The slowdown "alone" figure for each engine is taken with the engine not
  loaded (Piper: loaded and idle); comparisons use the requests after 15 s.

## 9. State before and after

| | Before (18:01 UTC) | After (21:20 UTC) |
| --- | --- | --- |
| `wyoming-piper` | `5b97cc70b9ce`, started 2026-11-16T01:05:02.386Z | same ID, same start |
| `homeassistant` | `98f56e2549d0`, 2026-11-18T00:14:34.556Z | same |
| `music-assistant-server` | `cf6b980f6630`, 2026-11-04T13:45:22.470Z | same |
| `kronk-litellm-1`, `kronk-nginx-1`, `kronk-voice_tracer-1` | `48da9ed1e321` 17:51:19, `80588ee1b80c` 17:51:31, `68307c8ccdff` 17:51:19 | same |
| llama units (gemma4-e4b, gemma3-4b, devstral) | PIDs 2828, 2827, 2826, `NRestarts=0`, `/health` ok | same |
| Whisper units (prod, dev) | PIDs 2809, 1202239, `NRestarts=0` | same |
| GPU | 0 %, 600 MHz, 9.1 W | 0 %, 600 MHz, 9.1 W |
| GTT | 50.96 / 109.05 GB | 50.99 / 109.05 GB |
| Memory | MemFree 9.71, MemAvailable 48.99 GB | MemFree 8.65, MemAvailable 48.97 GB |
| Listeners | (list in `~/kronk-tts-trial/state/before.txt`) | identical list, no 102xx port |
| Trial containers and processes | none | none (`kronk-tts-trial` project down, no volume or network left) |
| Disk free on `/` | 851 GB | 827 GB |

The production stack (orchestrator, tool_service, nginx, LiteLLM) was
recreated at 17:51 UTC, before this build started, by the M18 promotion.

## 10. What stays on disk

Everything under `~/kronk-tts-trial/` (24 GB): `venv/` 16 GB (chatterbox
15 GB, pocket 1.1 GB, wyoming-openai 38 MB), `cache/uv` 16 GB (hard-linked
with the venvs; about 16 GB on disk together), `hf/` 6.8 GB (Chatterbox and
Turbo weights), `data/` 368 MB (Pocket weights, devnen configs),
`home/` 276 MB (MIOpen kernel cache), `wav/` 102 MB, `src/` 70 MB,
`samples/` 21 MB, logs, runs, idle samples and state under 5 MB. Kept for
the cloning run and the listening test. Removal, not run:

    rm -rf /home/drew/kronk-tts-trial

No symlink there points into the repo's data or at a secret (the venvs'
`python` links point at `/usr/bin/python3.13`). Nothing else was created
outside the repo.

## 11. What needs Drew's decision

- The voice clips (consenting speaker, 10 to 30 s mono WAV), and whether
  Pocket TTS's gated cloning weights may be fetched with his Hugging Face
  account.
- Whether Chatterbox stays a no-go until the ROCm idle-spin is fixed, or is
  tried on CPU (not measured here).
- Whether Pocket TTS via the MisterEcks server (one maintainer, last commit
  March 2026, MIT) is acceptable as a dependency, or whether a server is
  written or forked for Kronk.
- The hard limit on model slowdown: Piper itself costs gemma 30 % under
  continuous synthesis; keep the limit as written, or measure against Piper.
- Whether the two pushes in section 8 call for anything beyond the
  incident doc.
