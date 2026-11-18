# M20 build report: TTS trial with a go/no-go (the part that needs no clips)

Status: in progress

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
