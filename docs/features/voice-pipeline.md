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
    → Wyoming faster-whisper STT (host systemd, GPU, large-v3-turbo, :10300;
      the kronk-dev pipeline uses a second instance with a prompt, :10301)
    → local intent matcher (prefer_local_intents ON — timers, MA blueprint)
    → fallback: Ollama shim  → Kronk pipeline (orchestrator /api/chat via nginx)
    → Wyoming Piper TTS (container, :10200; the pipelines ask for
      en_US-hfc_male-medium; lessac-medium is only the container's default)
  → audio back to the Voice PE
```

**What is kept** (sprint M12, `voice-capture.md`): with
`TELEMETRY_RECORD_TEXT=true`, each run's transcript and Kronk's reply are in
its trace for 90 days, readable in Grafana without a login. For two weeks
from the M12 install, every puck's speech audio is recorded to
`/home/drew/kronk-voice-recordings` (household and guests alike); a daily
reminder (`kronk-voice-capture-check.timer`) asks Drew to turn it off from
the end date on. `voice_tracer` marks a run the same puck corrected within
60 s ("not that", thumbs down, stop, the same request again). Turning each
part off: `voice-capture.md`, "Turning each part off".

Key design decisions (full rationale in the build journal):

- **HA is a device broker, nothing more.** Kronk stays the brain; HA talks to
  it through an Ollama-compatible shim (`orchestrator/main.py:/api/chat`)
  because HA 2026.5 shipped without an OpenAI Conversation card. Both shims
  share one transport-agnostic core generator.
- **STT on the host, not a container** — GPU passthrough is just `/dev/kfd` +
  group membership. Built CTranslate2 from source with `WITH_HIP=ON` for
  gfx1151, inside `rocm/dev-ubuntu-24.04:7.2-complete`
  (`~/services/wyoming-whisper/build/build-in-container.sh`; Talkie's
  llama-server was built in the same image).
- **The Whisper units' libraries** (`LD_LIBRARY_PATH`, both units, same
  order): `~/services/wyoming-whisper/runtime-libs` first (CTranslate2 itself,
  OpenBLAS, and hiprand, rocrand and libomp, which are the image's builds
  copied by hand), then `~/services/rocm-7.2/lib`: ten ROCm libraries and
  rocBLAS's kernel data, taken from that same image by
  `scripts/build_rocm_libs.sh`, with a `MANIFEST` beside `lib/` (work item
  M26; before it, the units loaded the Ollama install's rebuild of the same
  versions). Check it with `scripts/build_rocm_libs.sh verify
  ~/services/rocm-7.2`. `hipblaslt/library` is left out on purpose, as it was
  before M26; libdrm, libelf and libnuma are the host's. The image is local
  only and the script never pulls: if it is pruned, a rebuild needs a pull by
  the digest pinned in the script. A new ROCm release means rebuilding
  CTranslate2 in the new image and editing the script's digest and list
  together. README.md, "ROCm runtime libraries", has the rebuild recipe.
- **HA in its own compose project** (`docker-compose.ha.yml`, `kronk-ha`) so
  orchestrator rebuilds never cycle HA. Volume `kronk_ha-config` holds all
  integrations — never `down -v`.
- **Ambient context** (`kronk_facts()` in `agents.py`) injects LOCATION etc.
  into every agent path — fixed the "what location?" reflex on bare weather
  queries.

## The "ask Kronk to" lead-in (sprint M19)

**Say it plainly for the fast path; start with "ask Kronk to" and the model
handles it.** Plainly means one short sentence: a second sentence (trailing
speech the satellite kept listening to) or more than 20 words sends the
request to the coordinator whole, by the messy-transcript gate (sprint M14,
[`messy-transcript-gate.md`](messy-transcript-gate.md)).

HA takes no lead-in sentence locally (checked read-only on all three
satellites, 2026-11-17), so "Okay Nabu, ask Kronk to find some deep
house music and play it" reaches Kronk, where the first routing rule cuts
off "ask Kronk to" and hands the rest to the coordinator, past every
shortcut and direct dispatch. Slower by design: one or two model hops,
about 3–7 s on dev. "Kronk" is in the Whisper vocabulary prompt (plan M19
step 4), on the dev path only (sprint M13, below). Details:
[`lead-in.md`](lead-in.md).

## The dev Whisper instance and the vocabulary prompt (sprint M13)

**What.** HA's `kronk-dev` pipeline (Hey Jarvis) transcribes with a second
Whisper instance, `wyoming-whisper-dev.service`, on `127.0.0.1:10301`: the
same `large-v3-turbo` model and `float16` compute type as production, plus
`--initial-prompt` with a line of household names (rooms, Pandora
stations, top artists, Music Assistant players, and "Kronk"). The prompt
biases Whisper toward those spellings. Production's `wyoming-whisper`
(`:10300`, the `kronk` pipeline, Okay Nabu) has no prompt and stays that
way until the M12 clips show the prompt helps (Drew's decision,
2026-11-11). Build report: `docs/sprint/M13-build.md`.

**How.**

- HA: Wyoming entry "Kronk Whisper STT dev" (`127.0.0.1:10301`), entity
  `stt.faster_whisper_2`; `kronk-dev`'s `stt_engine` points at it. The
  `kronk` pipeline keeps `stt.faster_whisper`.
- The prompt: `scripts/build_stt_prompt.py` reads HA's template API and
  `tool_service-dev` (`GET /music/stations?provider=pandora`,
  `GET /music/taste/profile`), all read-only, and writes
  `~/services/wyoming-whisper/dev-prompt.env`
  (`WHISPER_INITIAL_PROMPT="..."`), which the unit loads as an
  `EnvironmentFile` and passes as one argument (`${WHISPER_INITIAL_PROMPT}`).
  Names only: no command verbs, so noise is not pulled toward "pause". Budget
  100 Whisper tokens, counted with the model's own tokenizer: Kronk and every
  room first, then stations, artists and players take turns by rank. The
  rule is in the script's docstring.
- Memory: about 2.1 GB GTT and 0.7 GB RSS, loaded on the first
  transcription after a start.

**Regenerate.** Nightly, `kronk-stt-prompt.timer` (04:00, after the 03:30
taste rebuild) runs the script with `--restart-unit
wyoming-whisper-dev.service`; the unit restarts only when the file
changed. By hand:

```bash
~/services/wyoming-whisper/.venv/bin/python scripts/build_stt_prompt.py --print   # preview, writes nothing
~/services/wyoming-whisper/.venv/bin/python scripts/build_stt_prompt.py \
    --restart-unit wyoming-whisper-dev.service                                   # write; restart if changed
```

A failed or empty source exits 1 and leaves the old file. The script
refuses to restart any unit not named `*-dev.service`.

**Stop it.** `systemctl --user disable --now wyoming-whisper-dev.service`
frees its memory; Hey Jarvis then fails at the STT stage. To keep Hey Jarvis
working without it, point `kronk-dev` back at production's Whisper first:
websocket `assist_pipeline/pipeline/update` on `kronk-dev` with
`stt_engine: "stt.faster_whisper"` (every other field unchanged).

**Measure it.** `scripts/stt_prompt_bench.py` (run with
`tests/.venv/bin/python`): `render` makes a synthetic set through Piper's
Wyoming port (files only, no speaker), `run` sends a sheet's audio to one
STT port, `compare` makes the tables. It reads the M12 labelling sheet
(`voice-capture.md`), so the real decision runs the same tool on labelled
household clips: procedure in `docs/sprint/M13-build.md`, "Deciding
production".

**Moving the prompt to production, when Drew decides.** One change, then a
voice check:

1. Deploy a tag that contains `GET /music/stations`, so production
   `tool_service` serves it.
2. Write a production prompt file with `--tool-service
   http://127.0.0.1:8003 --out ~/services/wyoming-whisper/prompt.env`.
3. Add `EnvironmentFile=` for that file and `--initial-prompt
   ${WHISPER_INITIAL_PROMPT}` to `systemd/wyoming-whisper.service`, copy it,
   `daemon-reload`, restart `wyoming-whisper` (Okay Nabu is down for a few
   seconds while the model reloads at start).
4. The nightly rebuild cannot restart production's unit (the script
   refuses it); widening that guard is part of the same decision.
5. Then retire the dev instance or keep it for the next experiment.

**Found on synthetic speech (M13, `docs/sprint/M13-e2e.md` D1 to D5); to
check on real clips before the prompt goes to production.** The
comma-separated prompt makes Whisper join sentences with commas, so the
M14 messy gate, which counts sentences, caught 16 of 33 multi-sentence
captures instead of 24. A player name in the prompt replaced a command
verb twice in 714 clips ("Shuffle …" heard as "SHIELD, …"). "Kronk" came
back on 2 of 40 non-speech clips. A name made only of command words
("Stop", "Next") is left out of the prompt and listed in the nightly
service's log line (`command_only_names_dropped`).

**Gotchas.**

- A prompt biases every transcription, silence and noise included. On
  synthetic non-speech the bench counts how often each instance returns
  text and whether it returns a prompt name (build report).
- Whisper without VAD returns text on silence: production answered all 40
  generated non-speech clips with words ("Thank you.", "You", "Bye.").
  Not changed by M13.
- Both units log each transcript at INFO in the user journal.
- The installed `wyoming-faster-whisper` 3.1.0 ignores `--local-files-only`
  for faster-whisper models; both units set `HF_HUB_OFFLINE=1` (production
  since work item M30) so a model load never asks the Hugging Face Hub and
  loads from the cache only. A by-hand faster-whisper upgrade that renames
  the model's repository makes that cache directory miss, and the start fails
  (the comment in `systemd/wyoming-whisper.service`).

## The voice signal (sprint M2)

`/voice/api/chat`, `/api/chat` and `/v1/chat/completions` all run the
pipeline as `transport="shim"`, so the transport cannot tell a spoken
request from a typed one. The mount can. `origin.voice`
(`orchestrator/origin.py`) is a request-scoped `ContextVar[bool]`:

- **Set once, by the mount.** `ollama_chat_voice` (`/voice/api/chat`) is the
  only caller that passes `voice=True`; every other entrypoint passes
  `False`. `_run_pipeline` holds it in `origin.voice` for the whole run,
  beside `origin.said`, and resets it in its `finally`. No field, header or
  message text can set it.
- **One source of truth.** The speech render profile (`render.to_speech`)
  in `_ollama_chat_impl` and the pipeline's value come from the same
  `voice` argument, so they cannot disagree.
- **Readable anywhere below**, without a parameter at every hop:
  `origin.voice.get()`. asyncio copies the context per task, so concurrent
  requests keep their own value (`tests/test_voice_signal.py`).
- **On the trace.** The pipeline root span carries `kronk.voice`
  (true/false). TraceQL: `{ span.kronk.voice = true }`; failed spoken
  requests: `{ span.kronk.voice = true } && { status = error }`.

What reads it today: the error style. On the speech mount failures are
spoken as one short plain sentence (`ERROR_STYLE_VOICE`, default
`friendly`); the other mounts keep debug wording (`ERROR_STYLE`, default
`debug`). Set `ERROR_STYLE_VOICE=debug` to hear full detail on a speaker.
The sentences and their debug twins are in `docs/features/verbose-errors.md`.
Every code-written failure leaves through `errors.failure_text`. A tool
result counts as failed only when the code that produced it says so
(`tools.ToolResult.failed`); model-written answers are spoken as written
(M2 fix 2; same doc).
M8's spoken-reply line keys on the same value (`_run_pipeline`'s
`voice`), and only the mount's handler chooses the streamed reply, so no
second flag exists. The M14 gate does not read it: it
applies on every mount (`messy-transcript-gate.md`).

## Sentence streaming and the spoken-reply line (sprint M8)

Two independent changes on the speech mount. Build report:
`docs/sprint/M8-build.md`.

**The spoken-reply line.** On a `/voice/api/chat` request the
coordinator's system text ends with one more block, `spoken_reply` in
`config/voice.yaml`: the reply will be read aloud, so one to three short
plain sentences, no lists or markdown. It is appended after every other
block (`_run_pipeline`, `coordinator_extra`), so the system text before
it, and llama.cpp's cached prompt prefix, is the same as a typed
request's. Routed and delegated specialists do not get it. It applies
whatever `VOICE_STREAM` says. Measured on dev, 10 long-answer questions ×
5 runs: mean reply 753 → 322 characters, 9.3 → 3.0 sentences, coordinator
generation 2.92 → 1.97 s. `spoken_reply: null` and a config reload turn
it off.

**Sentence streaming (`VOICE_STREAM`).** With the env switch on, a
streaming `/voice/api/chat` request gets its reply as it is generated
(`main._ollama_speech_stream`, `orchestrator/speech_stream.py`):

- **Units.** `speech_stream.split_units` is the one place the reply is
  cut: one sentence of a prose line, one whole line of any other kind
  (heading, quote, rule), or one whole list, table or code fence. Each
  unit goes through `render.to_speech` on its own and leaves as one NDJSON
  content chunk; after the first, a chunk starts with the whitespace the
  reply had there (`" "`, `"\n"`, `"\n\n"`), so HA's concatenation keeps
  sentences apart. The cut rules exist so that `to_speech` renders each
  unit exactly as inside the whole reply: sentence ends are
  `transcript_gate.ends_sentence` (abbreviations, initials, `a.m.`,
  `No. 5`, ellipses, decimals and URLs do not end a sentence), never after
  a closing quote, never inside emphasis, inline code or a link, never
  mid-line in a heading, quote, table row or list item, and a cut is made
  only once the next unit has started, on information later text cannot
  overturn. Joined, the chunks are the buffered path's text, whitespace
  between chunks aside (one listed exception, a space before a full stop
  `to_speech` adds on a non-table line that holds a `|`).
- **The hold.** The last `stream.hold_sentences` complete units
  (`config/voice.yaml`, default 1) are held: unit N leaves only when unit
  N + hold completes, or the stream ends, when everything held leaves as
  one chunk.
- **Retracts.** Text a model writes in a round that ends in a tool call is
  retracted (`TOOL_ROUND_RETRACT_PLAN`). While it is held it is dropped,
  as the buffered path drops it. A retract longer than the held text drops
  everything held; what was already sent stays spoken, and the event is
  logged (`voice_stream_retract_unhonoured`; its `preview` field is empty today, the text is in the same request's `round_content_retracted` event) and counted
  on the trace.
- **Blocks.** Nothing inside a list, table or fence leaves until the block
  ends (the first line of another kind). An unclosed block leaves at the
  end of the stream.
- **Short replies keep their shape.** A one-sentence answer, a direct
  dispatch, a terminal-tool result or a friendly failure leaves as one
  content chunk with today's text and the same `done` record.
- **Failure.** Decided by the pipeline's flag, as before: a turn that
  fails mid-reply ends with its friendly sentence as the last content and
  one `done` record. If the buffer itself raises (a bug), the rest of the
  reply is collected as the buffered path collects it and leaves as one
  last chunk; the pipeline always runs to its end and releases its model
  lock (since sprint M9 a per-server lock held for one generation).
- **Deadline and disconnects (sprint M9).** A speech-mount request stops at
  `config/voice.yaml` `deadline_s` (25 s) with one spoken sentence; on the
  streamed path what was sent stays and held text is dropped. A client that
  disconnects cancels its request on every route
  (`docs/features/model-locks.md`).
- **Unchanged:** `stream: false` on the voice mount (one JSON object),
  the display mounts (`/api/chat`, `/v1/chat/completions`, `/message`),
  and the whole buffered path when the switch is off.
- **Telemetry.** The pipeline root carries `kronk.voice_stream=true`; a
  `voice_stream` span under it carries `kronk.chunks`,
  `kronk.first_chunk_ms`, `kronk.done_ms`, `kronk.hold`, `kronk.retracts`,
  `kronk.retract_held_chars`, `kronk.retract_unhonoured`,
  `kronk.retract_unhonoured_chars` and `kronk.spoken_chars`; the
  `voice_stream` event carries the same. Buffered voice requests carry
  neither. TraceQL: streamed `{ span.kronk.voice_stream = true }`;
  leaked tool-round text `{ name = "voice_stream" &&
  span.kronk.retract_unhonoured > 0 }`.

**What HA does with it** (2026.5.4, read from its source): the Ollama
client sends `stream=True` and has no request timeout (ollama 0.5.1,
`timeout=None`; the 5 s `DEFAULT_TIMEOUT` covers only setup's `list()`).
The pipeline starts streamed TTS (`intent-progress` with
`tts_start_streaming`) once the reply passes 60 characters
(`STREAM_RESPONSE_CHARS`), and only when the run has a TTS stage. ESPHome
forwards that flag to the satellite, which starts fetching the TTS URL it
got at `run-start` (`stream_response: true`). `wyoming-piper` 2.2.2
streams by sentence by default. With one buffered chunk the flag fires at
the same moment as `intent-end`; streamed, it fires at the first chunk.

**The switch and rollback.** `VOICE_STREAM` (`on`/`off`, default off in
code; an unknown value is off and logged) is set per service in
`docker-compose.yml`: `on` for `orchestrator-dev`, `off` for production.
Production stays off (decision at M8's promotion, 2026-11-17): with a
one-sentence hold the coordinator's text before a tool call was spoken in
5 of 5 cases where it reached the stream, and with the spoken-reply line
the gain is about a quarter of a second (`docs/sprint/M8-e2e.md`, S1).

Production runs from a release directory (`../kronk-releases/<tag>/`) with
its own copy of `config/` and of `docker-compose.yml`. A change made in the
working tree does not reach it, and a bare `docker compose up -d
orchestrator` run from the working tree would recreate production from the
working tree. So:

- The switch: change the value in `docker-compose.yml`, commit, tag, and
  `scripts/deploy_tag.sh deploy <tag>`.
- The spoken-reply line and the hold: change `config/voice.yaml`, commit,
  tag and deploy the same way. For an immediate change without a deploy,
  edit `../kronk-releases/<running tag>/config/voice.yaml` and run
  `scripts/config_reload.sh prod`; the next deploy replaces that edit, so
  commit it too. Removing the line (`spoken_reply: null`) brings back long
  answers and, with them, markdown markers that `to_speech` does not strip
  from star bullets with bold labels.

**Measuring it:** `scripts/voice_stream_bench.py` (dev only): shim mode
times send, first content chunk and `done` on `/dev/voice/api/chat`; `ha`
mode runs HA's `kronk-dev` pipeline from intent to TTS with no satellite
and records when `tts_start_streaming` fires. Results in `docs/bench/`.

## Choosing a different TTS engine (sprint M20)

`scripts/tts_bench.py` speaks Wyoming to any TTS service on a loopback port
the way HA does, writes WAVs and plays nothing; the first trial (Piper
against Pocket TTS, Chatterbox and Chatterbox-Turbo, preset voices only) is
`docs/bench/tts_bench_2026-11-18.md`. Provisional result: Chatterbox and
Turbo are a no-go on this host (3 to 7 s to first audio, and their ROCm
PyTorch process holds the GPU at 100 % whenever it is loaded); Pocket TTS
behind the MisterEcks Wyoming server passes every limit measured so far.
The listening test and the cloning run wait for Drew's clips. Production
still speaks through Piper; nothing in HA changed.

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
- **Tool-round text longer than the hold is spoken when streaming.**
  On dev (2026-11-16/17) 31 of 33 voice tool rounds that wrote text wrote
  more than 80 characters (home agent median 447, max 5271): leaked
  deliberation that a one-sentence hold only partly catches. Watch
  `kronk.retract_unhonoured` before widening the hold or turning
  streaming on in production (`docs/sprint/M8-build.md`).
- `tts.speak` silently no-ops for announcements; `assist_satellite.announce`
  is the correct service (matters for timers, ROADMAP item 3).

## Testing utterances without speaking (2026-07-03 method)

The regression corpus replays every recorded utterance this way, silently:
`scripts/voice_replay.py` (`docs/features/voice-regression-corpus.md`). For a
single utterance, from the shell:

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
  local agent; a `pipeline.shim` trace appearing in Grafana (Tempo) = it fell
  through to Kronk.

## Blog hooks

- A hundred tokens of names: biasing Whisper toward a household's
  stations and rooms, and why command verbs stay out of the prompt.
- "Thank you." on silence: what a speech model says when nobody speaks,
  with and without a prompt.

- Building a fully-local voice assistant on one AMD Strix Halo box —
  the CTranslate2-on-gfx1151 build saga.
- The Whisper model-size ladder: hallucinations vs. empty transcriptions.
- The two-assistant-slot Voice PE trap.
- Shim archaeology: speaking Ollama's API because the OpenAI card vanished.
- One line in the system prompt cut spoken answers from nine sentences to
  three and a second off generation; sentence streaming then bought about
  0.3 s more. Measure the cheap fix first.
- Cutting a reply so a markdown scrubber renders the parts as the whole:
  a pure splitter, a property test over thousands of generated replies,
  and "never make a cut later text could overturn".
- You cannot unsay a sentence: why a streamed voice reply needs a hold,
  and what tool-round leaks say about how wide it must be.
- The mount is the signal: three shims share one transport, so "will this
  be spoken?" has to come from the URL the client chose, held in one
  ContextVar, never from anything the client sends.
