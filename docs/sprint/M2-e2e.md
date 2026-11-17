# M2 end to end: friendly errors on voice, and one voice signal in the pipeline

**M2 FAILS.**

Test agent, 2026-11-17 00:42 to 01:10 UTC, branch `refactor`, tree at
`57b0681` (build commit `60fa59a`). Tested against
`docs/sprint/M2-criteria.md`; `docs/sprint/M2-build.md` was read as claims.
Nothing was fixed. No safety control stopped a test.

Criterion 6 fails. Three failures on the speech mount are still spoken as
debug text:

1. **A news request while tool_service is down** speaks
   `[Tool news_brief error: [Errno -3] Temporary failure in name resolution].`
   Live on `/dev/voice/api/chat`, 7 of 7 requests. This is a tool_service
   outage, one of the kinds criterion 6 names.
2. **A magic mirror update that fails its preflight** speaks
   `I couldn't update the magic mirror. Mirror preflight failed: SSH key not found at /keys/kronk-mm-update — mount ./secrets/mm.`
   Live through Home Assistant on the dev voice path, 2 of 2 (replay rows).
3. **A model that returns nothing** speaks
   `[coordinator agent returned no response].` Scratch copy, TestClient.

Everything else passes: the signal (criteria 1 to 5) held against every
attempt to flip it, 48 concurrent requests each got their own mount's
style, the three other mounts are byte-identical to `9b6c435`, prompts and
tool menus for successful requests are unchanged, and none of 38 spoken
model-written failures contained technical text.

## Criteria

| # | Criterion | Result | Evidence |
| --- | --- | --- | --- |
| 1 | One request-scoped value, false off the speech mount | pass | 203 live dev requests joined to their traces: `kronk.voice` is true on all 101 handled by `/voice/api/chat` and false on all 102 handled by `/api/chat`, `/v1/chat/completions`, `/message`. Style of the reply agrees on all 128 forced code-authored failures (sections 1 to 3). |
| 2 | Set from the mount, never from content | pass | 53 content attempts (fields, headers, model name, system text, origin stamp, user text, query string) and 40 path variants through nginx's `/dev/`: no request got a style other than that of the route that handled it (sections 1, 2). Mutants M25 to M28 (content sets the signal) killed. |
| 3 | No leak between concurrent requests | pass | 24 voice/non-voice pairs fired at once with failures forced: 48 of 48 correct, 0 mismatches (section 3). Mutant M31 (plain module global) killed by `test_concurrent_requests_keep_their_own_value`. |
| 4 | Speech profile behaves as before, one source of truth | pass | Scratch copies of `9b6c435` and the tree through TestClient: for 5 successful scenarios on `/voice/api/chat`, streaming and not, the reply and every model input are identical (section 6). Mutant M20 (speech profile on, signal not passed) killed. |
| 5 | Value on the trace | pass | Tempo, 00:42 to 01:00 UTC: `{ span.kronk.voice = true && resource.service.name = "kronk-orchestrator-dev" }` returned 102 traces, the 101 driver voice requests plus one control; `= false` returned 102; production service 0 (section 4). |
| 6 | Every failure on the speech mount is a short plain sentence | **fail** | Findings 1 to 3. Routing failure, pipeline crash, unreachable and refusing model server, tool_service outage on music tools, specialist failure and non-terminal tool failures are friendly (sections 5, 6). |
| 7 | Friendly sentence says what kind of thing failed where the code knows | pass | Code-authored sentences name the failing part (section 6). Model-written: 35 of 38 name what failed; 3 do not say anything failed (observation 3). |
| 8 | Music failure sentences unchanged in both styles | pass | `_terminal_speech` old against new for 18 result lines, 4 tools and both styles (144 calls): the only differences are the friendly fallback for a result with no mapping (departure 4 of the build report). Music failures with their own sentence are identical on all four mounts (F17, F18). |
| 9 | Debug wording elsewhere unchanged, byte for byte | pass | 19 forced failures and 5 successes on `/message`, `/api/chat`, `/v1/chat/completions` (streaming and not): 720 compared fields (HTTP status, reply, every model input, trace level, trace status, trace output) identical to `9b6c435`, 0 different (section 6). |
| 10 | Logs and traces keep the full cause | pass | For all 47 spoken failures the cause is in the dev log (`tool_error` event with the tool and the error) and on an error span in the trace; `{ span.kronk.voice = true } && { status = error }` returned the 98 voice traces that had an error span (section 5). See observation 1 on the root span. |
| 11 | Friendly on voice by default, one documented setting to switch back | pass | `orchestrator-dev` env has `ERROR_STYLE=debug` and no `ERROR_STYLE_VOICE`, and voice was friendly live. `.env` sets neither. `ERROR_STYLE_VOICE=debug` switches voice back (scratch copy, section 6). Not tested live: it needs an `.env.dev` edit. Compose, `.env.dev.example`, README and `verbose-errors.md` agree with the code. |
| 12 | Stale smoke-test comments removed | pass | `grep -rn "smoke test" orchestrator tests/*.py docker-compose.yml docs/features README.md .env.dev.example`: no output. |
| 13 | Unit tests cover signal, isolation, each failure kind | pass | The five kinds criterion 6 names are tested on the speech mount and one other. Mutation: 31 of 33 killed (section 7). Gaps: no test speaks `news_brief`, `update_magicmirror` detail or an empty model reply on the speech mount; survivors M03 and M12. |
| 14 | Two real failures live on dev, both mounts | pass | Sections 3 and 5. |
| 15 | `run_tests.sh` passes | pass | `2276 passed, 2 skipped, 18 xfailed` (section 8). |
| 16 | Replay matches the M10 baseline; successful answers unchanged | pass | All 122 rows ran. 112 of the 114 baseline rows identical on status and method; the 2 that changed are known flappers (section 9). Model inputs for successful requests identical old to new and voice to non-voice (section 6). |
| 17 | Production untouched, no HA or MA restart, no sound | pass | Production containers keep the ids and start times from before the test (started 00:02 to 00:03 UTC, before the build's deploy); HA up since 2026-11-16 00:28 UTC, MA since 2026-11-04. Production `tool_service` log since 00:40 UTC: 0 lines for `/ops/exec`, `/magicmirror/update`, `/http_check`, `/fetch`; production orchestrator: 0 requests. 17 players checked, none playing, replay reports players unchanged. |
| 18 | Docs describe the signal and the setting | pass | `docs/features/voice-pipeline.md` "The voice signal (sprint M2)", `docs/features/verbose-errors.md` config table, `README.md:259-264`, compose comment. They match what was measured. |

## Findings

### Finding 1 (criterion 6, fail): news on the speech mount speaks the raw tool error

Request, with `tool_service-dev` stopped:

```
POST http://127.0.0.1/dev/voice/api/chat
{"model":"kronk","stream":false,"messages":[{"role":"user","content":"what's the news today"}]}
```

Reply, HTTP 200:

```
[Tool news_brief error: [Errno -3] Temporary failure in name resolution].
```

Same text for "give me the news briefing", "update the news feed", "get the
latest news", "any headlines this morning" and "catch me up on the news"
(7 of 7; traces `a8b84d22ad97…` and the rows in section 5). `/dev/api/chat`
returns the same text without the final full stop.

Cause: `agents._terminal_speech` returns a `news_brief` result as it is
(`agents.py:103`, `if tool == "news_brief": return result`) before it
reaches the friendly fallback, so `tools.execute`'s exception line
`[Tool news_brief error: …]` is spoken. `news_brief` is not in
`errors._SERVICE_BY_TOOL` either. In the scratch copy the reply is
`[Tool news_brief error: All connection attempts failed].` and is identical
in `9b6c435`: M2 did not change this path. No unit test speaks a
`news_brief` failure on the speech mount.

Related, scratch copy only (F16): when tool_service answers the brief with
an HTTP error, `tools._tool_news_brief` puts its `detail` into the sentence
unchanged, in both styles. With a stub detail the speech mount returned
`The news brief isn't available right now — upstream api.weather.gov returned HTTP 503 at http://10.0.0.5:8080/x. Ask me to research the news live if you need it sooner.`
The detail was mine; the real `/news/refresh` handler returns
`detail=str(e)` (`tool_service/main.py:791`), so an exception's text can
reach the speaker the same way through the refresh note.

### Finding 2 (criterion 6, fail): a failed mirror update speaks the preflight detail

Produced by the replay (rows `m4b-update-the-magicmirror`,
`m4b-okay-update-the-magic-mirror-please`), through Home Assistant's
`kronk-dev` pipeline to `/dev/voice/api/chat`. Said: "Update the
magicmirror". Spoken, trace `79f05865e9dcf71941d680692c3c9d6a`:

```
I couldn't update the magic mirror. Mirror preflight failed: SSH key not found at /keys/kronk-mm-update — mount ./secrets/mm.
```

A file path and an operator instruction, on a speaker. Cause:
`_terminal_speech` maps `Could not update the magic mirror: <detail>` to
`I couldn't update the magic mirror. <detail>` in both styles, and the
detail is tool_service's (`Mirror preflight failed: …`,
`tool_service/main.py:1135`). The other preflight details are
`could not reach the mirror at <ssh target>: <ssh error>` and
`could not stage the updater script: <output>`, which would put a host
name and ssh output on the speaker in production. Criterion 8 protects the
music sentences only. The dev container has no key, so nothing left this
host (see "Guard rails"). The same sentence is in the build's own replay
file for these rows.

### Finding 3 (criterion 6, fail, scratch copy): an empty model reply speaks a bracketed debug line

Scratch copy, TestClient, model stub yields no token and no tool call
(F19). `/voice/api/chat` returns

```
[coordinator agent returned no response].
```

and the other mounts the same without the full stop. `agents.py:720`
(`[{agent.name} agent returned no response]`) is yielded as a token in both
styles; the same line exists for forced synthesis (`agents.py:942`) and for
a delegated specialist (`agents.py:971`). Unchanged from `9b6c435`. Not
produced live.

### What passed in criterion 6

Code-authored, on `/voice/api/chat` (live where marked, otherwise scratch
copy; section 6 has the debug twins):

| Failure | Spoken |
| --- | --- |
| Routing raised | Sorry — I couldn't work out where to send that request. I've noted the details. |
| Pipeline crash | Sorry — something went wrong on my end. I've noted the details. |
| Model server unreachable (connect error, read timeout, LiteLLM 503) | Sorry — I couldn't reach my language model. Try again in a moment. |
| Model server refuses (LiteLLM 400) | Sorry — my language model couldn't handle that request. |
| Specialist and coordinator both fail | Sorry — I couldn't reach my language model. Try again in a moment. |
| tool_service down: `play_music` pinned and delegated, `control_music`, `now_playing` (live, 53 requests) | That didn't work — I couldn't get through to the music service. |
| tool_service down: `update_magicmirror` | That didn't work — I couldn't get through to the magic mirror updater. |

Model-written (38 live, section 5): 0 of 38 contain an exception name,
URL, port, status code, JSON, bracket, model name, "errno", "name
resolution", "tool_service", "HTTP", "DNS" or "API".

## Observations

1. **The root span of a failed tool turn is not marked ERROR.** When a
   tool raises, `tools.execute` returns `[Tool … error: …]` and the turn
   ends normally: `end_pipeline` gets no level and no status message
   (scratch copy: `trace_level=None` for F09 to F16), and live the
   `pipeline.shim` span has an empty status for all 47 spoken failures.
   The request is still found in one step, because the httpx child span
   carries `STATUS_CODE_ERROR` with `ConnectError: [Errno -3] Temporary
   failure in name resolution` and TraceQL's `&&` matches across spans.
   So criterion 10 holds, but `{ span.kronk.voice = true && status = error }`
   (one span) finds none of them, and a tool that fails without an HTTP
   call would leave no error span at all. Unchanged from `9b6c435`.
2. **With tool_service down every request has an error span**, because
   `agents.weather_context` (`GET /weather/cached`) fails on each one. In
   that state the "failed spoken requests" query also returns requests
   whose answer was fine.
3. **Three of 38 model-written sentences do not say anything failed:**
   "I do not have the current weather information for your location.",
   "I do not have the current weather forecast for Chicago on Friday.",
   "I do not have that information." Two more blame the listener or the
   wrong thing: "Please check your connection." and "the system had a
   temporary difficulty identifying the devices". The same happens on
   `/dev/api/chat` ("Could you check your internet connection?"), so it is
   the model, not the style. The build's departure 1 stands: instructed,
   not guaranteed.
4. **The friendly fallback says "couldn't get through" for any unmapped
   terminal result,** not only a transport failure. `[play_music error:
   query is required]` (the model sent no query) is spoken as "That didn't
   work — I couldn't get through to the music service." in friendly style.
5. **`errors.is_failed_tool_result` misses some failure shapes:**
   `[Hot tub status unknown: …]`, `[Hot tub status unavailable]`,
   `[Solar energy — <period>] no figure available: <error>…`,
   `[Unknown tool: …]`, `[<tool> error: … is required]`. Those results
   reach the model on the speech mount without the phrasing rule. Read
   from the code, not produced.
6. **Mutation survivors.** M12 (friendly style appends the phrasing rule
   to every tool result, not only failed ones) passes the whole suite:
   `test_system_prompt_is_the_same_in_both_styles` checks the system
   prompt only, so nothing pins that a successful tool result on the
   speech mount is unchanged. M03 (`origin.voice` never reset) also
   passes; each request runs in its own task context, so this one has no
   effect through HTTP.
7. **Protocol errors on the speech mount are JSON.** No user message:
   HTTP 400 `{"detail":"no user message with content"}`; malformed body:
   HTTP 422 with pydantic's JSON. These never enter the pipeline. What a
   puck says then is Home Assistant's choice.
8. **A trailing slash under `/dev/` redirects to production's path.**
   `POST /dev/voice/api/chat/` returns `307` with
   `location: http://127.0.0.1/voice/api/chat` (FastAPI's slash redirect
   does not know about nginx's prefix). Not followed here. An M17 matter,
   not M2.
9. **Leaked planning text on the non-retracting mounts.** One
   `/dev/v1/chat/completions` reply and one streamed `/dev/api/chat` reply
   carried the model's tool-round text before the debug sentence
   ("…6. **Finalize the response:** Generate the tool call.That didn't
   work — the tool failed with …"). Known behaviour of transports that
   cannot retract; the speech mount retracted it in all 101 requests.
10. **Replay row `eval-late-evening-from-radio-on-pandora` (xfail)** spoke
    `call:ask_home{query:<, ", >Play Radio on Pandora<, ", >}<tool_call, >.`
    Tool-call syntax on the speech mount, already marked expected-failure
    in the corpus; no failure path ran.
11. **Concurrent requests queue behind `_llm_lock`.** The 48 requests took
    121 s wall; the slowest waited 121 s. Expected, noted for M8.

## 1. Signal: request content (criterion 2)

`tool_service-dev` stopped, so "play some jazz number N" fails in code and
the reply shows the style: friendly is
`That didn't work — I couldn't get through to the music service.`, debug is
`That didn't work — the tool failed with [Errno -3] Temporary failure in name resolution.`
Each request's trace was read back from Tempo for `kronk.http_route` and
`kronk.voice`.

Tried on `/dev/api/chat`, `/dev/v1/chat/completions` and `/dev/message` to
turn the signal on (41 requests, plus 3 plain controls): body fields
`voice:true`; `profile:"voice"`; `profile:"speech", transport:"voice",
mount:"/voice/api/chat"`; `options:{voice:true, profile:"speech"}`;
`model:"voice"`; `model:"/voice/api/chat"`; `error_style`, `ERROR_STYLE`,
`ERROR_STYLE_VOICE` = `friendly`; headers `X-Kronk-Voice: true`,
`X-Voice: 1`, `X-Profile: speech`, `X-Error-Style: friendly`; headers
`X-Original-URI`, `X-Rewrite-URL`, `X-Forwarded-Prefix`, `X-Forwarded-Uri`,
`X-Original-URL` naming `/voice/api/chat`; `Referer` and a Home Assistant
`User-Agent`; `baggage: kronk.voice=true` and `tracestate`; a system
message with the origin stamp `[kronk-origin] device=abc123 area=Office`;
a system message saying it is a voice request; the same in the user text;
query string `?voice=true&profile=speech`.

Result: 44 of 44 debug, `kronk.voice` false on every trace. (One `/dev/v1/chat/completions` reply had leaked planning text before the debug sentence; observation 9.)

Tried on `/dev/voice/api/chat` to turn it off (12 requests, plus one plain
control): `voice:false`; `profile:"display", transport:"shim",
mount:"/api/chat"`; `options:{voice:false}`; `model:"display"`;
`error_style` fields = `debug`; headers `X-Kronk-Voice: false`,
`X-Voice: 0`, `X-Profile: display`, `X-Error-Style: debug`; original-URI
headers naming `/api/chat`; `baggage: kronk.voice=false`; no stamp and a
system message saying it is not voice; the same in the user text;
`?voice=false&profile=display`; `stream:true`.

Result: 13 of 13 friendly, `kronk.voice` true on every trace.

## 2. Signal: paths through nginx's `/dev/` (criterion 2)

Sent as written (no client-side normalisation). A reply's style always
matches the route that handled it. Variants that nginx normalises to
`/dev/voice/api/chat` are the speech mount and are friendly; that is the
mount deciding, not the content. No variant made a non-voice route answer
friendly or the voice route answer debug. `/dev%2Fvoice/api/chat` was not
sent: my driver refuses any path that does not start with `/dev/`, so
that nothing could fall through to production's catch-all.

| Path sent (as is) | HTTP | Handled as (trace `kronk.http_route`) | `kronk.voice` | Style of the reply | Trace |
| --- | --- | --- | --- | --- | --- |
| `/dev//voice/api/chat` | 200 | `/voice/api/chat` | true | friendly | `05b5a523d604` |
| `/dev/voice//api/chat` | 200 | `/voice/api/chat` | true | friendly | `0a1faa12a779` |
| `/dev/voice/api//chat` | 200 | `/voice/api/chat` | true | friendly | `b0267f147297` |
| `/dev///voice///api///chat` | 200 | `/voice/api/chat` | true | friendly | `ae4844c69bec` |
| `/dev/voice/api/chat/` | 307 | not handled | | no reply | |
| `/dev/voice/api/chat//` | 307 | not handled | | no reply | |
| `/dev/VOICE/api/chat` | 404 | not handled | | no reply | |
| `/dev/Voice/Api/Chat` | 404 | not handled | | no reply | |
| `/dev/voice/API/CHAT` | 404 | not handled | | no reply | |
| `/dev/voice%2Fapi%2Fchat` | 200 | `/voice/api/chat` | true | friendly | `bd2b5a110896` |
| `/dev/voice%2fapi/chat` | 200 | `/voice/api/chat` | true | friendly | `f9ad058c95d9` |
| `/dev/%2Fvoice/api/chat` | 200 | `/voice/api/chat` | true | friendly | `b66c3e36adcb` |
| `/dev/voice%252Fapi%252Fchat` | 404 | not handled | | no reply | |
| `/dev/%76oice/api/chat` | 200 | `/voice/api/chat` | true | friendly | `7eaec3b5ce31` |
| `/dev/voice/api/%63hat` | 200 | `/voice/api/chat` | true | friendly | `8199e0bea356` |
| `/dev/./voice/api/chat` | 200 | `/voice/api/chat` | true | friendly | `b8bab92c526c` |
| `/dev/api/../voice/api/chat` | 200 | `/voice/api/chat` | true | friendly | `ae0163f5cebd` |
| `/dev/voice/api/chat/../../../api/chat` | 200 | `/api/chat` | false | debug | `1cd33bd0aa5c` |
| `/dev/api/chat/../../voice/api/chat` | 200 | `/voice/api/chat` | true | friendly | `2a931a30fa78` |
| `/dev/voice/../api/chat` | 200 | `/api/chat` | false | debug | `79fe39f520dc` |
| `/dev/voice/..%2Fapi/chat` | 200 | `/api/chat` | false | debug | `50649394e874` |
| `/dev/api/chat/voice` | 404 | not handled | | no reply | |
| `/dev/api/chat;voice` | 404 | not handled | | no reply | |
| `/dev/api/chat%00/voice` | 400 | not handled | | no reply | |
| `/dev/api/chat%3Fvoice` | 404 | not handled | | no reply | |
| `/dev//api/chat` | 200 | `/api/chat` | false | debug | `4f1b12986166` |
| `/dev/api/chat/` | 307 | not handled | | no reply | |
| `/dev/API/chat` | 404 | not handled | | no reply | |
| `/dev/api%2Fchat` | 200 | `/api/chat` | false | debug | `f6325a1c8a71` |
| `/dev/voice/v1/chat/completions` | 404 | not handled | | no reply | |
| `/dev/voice/message` | 404 | not handled | | no reply | |
| `/dev/dev/voice/api/chat` | 404 | not handled | | no reply | |
| `/dev/voice/api/chat?x=/api/chat` | 200 | `/voice/api/chat` | true | friendly | `4c0fed44f92e` |
| `/dev/api/chat?x=/voice/api/chat` | 200 | `/api/chat` | false | debug | `664574b2890b` |
| `/dev/api/chat#/voice/api/chat` | 200 | `/api/chat` | false | debug | `0a073b0a1f3c` |
| `/dev/v1/chat/completions/../../../voice/api/chat` | 200 | `/voice/api/chat` | true | friendly | `a09c94a1f650` |
| `/dev//v1/chat/completions` | 200 | `/v1/chat/completions` | false | debug | `30f9f9b8a42b` |
| `/dev/v1//chat/completions/` | 307 | not handled | | no reply | |
| `/dev//message` | 200 | `/message` | false | debug | `427f9d7f767d` |
| `/dev/message/` | 307 | not handled | | no reply | |

## 3. Signal: concurrent requests (criterion 3)

24 pairs, all 48 requests released together by a barrier, `tool_service-dev`
stopped, a different genre in each sentence ("play some <genre> music pair
N voice side" / "… other side") so each reply is tied to its own trace.
Other side: 14 on `/dev/api/chat`, 5 on `/dev/v1/chat/completions`, 5 on
`/dev/message`; a third of the voice requests and a quarter of the others
asked for streaming. Wall time 121.2 s. 48 of 48 got the style of their
own mount; `kronk.voice` on each trace agrees.

| Pair | Voice side: s, style, trace | Other side: mount, s, style, trace |
| --- | --- | --- |
| 00 | 4.04 stream, friendly, voice=true, `e200bfe0f8d5` | `/dev/api/chat` stream, 22.28, debug, voice=false, `69192644d25c` |
| 01 | 26.26, friendly, voice=true, `1be7a7ae0329` | `/dev/api/chat`, 25.22, debug, voice=false, `6bef4d92826e` |
| 02 | 2.91, friendly, voice=true, `47e7b115b6d3` | `/dev/api/chat`, 18.25, debug, voice=false, `869c764d0a1e` |
| 03 | 13.17 stream, friendly, voice=true, `70a071c56e2c` | `/dev/api/chat`, 6.61, debug, voice=false, `69a29751bec0` |
| 04 | 29.92, friendly, voice=true, `f42f3587b045` | `/dev/api/chat` stream, 88.82, debug, voice=false, `1ed4494f4b6e` |
| 05 | 19.46, friendly, voice=true, `6837f0e33918` | `/dev/api/chat`, 15.72, debug, voice=false, `f74ca73fdc90` |
| 06 | 50.79 stream, friendly, voice=true, `b94f1f21ce71` | `/dev/api/chat`, 87.24, debug, voice=false, `4bfa0b0df37a` |
| 07 | 28.8, friendly, voice=true, `53e30366b7fc` | `/dev/api/chat`, 64.51, debug, voice=false, `7318442435b4` |
| 08 | 36.58, friendly, voice=true, `6125e96e57b8` | `/dev/api/chat` stream, 91.79, debug, voice=false, `ab5620decbb5` |
| 09 | 67.26 stream, friendly, voice=true, `46520c372f80` | `/dev/api/chat`, 53.44, debug, voice=false, `23d8b18f110a` |
| 10 | 41.39, friendly, voice=true, `875987347615` | `/dev/api/chat`, 34.69, debug, voice=false, `ec7e48e3743b` |
| 11 | 102.27, friendly, voice=true, `4a2f92be07d9` | `/dev/api/chat`, 32.47, debug, voice=false, `78d4b81a296f` |
| 12 | 58.3 stream, friendly, voice=true, `b9254cbb5e25` | `/dev/api/chat` stream, 94.46, debug, voice=false, `5e83682e896c` |
| 13 | 45.6, friendly, voice=true, `6d969472c989` | `/dev/api/chat`, 48.0, debug, voice=false, `3ba4e193336c` |
| 14 | 69.82, friendly, voice=true, `5902f911bedd` | `/dev/v1/chat/completions`, 38.68, debug, voice=false, `d2dbae11c91e` |
| 15 | 44.05 stream, friendly, voice=true, `4b9da0000013` | `/dev/v1/chat/completions`, 61.63, debug, voice=false, `4ed774c4731e` |
| 16 | 74.25, friendly, voice=true, `fc26884943fd` | `/dev/v1/chat/completions` stream, 97.27, debug, voice=false, `851d53d15afb` |
| 17 | 68.48, friendly, voice=true, `098f6b3d9b83` | `/dev/v1/chat/completions`, 72.64, debug, voice=false, `d462debc4091` |
| 18 | 81.82 stream, friendly, voice=true, `3c78b5d6f4d3` | `/dev/v1/chat/completions`, 84.47, debug, voice=false, `70406dbce695` |
| 19 | 76.06, friendly, voice=true, `13f7220ffc40` | `/dev/message`, 121.23, debug, voice=false, `84cfc9dbc89f` |
| 20 | 78.86, friendly, voice=true, `1662ec2f4a1a` | `/dev/message` stream, 111.84, debug, voice=false, `4dca51c17e65` |
| 21 | 100.11 stream, friendly, voice=true, `9ac8d1aa14bb` | `/dev/message`, 119.69, debug, voice=false, `d78b40144793` |
| 22 | 105.31, friendly, voice=true, `401e191efdc3` | `/dev/message`, 115.01, debug, voice=false, `d32edfcc3600` |
| 23 | 107.43, friendly, voice=true, `e23ca4bb6cc6` | `/dev/message`, 10.8, debug, voice=false, `863f8a3dacb6` |

## 4. The trace attribute in Tempo (criterion 5)

Window 2026-11-17 00:42:00 to 01:00:30 UTC, `localhost:3200/api/search`:

| TraceQL | Traces | Expected |
| --- | --- | --- |
| `{ span.kronk.voice = true && resource.service.name = "kronk-orchestrator-dev" }` | 102 | 101 driver requests handled by `/voice/api/chat` + 1 control |
| `{ span.kronk.voice = false && resource.service.name = "kronk-orchestrator-dev" }` | 102 | 102 driver requests on the other routes |
| `{ span.kronk.voice = true && resource.service.name = "kronk-orchestrator-dev" } && { status = error }` | 98 | the 98 driver voice traces with an error span |
| `{ span.kronk.voice = true }` | 102 | no other service has it |
| `{ span.kronk.voice = true && resource.service.name = "kronk-orchestrator" }` | 0 | production not deployed, not used |

Root span of a voice request (trace `a8b84d22ad9766e2c797d9274e6ff6f1`):
`pipeline.shim` with `kronk.transport=voice`,
`kronk.http_route=/voice/api/chat`, `kronk.voice=true`, `kronk.rid=9060d640`.

## 5. Spoken failures, live (criteria 6, 7, 10, 14)

`tool_service-dev` stopped 00:43:30 UTC, started 00:57:47 UTC, healthy
within 20 s. All requests to `http://127.0.0.1/dev/voice/api/chat`,
`stream:false`, one user message. Every reply was HTTP 200. Rows 1 to 38
are model-written; 39 to 47 are code-authored. Cause columns are from the
`orchestrator-dev` log (`tool_error` events for the request's trace id) and
from the trace's spans with `STATUS_CODE_ERROR` (path on
`http://tool_service-dev:8103`; each span's status message is
`ConnectError: [Errno -3] Temporary failure in name resolution`).

| # | Said | Spoken reply (verbatim) | Route → tools | Cause in log (`tool_error`) | Error span in Tempo | Trace |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | what's the weather tomorrow | I'm sorry, but I couldn't fetch the weather information as there was an issue with connecting to the weather service. | home → home:get_weather | get_weather: [Errno -3] Temporary failure in name resolution | `GET /weather?location=Laurel%2C%20MD` | `d130d825ed06` |
| 2 | what's the weather in Paris tomorrow | I'm sorry, I couldn't get the weather for Paris because there was a temporary issue connecting to the weather service. | home → home:get_weather | get_weather: [Errno -3] Temporary failure in name resolution | `GET /weather?location=Paris` | `b05ba555a914` |
| 3 | will it rain this weekend | I am unable to retrieve the weather forecast at this time due to a connection issue with the weather service. | direct → coordinator:ask_home, home:get_weather | get_weather: [Errno -3] Temporary failure in name resolution | `GET /weather?location=Laurel%2C%20MD` | `c51fe4a78487` |
| 4 | do I need an umbrella today | I do not have the current weather information for your location. | direct → coordinator:ask_home, home:get_weather | get_weather: [Errno -3] Temporary failure in name resolution | `GET /weather?location=Laurel%2C%20MD` | `cbff11290264` |
| 5 | how cold is it going to get tonight | I'm unable to retrieve the weather forecast for tonight in Laurel, MD due to a technical issue. | direct → coordinator:ask_home, home:get_weather | get_weather: [Errno -3] Temporary failure in name resolution | `GET /weather?location=Laurel%2C%20MD` | `b24d3fa6845b` |
| 6 | what's the forecast for Chicago on Friday | I do not have the current weather forecast for Chicago on Friday. | direct → coordinator:ask_home, home:get_weather, coordinator:ask_research, research:web_search | get_weather: [Errno -3] Temporary failure in name resolution; web_search: [Errno -3] Temporary failure in name resolution | `GET /search?q=Chicago%20weather%20forecast%20for%20Friday&count=5; GET /weather?location=Chicago` | `d2d7dea34e66` |
| 7 | is it going to snow this week | I am unable to check the snow forecast this week due to a technical issue accessing the weather data. | direct → coordinator:ask_home, home:get_weather, coordinator:ask_research, research:web_search | get_weather: [Errno -3] Temporary failure in name resolution; web_search: [Errno -3] Temporary failure in name resolution | `GET /search?q=weather%20forecast%20for%20Laurel%2C%20MD%20this%20week&count=5; GET /weather?location=Laurel%2C%20MD` | `25da4d5efdb3` |
| 8 | what's the temperature outside right now | The weather lookup service experienced a temporary connection failure, and I was unable to retrieve the current temperature. | direct → coordinator:ask_home, home:get_weather | get_weather: [Errno -3] Temporary failure in name resolution | `GET /weather?location=Laurel%2C%20MD` | `d1542dfd3617` |
| 9 | what's on my shopping list | I'm sorry, I couldn't retrieve your shopping list right now. | direct → coordinator:ask_home, home:shopping_list_view | shopping_list_view: [Errno -3] Temporary failure in name resolution | `GET /shopping_list` | `548043c6a2f2` |
| 10 | add milk to the shopping list | I am sorry, adding milk to the shopping list failed due to a temporary connection issue. | direct → coordinator:ask_home, home:shopping_list_add | shopping_list_add: [Errno -3] Temporary failure in name resolution | `POST /shopping_list` | `8cbf0dbfb024` |
| 11 | add eggs and butter to my shopping list | I am sorry, but there was a temporary connection issue and I was unable to add eggs and butter to your list. Please check your connection. | direct → coordinator:ask_home, home:shopping_list_add | shopping_list_add: [Errno -3] Temporary failure in name resolution | `POST /shopping_list` | `cba4d0246adf` |
| 12 | remove bread from the shopping list | A network issue prevented me from removing bread from the shopping list. | direct → coordinator:ask_home, home:shopping_list_remove | shopping_list_remove: [Errno -3] Temporary failure in name resolution | `DELETE /shopping_list/bread` | `2d0e2543229b` |
| 13 | is coffee on the shopping list | I am having trouble accessing your shopping list right now. | direct → coordinator:ask_home, home:shopping_list_view | shopping_list_view: [Errno -3] Temporary failure in name resolution | `GET /shopping_list` | `332ea4160373` |
| 14 | read me the shopping list | I am sorry, but I was unable to retrieve your shopping list right now due to a temporary connection issue. | direct → coordinator:ask_home, home:shopping_list_view | shopping_list_view: [Errno -3] Temporary failure in name resolution | `GET /shopping_list` | `3a9f11134cf6` |
| 15 | put bananas on the grocery list | I couldn't add bananas to your list; there was a temporary failure connecting to the service. | direct → coordinator:ask_home, home:shopping_list_add | shopping_list_add: [Errno -3] Temporary failure in name resolution | `POST /shopping_list` | `718b6ea4405e` |
| 16 | how many things are on my shopping list | I was unable to access your shopping list because of a temporary network failure. | direct → coordinator:ask_home, home:shopping_list_view | shopping_list_view: [Errno -3] Temporary failure in name resolution | `GET /shopping_list` | `fa4e0c32fbfa` |
| 17 | how are the solar panels doing | I am sorry, but I'm having trouble connecting to the solar panel system to check their current performance. | direct → coordinator:ask_home, home:solar_status | solar_status: [Errno -3] Temporary failure in name resolution | `GET /solar/status` | `197fc80d94da` |
| 18 | how much solar energy did we make today | I'm sorry, but I couldn't connect to the solar monitoring system to retrieve that information. | direct → coordinator:ask_home, home:solar_energy | solar_energy: [Errno -3] Temporary failure in name resolution | `GET /solar/energy?period=today` | `1ec974d27047` |
| 19 | is the solar array working | The solar monitoring system is currently unavailable. I cannot confirm if the array is working. | direct → coordinator:ask_home, home:solar_status, home:solar_energy | solar_energy: [Errno -3] Temporary failure in name resolution; solar_status: [Errno -3] Temporary failure in name resolution | `GET /solar/energy?period=today; GET /solar/status` | `53938ce225de` |
| 20 | how much power did the panels produce this month | I am currently having trouble connecting to the solar monitoring system. | direct → coordinator:ask_home, home:solar_energy | solar_energy: [Errno -3] Temporary failure in name resolution | `GET /solar/energy?period=month` | `fd8049540b41` |
| 21 | are any of the solar inverters failing | I'm having trouble checking the solar status right now. | direct → coordinator:ask_home, home:solar_status | solar_status: [Errno -3] Temporary failure in name resolution | `GET /solar/status` | `067ee574953e` |
| 22 | what's the solar production right now | I'm sorry, the system failed to retrieve the current solar energy production due to a temporary connection issue. | direct → coordinator:ask_home, home:solar_energy | solar_energy: [Errno -3] Temporary failure in name resolution | `GET /solar/energy?period=today` | `95d637142aae` |
| 23 | what's the hot tub temperature | I'm sorry, I encountered a temporary system issue and couldn't check the hot tub's current temperature. | direct → coordinator:ask_home, home:query_hottub | query_hottub: [Errno -3] Temporary failure in name resolution | `GET /hottub` | `a929c33e4f02` |
| 24 | is the hot tub ready | I'm sorry, I couldn't connect to the hot tub's system to check its status. | direct → coordinator:ask_home, home:query_hottub | query_hottub: [Errno -3] Temporary failure in name resolution | `GET /hottub` | `862fa7ec2565` |
| 25 | how hot is the hot tub right now | I was unable to retrieve the current temperature for the hot tub because the system experienced a temporary connection failure. | direct → coordinator:ask_home, home:query_hottub | query_hottub: [Errno -3] Temporary failure in name resolution | `GET /hottub` | `c33893656215` |
| 26 | is the hot tub heater on | The network connection is having a temporary issue, so I couldn't check the hot tub status. | direct → coordinator:ask_home, home:query_hottub | query_hottub: [Errno -3] Temporary failure in name resolution | `GET /hottub` | `311ca7e6c05d` |
| 27 | search the web for the latest news about the James Webb telescope | I apologize, but I was unable to search the web for the latest news about the James Webb telescope due to a temporary network failure. | research → research:web_search | web_search: [Errno -3] Temporary failure in name resolution | `GET /search?q=latest%20news%20James%20Webb%20telescope&count=3` | `6b2fbeb4feb4` |
| 28 | who won the Formula One race last weekend | I am unable to retrieve that information because the research tool is currently experiencing connectivity issues. | direct → coordinator:ask_research, research:web_search | web_search: [Errno -3] Temporary failure in name resolution | `GET /search?q=most%20recent%20Formula%201%20race%20winner%20November%202026&count=5; GET /search?q=who%20won%20the%20formula%20one%20race%20last%20weekend%20november%202026&count=5` | `019590076198` |
| 29 | look up the current price of bitcoin | I am able to search the web for current information, but the search tool is currently failing to retrieve live data like the current price of Bitcoin. | research → research:web_search | web_search: [Errno -3] Temporary failure in name resolution | `GET /search?q=current%20price%20of%20bitcoin&count=5` | `8ffe1c144806` |
| 30 | search for reviews of the Framework laptop 16 | I could not find reviews for the Framework laptop 16 due to a temporary network issue. I will try again if you ask later. | direct → coordinator:ask_research, research:web_search | web_search: [Errno -3] Temporary failure in name resolution | `GET /search?q=Framework%20laptop%2016%20reviews&count=5` | `949a46e83ed4` |
| 31 | what is the population of Lisbon, look it up | I apologize, but I was unable to look up the population of Lisbon due to a temporary technical failure with my search tool. | research → research:web_search | web_search: [Errno -3] Temporary failure in name resolution | `GET /search?q=population%20of%20Lisbon&count=5` | `d9fd271a0c55` |
| 32 | google the release date of the next Zelda game | I was unable to find the release date for the next Zelda game because the search function is experiencing a temporary connection issue. | research → research:web_search | web_search: [Errno -3] Temporary failure in name resolution | `GET /search?q=next%20Zelda%20game%20release%20date&count=5` | `f0d76f696975` |
| 33 | find out online when the next solar eclipse is | I do not have that information. | direct → coordinator:ask_research, research:web_search | web_search: [Errno -3] Temporary failure in name resolution | `GET /search?q=next%20solar%20eclipse%20date&count=5` | `dc5181cfeaab` |
| 34 | search the internet for a good sourdough recipe | I encountered a technical issue while searching for a sourdough recipe. Would you like me to try the search again? | direct → coordinator:ask_research, research:web_search | web_search: [Errno -3] Temporary failure in name resolution | `GET /search?q=best%20sourdough%20recipe%20for%20beginners&count=5` | `0b18f08098d3` |
| 35 | what's the weather tomorrow and what's on my shopping list | I couldn't retrieve the weather forecast or your shopping list right now because of a temporary connection failure. | home → home:get_weather, home:shopping_list_view | get_weather: [Errno -3] Temporary failure in name resolution; shopping_list_view: [Errno -3] Temporary failure in name resolution | `GET /shopping_list; GET /weather?location=Laurel%2C%20MD` | `ab6be031eacd` |
| 36 | is the hot tub warm and will it rain tonight | I could not retrieve the hot tub status due to a temporary connectivity failure. I also encountered a temporary service failure when trying to determine the rain forecast for Laurel, MD. | direct → coordinator:ask_home, home:query_hottub, home:get_weather | get_weather: [Errno -3] Temporary failure in name resolution; query_hottub: [Errno -3] Temporary failure in name resolution | `GET /hottub; GET /weather?location=Laurel%2C%20MD` | `9c33b4fd30d8` |
| 37 | add sunscreen to the shopping list and tell me if it's sunny tomorrow | I am unable to add sunscreen to the shopping list or check if it will be sunny tomorrow because the network connection to those services is currently failing. | direct → coordinator:ask_home, home:shopping_list_add, home:get_weather | get_weather: [Errno -3] Temporary failure in name resolution; shopping_list_add: [Errno -3] Temporary failure in name resolution | `GET /weather?location=Laurel%2C%20MD; POST /shopping_list` | `5b02d142d591` |
| 38 | check the solar panels and the hot tub | I was unable to check the status of the solar panels or the hot tub right now because the system had a temporary difficulty identifying the devices. | direct → coordinator:ask_home, home:solar_status, home:query_hottub | query_hottub: [Errno -3] Temporary failure in name resolution; solar_status: [Errno -3] Temporary failure in name resolution | `GET /hottub; GET /solar/status` | `251b7254c419` |
| 39 | what's the news today | [Tool news_brief error: [Errno -3] Temporary failure in name resolution]. | direct → coordinator:news_brief | news_brief: [Errno -3] Temporary failure in name resolution | `GET /news/brief` | `a8b84d22ad97` |
| 40 | give me the news briefing | [Tool news_brief error: [Errno -3] Temporary failure in name resolution]. | direct → coordinator:news_brief | news_brief: [Errno -3] Temporary failure in name resolution | `GET /news/brief` | `f8528def28a4` |
| 41 | what song is playing | That didn't work — I couldn't get through to the music service. | home → home:now_playing | now_playing: [Errno -3] Temporary failure in name resolution | `POST /music/now_playing` | `081a3b582524` |
| 42 | pause the music | That didn't work — I couldn't get through to the music service. | home → home:control_music | control_music: [Errno -3] Temporary failure in name resolution | `POST /music/control` | `c0ecded93dd2` |
| 43 | what's the news today | [Tool news_brief error: [Errno -3] Temporary failure in name resolution]. | direct → coordinator:news_brief | news_brief: [Errno -3] Temporary failure in name resolution | `GET /news/brief` | `f3933ecc37de` |
| 44 | update the news feed | [Tool news_brief error: [Errno -3] Temporary failure in name resolution]. | direct → coordinator:news_brief | news_brief: [Errno -3] Temporary failure in name resolution | `GET /news/brief; POST /news/refresh` | `c767db005b2f` |
| 45 | get the latest news | [Tool news_brief error: [Errno -3] Temporary failure in name resolution]. | direct → coordinator:news_brief | news_brief: [Errno -3] Temporary failure in name resolution | `GET /news/brief; POST /news/refresh` | `a6180f6b2bf8` |
| 46 | any headlines this morning | [Tool news_brief error: [Errno -3] Temporary failure in name resolution]. | direct → coordinator:news_brief | news_brief: [Errno -3] Temporary failure in name resolution | `GET /news/brief` | `e4159966564b` |
| 47 | catch me up on the news | [Tool news_brief error: [Errno -3] Temporary failure in name resolution]. | direct → coordinator:news_brief | news_brief: [Errno -3] Temporary failure in name resolution | `GET /news/brief` | `287025a5ac42` |

Technical text in the 38 model-written replies: 0. Rows 39, 40 and 43 to
47 are finding 1.

The same sentences on `/dev/api/chat` (debug style), for comparison:

| Said | `/dev/api/chat` reply (verbatim) | Trace |
| --- | --- | --- |
| what's the weather tomorrow | I'm having trouble connecting to the weather service right now. Would you like me to check the weather for a different location? | `d9c67a7e8c87` |
| how cold is it going to get tonight | I do not have access to a current weather forecast for tonight. | `41763d7014a0` |
| what's on my shopping list | I'm sorry, I ran into a temporary network issue and couldn't retrieve your shopping list right now. Please try again in a moment. | `ab2a06d03412` |
| is coffee on the shopping list | I'm sorry, I ran into a temporary technical error and couldn't check your shopping list. | `ea7c835db48e` |
| how are the solar panels doing | I am having trouble connecting to the solar system right now. Could you check your internet connection? | `500268bc53ea` |
| are any of the solar inverters failing | I am having trouble connecting to the solar monitoring system and cannot check for any inverter failures at this time. | `220dc6dfabbe` |
| how hot is the hot tub right now | I am having trouble connecting to the hot tub's system right now. Please try your request again later. | `31c102bff94d` |
| look up the current price of bitcoin | I apologize, I am currently unable to provide the real-time price of Bitcoin due to technical difficulties accessing current financial data. | `790c48fb7d2b` |
| find out online when the next solar eclipse is | I don't have that information at the moment. | `50405c6b88de` |
| add sunscreen to the shopping list and tell me if it's sunny tomorrow | I am unable to add sunscreen to your shopping list and I cannot access the weather forecast for tomorrow due to a technical error. | `5c7c8443a197` |
| what song is playing | That didn't work — the tool failed with [Errno -3] Temporary failure in name resolution. | `dde4345f8596` |
| what's the news today | [Tool news_brief error: [Errno -3] Temporary failure in name resolution] | `78d2b726472a` |
| update the news feed | [Tool news_brief error: [Errno -3] Temporary failure in name resolution] | `d745c8673f85` |
| get the latest news | [Tool news_brief error: [Errno -3] Temporary failure in name resolution] | `1f6c240f21ec` |
| any headlines this morning | [Tool news_brief error: [Errno -3] Temporary failure in name resolution] | `242b7202438b` |
| catch me up on the news | [Tool news_brief error: [Errno -3] Temporary failure in name resolution] | `3741823db6ec` |

Controls after `tool_service-dev` was started again:

| Mount | Said | Reply | s | `kronk.voice` | Trace |
| --- | --- | --- | --- | --- | --- |
| `/dev/voice/api/chat` | what's the weather tomorrow | Tomorrow, Wednesday, is expected to be Sunny with a high near 64°F. The wind will be around 3 mph from the west. | 4.95 | true | `7d4b3e5aa211` |
| `/dev/voice/api/chat` | what's on my shopping list | Your shopping list is currently empty. | 7.21 | true | `9a453ee93d20` |
| `/dev/voice/api/chat` | what is the capital of France | Paris. | 0.14 | true | `2fb18b39cfbe` |
| `/dev/api/chat` | what's the weather tomorrow | The weather tomorrow, Wednesday, will be sunny with a high near 64°F and a light west wind. | 3.97 | false | `5691b63e07b6` |
| `/dev/api/chat` | what's on my shopping list | Your shopping list is currently empty. | 4.35 | false | `084c1d43724b` |
| `/dev/api/chat` | what is the capital of France | Paris is the capital of France. | 0.17 | false | `4a2d8de2d073` |
| `/dev/v1/chat/completions` | what's the weather tomorrow | Tomorrow in Laurel, MD will be sunny with a high near 64°F. | 4.3 | false | `dc196634f0b8` |
| `/dev/v1/chat/completions` | what's on my shopping list | Your shopping list is currently empty. | 4.23 | false | `2681c39cbae8` |
| `/dev/v1/chat/completions` | what is the capital of France | Paris | 1.3 | false | `9f3ade548d25` |
| `/dev/message` | what's the weather tomorrow | Tomorrow, Wednesday, will be sunny with a high of 64°F and a light west wind around 3 mph. | 5.61 | false | `7cc1876a5364` |
| `/dev/message` | what's on my shopping list | Your shopping list is currently empty. | 7.81 | false | `7a7998b84cd6` |
| `/dev/message` | what is the capital of France | The capital of France is Paris. | 1.88 | false | `c6e721a715bf` |

## 6. Scratch copies through TestClient (criteria 4, 6, 8, 9, 11, 16)

Two scratch copies (`git archive` of `9b6c435` and of `HEAD`), each driven
through FastAPI's TestClient by the same harness: router, model
(`agents.llm.stream`) and tool_service (`tools.httpx.AsyncClient`) stubbed,
request id fixed to `abcd1234`, clock fixed, every IPv4/IPv6 lookup,
connect and send patched to raise (0 attempts recorded in either run).
Each scenario ran on `/voice/api/chat`, `/api/chat`,
`/v1/chat/completions` (each streaming and not) and `/message`.

| Scenario | `/voice/api/chat` (tree) | `/voice/api/chat` (`9b6c435`) | `/api/chat`, `/v1/chat/completions`, `/message` (tree = `9b6c435`) | Trace status (tree) |
| --- | --- | --- | --- | --- |
| F01 routing raises | Sorry — I couldn't work out where to send that request. I've noted the details. | Error: routing failed: router exploded at http://litellm:4000 (HTTP 500) [rid abcd1234]. | Error: routing failed: router exploded at http://litellm:4000 (HTTP 500) [rid abcd1234] | ERROR: routing: router exploded at http://litellm:4000 (HTTP 500) |
| F02 pipeline crash | Sorry — something went wrong on my end. I've noted the details. | Error: the pipeline failed unexpectedly (KeyError: 'stages') [rid abcd1234]. | Error: the pipeline failed unexpectedly (KeyError: 'stages') [rid abcd1234] | ERROR: KeyError: 'stages' |
| F03 model unreachable (connect) | Sorry — I couldn't reach my language model. Try again in a moment. | [coordinator agent error: All connection attempts failed]. | [coordinator agent error: All connection attempts failed] | ERROR: coordinator: [coordinator agent error: All connection attempts failed] |
| F04 model unreachable (timeout) | Sorry — I couldn't reach my language model. Try again in a moment. | [coordinator agent error: timed out]. | [coordinator agent error: timed out] | ERROR: coordinator: [coordinator agent error: timed out] |
| F05 model server 503 | Sorry — I couldn't reach my language model. Try again in a moment. | [coordinator agent error: LiteLLM returned 503: litellm.ServiceUnavailableError: no healthy upstream http://host.docker.internal:11434]. | [coordinator agent error: LiteLLM returned 503: litellm.ServiceUnavailableError: no healthy upstream http://host.docker.internal:11434] | ERROR: coordinator: [coordinator agent error: LiteLLM returned 503: litellm.ServiceUnavailableError: no healthy upstream http://host.docker.internal:11434] |
| F06 model server refuses 400 | Sorry — my language model couldn't handle that request. | [coordinator agent error: LiteLLM returned 400: Invalid model name passed in model=gemma-4-e4b]. | [coordinator agent error: LiteLLM returned 400: Invalid model name passed in model=gemma-4-e4b] | ERROR: coordinator: [coordinator agent error: LiteLLM returned 400: Invalid model name passed in model=gemma-4-e4b] |
| F07 specialist fails, coordinator answers | The home controls aren't working right now. | same | The home controls aren't working right now. | ERROR: home specialist: [home agent error: LiteLLM returned 500: home model crashed] |
| F08 specialist and coordinator both fail | Sorry — I couldn't reach my language model. Try again in a moment. | [coordinator agent error: All connection attempts failed]. | [coordinator agent error: All connection attempts failed] | ERROR: coordinator: [coordinator agent error: All connection attempts failed] |
| F09 tool_service down, play_music (pinned home) | That didn't work — I couldn't get through to the music service. | That didn't work — the tool failed with All connection attempts failed. | That didn't work — the tool failed with All connection attempts failed. | none |
| F10 tool_service down, play_music (delegated) | That didn't work — I couldn't get through to the music service. | That didn't work — the tool failed with All connection attempts failed. | That didn't work — the tool failed with All connection attempts failed. | none |
| F11 tool_service down, control_music | That didn't work — I couldn't get through to the music service. | That didn't work — the tool failed with All connection attempts failed. | That didn't work — the tool failed with All connection attempts failed. | none |
| F12 tool_service down, update_magicmirror | That didn't work — I couldn't get through to the magic mirror updater. | That didn't work — the tool failed with All connection attempts failed. | That didn't work — the tool failed with All connection attempts failed. | none |
| F13 tool_service down, news_brief | [Tool news_brief error: All connection attempts failed]. | same | [Tool news_brief error: All connection attempts failed] | none |
| F14 tool_service down, get_weather (non-terminal) | The weather service isn't answering right now. | same | The weather service isn't answering right now. | none |
| F15 tool_service HTTP 500, get_weather | The weather service isn't answering right now. | same | The weather service isn't answering right now. | none |
| F16 tool_service HTTP 500, news_brief | The news brief isn't available right now — upstream api.weather.gov returned HTTP 503 at http://10.0.0.5:8080/x. Ask me to research the news live if you need it sooner. | same | The news brief isn't available right now — upstream api.weather.gov returned HTTP 503 at http://10.0.0.5:8080/x. Ask me to research the news live if you need it sooner. | none |
| F17 music failure with its own sentence (play) | I couldn't play that. nope. | same | I couldn't play that. nope | none |
| F18 music failure with its own sentence (control) | I couldn't do that. Nothing is playing on the Office speaker. | same | I couldn't do that. Nothing is playing on the Office speaker. | none |
| F19 model returns nothing | [coordinator agent returned no response]. | same | [coordinator agent returned no response] | none |
| S01 direct answer | Paris. | same | Paris. | none |
| S02 home, get_weather ok | Sunny, high of 64. | same | Sunny, high of 64. | none |
| S03 delegated ask_home, shopping list ok | Milk and eggs. | same | Milk and eggs. | none |
| S04 research, web answer | Frank Herbert. | same | Frank Herbert. | none |
| S05 news brief ok | Evening news brief, generated 6:00 PM: ⏎  ⏎ Nothing happened. | same | Evening news brief, generated 6:00 PM: ⏎  ⏎ Nothing happened. | none |

Old against new on the three non-voice mounts (5 mount variants, 24
scenarios, 6 fields each: HTTP status, reply, the full list of model calls
with messages and tool menus, trace level, trace status message, trace
output): **720 identical, 0 different.**

Old against new on `/voice/api/chat`:

- Replies differ exactly where the table shows (F01 to F06, F08 to F12).
- Model inputs differ in two places only. F07/F08: the specialist-failure
  block in the coordinator's system prompt ends with the friendly
  instruction ("tell the user it didn't work in one short natural sentence
  that says what kind of thing failed — never mention HTTP status codes,
  error names, URLs, ports, model names or other technical identifiers")
  instead of "report the failure and its cause to the user plainly". F14/F15:
  the failed tool result has `errors.FRIENDLY_TOOL_PHRASING` appended. The
  failure detail itself is unchanged in both.
- S01 to S05 (successful requests): reply, system prompts, messages and
  tool menus identical to `9b6c435`, streaming and not; and in the tree the
  model inputs on `/voice/api/chat` are identical to those on `/api/chat`.
  The friendly rule appears nowhere in them.

`_terminal_speech`, 18 result lines × 4 tools × 2 styles: identical except
the friendly fallback for a result with no mapping, which changed from
"That didn't work — I couldn't finish that request." to "… I couldn't get
through to the music service." / "… the magic mirror updater." (the
build's departure 4). All mapped music sentences are identical.

The setting, scratch copy of the tree, routing forced to raise:

| Environment | `/voice/api/chat` | `/api/chat` |
| --- | --- | --- |
| neither set | Sorry — I couldn't work out where to send that request. I've noted the details. | Error: routing failed: router exploded [rid abcd1234] |
| `ERROR_STYLE_VOICE=debug` | Error: routing failed: router exploded [rid abcd1234]. | Error: routing failed: router exploded [rid abcd1234] |
| `ERROR_STYLE_VOICE=friendly`, `ERROR_STYLE=friendly` | Sorry — I couldn't work out where to send that request. I've noted the details. | Sorry — I couldn't work out where to send that request. I've noted the details. |
| `ERROR_STYLE_VOICE=freindly` | Error: routing failed: router exploded [rid abcd1234]. | Error: routing failed: router exploded [rid abcd1234] |

Logs in the scratch run keep the cause on the speech mount: F01
`ERROR orchestrator.main: Router failed (shim): router exploded at
http://litellm:4000 (HTTP 500)`; F02 `ERROR orchestrator.main: Pipeline
failed (shim, rid=abcd1234)`; F03 `ERROR agents: Agent 'coordinator'
stream failed: All connection attempts failed`.

## 7. Mutation check (criterion 13)

Scratch copy of the whole tracked tree. One mutant at a time, file
restored after each (all five files compared equal to the tree at the
end). Every test ran behind the repo's `no_network` fixture, applied to
all tests by a scratch pytest plugin; no test reported `NetworkBlocked`.
Unmutated baseline with the plugin: `2276 passed, 2 skipped, 18 xfailed`.
Runs stop at the first failure (`-x`).

| Mutant | Result | First failing test |
| --- | --- | --- |
| M01 signal always True in _run_pipeline | killed | `tests/test_error_style.py::test_tool_service_outage_elsewhere_is_debug_unchanged[direct]` |
| M02 signal always False in _run_pipeline | killed | `tests/test_error_style.py::test_tool_service_outage_on_voice_is_friendly[direct]` |
| M03 signal never reset | survived |  |
| M04 /api/chat passes voice=True | killed | 1 test failed (name not captured; the first matching output line was a captured log line) |
| M05 /voice/api/chat passes voice=False | killed | 1 test failed (name not captured; the first matching output line was a captured log line) |
| M06 style keyed on transport again | killed | 1 test failed (name not captured; the first matching output line was a captured log line) |
| M07 voice default debug | killed | `tests/test_error_style.py::test_defaults_voice_friendly_everything_else_debug` |
| M08 non-voice default friendly | killed | 1 test failed (name not captured; the first matching output line was a captured log line) |
| M09 current_style ignores the signal | killed | `tests/test_error_style.py::test_current_style_reads_the_request_signal` |
| M10 kronk.voice always true on the span | killed | `tests/test_telemetry.py::test_pipeline_root_carries_the_voice_signal[False]` |
| M11 kronk.voice dropped from the span | killed | `tests/test_telemetry.py::test_pipeline_root_carries_the_voice_signal[True]` |
| M12 friendly rule appended to every tool result | survived |  |
| M13 friendly rule never appended | killed | `tests/test_error_style.py::test_failed_non_terminal_tool_carries_the_rule_only_on_voice[/voice/api/chat-True]` |
| M14 friendly rule appended in debug style too | killed | `tests/test_error_style.py::test_failed_non_terminal_tool_carries_the_rule_only_on_voice[/api/chat-False]` |
| M15 delegated specialists always debug | killed | `tests/test_error_style.py::test_tool_service_outage_on_voice_is_friendly[direct]` |
| M16 friendly llm: refused and unreachable swapped | killed | `tests/test_error_style.py::test_friendly_renderings_say_what_kind_of_thing_failed` |
| M17 friendly routing falls to generic | killed | `tests/test_error_style.py::test_friendly_renderings_say_what_kind_of_thing_failed` |
| M18 friendly tool failure back to old sentence | killed | `tests/test_error_style.py::test_terminal_speech_friendly_drops_the_cause_and_names_the_service` |
| M19 shim wrapper drops voice | killed | 1 test failed (name not captured; the first matching output line was a captured log line) |
| M20 speech profile on, signal not passed | killed | 1 test failed (name not captured; the first matching output line was a captured log line) |
| M21 /message passes voice=True | killed | 1 test failed (name not captured; the first matching output line was a captured log line) |
| M22 debug routing wording changed | killed | 1 test failed (name not captured; the first matching output line was a captured log line) |
| M23 debug pipeline wording changed | killed | `tests/test_error_style.py::test_debug_renderings_are_the_pinned_contracts` |
| M24 specialist block: styles swapped | killed | `tests/test_error_style.py::test_failed_block_friendly_forbids_technical_identifiers` |
| M25 body field voice:true turns the signal on | killed | `tests/test_voice_signal.py::test_content_cannot_turn_it_on[/api/chat]` |
| M26 body field voice:false turns the signal off | killed | `tests/test_voice_signal.py::test_content_cannot_turn_it_off` |
| M27 origin stamp turns the signal on | killed | `tests/test_voice_signal.py::test_content_cannot_turn_it_on[/api/chat]` |
| M28 model name 'voice' turns the signal on | killed | `tests/test_voice_signal.py::test_content_cannot_turn_it_on[/api/chat]` |
| M29 friendly sentence leaks the cause | killed | `tests/test_error_style.py::test_friendly_renderings_hide_technical_detail[llm]` |
| M30 music failure sentence changed | killed | `tests/test_error_style.py::test_music_failure_sentences_are_unchanged_in_both_styles[debug-[Could` |
| M31 voice signal a plain module global | killed | `tests/test_voice_signal.py::test_concurrent_requests_keep_their_own_value` |
| M32 pipeline failure not logged | killed | `tests/test_error_style.py::test_pipeline_crash_on_voice_is_friendly_and_logged` |
| M33 routing failure not on the trace | killed | 1 test failed (name not captured; the first matching output line was a captured log line) |

31 of 33 killed. Survivors: observation 6.

## 8. Test output

```
$ ./scripts/run_tests.sh -q
2276 passed, 2 skipped, 18 xfailed, 3 warnings in 16.15s
```

Matches the claim.

## 9. Replay output (criterion 16)

One run, 01:02:47 to 01:08:53 UTC. No media player was playing; no row was
skipped for an active player and the run did not stop itself, so no rerun
was needed. The 118 `pass`/`xfail` lines are left out; the per-row record
is `docs/bench/voice_replay_2026-11-17_010247_m2-e2e.json`.

```
$ python3 -u scripts/voice_replay.py --label m2-e2e
voice_replay: pipeline kronk-dev → http://localhost/dev/voice, dry-run on, 122 rows (0 not selected)
  s skip  skipped    inc-1017-try-playing-it-ytm
  s skip  skipped    inc-1019-ytm-link
  X xpass executed   eval-shuffle-it  []
  s skip  skipped    m4b-fetch-and-run-on-the-mirror
voice_replay: {'pass': 99, 'xfail': 19, 'skip': 3, 'xpass': 1}; executed 53, checked read-only 66; players unchanged (17 checked)
  skipped inc-1017-try-playing-it-ytm: follow-up that needs conversation history; a replay has none
  skipped inc-1019-ytm-link: typed link; a spoken request carries no URL
  skipped m4b-fetch-and-run-on-the-mirror: typed link; a spoken request carries no URL
voice_replay: wrote /home/drew/git-repos/drawsmcgraw/kronk/docs/bench/voice_replay_2026-11-17_010247_m2-e2e.json
exit=0
Tue Nov 17 01:08:53 UTC 2026
```

Against `docs/bench/voice_replay_2026-11-16_012824_m10-baseline.json`, row
by row on status and method:

| Row | Baseline | This run |
| --- | --- | --- |
| 112 of the 114 baseline rows | | identical |
| `inc-0930-search-deep-house-ytm` | fail | pass (known YouTube Music flapper) |
| `eval-shuffle-it` | xfail | xpass (known flapper) |
| 7 `m4b-*` rows (not in the baseline) | | pass |
| `m4b-fetch-and-run-on-the-mirror` (not in the baseline) | | skip (typed link) |

Against the build's rerun (`…_003150_m2-build-rerun.json`): one row
differs, `eval-search-youtube-music-for-some-deep-chill`, fail there and
pass here (known flapper).

## Checks before the first live request and at the end

| Check | Before (00:42 UTC) | End (01:10 UTC) |
| --- | --- | --- |
| `orchestrator-dev` runs the tree's code | md5 of all 14 `orchestrator/*.py` equal | equal |
| `tool_service-dev` runs the tree's code | not compared | md5 of the 8 `tool_service/*.py` code files equal (`__init__.py` is empty and not in the image) |
| `tool_service-dev` mounts | `/ops`, `/data`, `/shared`, `/data/hottub`; no `/keys` | same |
| `ops/` | `hosts.example.json` only | same |
| Dev pair | both up, healthy | both up, healthy (`orchestrator-dev` `bb1337f9f7b5`, not restarted; `tool_service-dev` `b1f1a9092de5`, same container, started 00:57:47) |
| Production containers | ids recorded | same ids, same start times |

No rebuild was needed.

## Guard rails

- Only `tool_service-dev` was stopped and started (00:43:30 to 00:57:47
  UTC), with the two commands in the brief. No other container, unit,
  LiteLLM, HA or MA was stopped, restarted or reconfigured.
  `kronk-hottub-monitor.service` and the three `llama-*` units active
  throughout.
- Requests went only to `127.0.0.1` (`/dev/…` on port 80, Tempo on 3200,
  HA's `/api/states` once at the end, read-only) and, from the replay
  script, to HA on localhost. The driver refuses any path outside `/dev/`.
  No redirect was followed. No LAN scan, no `.local` lookup.
- No sound: every music request from the driver ran while
  `tool_service-dev` was stopped; the replay is dry-run gated and reported
  players unchanged (17 checked); none playing at the end.
- No prompt asked for `http_check` or `remote_exec`. The replay's own
  corpus rows produced, on dev only: two `POST /magicmirror/update` on
  `tool_service-dev`, both 502 in 0.01 s from the preflight's key check
  (`SSH key not found at /keys/kronk-mm-update`, checked before any
  connection; there is no `/keys` mount), four `update_magicmirror` calls
  refused in the orchestrator (`mm_update_refused`), and one `POST
  /ops/exec` answered 404 by `tool_service-dev`. Nothing reached the
  MagicMirror Pi or any managed host. Production `tool_service`: 0 such
  lines.
- `.env`, `.env.dev`, repo code, tests and the corpus were not edited
  (`HA_TOKEN` was read from `.env` for the one HA state read).
  `pipeline_bench.sh` not run. No `down -v`, no `--remove-orphans`.
- Mutants and both TestClient harnesses ran with the network blocked.

## Changed temporarily and reverted

- `tool_service-dev` stopped, then started; healthy.
- `/dev/message` requests wrote 44 rows to the dev session store
  (`data/dev`, session id `m2-e2e`). Removed at the end with
  `sessions.clear('m2-e2e')` inside `orchestrator-dev`; the other sessions
  in that store were not touched.
- Scratch (session scratchpad only): request driver and results, harness
  outputs, three `git archive` copies of the tree (two of `HEAD`, one of
  `9b6c435`; no symlinks, no secrets), replay log. All removed with
  `rm -f` on named files after this report was written. No background job
  left running.
- Added to the repo: this report and
  `docs/bench/voice_replay_2026-11-17_010247_m2-e2e.json`. Nothing else.
