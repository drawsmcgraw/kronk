# M8 end to end: sentence-level streaming on the voice mount

**M8 PASSES.**

End-to-end test agent, 2026-11-17, 14:09 to 15:55 UTC, branch `refactor` at
`05bbcc6` (build `a7f4112..05bbcc6`). Tested against
`docs/sprint/M8-criteria.md`; `docs/sprint/M8-build.md` was read as claims.
Nothing was fixed. The six coordinator rulings on the builder's departures
were applied as given; each is confirmed or contradicted where it is used.

In short:

- Every criterion passes. The buffered path and the other routes are byte
  for byte what `a7f4112` sends (488 responses per tree, 0 differences).
- The spoken-reply line does not cost tool calls: the expected tool was
  missed in 52 of 310 runs with the line and 60 of 310 without it, on rows
  the replay already reports as expected failures or on the two known
  "YouTube Music" flappers; the deep-house row called its tool in 20 of 20
  runs each way.
- The line costs depth, not usually the thing asked for: answers that need
  length came back at about a third of the size (mean 354 against 991
  characters), 2 of 27 lost what was asked.
- Nothing blocks promotion with the switch off.
- With the switch on, every retract that reached the stream leaked: 5 of 5
  spoke between 41 and 556 characters of the coordinator's deliberation
  before the answer. That is the evidence for keeping production's
  `VOICE_STREAM` off; the decision is the coordinator's and Drew's.
- The builder's promotion note about `config/` is wrong: production mounts
  its release's own copy, not a shared one. What that means is under
  "Telemetry, docs and the production mount".

## Criteria

| # | Criterion | Result | Evidence |
|---|---|---|---|
| 1 | Check first, reported before the design | pass | The build report states all four findings. Rechecked here: `tts_start_streaming` fires on `kronk-dev` for replies over 60 characters (16 runs below); retracts recounted from Tempo (52 voice requests on production, none with a tool round that wrote text; dev figures below). |
| 2 | Several chunks, whole units, each through `to_speech`; first chunk before the model finishes | pass | Offline through the app: 122 streamed replies in 1, 2, 3 or 5 chunks (42, 38, 34, 8). Every chunk of 6,846 buffer runs satisfies `chunk.strip() == to_speech(chunk.strip())`. On dev: 40 of 50 long answers left in 2 or more chunks, first chunk a median 0.27 s before `done`. |
| 3 | Same spoken text as the buffered path | pass | 100 inputs of mine, fed character by character, word by word, whole, and in 20 random token splits, at hold 1, 2 and 3: 6,846 runs, 0 differences from `to_speech` of the whole reply after whitespace is normalised. Ruling 6 holds: I found no class of difference at all. |
| 4 | One unit held; a retract inside it removes it | pass | Through the app: a coordinator preamble before `ask_home`, a home preamble before `play_music`, two tool rounds in one request: streamed text equals buffered text. `SpeechBuffer` cases below. |
| 5 | A retract larger than the held text is defined, pinned, logged and counted | pass | Defined and stable (table "Retracts" below). Live: 5 events, each logged as `voice_stream_retract_unhonoured` and counted on the `voice_stream` span. The event's `preview` field is always empty on a real request (finding S2). |
| 6 | Blocks are units; an unclosed block leaves at the end | pass | Tables, nested lists, fences, an unclosed fence, a reply that is one unclosed fence, a table followed at once by prose: text equal to buffered in every split. A 5,315-character leaked list on dev was held whole (item 4). |
| 7 | No cut inside abbreviations, decimals, initials, URLs, times; one pure splitter; boundary rule shared | pass | "U.S.", "a.m.", "3.14", "v2.0", "e.g.", "St.", a URL with a full stop: no cut inside any. `split_units` is pure; `transcript_gate.ends_sentence` is the shared rule. |
| 8 | Short replies unchanged in shape | pass | One sentence, direct dispatch (ok, 409, tool_service down), terminal tool (ok, 409, down), friendly failures: one content chunk and the same `done` record with the switch on and off. Ruling 3 (a code-written reply of three or more sentences is chunked) seen live: the news brief left in 2 chunks. |
| 9 | NDJSON valid for HA's client; `stream: false` is one object | pass | 720 streamed requests of mine on dev: every line one JSON object, 0 bad lines, HTTP 200 on all. Offline: exactly one `done: true`, same fields and values as the buffered record. `stream: false` on the voice mount is byte-identical to `a7f4112`. Through HA: 72 replay rows and 16 bench runs on `kronk-dev`, no error event, no Ollama or pipeline line in HA's log. |
| 10 | Failure in mid-stream | pass | Model failure after three sentences, after a tool round, and at once; a specialist failing after writing text: the stream ends with the friendly sentence and one `done`, nothing repeated, text equal to the buffered path's. |
| 11 | What is stored is what was said | pass | Streamed trace `67f6b81f633d13563be1bb9c6b396c66`: `pipeline.shim` `kronk.output_chars` 329, `voice_stream` `kronk.spoken_chars` 329. On a leak the trace keeps the final reply only (`696794cb83f9e046ee81e4dc09ca6551`: output 118 characters, spoken 359). |
| 12 | `VOICE_STREAM` selects the path; off is today's behaviour byte for byte | pass | `a7f4112` against HEAD with the switch unset and `off`: 488 responses each, 0 differences. Code default off, dev compose `on`, production compose `off`. `tests/test_render.py` and `tests/test_retract.py` are unchanged in the build diff. |
| 13 | Display profile and other routes unchanged | pass | `/api/chat`, `/v1/chat/completions` (streaming and not) and `/message`: `a7f4112` against HEAD with the switch on, off and unset, 0 differences. |
| 14 | The line ends the coordinator's system text on voice requests only; wording in `config/` | pass | 165 coordinator calls on the voice mount: HEAD's system text is `a7f4112`'s text plus `"\n\n" + line`, nothing else changed. 411 other model calls: identical messages. `spoken_reply: null` removes it (576 of 576 calls identical to `a7f4112`). Reload and rejection on dev below. |
| 15 | Coordinator only; tool choice unchanged | pass | No specialist call carries the line (routed or delegated). Tool choice: item 3 below, and the replay. |
| 16 | Ten long-answer questions, five runs, before and after the line | pass | The builder's table is in the build report (753 to 322 characters). Rerun on the final code, line on: 333 characters mean with the switch off, 302 with it on, 3 sentences median in both. The "before" side was not rerun on those ten questions; my ten other requests, line removed against line on, show the same direction (991 to 354). |
| 17 | Time to first chunk, off against on; and through HA | pass | Tables below. Ruling 5: my own off-against-on runs agree in scale with the builder's (lead 0.24 s median against 0.29 s). Through HA with the switch on, `tts_start_streaming` led `intent-end` in 7 of 8 runs. |
| 18 | If HA does not start speech earlier, say so | pass | It does, by the events. The rule does not trigger. |
| 19 | Latency stated up front | pass | In the build report; my numbers below do not contradict it. |
| 20 | `./scripts/run_tests.sh` passes; new tests do not reach the network | pass | `4435 passed, 3 skipped, 53 xfailed`. None of the M8 test files makes a network attempt. Older tests do (observation O3). |
| 21 | Replay matches the M10 baseline with the switch on | pass | One row differs, a known flapper, in the good direction. |
| 22 | Dev only, no sound, no change to HA, pucks, Piper, MA, production | pass | No satellite and no speaker changed state in HA's history for the whole run. One television went from `off` to `unavailable` on its own (O11). Production containers still have their 13:17 UTC creation times. |
| 23 | Telemetry: streamed or not, chunks, time to first chunk, unhonoured retracts | pass | Trace `67f6b81f…`: `kronk.voice_stream: true` on the root; `voice_stream` span with `kronk.chunks 2`, `first_chunk_ms 4042`, `done_ms 4310`, `hold 1`, `retracts 0`, `retract_unhonoured 0`. Buffered trace `c7a84dee83e14e966d101a03a5be6700` has neither (ruling 4). The values of `first_chunk_ms` and `done_ms` are not pinned by any test (O9). |
| 24 | Docs | pass | `voice-pipeline.md`, `render-profiles.md` and `config-directory.md` describe the streamed path, hold, block rule, switch and rollback. Two statements in them are inaccurate (S2, O2). |

## Findings

### Blocks promotion with the switch off

None.

### Blocks turning the switch on

**S1. With hold 1, tool-round text is spoken whenever it reaches the
stream.** In 720 requests on the dev speech mount with the switch on, a
retract reached the stream 5 times and all 5 had already sent text: 41,
240, 307, 513 and 556 characters. All five are coordinator rounds on the
default route. One happened with the line on (1 in 360), four with the line
removed (4 in 360).

Exact input and result, line on:
`eval-i-shuffled-my-youtube-playlist-old-man`, "I shuffled my YouTube
playlist, Old Man Video Games.", run 2, trace
`696794cb83f9e046ee81e4dc09ca6551`. The coordinator wrote 369 characters,
then called `ask_home`. Sent, in order:

1. at 3.32 s: `... are commands for the music on the speaker that asked — call ask_home at once with the command; never ask what to stop, and never claim you stopped anything.".`
2. at 3.52 s: `The command 'shuffle' isn't explicitly listed, but it is a playback control.`
3. at 5.88 s: `Dry run: would play the playlist Old man video games on the Office speaker, shuffled. Nothing was sent to the speaker.`

The first chunk is the coordinator quoting its own system prompt. The
buffered path sends only the third. Derived from the chunk boundaries, a
hold of 2 would still have sent the first chunk (162 characters); a hold of
3 would have sent none of it. Numbers for all five are under item 4.

**S2. The unhonoured-retract event carries no preview.**
`_ollama_speech_stream` logs `preview=ev.get("preview", "")[:160]`, but
`_run_pipeline` re-yields retracts as `{"type": "retract", "chars": …}`
(`orchestrator/main.py:451` and `:558`), so the field is `''` on every real
request (all 5 above). The build report and `voice-pipeline.md` both say the
event is logged "with a preview". The text is still findable: the
`round_content_retracted` event of the same request id carries it. If the
switch is turned on to watch for leaks, the event that counts them should
say what was heard.

### Observations

**O1. Production does not read `config/` from a shared mount.** See
"Telemetry, docs and the production mount". The builder's promotion step 2
is wrong in both halves.

**O2. The rollback commands in the docs do not say where to run them.**
Production's compose project lives in the release directory
(`com.docker.compose.project.working_dir` =
`…/kronk-releases/sprint-M11-shadow`). `docker compose up -d orchestrator`
run from the working tree would recreate production from the working tree's
compose file and build context. `voice-pipeline.md` ("The switch and
rollback") and the build report's "How to roll back" give the bare command.

**O3. Older tests reach for the network on every plain run of the suite.**
Found while checking the mutants. With a recorder that refuses and logs
every lookup and connect, one run of `tests/` shows 34 attempts, none from
`test_speech_stream.py`, `test_voice_stream.py`, `test_spoken_reply.py` or
`test_m21_pins.py`:

| Test file | Attempts | What |
|---|---|---|
| `test_agentic_loop.py` | 10 | lookup of `fake-tools` |
| `test_error_surfacing.py` | 5 | lookup of `fake-tools` |
| `test_retract.py` | 5 | lookup of `fake-tools` |
| `test_render.py` | 4 | lookup of `fake-tools` |
| `test_playback_control.py` | 1 | lookup of `fake-tools` |
| `test_tool_service_weather.py` | 8 | lookups of `feeds.bbci.co.uk`, `feeds.npr.org`, `www.theguardian.com` |
| `test_magicmirror.py::test_announce_is_non_fatal` | 1 | `localhost:8123`, a websocket to the running Home Assistant with a made-up token |

The last one leaves a line in HA's log each time the suite runs:
`WARNING … [homeassistant.components.http.ban] Login attempt or request with invalid authentication from localhost (127.0.0.1). Requested URL: '/api/websocket'. (Python/3.13 websockets/16.0)`.
There are 21 of them since 12:00 UTC today; one is mine, at 14:20:10 UTC, from the plain
`./scripts/run_tests.sh` the brief asked for. That run therefore made the
lookups above too. The suite passes with all of them refused (4435 passed),
so nothing depends on them. Not an M8 change; it predates the build.

**O4. `to_speech` leaves `**` in spoken text for star bullets with bold
labels.** Input `*   **Cost:** Moderate upfront cost.` renders as
`   Cost:** Moderate upfront cost.` Buffered and streamed alike (66 of 66
runs equal), so not an M8 difference. With the line on the coordinator
writes no markdown and it did not occur (0 of 27 long answers); with
`spoken_reply: null` it reached the reply in 8 of 27. Rolling the line back
brings it back.

**O5. The buffered path wipes the reply on a zero-length retract.**
`_ollama_collect`: `reply[:-0]` is `""`. The streamed buffer keeps the text.
Not reachable today: `agents.run_stream` emits a retract only for non-empty
text. The same expression is in the streamed writer's fallback branch.

**O6. The line is advice, not a limit.** 13 of 27 long answers with the
line on ran over three sentences (up to 14). Details under item 3.

**O7. Answers in words that claim an action, on rows the corpus marks as
expected failures, with or without the line.** Example, line on:
"We play Justin Johnson Radio on Pandora." answered "Justin Johnson Radio
is currently playing on Pandora." with no tool call (4 of 5 runs; 5 of 5
without the line). "I shuffled my YouTube playlist, Old Man Video Games."
answered "I understand. The playlist "Old Man Video Games" is now
shuffled." Not caused by M8; recorded because tenet 6 names it.

**O8. The news brief is spoken whole.** "tell me the whole news briefing"
returns the code-written brief, 2,432 characters and 23 sentences, with or
without the line. The line shapes model text only.

**O9. Mutation survivors.** 59 of 344 valid mutants survive the suite.
Listed under "Mutation check". The ones that are real gaps: nothing tests a
client disconnect (`await pipeline.aclose()` can be deleted), the empty
reply's one empty chunk, a retract during the buffer-fault fallback, the
values of `first_chunk_ms` and `done_ms`, the `retracts` and
`retract_held_chars` counters, and the kind of whitespace that leads a
chunk.

**O10. A dropped streamed request leaves no closing record.** Three
requests dropped on dev: the pipeline stopped at the drop, the lock was
free, the trace closed. But there is no `request_complete`, no
`voice_stream` event or span, and nothing on the trace that says the client
went away. By the code, the buffered path collects the whole reply before
the response starts, so there the pipeline would run to its end and log it;
I did not test a drop on the buffered path.

**O11. Players.** `media_player.xbr_49x800d` (a television) went from `off`
to `unavailable` at 14:32:30 UTC; HA's log has
`pychromecast … [XBR-49X800D(192.168.1.254):8009] Failed to connect` at
14:32:45. Nothing I sent names it: the request in flight was a dry-run
music play on the Office speaker. It is still `unavailable`; I did not
touch it. `media_player.sonos_move_derp` went `unavailable` at 14:09:27,
before my first live request (14:10:02), and is in my starting snapshot
that way.

**O12. A chunk can end inside a quotation.** `He said "Stop. Now." and
left.` leaves as `He said "Stop.` and ` Now." and left.` The text is the
buffered text; only the chunk boundary falls inside the quote.

**O13. CRLF list items render as `a\r.`** in `to_speech`, buffered and
streamed alike. Not an M8 change.

**O14. A retract inside a delegated specialist never reaches the client's
stream.** A specialist called through `ask_*` returns its text to the
coordinator; its tokens and its retract are not sent. 10 of my 720
requests had one; none could leak. The denominators in item 4 count only
rounds whose text is in the stream.

**O15. The builder's "7 voice requests on production" counts traces with
`kronk.voice = true`**, an attribute that exists since M2. By route
(`kronk.http_route = /voice/api/chat`) Tempo holds 52, from 2026-11-08.
None had a round that wrote text and then called a tool.

**O16. One request of mine I did not intend.** A `POST` to
`http://localhost/dev/config/reload` through nginx, to see the refusal. It
answered 404 and reached nothing.

## Item 2: what reaches production whatever the switch says

### (a) The buffered path against `a7f4112`

A scratch worktree of `a7f4112` and the working tree at `05bbcc6`, the same
script in each: the real FastAPI app, a scripted model, a fake
tool_service, every lookup and connect refused and counted (0 attempts in
all six runs). 39 scenarios: 17 markdown replies (prose, emphasis, list,
nested list, table, fence, unclosed fence, heading, quote, rule, link,
abbreviations, ellipsis, CRLF, other scripts and emoji, empty, whitespace
only), coordinator delegation with a short and a long preamble, two tool
rounds, an unknown tool, the home agent with a terminal tool (ok, 409,
tool_service down, no preamble, long preamble), a home answer in words, a
specialist failing after text, model failure at once, after text and after
a tool round, and eight direct dispatches (ok, 409, down). Markdown and
retract scenarios run twice, word tokens and random 1 to 9 character
tokens. Each through `/voice/api/chat` streaming, not streaming, and
streaming without the origin stamp, `/api/chat` and `/v1/chat/completions`
streaming and not, and `/message`: 488 responses per run. Compared: status,
content type, body bytes with `created`/`created_at` and timing fields
normalised, and for `/message` the stored history.

| Compared | Responses | Differ |
|---|---|---|
| `a7f4112` against `a7f4112` with `VOICE_STREAM=on` (the harness itself) | 488 | 0 |
| `a7f4112` against HEAD, switch unset | 488 | 0 |
| `a7f4112` against HEAD, `VOICE_STREAM=off` | 488 | 0 |
| `a7f4112` against HEAD, off, `spoken_reply` null | 488 | 0 |
| `a7f4112` against HEAD, `VOICE_STREAM=on`, every route but voice streaming | 366 | 0 |
| HEAD on against off, voice streaming: joined text and `done` record | 122 | 8 |

The 8 are the two long-preamble scenarios (three sentences, then a tool
call), in both token splits and with and without the stamp: the streamed
path sends `First I check.` before the tool's sentence, and the `done`
record's `eval_count` is 24 against 20. That is criterion 5's defined
behaviour, the same thing as finding S1.

### (b) The line

From the model calls recorded in the same runs (the clock line of the
system text normalised):

| Check | Result |
|---|---|
| Coordinator calls on the voice mount (streaming, not, without the stamp, after a specialist failure) | 165 of 165: `a7f4112`'s system text, then `"\n\n"`, then the line, and nothing after it; the other messages identical |
| Every other model call (specialists routed and delegated, coordinator on `/api/chat`, `/v1`, `/message`) | 411 of 411 identical to `a7f4112`; none carries the line |
| `spoken_reply: null` | 576 of 576 calls identical to `a7f4112` |

The line as sent: `[Spoken reply] Your reply will be read aloud by a
speaker, not shown on a screen. Answer in one to three short, plain
sentences. Do not use lists, headings, tables, links, code or markdown.`

On dev, scratch edits of `config/voice.yaml` and `scripts/config_reload.sh dev`:

```
hold_sentences: 9
422 {"ok":false,"errors":[{"file":"config/voice.yaml","line":32,"message":"stream.hold_sentences: Input should be less than or equal to 5 (got 9)"}],"kept":"last good values (files, loaded 2026-11-17 14:15:12)"}

spoken_reply: 5
422 {"ok":false,"errors":[{"file":"config/voice.yaml","line":16,"message":"spoken_reply: Input should be a valid string (got 5)"}],"kept":"last good values (files, loaded 2026-11-17 14:15:12)"}

spoken_replyy: "x"   (appended)
422 {"ok":false,"errors":[{"file":"config/voice.yaml","line":33,"message":"spoken_replyy: unknown key"}],"kept":"last good values (files, loaded 2026-11-17 14:15:12)"}

spoken_reply: null
200 {"ok":true,"changed":["voice.yaml:spoken_reply: '[Spoken reply] Your reply will be read aloud by a speaker, not shown on a scre… -> None"]}

file restored
200 {"ok":true,"changed":["voice.yaml:spoken_reply: None -> '[Spoken reply] Your reply will be read aloud by a speaker, not shown on a scre…"]}
```

(The loader cuts the value off itself.) After the third rejection
`/api/config` still reported the values loaded at 14:15:12 as the ones in
effect.

## Item 3: does the line change what the coordinator does?

Sentences sent straight to `/dev/voice/api/chat`, `stream: true`, with HA's
origin stamp, dry run on, `VOICE_STREAM=on`. "Line on" is the committed
`config/voice.yaml`; "line removed" is `spoken_reply: null`, reloaded. All
of the line-on runs first, then all of the line-removed runs. A run counts
as missed when the tool the corpus row expects was not called; "words only"
is the subset where no tool was called at all. Every row and reply is in
`docs/bench/voice_line_runs_2026-11-17_m8-e2e.json`.

### Corpus rows that expect a tool call or a delegation

62 rows: every row with `tier: kronk` and an expected tool, less the two
tagged `needs-context` or `url`, plus `m19-ask-kronk-search-the-web`
(delegation, no tool named). 5 runs each way, 310 runs each way. 41 of the
62 reach the coordinator; 17 are direct dispatches with no model call and 4
are routed by rule to the home agent, and the line reaches neither. 46 rows
had no miss either way and are not listed.

| Row | Expected | Line on: missed / runs (words only) | Line removed: missed / runs (words only) |
|---|---|---|---|
| `plan-like-massive-attack-radio` (xfail) | play_music | 5 / 5 (3) | 5 / 5 (4) |
| `plan-mishearing-pasta` (xfail) | control_music | 5 / 5 (5) | 5 / 5 (5) |
| `plan-mishearing-whats-up` (xfail) | control_music | 5 / 5 (5) | 5 / 5 (5) |
| `inc-0930-search-deep-house-ytm` (xfail) | play_music | 1 / 5 (0) | 4 / 5 (0) |
| `inc-1017-pay-the-album-hotel-costes-3` (xfail) | play_music | 0 / 5 (0) | 1 / 5 (1) |
| `inc-1021-ninetys-no-verb` (xfail) | play_music | 4 / 5 (2) | 5 / 5 (2) |
| `eval-played-the-album-toys-in-the-attic` (xfail) | play_music | 3 / 5 (2) | 3 / 5 (3) |
| `eval-skip-the-current-song` (xfail) | control_music | 0 / 5 (0) | 1 / 5 (0) |
| `eval-volume-50` (xfail) | control_music | 5 / 5 (0) | 5 / 5 (0) |
| `eval-i-shuffled-my-youtube-playlist-old-man` (xfail) | play_music | 4 / 5 (4) | 4 / 5 (4) |
| `eval-late-evening-from-radio-on-pandora` (xfail) | play_music | 2 / 5 (0) | 1 / 5 (0) |
| `eval-j-envoie-my-youtube-playlist-video-game` (xfail) | play_music | 4 / 5 (4) | 5 / 5 (5) |
| `eval-look-for-some-good-deep-house-chill` (xfail) | play_music | 0 / 5 (0) | 1 / 5 (0) |
| `eval-we-play-justin-johnson-radio-on-pandora` (xfail) | play_music | 4 / 5 (4) | 5 / 5 (5) |
| `m19-ask-kronk-what-song-is-this` (xfail) | now_playing | 5 / 5 (5) | 5 / 5 (5) |
| `m14-thumbs-up-missed-best-part` (xfail) | rate_music | 5 / 5 (5) | 5 / 5 (5) |
| **All 62 rows** | | **52 / 310 (39)** | **60 / 310 (44)** |
| **The 41 rows that reach the coordinator** | | **52 / 205 (39)** | **60 / 205 (44)** |

Fisher's exact test, two-sided: missed, p = 0.47; words only, p = 0.64.
Every row in the table carries an `xfail` marker in the corpus. 14 of the
16 are rows the replay reports as `xfail` (expected to fail on the live
path or in routing). The other two, `inc-0930-search-deep-house-ytm` and
`eval-look-for-some-good-deep-house-chill`, are marked for the parser layer
only and are expected to pass live; both are "YouTube Music" search rows on
the known-flapper list, and with the 20 extra runs below they missed 1 of
30 with the line and 5 of 30 without. No other row that is expected to pass
missed its tool in either state. No request failed (720 of 720 HTTP 200, 0
malformed lines, long-answer set included).

### `eval-look-for-some-good-deep-house-chill`, 20 runs each way

| | Line on | Line removed |
|---|---|---|
| Expected tool not called | 0 / 20 | 0 / 20 |
| Answered in words, no tool | 0 / 20 | 0 / 20 |

With the five runs in the table above: 0 of 25 with the line, 1 of 25
without (that one called `ask_research`, then said in words that it would
start a radio, and called nothing else). The builder's 1 failure in 6 did
not recur.

### Answers that need length: ten requests, 3 runs each way

Excluding the news brief (same text both ways): 27 runs each way.

| | Line on | Line removed |
|---|---|---|
| Characters, mean / median | 354 / 322 | 991 / 993 |
| Sentences, mean / median | 4.1 / 3 | 15.7 / 14 |
| Runs over three sentences | 13 / 27 | 19 / 27 |
| Send to `done`, mean / median (s) | 4.22 / 2.97 | 4.67 / 4.18 |
| Replies with `**` or a backtick in the spoken text | 0 / 27 | 8 / 27 |

Characters (sentences) per run:

| Request | Line on | Line removed | What was cut with the line on |
|---|---|---|---|
| give me a recipe for pancakes with all the steps | 457 (6), 57 (1), 485 (5) | 983 (24), 999 (27), 1097 (27) | Runs 1 and 3 keep the seven ingredients with amounts and four or five steps in running prose; lost: "do not overmix", resting the batter, cooking times. Run 2 answered "I do not have a recipe for pancakes in my knowledge base." Everything asked was lost in that run. |
| tell me the whole news briefing | 2432 (23) ×3 | 2432 (23) ×3 | Nothing. Code-written text from `news_brief`; the line does not apply (O8). |
| give me step by step directions to change a flat tire | 708 (14), 616 (9), 359 (3) | 1415 (28), 1054 (30), 1337 (28) | Runs 1 and 2 keep seven to nine steps. Run 3 has three sentences and sends the user to "your vehicle's manual for specific steps": the steps asked for are lost. Lost in all: loosening the nuts before jacking (run 1), the star-pattern detail (run 3). |
| compare a heat pump, a gas furnace and electric baseboard heat for cost, comfort and upkeep | 362 (3), 464 (3), 322 (3) | 1253 (13), 993 (15), 1540 (19) | All three options in every run, one sentence each. Lost: one of the nine cells in run 1 (upkeep for the heat pump), the per-option comfort and upkeep in run 3, and all of the reasoning behind each rating. |
| explain in detail how the immune system fights a virus | 665 (4), 915 (8), 840 (7) | 3686 (37), 2501 (29), 3951 (43) | Innate response, T-cells, B-cells and antibodies, memory cells are in every run. Lost: the detail asked for (receptors, interferons, antigen presentation, the timeline), about three quarters of the text. |
| list the twelve months of the year in Spanish | 226 (1), 101 (1), 101 (1) | 101 (1), 100 (1), 198 (12) | Nothing. All twelve in every run. |
| walk me through every step of resetting a tripped circuit breaker | 334 (4), 330 (5), 467 (6) | 1121 (14), 1201 (16), 1048 (17) | The off-then-on steps and the "if it trips again" warning are in every run. Lost: unplugging the load first, the prevention tips, when to call an electrician (run 2). |
| name five things to pack for a beach trip and say why for each | 263 (5), 273 (5), 267 (5) | 492 (10), 289 (10), 441 (10) | Nothing. Five things, each with its reason. |
| name all eight planets in order and say which ones have rings | 235 (3), 163 (2), 163 (2) | 242 (3), 159 (2), 176 (2) | Nothing. |
| tell me the first ten prime numbers and then the first five square numbers | 126 (2) ×3 | 126 (2), 127 (2), 127 (2) | Nothing. |

### Judgement

The line does not cost tool calls. Misses were 52 of 310 with it and 60 of
310 without, the difference is inside run-to-run variance and in the line's
favour, and every miss is on a row the replay already reports as an
expected failure or on a known flapper.

The line costs content in the sense of depth. The model stretches past
three sentences when the request needs it (13 of 27), so lists of five,
twelve months and planets survive whole. What goes is explanation and
secondary steps. In 2 of 27 runs the thing asked for was lost outright
(one refusal to give a recipe, one set of directions reduced to "follow the
manual"); neither happened in the 27 runs without the line. Two in 27 is
too few to call a rate.

## Item 4: the leak question

Where the numbers come from:

- **Tempo, read-only**, every trace whose `pipeline.shim` span has
  `kronk.http_route = /voice/api/chat` or `kronk.voice = true`: 5,709 on dev
  (2026-11-16 00:20 to 2026-11-17 15:46 UTC, of which 914 are this run's)
  and 52 on production (from 2026-11-08). A "round that wrote text and
  ended in tool calls" is a generation span with tool calls and
  `kronk.output_chars > 0`.
- **The retract text is not recoverable from traces.** Tempo holds the
  character count, not the text. The dev log's `round_content_retracted`
  events hold the first 160 characters, but the dev container's log starts
  at its last recreation (the builder's at 14:03, mine at 14:15), so no
  earlier event survives. For the requests before this run I report
  character counts.
- **This run's own requests**, where the text is known: 720 on the speech
  mount with the switch on (what was actually sent at hold 1), and 144 on
  the dev display mount (`/dev/api/chat`, `stream: true`, 18 retract-prone
  rows × 8), which carries every pipeline token and cannot retract, so the
  token and retract sequence is exact and replays offline through
  `SpeechBuffer` at any hold. Files:
  `docs/bench/voice_line_runs_2026-11-17_m8-e2e.json`,
  `docs/bench/voice_capture_2026-11-17_m8-e2e.json`.

Almost all of the dev traffic is replays and test runs, not household
speech; the rates describe this corpus on this model.

### The denominator: how many voice requests had such a round

Only rounds of the top-level agent are in the client's stream (O14).

| Route | Requests | With a text-then-tool round in the stream | Share | Such a round only inside a delegated specialist |
|---|---|---|---|---|
| **Dev, before this run** | | | | |
| Direct dispatch | 376 | 0 | 0.00% | 0 |
| Specialist (routed by rule) | 1632 | 30 | 1.84% | 0 |
| Coordinator | 2787 | 17 | 0.61% | 19 |
| All | 4795 | 47 | 0.98% | 19 |
| **Dev, this run** | | | | |
| Direct dispatch | 186 | 0 | 0.00% | 0 |
| Specialist | 47 | 0 | 0.00% | 0 |
| Coordinator | 681 | 5 | 0.73% | 10 |
| All | 914 | 5 | 0.55% | 10 |
| **Production** | | | | |
| Direct dispatch | 1 | 0 | | 0 |
| Specialist | 18 | 0 | | 0 |
| Coordinator | 33 | 0 | | 0 |
| All | 52 | 0 | 0.00% | 0 |

The 47 earlier rounds by agent: home 24, coordinator 19, research 3,
devops 1. Characters: minimum 53, median 223, p90 1,370, maximum 5,271.
41 are over 80 characters, 32 over 160, 22 over 240. Read as a bound: if
the text is prose, more than about one, two or three sentences were
written before the tool call, so up to 41, 32 and 22 of the 47 had
something to leak at hold 1, 2 and 3. The bound is loose in the other
direction too: text shaped as one list is one unit and is held whole,
whatever its length (the capture below).

### Replays at hold 1, 2 and 3

**Speech mount, switch on, hold 1 measured, 720 requests.** A retract
reached the stream in 5; all 5 had sent text. Hold 2 and hold 3 are derived
from the chunks: at hold `h` the first leaked unit leaves when the hold-1
stream sent its `h`-th chunk, so the last `h − 1` leaked chunks would have
stayed held.

| Row, run, line, trace | Round | Sent at hold 1 | Hold 2 | Hold 3 |
|---|---|---|---|---|
| `eval-i-shuffled-my-youtube-playlist-old-man` r2, line on, `696794cb83f9e046ee81e4dc09ca6551` | 369 | 240 | 162 | 0 |
| `eval-volume-50` r4, line removed, `0d4c95010727a8efeb57201465be1ad7` | 238 | 41 | 0 | 0 |
| `inc-1021-ninetys-no-verb` r1, line removed, `817143ab597c410a81ecb400932ece21` | 751 | 307 | 270 | 112 |
| `inc-1021-ninetys-no-verb` r3, line removed, `11aebe68b7b385cfefbb5abece6b4070` | 680 | 513 | 470 | 445 |
| `eval-played-the-album-toys-in-the-attic` r4, line removed, `f2c08d15cc87bd93d187a74af44a194c` | 777 | 556 | 358 | 309 |
| **Requests that would have spoken retracted text** | 5 | **5 of 5** | **4 of 5** | **3 of 5** |
| **Characters, total** | 2815 | 1657 | 1260 | 866 |

"Sent at hold 1" is the logged `retract_unhonoured_chars` (raw
characters); the hold 2 and 3 figures are the lengths of the chunks as
sent. What would have been heard, at hold 1, before the answer:

- `eval-volume-50`: `The user wants to set the volume to 50%.`
- `eval-played-the-album-toys-in-the-attic`: `Play Toys in the Attic.` /
  `I need to use ask_home to start the music.` /
  `The user said "Played the album Toys in the Attic.".` /
  `If I assume this is a command to start playback, I will use ask_home.` /
  `Tool call: ask_home with the query "Play the album Toys in the Attic".` /
  `Wait, the user might have just stated a fact.` /
  `The instructions imply I should act on commands.` and one more sentence,
  then the dry-run sentence.
- `inc-1021-ninetys-no-verb` r1: `I can provide a list of hits for the 90s
  and 2000s, and I will check current charts for today's hits.` /
  `90s Hits:` / five song titles / `2000s Hits:`, then the answer.
- The line-on case is quoted under S1.

By line state: 1 leak in 360 requests with the line on (1 in 255 that
reached the coordinator), 4 in 360 with it removed.

**Display mount, exact sequences, 144 requests.** 3 had a retracted round.
2 were inside a delegated specialist and are not in the stream. 1 is in the
stream: `eval-shuffle-it` run 7, "Shuffle it.", route `home/music`, trace
`e641b3898c92635f6e7efa8c952acc8b`. The home agent wrote 5,315 characters
over 16 seconds (tokens from 4.7 s to 20.8 s), then called `play_music`.
The text begins ``named`, `genre`, `another`)`` and continues as one
indented, numbered list (`6. **Map the user input to parameters:**` …).
`split_units` finds one complete unit before the tool call (the 28-character
first line) and one open list of 5,287 characters.

| Hold | Would have spoken retracted text | Characters | Heard |
|---|---|---|---|
| 1 | no | 0 | `Dry run: would play E.VAX on the Office speaker. Nothing was sent to the speaker.` |
| 2 | no | 0 | the same |
| 3 | no | 0 | the same |

So the largest kind of leak in the earlier data (the 5,271-character home
round is the same row) is held by the block rule, not by the hold width. Had
its first line been followed by a second prose line, hold 1 would have
spoken the first.

### The gain at each hold width

Timed runs with the switch on at hold 1. For hold 2 and 3 the first chunk
would leave when the hold-1 stream sent its second or third chunk; a reply
with too few chunks leaves whole at `done`.

| Runs | Hold | First chunk before `done` | Lead when it does, median / max (s) | Mean lead over all runs (s) |
|---|---|---|---|---|
| Builder, 10 questions × 5 (`m8-stream-on`) | 1 | 41 / 50 | 0.32 / 0.82 | 0.29 |
| | 2 | 8 / 50 | 0.32 / 0.56 | 0.05 |
| | 3 | 2 / 50 | 0.29 / 0.33 | 0.01 |
| Mine, the same 10 × 5 (`m8-e2e-on`) | 1 | 40 / 50 | 0.27 / 0.60 | 0.24 |
| | 2 | 7 / 50 | 0.23 / 0.40 | 0.03 |
| | 3 | 0 / 50 | | 0.00 |
| Mine, requests that need length, line on (27) | 1 | 17 / 27 | 0.48 / 1.55 | 0.40 |
| | 2 | 12 / 27 | 0.42 / 1.29 | 0.26 |
| | 3 | 10 / 27 | 0.30 / 1.13 | 0.17 |
| Mine, the same with the line removed (27) | 1 | 14 / 27 | 2.08 / 9.17 | 1.60 |
| | 2 | 13 / 27 | 2.09 / 8.99 | 1.53 |
| | 3 | 10 / 27 | 1.40 / 8.67 | 0.97 |
| Mine, corpus rows, line on (310) | 1 | 12 / 310 | 0.14 / 2.56 | 0.01 |
| | 2 | 2 / 310 | 1.25 / 2.37 | 0.01 |
| | 3 | 0 / 310 | | 0.00 |

The builder's 100 recorded replies (`tests/fixtures/m8/replies.json`) have
text and no chunk times, and are stored after `to_speech`, so their lists
are already flattened. Counting units (a first chunk leaves early only when
the reply has at least hold + 2 units):

| Recorded set | Hold | Replies whose first chunk would leave early | Share of the reply still to come at that moment, mean / median |
|---|---|---|---|
| Before the line (`m8-pre`, 50) | 1 | 49 / 50 | 0.68 / 0.71 |
| | 2 | 44 / 50 | 0.37 / 0.36 |
| | 3 | 26 / 50 | 0.17 / 0.10 |
| With the line (`m8-line`, 50) | 1 | 43 / 50 | 0.31 / 0.33 |
| | 2 | 6 / 50 | 0.04 / 0.00 |
| | 3 | 0 / 50 | 0.00 / 0.00 |

Reading the two together: with the line on, hold 1 gains about a quarter
of a second on a three-sentence answer and leaks whenever the coordinator
thinks aloud before a tool call. Hold 2 keeps 4 of the 5 leaks and gains
nothing on most answers. Hold 3 keeps 3 of 5 and gains nothing. No hold
width in the range has both.

## Item 5: the streamed path, my own inputs

Offline, `SpeechBuffer` over `split_units`, no app, no sockets. 100 inputs:
a token boundary inside every markdown marker (bold, bold across a sentence
end, italic with `*` and `_`, `__`, `***`, `~~`, inline code, code with
full stops, links, an image, an autolink, headings, a heading with no
space, block quote, rules, bullets with `-` `*` `+`, numbered with `.` and
`)`, nested and mixed lists, a checklist, a fence, tilde fence, inline
triple backticks, empty fence, fence inside a list, a reply that is one
unclosed fence, an unclosed fence after prose, a table followed at once by
prose, a table at the end, a `|` in prose), sentence ends ("U.S.", "a.m.",
"e.g.", "i.e.", "St.", "Mt.", "Dr.", "Mr.", "vs.", "No. 5", "3.14",
"v2.0", "v1.9.3", "$3.50", "7:30", "7.30", initials, `...`, `…`, a reply
ending in `...`, `?!`, straight and curly quotes, parentheses, a lowercase
word after a full stop, no final punctuation, one sentence, one word,
empty, whitespace only, a lone full stop, digits starting sentences, a URL
ending in a full stop, a URL in mid-sentence), CRLF, lone CR, tabs, runs of
newlines, trailing and leading spaces, a non-breaking space, a zero-width
space, emoji, German, Japanese, Cyrillic, Arabic, Spanish, HTML tags,
backslash escapes, unbalanced `*`, `` ` `` and `[`, footnotes, a 2,000-word
line with no full stop, and a 20,000-character reply. Each fed character
by character, word by word, whole, and in 20 random splits of 1 to 12
characters, at hold 1, 2 and 3.

| | Count |
|---|---|
| Stream runs | 6,846 |
| Joined chunks differ from `to_speech` of the whole reply (whitespace normalised) | 0 |
| Chunks that `to_speech` would change again, or empty chunks | 0 |
| Prefixes at which `split_units` withdrew an earlier cut (every prefix of every input up to 2,500 characters) | 0 |

The builder's one listed difference (`Moves .` against `Moves.`) did not
occur in my inputs, which include two `|`-in-prose lines with trailing
spaces. No difference of any class to list.

### Retracts

`SpeechBuffer` against the buffered path's rule
(`reply[:-chars] if chars <= len(reply) else ""`), word tokens and
character tokens, same result in both.

| Case | Hold 1 | Hold 2 | Hold 3 |
|---|---|---|---|
| Retract inside the hold | same | same | same |
| Two retracts in a row, both held | same | same | same |
| Two tool rounds, each retracted, then a long answer | same | same | same |
| Retract of part of the held unit | same | same | same |
| Retract inside an open list | same | same | same |
| Retract inside an open fence | same | same | same |
| An answer, then a late preamble retracted | same | same | same |
| Retract larger than everything, nothing sent yet | same; recorded as unhonoured (5000, 19, 4981) | same | same |
| Retract on an empty buffer | same; recorded as unhonoured (40, 0, 40) | same | same |
| Retract after chunks were sent (three-sentence preamble) | **differs**: `First I check. Paused on the Office speaker.` against `Paused on the Office speaker.`; unhonoured (41, 26, 15) | same | same |
| Retract larger than everything, chunks sent | **differs**, as above; unhonoured (5000, 26, 4974) | same; unhonoured (5000, 41, 4959) | same |
| Two retracts in a row, the second past the hold | **differs**, as above; unhonoured (41, 26, 15) and (12, 0, 12) | same; unhonoured (12, 0, 12) | same |
| Retract of zero after held text | **differs**: streamed `One. Two. Three.`, buffered `Two. Three.` | differs | differs |
| Retract of zero after sent chunks | **differs**: streamed `One. Two. Three. Four.`, buffered `Four.` | differs | differs |
| Retract of zero, nothing before | same | same | same |

The first three differing rows are criterion 5's defined behaviour: what
was sent stays spoken, later chunks are intact. The unhonoured counter also
counts a retract that is larger than the reply when nothing was spoken
wrongly (rows 8 and 9); the pipeline never sends one. The zero rows are O5:
the buffered path is the one that is wrong, and no agent emits a zero
retract.

Through the app (item 2's harness, on against off): a failure after chunks
were sent ends `One sentence here.` / ` Two sentences here. Three sentences
here.` / ` Sorry — I couldn't reach my language model. Try again in a
moment.`, text equal to the buffered path's; an empty reply is one chunk
with the empty-reply sentence; a whitespace-only reply is one chunk with
`""`, as buffered.

### A client that disconnects, and two requests at once (dev, live)

"explain in detail how the immune system fights a virus" on
`/dev/voice/api/chat`, the connection reset; then "why is the sky blue"
sent at once.

| Dropped | Dropped request | The request sent right after |
|---|---|---|
| After the first content chunk (4.47 s) | trace `0e36d32503cb65f09aa4c8726d6ba2ed`: `pipeline.shim` closed at 4.47 s; no `request_complete`, no `voice_stream` | 200, 3 lines, one `done`, first chunk 0.54 s, done 0.89 s |
| 0.4 s after sending, before any chunk | trace `2f1984994d99d1fec66a1a4c4499521e`: closed at 0.40 s | 200, 2 lines, one `done`, done 1.62 s |
| 1.5 s after sending | trace `58d0d6f266cc9c6e7fbcd2b42e12d09b`: closed at 1.50 s | 200, 3 lines, one `done`, first chunk 1.32 s, done 1.56 s |

The lock is released and nothing is left running: the next request's
generation started at once in all three, and the dropped request's spans
end at the moment of the drop with no generation span after it. No
traceback or error line in the dev orchestrator's log. What is missing is
a closing record (O10).

Two voice requests sent together: both 200, each one valid stream with one
`done` (5 lines and 3 lines). The second waited for the first (`route`
event at 1.95 s, when the first completed), then ran: 1.95 s and 3.51 s.
Neither stream held text from the other.

## Item 6: protocol against HA's client

The replay below ran 72 rows through `kronk-dev` with the switch on; every
one has a `voice_stream` event, none an error. The 16 bench runs through
`kronk-dev` returned no error event. HA's log from 14:05 UTC to the end of
the run has three lines: the Sonos Move marked unavailable (14:09:27), the
invalid-authentication warning from the test suite (14:20:10, O3), and the
television's cast connection failing (14:32:45, O11). No Ollama,
conversation or assist-pipeline line.

## Item 7: timing

`scripts/voice_stream_bench.py` on dev at `05bbcc6`, the line on in both.
Switch on first (as found), then off by a scratch compose override for
`orchestrator-dev` only, then on again. 10 questions × 5 runs each, no
errors.

### Criterion 17: time to first content chunk, medians of 5 runs (s from send)

| Question | Off: first chunk = done | On: first chunk | On: done | On: done − first | On: chunks |
|---|---|---|---|---|---|
| explain how a heat pump works | 2.36 | 1.77 | 2.08 | 0.27 | 2 |
| how does a refrigerator keep food cold | 1.91 | 1.42 | 2.01 | 0.23 | 2 |
| why is the sky blue | 1.25 | 1.17 | 1.40 | 0.24 | 2 |
| how do vaccines work | 1.74 | 1.32 | 1.64 | 0.32 | 2 |
| what causes the seasons on earth | 2.71 | 2.42 | 2.42 | 0.20 | 2 |
| how does a solar panel make electricity | 2.05 | 0.62 | 0.81 | 0.23 | 2 |
| explain how a car engine works | 1.60 | 1.14 | 1.42 | 0.29 | 2 |
| how do airplanes stay in the air | 2.14 | 1.53 | 1.83 | 0.32 | 2 |
| what is the difference between a virus and a bacterium | 1.97 | 1.74 | 1.74 | 0.00 | 1 |
| how does a microwave oven heat food | 1.21 | 1.19 | 1.43 | 0.20 | 2 |

| Mean / median over runs | Off: done | On: first chunk | On: done | On: done − first (max) |
|---|---|---|---|---|
| First 5 questions, 25 runs, mine | 2.02 / 1.74 | 1.87 / 1.42 | 2.13 / 1.82 | 0.26 / 0.27 (0.60) |
| First 5 questions, builder | 2.00 / 1.75 | 1.29 / 1.06 | 1.58 / 1.42 | 0.29 / 0.29 (0.79) |
| All 10, 50 runs, mine | 2.09 / 1.91 | 1.69 / 1.39 | 1.93 / 1.69 | 0.24 / 0.24 (0.60) |
| All 10, builder | 1.99 / 1.78 | 1.30 / 1.09 | 1.59 / 1.46 | 0.29 / 0.29 (0.82) |

Single-chunk replies with the switch on: 10 of 50 (builder 9). The
builder's "off" column came from an earlier commit; my off run on the final
code gives the same scale (ruling 5). Between separate runs `done` itself
moves by several tenths of a second (my on run was 0.16 s faster than my
off run, the builder's 0.40 s), so the clean measure is inside the on run:
the first chunk leaves a median 0.24 s before `done`.

### Criterion 16 on the final code (line on in both)

| Run | Characters, mean / median | Sentences, mean / median | Generation s, mean / median |
|---|---|---|---|
| Builder, before the line (`a7f4112`) | 753 / 648 | 9.28 / 5.5 | 2.92 / 2.90 |
| Builder, with the line (`fba7451`) | 322 / 325 | 2.98 / 3 | 1.97 / 1.75 |
| Mine, switch off (`05bbcc6`) | 333 / 334 | 3.14 / 3 | 2.06 / 1.88 |
| Mine, switch on (`05bbcc6`) | 302 / 298 | 2.94 / 3 | 1.90 / 1.67 |

Replies over three sentences: 9 of 50 off, 7 of 50 on; the longest 6.

### Through HA's `kronk-dev` pipeline, intent to TTS, no device id (s from the run request)

| Run | Question | Content deltas | `tts_start_streaming` | `intent-end` | `tts-start` | Lead | Trace |
|---|---|---|---|---|---|---|---|
| off r1 | explain how a heat pump works | 1 | 4.585 | 4.585 | 4.585 | 0.0 | `727771039b52564ccb5e7a66c02a3595` |
| off r1 | how does a refrigerator keep food cold | 1 | 3.221 | 3.221 | 3.221 | 0.0 | `64613ceb98de7a8cb1a2746d3d997382` |
| off r1 | how do vaccines work | 1 | 2.245 | 2.245 | 2.245 | 0.0 | `bda8d9787c2cd8d979305d3af8aa7459` |
| off r1 | what causes the seasons on earth | 1 | 3.015 | 3.015 | 3.015 | 0.0 | `c0e5708ca5426a3df4417eb076c880a8` |
| off r2 | explain how a heat pump works | 1 | 1.592 | 1.592 | 1.592 | 0.0 | `00f90e796a84ff5665bf5308a611fa4b` |
| off r2 | how does a refrigerator keep food cold | 1 | 3.324 | 3.324 | 3.324 | 0.0 | `454eb54e9003dfa69efc6c4011bdca48` |
| off r2 | how do vaccines work | 1 | 1.831 | 1.831 | 1.831 | 0.0 | `d652f669a35e3dcdf05a8b9d4c73a846` |
| off r2 | what causes the seasons on earth | 1 | 1.794 | 1.794 | 1.794 | 0.0 | `7be2d7a8fb066e581d9f7bfc144137d4` |
| on r1 | explain how a heat pump works | 1 | 2.450 | 2.450 | 2.450 | 0.0 | `837fc7f7b6f2ae4d0c0997c686523ba8` |
| on r1 | how does a refrigerator keep food cold | 3 | 0.873 | 1.476 | 1.477 | 0.603 | `26a1301997b50da6c55fdad016bb2521` |
| on r1 | how do vaccines work | 2 | 0.512 | 0.788 | 0.788 | 0.276 | `e2bbd4d9b4b576bc59755971ee81bc12` |
| on r1 | what causes the seasons on earth | 2 | 1.305 | 1.518 | 1.518 | 0.213 | `29f9e790db067a1459e56c630329c789` |
| on r2 | explain how a heat pump works | 2 | 1.527 | 1.773 | 1.773 | 0.246 | `b5646069b83519a13cc9da70d8d06bbd` |
| on r2 | how does a refrigerator keep food cold | 2 | 2.135 | 2.340 | 2.340 | 0.205 | `2aa3a42f389b8ca8c50bf8f06832c736` |
| on r2 | how do vaccines work | 2 | 1.427 | 1.692 | 1.692 | 0.265 | `6fc7319bcd87a7aaffaabdd4e00e14c1` |
| on r2 | what causes the seasons on earth | 2 | 0.635 | 0.916 | 0.916 | 0.281 | `81542e08ed7446a075d7e4a96f1eb2d3` |

"why is the sky blue" was skipped in both: HA's sentence debug shows a
fuzzy `HassGetCurrentTime` match and the script runs nothing HA might take
itself. With the switch off HA starts TTS at `intent-end` in 8 of 8; with
it on, before `intent-end` in 7 of 8 (the eighth was a one-chunk reply).

Ruling 1 (these runs end at the TTS stage with no device id): confirmed
silent. Every `media_player` and `assist_satellite` state, title and
`last_changed` was the same before and after each of the two batches, and
HA's state history for the three satellites and the fourteen players that
were not already `unavailable` shows no change in the window (the two exceptions in O11 are outside it or
unrelated). `wyoming-piper` logged nothing; at its log level that does not
show whether HA synthesised audio into its cache. No audio went to a
device.

## Item 8: telemetry, docs and the production mount

Telemetry (criterion 23) and docs (criterion 24) pass; evidence in the
table above.

`docker inspect kronk-orchestrator-1`:

```
…/kronk-releases/sprint-M11-shadow/data -> /data
…/kronk-releases/sprint-M11-shadow/kronk-context.md -> /kronk-context.md
…/kronk-releases/sprint-M11-shadow/litellm/config.yaml -> /litellm-config.yaml
…/kronk-releases/sprint-M11-shadow/config -> /config
com.docker.compose.project.working_dir: …/kronk-releases/sprint-M11-shadow
```

In the release directory `.env`, `.env.dev`, `data`, `ops`, `searxng` and
`secrets` are symlinks to the working tree; `config` is a real directory,
the tag's own copy (`lead_in.yaml`, `music.yaml`, `prompts/`,
`tuning.yaml`). Production mounts that copy.

What it means:

- **At promotion.** `config/voice.yaml` reaches production only inside the
  new release's checkout, together with the M8 code. Production today has
  no `voice.yaml` in its mount, so the builder's "its loader lists it as a
  stray file until production runs M8 code" cannot happen, and "the line
  takes effect with the new image … from the shared `./config` mount" is
  right about the effect and wrong about the mechanism.
- **Rolling the line back.** `spoken_reply: null` has to be written in the
  release's copy, `kronk-releases/<tag>/config/voice.yaml`, then
  `scripts/config_reload.sh prod`. Editing `config/voice.yaml` in the
  working tree and reloading changes nothing in production. The edit in the
  release directory is outside git, and the next deploy from a tag puts the
  line back unless the change is also committed.
- **Rolling the switch back.** Same directory: the compose command has to
  run against the release's compose file (O2).
- **Rolling the line back also brings back** markdown markers in speech
  (O4) and long answers.

## Item 9: tests, mutation check, replay

### Test output (verbatim)

```
$ ./scripts/run_tests.sh -q
…
-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
4435 passed, 3 skipped, 53 xfailed, 6 warnings in 37.63s
```

The same suite with every lookup and connect refused and recorded:
`4435 passed, 3 skipped, 53 xfailed, 6 warnings in 37.33s`, 34 attempts,
none from M8's test files (O3).

### Mutation check

Four scratch worktrees of `05bbcc6`. Targets:
`orchestrator/speech_stream.py` lines 56 to 286 (all of the code) and
`orchestrator/main.py` lines 519 to 530 (the line) and 1028 to 1215 (the
switch, the streamed writer, the voice branch). Operators: comparison,
boolean and arithmetic swaps, `not` dropped, `is None` flipped, constants
0, 1, 2, 3 bumped, `in` flipped, `continue`/`break` swapped, a condition
negated, a statement replaced by `pass`, a return value replaced by `None`.
Each mutant ran the seven nearest test files; a mutant that survived them
ran the whole suite. Every run had the network refused for the whole
session; 0 attempts.

| | `speech_stream.py` | `main.py` | Total |
|---|---|---|---|
| Mutants | 233 | 121 | 354 |
| Do not compile | 5 | 5 | 10 |
| Killed (6 by timeout, 2 only by the whole suite) | 193 | 92 | 285 |
| Survived | 35 | 24 | 59 |

Each survivor was then run against my own inputs (item 5's 6,846 buffer
runs and retract table, item 2's 488 responses with the switch on); 4 of
59 are caught there.

Survivors that are real gaps in the suite:

| Where | Mutant | What is not tested |
|---|---|---|
| `main.py:1114` | `await pipeline.aclose()` → `pass` | Nothing closes the pipeline on a client disconnect in any test. Not caught by my offline inputs either; the live drop test is the only check. |
| `main.py:1132` | the empty chunk for an empty reply → `pass` | A reply that renders to nothing must still send one content chunk. Caught by my harness (4 responses). |
| `main.py:1090`, `:1091` (two), `:1106`, `:1107`, `:1119`, `:1120`, `:1121`, `:1124` | the fallback after a buffer fault: the retract branch, the error text, the leading space | A retract arriving after the buffer has failed; the recorded error; the fault at the very end of the stream. |
| `main.py:1109` (two), `:1110` (two), `:1126` (two), `:1127` (two), `:1146` | `first_ms` and `done_ms` arithmetic and conditions | Tests check that the fields exist, not their values. |
| `main.py:1042` | `and` → `or` in the unknown-value warning | "Logged once" is not tested; it may log every request. |
| `main.py:1205`, `:1206` | `total_duration`, `load_duration` 0 → 1 in the non-streaming answer | Not M8 code. No test in the suite pins these two fields on any route; my byte comparison does (122 responses). |
| `speech_stream.py:237`, `:238`, `:254` (three), `:257` (two), `:261` (two) | the `retracts` and `retracted_held` counters | `kronk.retracts` and `kronk.retract_held_chars` on the span are never asserted. |
| `speech_stream.py:274`, `:277` | `_gap` not kept | Which whitespace leads a chunk (`" "`, `"\n"`, `"\n\n"`) is compared only after normalising. `:277` is caught by my inputs (the retract table and 28 responses). |

Survivors that do not change the text (my judgement from reading them;
none is caught by my inputs): `speech_stream.py:80` (`"line"` → `None`, any
kind that is not prose, fence, list or table behaves the same), `:90`,
`:91`, `:92` (`_decided`: a line's kind is settled one token later), `:105`
and `:136` (the first character of the reply), `:114`, `:116`, `:141`,
`:143`, `:146` (a falsy return for a falsy return, or a return value nobody
reads), `:152` (two; a reply starting with a newline is read as one long
line, same units), `:173` and `:183` (the block state is reset on the next
line anyway), `:190` (a digit token is decided one token later), `:231`
(the default hold; every caller passes it), `:245` (sends an empty string,
which sends nothing), `:286` (the `text` property is unused);
`main.py:1045` (`False` → `None`). Several of these delay a cut by one
token, which no test would notice and no listener either.

Full list with each mutant's text: `docs/bench/m8_e2e_2026-11-17_offline.json`.

### Replay (verbatim)

`python3 scripts/voice_replay.py --label m8-e2e`, pipeline `kronk-dev`,
`VOICE_STREAM=on`, `config/voice.yaml` as committed. It ran once, in full;
no row was skipped or stopped for player state.

```
voice_replay: {'pass': 134, 'xfail': 22, 'skip': 3, 'xpass': 1}; executed 72, checked read-only 85; players unchanged (17 checked)
  skipped inc-1017-try-playing-it-ytm: follow-up that needs conversation history; a replay has none
  skipped inc-1019-ytm-link: typed link; a spoken request carries no URL
  skipped m4b-fetch-and-run-on-the-mirror: typed link; a spoken request carries no URL
voice_replay: wrote /home/drew/git-repos/drawsmcgraw/kronk/docs/bench/voice_replay_2026-11-17_154050_m8-e2e.json
```

Row by row against `docs/bench/voice_replay_2026-11-16_012824_m10-baseline.json`
(114 rows), and against `voice_replay_2026-11-17_130856_m11-e2e.json` for
the 46 rows added since:

- `inc-0930-search-deep-house-ytm`: fail in the baseline, pass here
  (`coordinator:ask_home`, `home:play_music`; trace
  `0f64190e61a3c5201dae29b5f789d408`). One of the three "YouTube Music"
  search rows; known flapper.
- `m19-what-happens-if-i-ask-kronk-to-pause`: xpass, as in the M11 run
  (`xfail.live`).
- Every other row: the same status as its reference.
- `eval-look-for-some-good-deep-house-chill`, which failed in the builder's
  replay, passed.
- All 72 executed rows were streamed (a `voice_stream` event each); none
  had a retracted round.

## What I changed temporarily and put back

- `orchestrator-dev` recreated twice with
  `docker compose --profile dev … up -d --no-deps orchestrator-dev`: once
  with a scratch override file setting `VOICE_STREAM=off` (14:12), once
  without it (14:15), back to `on`. No other container was touched;
  `.env.dev` was not edited. Recreating the container discards its log, so
  the dev orchestrator's log now starts at 14:15 UTC.
- `config/voice.yaml`: three invalid values, then `spoken_reply: null`
  (14:51 to 15:24), each followed by `scripts/config_reload.sh dev`; then
  the committed file restored and reloaded. `git status` shows no change
  under `config/`, and dev reports the same digest for `voice.yaml` as at
  the start (`ad302933f693`).
- Five scratch worktrees (`a7f4112` once, `05bbcc6` four times), removed
  with `git worktree remove`; mutated files were restored before removal.
- Scratch files in the session's scratch directory. My harness also made 65
  temporary directories under `/tmp` (`m8e2e-*`, two SQLite files each) and
  six timed-out mutants left empty `kronk-taste-*` directories there; all
  removed. Three `pytest-of-drew/pytest-36x` directories from failing
  mutants remain, under pytest's own retention.
- Chat on dev: about 970 requests (bench, line runs, captures, drop test)
  on `/dev/voice/api/chat` and `/dev/api/chat`, and 88 pipeline runs through
  `kronk-dev`. Dry run was on throughout and checked before every request.
  Nothing was sent to production's chat routes, to the MagicMirror Pi, or to
  any LAN address.

End state: `orchestrator-dev` and `tool_service-dev` up and healthy, their
code md5-identical to the working tree, `VOICE_STREAM=on`,
`MUSIC_DRY_RUN=1`, no `/keys` mount in `tool_service-dev`, `ops/` holding
only `hosts.example.json`. Every satellite and player is in the state I
found it except the television in O11. `git status` is clean apart from
this report and the result files:

- `docs/bench/voice_stream_2026-11-17_141002_m8-e2e-on.json`
- `docs/bench/voice_stream_2026-11-17_141239_m8-e2e-off.json`
- `docs/bench/voice_stream_2026-11-17_141439_m8-e2e-ha-off.json`
- `docs/bench/voice_stream_2026-11-17_141531_m8-e2e-ha-on.json`
- `docs/bench/voice_line_runs_2026-11-17_m8-e2e.json`
- `docs/bench/voice_capture_2026-11-17_m8-e2e.json`
- `docs/bench/voice_replay_2026-11-17_154050_m8-e2e.json`
- `docs/bench/m8_e2e_2026-11-17_offline.json`

## Not tested

- First audio at a speaker. No sound was made; whether the 0.2 to 0.3 s of
  earlier `tts_start_streaming` is audible is Drew's test.
- The retract text of the 47 earlier tool rounds: not recoverable, counted
  by length only.
- Production. Nothing was sent to it.
