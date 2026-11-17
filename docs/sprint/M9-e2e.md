# M9 end to end: narrow the global lock

**M9 FAILS.**

End-to-end test agent, 2026-11-17, 17:19 to 18:40 UTC, branch `refactor` at
`66e63eb` (build `b762fe9..66e63eb`). Tested against
`docs/sprint/M9-criteria.md`; `docs/sprint/M9-build.md` was read as claims.
Nothing was fixed. The six coordinator rulings were applied as given; each
is confirmed or contradicted where it is used.

The verdict rests on one narrow finding (B1): `news_brief` is on the list of
read-only tools, and with `refresh: true` it sends a call that changes
something. A voice request stopped at its deadline during that call says
"so I stopped" while the refresh carries on. The brief names that as a
failure (a tool wrongly on the list; a deadline sentence that denies a sent
action). It is a one-line change and a test. Whether a news refresh counts
as "an action" is the coordinator's to rule; I applied the rule as written.

Everything else passes:

- **A lone request is not slower and cache reuse does not drop.** Offline,
  every route sends the same model calls, tool calls and bytes as
  `b762fe9`, and the orchestrator adds a median 47 µs per request. Live,
  `MODEL_LOCK=server` against `MODEL_LOCK=pipeline` in interleaved blocks,
  10 runs per route and mount: the orchestrator's share is 0.003 to 0.055 s
  in both modes; for the same prompt work llama.cpp's prompt-eval time
  differs by -4 to +5 ms between modes, and by at most 14 ms from the
  pre-M9 baseline runs. Ruling 1 is confirmed: the latency spread on a
  route is answer length, minute-boundary cache misses and tool time.
- **Two clients (ruling 2).** Behind a short research query the voice
  request's lock wait falls from a median 2.3 s to 0.9 s and its end-to-end
  time by about a second, inside the spread: nothing a person would
  reliably notice. Behind a long one the voice request was answered in
  10.5 s with the per-server lock and hit the 25 s deadline sentence with
  the pipeline lock.
- **Interleaving and the cache.** Two conversations alternating on one
  server evaluate 30 to 80% more prompt tokens than alone, with either
  lock; with both at once the totals are equal in the two modes (31,975
  against 32,539 tokens).
- **No deadlock, no lock left held, no state crossing between requests.**
  400 cross-server requests, 117 mixed requests x 5 seeds x 2 modes against
  each alone (0 differences), the load check, a devstral delegation beside
  gemma requests.
- **Disconnects.** 21 of 21 drops on seven route shapes ended at the drop
  time as `client_gone`, the generation cancelled, the next request
  answered. **No normal client was cut off: 123 of 123**, plus 72 replay
  rows through HA's client. A half-closing client is not answered, and was
  not before M9 (uvicorn and nginx, shown on routes the middleware does not
  touch).
- Suite `4516 passed, 3 skipped, 53 xfailed` in 4 of 4 runs. 40 of 114
  mutants survive; the behaviours behind the important survivors are
  correct in my offline and live runs, but no test pins them (O7).

## Criteria

| # | Criterion | Result | Evidence |
|---|---|---|---|
| 1 | Check first: slots, slot choice, shared state, disconnects today | pass | In the build report, committed first (`634fb5b`). Rechecked where I could: the per-minute prompt lines and the RAM-cache restore show in my runs (cached up to token 893 or 1087 after a minute tick); a buffered request not noticing a disconnect before M9 is consistent with uvicorn's `receive()` (read in the container). Nothing besides `sessions` found relying on the lock: the interleaving run agrees (0 differences). |
| 2 | A bench on dev only, baseline before any lock change | pass | `scripts/m9_lock_bench.py` refuses any base but `/dev`, requires dry-run, waits on production, clears nothing; I drove its functions for 190 single and 20 two-client requests. Baseline `36a1a2b` precedes the first lock change `f79b68a`. |
| 3 | Step 1: model-free work outside the lock | pass | Trace `cd422fec…`: `GET /weather/cached` and `routing.decide` end before `lock.wait` starts. Offline, with `MODEL_LOCK=pipeline` and a request stuck in a tool call, a direct-dispatch request is answered and a coordinator request waits. Bytes unchanged (criterion 12). |
| 4 | Step 2: one lock per server, one generation; tools hold nothing; servers independent | pass | Load check: 11 holds of `127.0.0.1:11438`, none overlapping. Devstral delegation: the request holds only `:11440` for 12.9 s while four gemma requests are answered. Offline: a request in a tool call does not delay another's generation; 400 cross-server requests, no generation without its lock, no server with two. |
| 5 | Step 3: voice first, first come first served within a class, no preemption, no starvation | pass | Tables under "Priority, starvation and the two servers"; live grant order with the guard firing at `max_overtakes: 3`. Two behaviours at the edges differ from the config comment or may surprise (O2, O3). |
| 6 | Step 4: voice deadline in `config/`, justified, one sentence, valid response, no lock after; the sentence never denies a sent action | **fail** | The value, the loader, the reload, the valid response, the released lock and the sentence all hold in 21 phases offline and live (25.006 s, trace `374aa493…`). The exception is B1: `news_brief` with `refresh: true`. |
| 7 | Disconnects cancel on every route; lock released; trace says so; next request served; a sent tool call not retried or reported failed; M8's writer works | pass | 21 of 21 live; 40 drops around a tool call, never two calls, no failure event; streamed voice shape included; 0 false positives in 123 normal requests. |
| 8 | Step 5: session writes safe | pass | Live: 18 requests at once on one session in three rounds, 36 messages, every pair adjacent and its own; `DELETE /dev/history` during a request. Offline: `/message` on three shared sessions in the 117-request runs, history equal to alone. |
| 9 | Each step measured against the baseline, kept or reverted; the stopping rule considered | pass | In the build report. My runs agree with its conclusion on the final code; its per-step latency medians are not evidence either way (ruling 1), its overhead and evaluated-token columns are. The stopping rule: see "Interleaving and the prompt cache". |
| 10 | Nothing leaks between concurrent requests | pass | 117 requests x 5 seeds x 2 modes, each against itself alone: 0 differences in replies, tool payloads (origin, `said`), model inputs, trace attributes, history, guard events. A deliberate leak is detected. |
| 11 | Mirror pin, devops tool set, fetch block, dry-run not bypassed by timing | pass | Same runs: 9 mirror POSTs for 9 pinned sentences, 36 refusals for the devops agent, alone and together alike. Live, dry-run held for every music request (46 dry-run lines at `tool_service-dev`, no player changed by me). The fetch block lives in tool_service and was not re-tested. |
| 12 | Model output unchanged for a request alone | pass | 56 cells: model calls, tool calls and response bytes identical on `b762fe9`, HEAD server and HEAD pipeline. |
| 13 | Failure paths release everything | pass | Offline: model error, tool error, deadline in 21 phases, cancelled waiter, cancelled holder, exception in the holder: lock free and the next request answered each time. Live: after 21 disconnects, after the deadline, after 40 drops around a tool call. A model failure was not provoked live (not allowed to break a model server). |
| 14 | Two-client result | pass | Lock wait median 913 ms (server) against 2283 ms (pipeline), n = 10 each; the long case above. Ruling 2 applies. |
| 15 | Load check | pass | Eight at once, all 200, order as criterion 5 says, each wait and total in the table, no slot or context error in the journals or LiteLLM's log. |
| 16 | Telemetry | pass | `lock.wait` span under the generation span with lock, class and wait; `kronk.ended` findable in one query for each ending. |
| 17 | `./scripts/run_tests.sh` | pass | `4516 passed, 3 skipped, 53 xfailed` four times of four. The M9 tests make no network attempt. Older tests do (O12). |
| 18 | Replay matches the M10 baseline apart from the known flappers | pass, on rerun | 111 of 114 baseline rows have the baseline's status. Two differ on known flappers; one row not on the list failed once and passed 6 of 6 on rerun, as did one newer row (O8). No row was skipped for a busy player. |
| 19 | Dev only, no sound, production untouched | pass | Production containers keep their 15:57 UTC creation times; nginx shows three POSTs to production chat routes in the window, all HA's (Drew's spoken requests at 17:10, 17:19, 17:23). The kitchen speaker went from playing to idle at 17:24:16 after a spoken command at that puck. Only `orchestrator-dev` was recreated. Nothing was sent to the Pi. |
| 20 | Docs | pass | See "Telemetry, docs and the promotion notes". Three inaccuracies (O1, O2, O5). |
| 21 | Rollback | pass | `MODEL_LOCK=pipeline` on dev: one request's model phases at a time. Unknown value: pipeline, one error line. Unset: server. |

## Findings

### Blocks promotion

**B1. `news_brief` is on `READ_ONLY_TOOLS`; with `refresh: true` it is not
read-only, and the deadline sentence then says "I stopped" after the call
was sent.** `orchestrator/tools.py:1049-1052` lists `news_brief`.
`_tool_news_brief` (`tools.py:986-991`) sends `POST /news/refresh` when the
model passes `refresh: true`; tool_service's `news_refresh` runs
`news.generate()`: a model call and a new stored brief. The list's own
comment says "tools that change nothing anywhere".

Exact input and result, offline through the app (stubbed model and
tool_service, no sockets): voice mount, "get me fresh news please"; the
model stub calls `news_brief {"refresh": true}`; the POST does not answer;
deadline 0.4 s. Spoken: `Sorry, that took too long, so I stopped. Please
try again.` tool_service calls: `['POST /news/refresh']`. Event:
`request_ended reason deadline tools_sent ['news_brief']`. tool_service
does not stop the refresh when the orchestrator goes away, so the brief is
regenerated and stored after Kronk said it stopped.

How likely: the tool's own description tells the model a refresh "takes ~30
seconds"; the tool timeout is 90 s; the deadline is 25 s. A spoken "get me
the latest news" that the model answers with a refresh is the realistic
way to meet it. Effect: nothing in the house changes; the person is told
to try again and the second ask finds a fresher brief (or starts a second
refresh beside the first). Every other tool is classified correctly (table
under "The deadline"), and all ten acting tools added to the list one at a
time are caught by the builder's tests. Not live-tested: the dev
tool_service has `NEWS_ENABLED=0`, and a refresh would have put a
generation on the shared model server from outside the lock.

### Observations

**O1. The rollback instructions do not say where to run them.** Production
runs from `kronk-releases/<tag>` with that directory's compose file. The
build report's "`MODEL_LOCK=pipeline` on the orchestrator service and
recreate it (`up -d`, no rebuild)" and the feature doc's "env on the
orchestrator service" would, run from the working tree, recreate
production's orchestrator from the working tree's compose file and build
context (the same point as M8-e2e O2). The edit belongs in the release
directory's `docker-compose.yml`, the command is
`docker compose -p kronk -f <release>/docker-compose.yml up -d orchestrator`,
and nginx must be restarted after (it pins the orchestrator's address).

**O2. `max_overtakes: 0` is display first, not "first come first served for
everyone".** `config/tuning.yaml:95-96` and the class docstring say the
latter. `_pick` treats every display waiter as starving when the limit is
0. Input: a holder, then arrivals V1 V2 D1 V3 D2. Served: D1 D2 V1 V2 V3.
The default 3 is not affected.

**O3. At the limit, every waiting display request goes ahead of voice
together.** `max_overtakes: 3`, arrivals D1 D2 D3 D4 V1..V5: served V1 V2
V3 D1 D2 D3 D4 V4 V5. The count is per display waiter and all four reach 3
at once, so the fourth voice request waits for four display generations,
not one. A grant cancelled before it runs still counts as an overtake.

**O4. A streamed client that stops reading holds the server lock.** The
lock is held while tokens are yielded, and a token travels up to the
response writer inside the hold. Offline, with `send()` blocked after the
first chunk on `/api/chat` streamed and on the streamed voice mount, a
second request on that server was not answered until the client read
again. The voice deadline does not cover it (the timer wraps the
pipeline's next event, not the write). uvicorn blocks `send()` only above
its write high-water mark; the pipeline lock was held in the same
situation before M9; a disconnect frees it.

**O5. The "already sent" sentence can be spoken when the call had not left
yet.** `tools.execute` records the tool before the HTTP client is entered.
With the deadline in that gap (offline, `play_music` and direct "pause"):
"That's taking too long to confirm. I've already sent the request, so it
may still go through.", tool_service calls: none. The safe side, never a
denial; the docs say "a call in flight counts as sent".

**O6. Two silent values.** `deadline_s:` with nothing after the colon is
read as null and switches the deadline off with no error. `MODEL_LOCK=`
(empty) is the pipeline lock, logged once at ERROR.

**O7. 40 of 114 mutants survive the whole suite; these are the gaps that
matter.** The behaviour is right in each case (my offline and live runs
say so); no test would notice it breaking.

| Mutants | Unpinned behaviour | Where I saw the behaviour hold |
|---|---|---|
| D13 | The buffered voice reply at the deadline is the sentence alone, not partial text and then the sentence. Production's path. | Live: the 25 s request was cut in the coordinator's synthesis and replied with the sentence alone. |
| C01, C19 | The `complete` flag in `ClientWatch`: a connection closing after the response is not a disconnect. `test_a_completed_response_is_not_a_disconnect` passes without it (the app task is already done). | Live: 123 normal requests, keep-alive included, none ended `client_gone`. |
| R02, D08, D09, D09b, D11 | A disconnect on a voice request with the deadline configured: every disconnect test sets `deadline_s` to none. Production has both. | Live: 6 voice drops with `deadline_s: 25`, all `client_gone`. Offline: client gone before the deadline, and after it. |
| A05, A06, D29, D26 | Every `aclosing` wrapper can be made a no-op, and the `GeneratorExit` branch removed: no test closes a generator between events. | Offline: the lock is free when `aclose()` returns. |
| D05 | `deadline_s: null` through a request. | Live on dev: a 9.3 s voice request with null after a 5 s deadline cut the same kind at 5.0 s. |
| D06 | A stopped turn that already has text is not stored (ruling 3). | Offline: 42 disconnect cases, `/message` among them, nothing stored. Live: 3 `/message` drops; the session read back holds no part of them. |
| D20 | That the stop happens at `deadline_s`: a deadline ten times longer passes every test. | Live: 25.006 s, 5.005 s. |
| D25, A03, A07 | Cancellation inside a direct dispatch, the forced synthesis round, the tool span's end. | Live: 9 drops inside the direct "pause" call. |
| L17, M02, D15 | Order between two starving display waiters; that `pipeline` takes no per-server lock; the space before the sentence on the streamed path. | Offline, lock table. |

19 more are equivalent or defensive. S11 is worth knowing: the pipeline
writing the pair as two `append` calls passes every test, because nothing
awaits between them; step 5 is pinned by the thread tests on
`sessions.py`, not through the pipeline.

**O8. Two replay rows failed once and are not on the flapper list.**
`eval-start-a-zzqx-florbent-radio-station-on` (baseline pass, never failed
in 50 earlier result files): the home agent asked "Which speaker should I
use?" instead of calling `play_music`. `m14-resume-get-the-door` (newer
than the baseline, pass in its six earlier runs): the coordinator's
`ask_home` came back without a `control_music` call and the reply said "I
have resumed your music playback". Each passed 6 of 6 when rerun alone
(`…_m9-e2e-rerun1..6.json`). The model's input for these requests is byte
for byte what `b762fe9` sends (criterion 12), so I read both as the
model's tool choice at temperature 1.0; the second is the old claimed
success without a tool, which tenet 5 is about. Reported so the
coordinator can rule.

**O9. An abandoned pipeline generator raises in its finaliser.** Dropping
the generator without closing it (no route does) gives `ValueError:
<Token var=<ContextVar name='kronk_voice'…> was created in a different
Context` at `main.py:628` as an un-retrieved task exception; the lock is
released when the loop's async-generator finaliser runs. The reset pattern
predates M9.

**O10. Two model servers share one GPU.** During the 12.9 s devstral
generation, four gemma coordinator requests took 2.2 to 5.1 s each against
1.3 to 1.5 s alone. Before M9 they would have waited for the whole coding
request (19 s). The per-server lock makes this overlap possible inside one
process; it already happened between production and dev.

**O11. The per-minute prompt lines cost more than the lock ever did.**
Every cache miss in the lone-request bench is one of them (the clock at
token 893, the weather age at token 1087); each costs 0.9 to 3.5 s of
prompt evaluation. The builder reported this (departure 8); my runs
confirm it. Not M9's.

**O12. Older tests still reach for the network on a plain run of the
suite** (M8-e2e O3, unchanged): 38 to 40 attempts per run from seven older
test files (`fake-tools`, three news feed hosts, `localhost:8123`). The
brief forbids tests opening sockets, so every run here, the one through
`./scripts/run_tests.sh` included, loaded a recorder that refuses and logs
them. The suite passes with all refused. No M9 test file made one.

**O13. A half-closing client is never answered** on this stack, on any
route, before or after M9 (see "Disconnects"). Through uvicorn directly it
is now logged as `client_gone` on the chat routes.

**O14. A lone request does cost more: about 0.05 ms** (0.09 ms on the
voice mount). Stated because the constraint says "no slower"; it is 0.5%
of the fastest live request and cannot be seen in one.

**O15. The streamed voice writer and the 5 s deadline on dev**: one
streamed story ended at 5.005 s with `request_ended reason deadline`; I
recorded the final record of that response but not its content chunks, so
"text already sent, then the sentence" rests on the offline row.

## The hard constraint: a lone request, and cache reuse

**Stated plainly: a lone request is not slower, and cache reuse does not
drop.** Three independent measurements agree.

### Offline, through the app, `b762fe9` against the tree

A scratch worktree of `b762fe9` and the working tree, stubbed model (below
the lock) and tool_service, no sockets (0 refused attempts). Seven route
shapes x eight scenarios (direct dispatch acting and read-only, routed
specialist with a tool, coordinator alone, coordinator delegating, research
with a web search, terminal tool, model error) = 56 cells, on `b762fe9`,
HEAD `MODEL_LOCK=server` and HEAD `MODEL_LOCK=pipeline`.

- Calls: for all 56 cells the model calls (model, exact messages, exact tool
  definitions, clock line normalised), the tool_service calls (method, path,
  payload), their order, and the response bytes are identical on all three.
  Model and tool_service calls per scenario: direct 0/1, specialist 2/1,
  coordinator 1/0, delegate 2/1, search 2/1, terminal 1/1, model error 1/0.
- Wall time with zero-delay stubs, 600 repetitions per cell and tree, the
  trees taking turns in separate processes: HEAD server minus `b762fe9`,
  median of the 56 per-cell differences **+47 µs** with tracing off (min
  -67, max +147) and **+77 µs** with an in-memory span exporter (min -65,
  max +198). By mount, tracing off, mean of cell medians: voice buffered
  486 to 580 µs (+94), voice streamed 612 to 697 (+86), `/api/chat` 462 to
  508 (+45), `/v1` 531 to 574 (+43), `/message` 710 to 694 (-16).

So the orchestrator does add measurable work to a request run alone: about
a twentieth of a millisecond, a tenth on the voice mount (the middleware's
two tasks and the `Run` object, one `lock.wait` span and one `model_lock`
event per generation, `asyncio.timeout_at` per event on the voice mount,
the `aclosing` frames). The fastest live request is 0.02 s (direct
dispatch), so this is 0.5% of the shortest request and below what any live
figure can show. I do not count it as "slower".

### Live on dev, `MODEL_LOCK=server` against `MODEL_LOCK=pipeline`

The builder's bench functions (`scripts/m9_lock_bench.py`), driven in ten
blocks interleaved in time (S P P S S P P S S P, 17:22 to 17:40 UTC), the
mode switched by recreating `orchestrator-dev` alone with a scratch compose
override; one discarded warm-up request per block. Per block: 2 runs per
route and mount (web search 1), 2 two-client runs. Totals per mode: **10
runs per route and mount; 5 for the web-search route** (20 web searches in
all; the brief asks for at least 10 and also for few web searches, so I
stopped at 5 and say so); 10 two-client runs. Production was idle or on a
direct-dispatch request throughout (two spoken "Thumbs up" at 17:19:54 and
17:23:58, no model call). No slot or context error in the llama.cpp
journals or LiteLLM's log in any block. Files:
`docs/bench/m9_lock_2026-11-17_174015_m9-e2e-server.json` and
`..._m9-e2e-pipeline.json`; each row carries llama.cpp's own timings for
its generations, read from the journal.

#### Lone request, MODEL_LOCK=server against MODEL_LOCK=pipeline (same code, blocks interleaved)

Seconds. `rest` = client time minus llama.cpp's own prompt-eval and generation time (journal) minus service tool time (Tempo): the orchestrator, LiteLLM and HTTP. `overhead` is the builder's measure (client minus agent_round durations minus tool time).

| Route / mount | Mode | n | Client median (range) | Prompt-eval median (range) | Generation median (range) | Output tokens median (range) | Tools median (range) | Lock wait max | rest median (range) | overhead median | Tokens evaluated median (range) | Reuse |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| direct / voice | server | 10 | 0.02 (0.01-0.03) | 0.00 (0.00-0.00) | 0.00 (0.00-0.00) | 0 (0-0) | 0.02 (0.01-0.02) | 0.000 | 0.004 (0.003-0.004) | 0.004 | 0 (0-0) | None |
| direct / voice | pipeline | 10 | 0.02 (0.01-0.03) | 0.00 (0.00-0.00) | 0.00 (0.00-0.00) | 0 (0-0) | 0.02 (0.01-0.02) | 0.000 | 0.003 (0.002-0.004) | 0.003 | 0 (0-0) | None |
| direct / display | server | 10 | 0.03 (0.02-0.03) | 0.00 (0.00-0.00) | 0.00 (0.00-0.00) | 0 (0-0) | 0.02 (0.02-0.03) | 0.000 | 0.004 (0.004-0.005) | 0.004 | 0 (0-0) | None |
| direct / display | pipeline | 10 | 0.02 (0.02-0.03) | 0.00 (0.00-0.00) | 0.00 (0.00-0.00) | 0 (0-0) | 0.02 (0.01-0.02) | 0.000 | 0.004 (0.003-0.004) | 0.004 | 0 (0-0) | None |
| specialist / voice | server | 10 | 6.59 (4.79-8.48) | 2.76 (2.33-3.04) | 3.43 (2.35-4.20) | 314 (240-411) | 0.64 (0.00-1.07) | 0.000 | 0.159 (0.108-0.207) | 0.040 | 4546 (3926-4555) | 0.3539 |
| specialist / voice | pipeline | 10 | 6.89 (5.91-7.91) | 2.76 (2.75-2.89) | 3.28 (2.20-4.29) | 359 (223-429) | 0.71 (0.60-0.77) | 0.000 | 0.176 (0.156-0.210) | 0.048 | 4546 (4546-4547) | 0.4634 |
| specialist / display | server | 10 | 4.95 (3.13-6.50) | 0.46 (0.45-2.26) | 3.24 (1.83-3.93) | 340 (199-406) | 0.67 (0.59-0.75) | 0.000 | 0.169 (0.130-0.197) | 0.047 | 621 (621-3654) | 0.8216 |
| specialist / display | pipeline | 10 | 4.53 (3.23-7.62) | 0.45 (0.44-2.26) | 3.25 (1.95-4.50) | 343 (220-473) | 0.66 (0.58-0.78) | 0.000 | 0.156 (0.126-0.208) | 0.049 | 621 (621-3653) | 0.8909 |
| coord / voice | server | 10 | 1.49 (1.32-3.47) | 1.19 (1.19-1.31) | 0.14 (0.05-2.15) | 14 (8-267) | 0.00 (0.00-0.00) | 0.000 | 0.099 (0.078-0.125) | 0.023 | 2058 (2058-2058) | 0.0 |
| coord / voice | pipeline | 10 | 1.37 (1.33-3.54) | 1.20 (1.19-1.23) | 0.07 (0.05-2.22) | 8 (8-267) | 0.00 (0.00-0.00) | 0.000 | 0.095 (0.076-0.114) | 0.024 | 2058 (2058-2058) | 0.0 |
| coord / display | server | 10 | 0.79 (0.14-2.34) | 0.03 (0.03-0.89) | 0.39 (0.02-2.24) | 48 (2-267) | 0.00 (0.00-0.00) | 0.000 | 0.065 (0.056-0.084) | 0.025 | 1 (1-1429) | 0.8628 |
| coord / display | pipeline | 10 | 0.29 (0.13-2.42) | 0.03 (0.03-0.03) | 0.20 (0.04-2.32) | 26 (3-267) | 0.00 (0.00-0.00) | 0.000 | 0.060 (0.046-0.069) | 0.024 | 1 (1-1) | 0.9995 |
| delegate / voice | server | 10 | 5.46 (4.93-7.25) | 2.75 (2.75-2.93) | 2.51 (1.82-4.30) | 300 (181-538) | 0.02 (0.02-0.03) | 0.000 | 0.171 (0.153-0.178) | 0.055 | 4467 (4466-4469) | 0.2294 |
| delegate / voice | pipeline | 10 | 5.85 (4.24-6.85) | 2.76 (2.74-3.09) | 2.89 (1.31-3.55) | 330 (148-411) | 0.02 (0.01-0.02) | 0.000 | 0.158 (0.135-0.194) | 0.052 | 4468 (4466-5109) | 0.2183 |
| delegate / display | server | 10 | 3.88 (2.73-6.04) | 0.07 (0.05-0.07) | 2.59 (1.52-4.93) | 298 (179-535) | 0.02 (0.01-0.02) | 0.000 | 1.107 (0.844-1.273) | 0.045 | 13 (2-14) | 0.9979 |
| delegate / display | pipeline | 10 | 4.94 (2.73-7.85) | 0.07 (0.07-3.56) | 2.87 (1.43-4.17) | 350 (164-459) | 0.02 (0.01-0.02) | 0.000 | 0.832 (0.200-1.281) | 0.048 | 14 (10-5751) | 0.5987 |
| search / voice | server | 5 | 4.69 (3.97-6.18) | 0.86 (0.86-0.87) | 2.41 (2.17-4.46) | 294 (265-494) | 0.74 (0.62-1.80) | 0.000 | 0.146 (0.122-0.153) | 0.043 | 1366 (1331-1366) | 0.3559 |
| search / voice | pipeline | 5 | 5.13 (3.84-7.51) | 0.87 (0.86-0.90) | 2.75 (1.81-3.45) | 320 (212-467) | 0.86 (0.67-3.60) | 0.000 | 0.145 (0.135-0.774) | 0.032 | 1366 (1331-1373) | 0.3554 |
| search / display | server | 5 | 4.28 (3.75-5.11) | 0.41 (0.41-0.42) | 3.06 (2.65-3.82) | 358 (297-532) | 0.60 (0.56-0.80) | 0.000 | 0.135 (0.120-0.140) | 0.046 | 617 (617-617) | 0.7084 |
| search / display | pipeline | 5 | 4.47 (3.37-5.22) | 0.70 (0.41-0.72) | 2.78 (1.73-3.19) | 315 (186-381) | 0.83 (0.72-2.08) | 0.000 | 0.114 (0.105-0.119) | 0.034 | 1054 (617-1079) | 0.5788 |

Rows where the journal's task count differs from the request's generation count (production or a neighbour in the window): 0.

#### Per generation: prompt tokens, cached, evaluated, and llama.cpp's prompt-eval time

One line per route, mount, mode and generation (in order). Tokens from the `agent_round` events (LiteLLM's pass-through of llama.cpp's count); time from llama.cpp's journal.

| Route / mount | Mode | Gen | Agent, phase | n | Prompt median (range) | Cached median (range) | Evaluated median (range) | Prompt-eval ms median (range) | ms per evaluated token |
|---|---|---|---|---|---|---|---|---|---|
| specialist / voice | server | 1 | home, plan_1 | 10 | 3926 (3926-3926) | 0 (0-0) | 3926 (3926-3926) | 2336 (2327-2571) | 0.61 |
| specialist / voice | server | 2 | home, synthesis | 6 | 4546 (4546-4555) | 3926 (3926-3926) | 620 (620-629) | 432 (424-472) | 0.70 |
| specialist / voice | pipeline | 1 | home, plan_1 | 10 | 3926 (3926-3926) | 0 (0-0) | 3926 (3926-3926) | 2335 (2325-2454) | 0.60 |
| specialist / voice | pipeline | 2 | home, synthesis | 10 | 4546 (4546-4547) | 3926 (3926-3926) | 620 (620-621) | 427 (423-432) | 0.69 |
| specialist / display | server | 1 | home, plan_1 | 10 | 3926 (3926-3926) | 3925 (893-3925) | 1 (1-3033) | 23 (21-1840) | 0.63 |
| specialist / display | server | 2 | home, synthesis | 10 | 4546 (4546-4547) | 3926 (3926-3926) | 620 (620-621) | 424 (423-474) | 0.70 |
| specialist / display | pipeline | 1 | home, plan_1 | 10 | 3926 (3926-3926) | 3925 (893-3925) | 1 (1-3033) | 23 (21-1835) | 0.67 |
| specialist / display | pipeline | 2 | home, synthesis | 10 | 4546 (4546-4547) | 3926 (3926-3926) | 620 (620-621) | 426 (422-432) | 0.69 |
| coord / voice | server | 1 | coordinator, synthesis | 10 | 2058 (2058-2058) | 0 (0-0) | 2058 (2058-2058) | 1194 (1192-1310) | 0.59 |
| coord / voice | pipeline | 1 | coordinator, synthesis | 10 | 2058 (2058-2058) | 0 (0-0) | 2058 (2058-2058) | 1198 (1188-1230) | 0.59 |
| coord / display | server | 1 | coordinator, synthesis | 10 | 2011 (2011-2011) | 2010 (582-2010) | 1 (1-1429) | 34 (31-892) | 0.72 |
| coord / display | pipeline | 1 | coordinator, synthesis | 10 | 2011 (2011-2011) | 2010 (2010-2010) | 1 (1-1) | 33 (31-34) | 33.10 |
| delegate / voice | server | 1 | coordinator, plan_1 | 10 | 2059 (2059-2059) | 1330 (1330-1330) | 729 (729-729) | 491 (487-556) | 0.69 |
| delegate / voice | server | 2 | home, plan_1 | 10 | 3738 (3737-3740) | 0 (0-0) | 3738 (3737-3740) | 2264 (2256-2374) | 0.61 |
| delegate / voice | pipeline | 1 | coordinator, plan_1 | 10 | 2059 (2059-2059) | 1330 (688-1330) | 729 (729-1371) | 491 (489-838) | 0.67 |
| delegate / voice | pipeline | 2 | home, plan_1 | 10 | 3739 (3737-3740) | 0 (0-0) | 3739 (3737-3740) | 2261 (2250-2344) | 0.61 |
| delegate / display | server | 1 | coordinator, plan_1 | 10 | 2012 (2012-2012) | 2011 (2011-2011) | 1 (1-1) | 30 (28-31) | 29.77 |
| delegate / display | server | 2 | home, plan_1 | 10 | 3738 (3737-3740) | 3726 (3726-3736) | 12 (1-13) | 38 (21-42) | 3.40 |
| delegate / display | pipeline | 1 | coordinator, plan_1 | 10 | 2012 (2012-2012) | 2011 (0-2011) | 1 (1-2012) | 31 (29-1212) | 0.61 |
| delegate / display | pipeline | 2 | home, plan_1 | 10 | 3739 (3737-3740) | 3726 (0-3728) | 12 (9-3739) | 39 (37-2344) | 0.62 |
| search / voice | server | 1 | research, plan_1 | 5 | 750 (750-750) | 0 (0-0) | 750 (750-750) | 470 (467-472) | 0.63 |
| search / voice | server | 2 | research, synthesis | 5 | 1366 (1331-1366) | 750 (750-750) | 616 (581-616) | 393 (387-400) | 0.65 |
| search / voice | pipeline | 1 | research, plan_1 | 5 | 750 (750-750) | 0 (0-0) | 750 (750-750) | 472 (468-490) | 0.63 |
| search / voice | pipeline | 2 | research, synthesis | 5 | 1366 (1331-1373) | 750 (750-750) | 616 (581-623) | 395 (389-410) | 0.65 |
| search / display | server | 1 | research, plan_1 | 5 | 750 (750-750) | 749 (749-749) | 1 (1-1) | 18 (18-19) | 18.49 |
| search / display | server | 2 | research, synthesis | 5 | 1366 (1366-1366) | 750 (750-750) | 616 (616-616) | 392 (390-400) | 0.64 |
| search / display | pipeline | 1 | research, plan_1 | 5 | 750 (750-750) | 287 (287-749) | 463 (1-463) | 310 (18-323) | 0.71 |
| search / display | pipeline | 2 | research, synthesis | 5 | 1366 (1341-1366) | 750 (750-750) | 616 (591-616) | 392 (389-402) | 0.65 |

#### Same prompt work, both modes: prompt-eval time for generations that evaluated the same number of tokens

Generations grouped by route, mount, generation and tokens evaluated (within 2%); only groups seen in both modes.

| Route / mount, gen | Tokens evaluated | n server | n pipeline | Prompt-eval ms server median (range) | Prompt-eval ms pipeline median (range) | Difference of medians ms |
|---|---|---|---|---|---|---|
| specialist / voice, gen 1 | 3926 | 10 | 10 | 2336 (2327-2571) | 2335 (2325-2454) | +0 |
| specialist / voice, gen 2 | 629 | 6 | 10 | 432 (424-472) | 427 (423-432) | +5 |
| specialist / display, gen 1 | 1 | 7 | 9 | 22 (21-24) | 22 (21-24) | -0 |
| specialist / display, gen 1 | 3033 | 2 | 1 | 1839 (1838-1840) | 1835 (1835-1835) | +4 |
| specialist / display, gen 2 | 620 | 10 | 10 | 424 (423-474) | 426 (422-432) | -2 |
| coord / voice, gen 1 | 2058 | 10 | 10 | 1194 (1192-1310) | 1198 (1188-1230) | -4 |
| coord / display, gen 1 | 1 | 8 | 10 | 34 (31-35) | 33 (31-34) | +1 |
| delegate / voice, gen 1 | 729 | 10 | 9 | 491 (487-556) | 490 (489-516) | +0 |
| delegate / voice, gen 2 | 3737 | 10 | 10 | 2264 (2256-2374) | 2261 (2250-2344) | +3 |
| delegate / display, gen 1 | 1 | 10 | 6 | 30 (28-31) | 30 (29-31) | -0 |
| delegate / display, gen 2 | 10 | 6 | 5 | 38 (38-39) | 38 (37-39) | +0 |
| delegate / display, gen 2 | 13 | 3 | 1 | 38 (38-42) | 39 (39-39) | -1 |
| search / voice, gen 1 | 750 | 5 | 5 | 470 (467-472) | 472 (468-490) | -2 |
| search / voice, gen 2 | 581 | 1 | 1 | 387 (387-387) | 389 (389-389) | -2 |
| search / voice, gen 2 | 609 | 4 | 3 | 395 (391-400) | 399 (391-410) | -4 |
| search / display, gen 1 | 1 | 5 | 2 | 18 (18-19) | 20 (18-21) | -1 |
| search / display, gen 2 | 616 | 5 | 4 | 392 (390-400) | 396 (392-402) | -4 |

#### Where a route's latency spread comes from

Per route and mount, both modes pooled: the spread (max minus min, seconds) of the client time and of each part.

| Route / mount | n | Client spread | Prompt-eval spread | Generation spread | Tools spread | rest spread | Correlation of client time with prompt-eval + generation + tools |
|---|---|---|---|---|---|---|---|
| direct / voice | 20 | 0.01 | 0.00 | 0.00 | 0.01 | 0.00 | 0.9892 |
| direct / display | 20 | 0.01 | 0.00 | 0.00 | 0.01 | 0.00 | 0.9895 |
| specialist / voice | 20 | 3.70 | 0.71 | 2.09 | 1.07 | 0.10 | 0.9997 |
| specialist / display | 20 | 4.49 | 1.82 | 2.67 | 0.20 | 0.08 | 0.9999 |
| coord / voice | 20 | 2.21 | 0.12 | 2.18 | 0.00 | 0.05 | 1.0000 |
| coord / display | 20 | 2.29 | 0.86 | 2.29 | 0.00 | 0.04 | 0.9999 |
| delegate / voice | 20 | 3.01 | 0.35 | 2.99 | 0.02 | 0.06 | 0.9998 |
| delegate / display | 20 | 5.12 | 3.51 | 3.50 | 0.01 | 1.08 | 0.9874 |
| search / voice | 10 | 3.67 | 0.05 | 2.65 | 2.97 | 0.65 | 0.9845 |
| search / display | 10 | 1.85 | 0.31 | 2.09 | 1.52 | 0.03 | 0.9998 |

#### Minute boundaries

For each generation whose prompt was seen before in the run: tokens evaluated when the previous request with the same route and mount was in the same clock minute, against a different minute.

| Route / mount | Mode | Same minute: n, evaluated median (range) | Different minute: n, evaluated median (range) |
|---|---|---|---|
| specialist / voice | server | 4, 4546 (3926-4546) | 5, 3926 (3926-4555) |
| specialist / voice | pipeline | 2, 4546 (4546-4546) | 8, 4546 (4546-4547) |
| specialist / display | server | 8, 621 (621-3459) | 2, 3654 (3653-3654) |
| specialist / display | pipeline | 9, 621 (621-622) | 1, 3653 (3653-3653) |
| coord / voice | server | 3, 2058 (2058-2058) | 6, 2058 (2058-2058) |
| coord / voice | pipeline | 2, 2058 (2058-2058) | 8, 2058 (2058-2058) |
| coord / display | server | 9, 1 (1-1323) | 1, 1429 (1429-1429) |
| coord / display | pipeline | 10, 1 (1-1) | 0, - (-) |
| delegate / voice | server | 3, 4466 (4466-4467) | 6, 4468 (4466-4469) |
| delegate / voice | pipeline | 3, 4468 (4468-4469) | 7, 4469 (4466-5109) |
| delegate / display | server | 10, 13 (2-14) | 0, - (-) |
| delegate / display | pipeline | 8, 13 (10-5751) | 2, 5751 (5751-5751) |
| search / voice | server | 0, - (-) | 4, 1366 (1331-1366) |
| search / voice | pipeline | 0, - (-) | 5, 1366 (1331-1373) |
| search / display | server | 5, 617 (617-617) | 0, - (-) |
| search / display | pipeline | 2, 617 (617-617) | 3, 1079 (1054-1079) |

#### Two clients (research on /dev/api/chat, voice weather one second later)

| Mode | n | Voice latency median (runs) | Voice lock wait ms, sum per run | Research s (runs) | Research tokens evaluated |
|---|---|---|---|---|---|
| server | 10 | 7.73 (6.25, 7.70, 7.66, 5.97, 9.53, 9.90, 8.36, 9.88, 7.15, 7.76) | 575, 539, 950, 255, 949, 1675, 876, 1610, 786, 1279 | 9.07, 6.55, 6.17, 8.63, 7.16, 8.30, 7.05, 8.32, 5.90, 6.28 | 1310, 1338, 1351, 1351, 1351, 1351, 1271, 1302, 1351, 1302 |
| pipeline | 10 | 8.85 (8.52, 6.69, 9.93, 10.42, 6.88, 6.89, 8.65, 12.66, 9.79, 9.06) | 2014, 1768, 1892, 2532, 2405, 2160, 2691, 7454, 3218, 1918 | 3.03, 2.79, 2.92, 3.56, 3.42, 3.19, 3.70, 8.47, 4.23, 2.94 | 1351, 1351, 1302, 1351, 1767, 1779, 1780, 1351, 1289, 1361 |

#### Before M9 (the builder's two baseline runs, `36a1a2b`), from llama.cpp's journal, against my server-mode runs

Prompt-eval time per generation for generations that evaluated the same number of tokens (within 2%).

| Route / mount, gen | Tokens evaluated | n before | n after (server) | Prompt-eval ms before median (range) | Prompt-eval ms after median (range) |
|---|---|---|---|---|---|
| coord / display, gen 1 | 1 | 11 | 8 | 34 (31-36) | 34 (31-35) |
| coord / display, gen 1 | 1429 | 1 | 1 | 894 (894-894) | 892 (892-892) |
| coord / voice, gen 1 | 2058 | 11 | 10 | 1197 (1192-1202) | 1194 (1192-1310) |
| delegate / display, gen 1 | 1 | 11 | 10 | 30 (29-32) | 30 (28-31) |
| delegate / display, gen 2 | 1 | 1 | 1 | 19 (19-19) | 21 (21-21) |
| delegate / display, gen 2 | 10 | 3 | 2 | 39 (38-39) | 39 (38-39) |
| delegate / display, gen 2 | 14 | 5 | 7 | 39 (38-39) | 38 (38-42) |
| delegate / voice, gen 1 | 729 | 12 | 10 | 491 (488-492) | 491 (487-556) |
| delegate / voice, gen 2 | 3739 | 11 | 10 | 2263 (2256-2273) | 2264 (2256-2374) |
| search / display, gen 1 | 1 | 3 | 5 | 19 (18-22) | 18 (18-19) |
| search / display, gen 2 | 625 | 3 | 5 | 395 (394-398) | 392 (390-400) |
| search / voice, gen 1 | 750 | 4 | 5 | 472 (471-486) | 470 (467-472) |
| search / voice, gen 2 | 581 | 1 | 1 | 390 (390-390) | 387 (387-387) |
| search / voice, gen 2 | 609 | 3 | 4 | 397 (391-401) | 395 (391-400) |
| specialist / display, gen 1 | 1 | 10 | 7 | 23 (22-24) | 22 (21-24) |
| specialist / display, gen 1 | 2839 | 1 | 1 | 1747 (1747-1747) | 1757 (1757-1757) |
| specialist / display, gen 1 | 3033 | 1 | 2 | 1853 (1853-1853) | 1839 (1838-1840) |
| specialist / voice, gen 1 | 3926 | 12 | 10 | 2332 (2317-2350) | 2336 (2327-2571) |

Reading the tables (ruling 1, confirmed independently):

- **The orchestrator's share is constant and equal in both modes.** `rest`
  (client time minus llama.cpp's own prompt-eval and generation time minus
  tool time) has a spread of 0.03 to 0.10 s on every route but two, and the
  builder's `overhead` is 0.003 to 0.055 s in both modes on every route.
  The two exceptions are `delegate / display` (about 1 s in both modes) and
  one `search / voice` run: the time is inside llama.cpp, between tasks,
  when it restores a saved prompt for the second prompt family of the
  request (the home agent's prompt after the coordinator's); it is not in
  the orchestrator (`overhead` 0.045 and 0.048 s) and it is the same in
  both modes.
- **Latency spread on a route is answer length, cache misses and tools.**
  Client time correlates with llama.cpp prompt-eval + generation + tool
  time at 0.985 to 1.000 on every route. Coordinator / voice: the prompt
  evaluated was 2058 tokens in 20 of 20 runs and took 1.19 to 1.31 s; the
  2.2 s spread is the answer (8 to 267 tokens). Specialist: generation
  spread 2.1 to 2.7 s (199 to 473 tokens), the weather call 0.59 to 1.07 s
  in my runs.
- **Cache misses follow the clock, not the lock.** Every large
  tokens-evaluated figure in either mode sits on a request whose prompt
  family was last used in an earlier clock minute (table "Minute
  boundaries"): specialist / display 3653 or 3654 on 3 of 3 different-minute
  runs against 621 on 16 of 17 same-minute runs; delegate / display 5751 on
  2 of 2 different-minute runs (both happened to be pipeline blocks);
  coordinator / display 1429 on 1 of 1. The three clock-minute misses on
  specialist / display were cached up to token 893 (the clock line); the
  one miss inside a clock minute (server, 17:29:28, 3459 evaluated) was
  cached up to token 1087, the prompt's second per-minute line (the
  weather block's "fetched N minutes ago"), which ticks on its own clock.
- **With the prompt work equal, the time is equal.** Table "Same prompt
  work, both modes": for 17 groups of generations that evaluated the same
  number of tokens, the medians of llama.cpp's prompt-eval time differ by
  -4 to +5 ms. Table "Before M9": the same comparison against the
  builder's two baseline runs on the pre-M9 code, using llama.cpp's journal
  for those runs and not the builder's figures: 18 groups, medians within
  14 ms of each other (2332 against 2336 ms for the 3926-token prompt).
- Reuse (cached / prompt) per route differs between modes only where a
  minute boundary fell: for example coordinator / display 0.8628 (server,
  one different-minute run) against 0.9995, and delegate / display 0.9979
  (server) against 0.5987 (pipeline, two different-minute runs). Neither
  direction belongs to the lock.

### Two clients (ruling 2)

The table above: with the bench's short research query the voice request's
**lock wait** is 255 to 1675 ms in server mode (median 913) against 1768
to 7454 ms in pipeline mode (median 2283). That is what criterion 14 asks:
the voice request waits for at most the generation in progress. End to end
the voice request took a median 7.73 s (server) against 8.85 s (pipeline):
about a second, inside the run-to-run spread (5.97 to 9.90 against 6.69 to
12.66). The research request is slower in server mode (median 7.11 s
against 3.31 s) because the voice request's generations now run between its
rounds.

What a person would notice: with a short research query, nothing reliable.
With a long one, the difference between an answer and no answer. One run
each (`docs/bench/m9_e2e_2026-11-17_182702_two_client_long.json`), a
display research question with three to seven web searches (40.7 and
41.9 s), voice weather started one second later:

| Mode | Voice request | Lock waits | What the puck would say |
|---|---|---|---|
| server | 10.50 s, answered | 2940 ms and 1693 ms | the weather |
| pipeline | 25.01 s, `request_ended reason deadline` | 24992 ms (never granted) | "Sorry, that took too long, so I stopped. Please try again." |

## Interleaving and the prompt cache

Two scripted conversations on `/dev/api/chat` (the history travels in the
request; three seeded exchanges, then ten turns; fixed user turns and fixed
assistant text in the history, so turn k's prompt is the same in every
condition apart from a condition tag in the first message). Conversation A
has three turns that delegate to the home agent, B has three. Conditions:
each alone; alternating one after the other (A1 B1 A2 B2 ...); both at once
(B started 0.3 s after A), ten times. Tokens evaluated for a turn = sum
over its generations of prompt minus cached. Both modes, 18:02 to 18:08
UTC, production idle, no log errors.
`docs/bench/m9_e2e_2026-11-17_180859_cache_interleave.json`.

| Mode | Conversation | Condition | Turns 200 | Tokens evaluated per turn (turn 1..10) | Sum | Median | Client s median | Generations |
|---|---|---|---|---|---|---|---|---|
| server | A | alone | 10 | 2250, 73, 3808, 73, 1179, 6357, 73, 2039, 87, 74 | 16013 | 633 | 3.00 | 19 |
| server | A | alternating | 10 | 2250, 324, 405, 471, 2635, 2627, 691, 2079, 2840, 914 | 15236 | 1496 | 1.95 | 15 |
| server | A | together | 10 | 251, 1869, 88, 2471, 544, 4357, 2691, 5082, 5890, 2914 | 26157 | 2581 | 4.29 | 20 |
| server | B | alone | 10 | 2248, 1635, 72, 13, 3753, 73, 78, 74, 15, 74 | 8035 | 74 | 1.50 | 11 |
| server | B | alternating | 10 | 249, 336, 2394, 1883, 6278, 2612, 691, 4502, 838, 912 | 20695 | 1398 | 2.30 | 13 |
| server | B | together | 10 | 249, 337, 395, 466, 540, 613, 691, 777, 838, 912 | 5818 | 576 | 3.16 | 12 |
| server | | log errors: {'llama': [], 'litellm': []}; production requests during: 0 | | | | | | |
| pipeline | A | alone | 10 | 2251, 73, 3813, 2471, 1266, 74, 16, 75, 86, 74 | 10199 | 80 | 1.72 | 15 |
| pipeline | A | alternating | 10 | 2251, 324, 6135, 471, 544, 2618, 691, 766, 2850, 914 | 17564 | 840 | 2.12 | 12 |
| pipeline | A | together | 10 | 251, 324, 2405, 2471, 1968, 2618, 691, 766, 2852, 914 | 15260 | 1441 | 2.17 | 15 |
| pipeline | B | alone | 10 | 2249, 85, 72, 2466, 3813, 73, 78, 80, 73, 17 | 9006 | 79 | 2.19 | 13 |
| pipeline | B | alternating | 10 | 249, 332, 395, 466, 550, 613, 691, 5816, 2838, 912 | 12862 | 582 | 2.91 | 13 |
| pipeline | B | together | 10 | 249, 336, 395, 466, 6275, 613, 691, 4504, 2838, 912 | 17279 | 652 | 3.24 | 13 |
| pipeline | | log errors: {'llama': [], 'litellm': []}; production requests during: 0 | | | | | | |

- **Alternation costs cache, with either lock.** Alone, a turn evaluates
  about 75 tokens (the previous scripted answer and the new question) unless
  it crosses a minute or calls the home agent. Alternating strictly one
  after the other, which no lock can affect, the conversation's own history
  is not reused: cached stays at 1999 or 2000 tokens (the shared system
  text) and the evaluated count grows with the history (249, 324, 405, ...
  914). Sum for A and B: 24,048 alone against 35,931 alternating in server
  mode; 19,205 against 30,426 in pipeline mode. The two modes ran the same
  serial sequence, so the 18% between 35,931 and 30,426 is the noise of this
  measure (minute boundaries: each 2,400 to 6,300 figure is one).
- **Both at once: the per-server lock does not add to it.** Sum for A and
  B: 31,975 tokens in server mode, 32,539 in pipeline mode. In server mode
  the cost moved between the two conversations (A 26,157, B 5,818) instead
  of being shared; the total is the same.
- So the plan's hazard is real as a property of two conversations sharing
  one server (about 30 to 80% more prompt tokens evaluated than alone, in
  both modes), and M9 does not make it worse within what ten alternations
  can resolve. The plan's stopping rule compares that cost with the
  queueing saved; the long two-client run above is the queueing saved.

## Priority, starvation and the two servers

Offline with the real `PriorityLock` (D = display, V = voice, arrivals
queued behind a holder):

| Case | Result |
|---|---|
| D1 V1 D2 V2 D3 V3 V4 V5, `max_overtakes` 100 | V1 V2 V3 V4 V5 D1 D2 D3 |
| same, 3 | V1 V2 V3 D1 V4 D2 V5 D3 |
| same, 1 | V1 D1 V2 D2 V3 D3 V4 V5 |
| same, 0 | D1 D2 D3 V1 V2 V3 V4 V5 |
| V1 V2 D1 V3 D2, `max_overtakes` 0 | D1 D2 V1 V2 V3 (observation O2) |
| D1 D2 D3 D4 V1..V5, 3 | V1 V2 V3 D1 D2 D3 D4 V4 V5 (observation O3) |
| a display waiter with a new voice waiter arriving before every release, at 0, 1, 3, 100 | voice grants before it: 0, 1, 3, 100 |
| waiter cancelled while queued | removed; next waiter served; lock free after |
| waiter granted, then cancelled before it runs | passes the lock on; lock free after |
| sole waiter granted then cancelled | lock free, no waiter |
| holder cancelled (through `held()`) | waiter served; lock free after |
| exception in the holder | `RuntimeError` propagates; lock free |
| `release()` twice | `RuntimeError: model lock t released while not held` |
| the same task nesting two holds of one lock (not a path the app has) | hangs: the lock is not re-entrant |

Through the app, models mapped to two servers:

| Case | Result |
|---|---|
| coordinator delegating to a specialist on the same server | generations gemma, gemma; the lock is released at the end of each `llm.stream`, before the tool round; no deadlock |
| coordinator (gemma) delegating to coding (devstral) | gemma, devstral, gemma; never two held at once |
| devops (devstral) escalating to the coordinator (gemma) | devstral, gemma |
| A (gemma -> devstral) queued on devstral behind B: is gemma held? | no; a third gemma request answered in 2 ms |
| B' (devstral -> gemma) queued on gemma behind D: is devstral held? | no; another devstral request answered |
| 5 seeds x 80 requests at once, both crossing directions and same-server delegations | 400 of 400 HTTP 200, no timeout, no generation without its lock, no server with two, locks free after |
| pipeline generator closed in mid-generation | lock free when `aclose()` returns |
| streamed route, the client stops reading after the first chunk | the server lock stays held until the client reads or disconnects (observation O4) |

Live on dev, `MODEL_LOCK=server`. Load check, eight at once (four voice,
four display), 17:41 UTC, all HTTP 200, no log error
(`docs/bench/m9_e2e_2026-11-17_174109_load-server.json`). Holds of
`127.0.0.1:11438` in grant order, seconds from the first grant:

```
    0.00 ->   2.52  display  wait      0 ms  "what's playing in the kitchen"
    2.53 ->   3.76  voice    wait   2528 ms  "what's playing in the office"
    3.78 ->   3.90  voice    wait   3766 ms  'what is the capital of Spain'
    3.90 ->   9.29  voice    wait   3888 ms  "what's the weather like"
    9.30 ->  10.69  display  wait   9297 ms  'what is the capital of Italy'
   10.71 ->  15.21  voice    wait   6917 ms  "what's playing in the office"
   15.24 ->  16.63  display  wait  15226 ms  'search the web for how tall the statue of li'
   16.65 ->  19.64  display  wait  16638 ms  'what is the weather going to be like tomorro'
   19.67 ->  21.29  display  wait  17129 ms  "what's playing in the kitchen"
   21.31 ->  24.75  display  wait   3814 ms  'search the web for how tall the statue of li'
   24.76 ->  27.40  display  wait   4350 ms  'what is the weather going to be like tomorro'
```

No two holds overlap. Voice goes first; the display request "capital of
Italy" is granted at 9.30 ahead of the waiting voice round because three
voice requests had overtaken it (`max_overtakes: 3`). Totals: voice 0.08,
3.97, 9.37, 15.32 s; display 10.78, 21.40, 24.83, 27.47 s.

A coordinator request delegating to devstral beside gemma voice requests
(`docs/bench/m9_e2e_2026-11-17_180945_devstral.json`):

```
    0.02 ->   2.36  127.0.0.1:11438  display  wait      0 ms  'ask the coding agent to write a python funct'
    2.36 ->  15.30  127.0.0.1:11440  display  wait      0 ms  'ask the coding agent to write a python funct'
    2.37 ->   6.25  127.0.0.1:11438  voice    wait    353 ms  'what is the capital of Sweden, side 1'
    6.59 ->   9.14  127.0.0.1:11438  voice    wait      0 ms  'what is the capital of Sweden, side 2'
    9.49 ->  11.64  127.0.0.1:11438  voice    wait      0 ms  'what is the capital of Sweden, side 3'
   11.99 ->  17.01  127.0.0.1:11438  voice    wait      0 ms  'what is the capital of Sweden, side 4'
   17.03 ->  19.14  127.0.0.1:11438  display  wait   1697 ms  'ask the coding agent to write a python funct'
   19.16 ->  21.39  127.0.0.1:11438  voice    wait   1803 ms  'what is the capital of Sweden, side 5'
```

The request holds only the devstral lock during its 12.9 s devstral
generation; four gemma requests are answered meanwhile. They take 2.2 to
5.1 s each against 1.3 to 1.5 s alone: two servers share one GPU
(observation O10).

## The deadline

Offline, deadline patched to 0.4 s, the stub blocking for ever at the named
point; after each stop a 0.3 s wait (a retry would show), a lock check and
a following voice request. The 21 phase rows below were run in both lock
modes with the same result.

| Phase the deadline falls in | Request | Spoken (exact) | tool_service calls | Sent again | `request_ended` tools_sent | Stored | Locks after | Next request |
|---|---|---|---|---|---|---|---|---|
| waiting for the lock | what is the capital of France | Sorry, that took too long, so I stopped. Please try again. | none | no | [] | nothing | free | answered |
| first generation (coordinator) | what is the capital of France | Sorry, that took too long, so I stopped. Please try again. | none | no | [] | nothing | free | answered |
| first generation (routed specialist) | what's the weather like in Paris | Sorry, that took too long, so I stopped. Please try again. | none | no | [] | nothing | free | answered |
| read-only tool before its call is sent | what's the weather like in Paris | Sorry, that took too long, so I stopped. Please try again. | none | no | [get_weather] | nothing | free | answered |
| read-only tool in flight | what's the weather like in Paris | Sorry, that took too long, so I stopped. Please try again. | GET /weather | no | [get_weather] | nothing | free | answered |
| acting tool before its HTTP call is sent | play some jazz | That's taking too long to confirm. I've already sent the request, so it may still go through. | none | no | [play_music] | nothing | free | answered |
| acting tool sent, no answer yet | play some jazz | That's taking too long to confirm. I've already sent the request, so it may still go through. | POST /music | no | [play_music] | nothing | free | answered |
| synthesis after a read-only tool answered | search the web for when the eiffel tower was built | Sorry, that took too long, so I stopped. Please try again. | GET /search | no | [web_search] | nothing | free | answered |
| delegated specialist's generation | what's playing in the kitchen | Sorry, that took too long, so I stopped. Please try again. | none | no | [] | nothing | free | answered |
| delegated specialist's read-only tool in flight | what's playing in the kitchen | Sorry, that took too long, so I stopped. Please try again. | POST /music/now_playing | no | [now_playing] | nothing | free | answered |
| delegated specialist's acting tool in flight | please add eggs to the list | That's taking too long to confirm. I've already sent the request, so it may still go through. | POST /shopping_list | no | [shopping_list_add] | nothing | free | answered |
| delegated specialist's generation after its acting tool answered | please add eggs to the list | That's taking too long to confirm. I've already sent the request, so it may still go through. | POST /shopping_list | no | [shopping_list_add] | nothing | free | answered |
| coordinator synthesis after a delegated acting tool answered | please add eggs to the list | That's taking too long to confirm. I've already sent the request, so it may still go through. | POST /shopping_list | no | [shopping_list_add] | nothing | free | answered |
| direct dispatch, read-only, in flight | what song is this | Sorry, that took too long, so I stopped. Please try again. | POST /music/now_playing | no | [now_playing] | nothing | free | answered |
| direct dispatch, acting, before its call is sent | pause | That's taking too long to confirm. I've already sent the request, so it may still go through. | none | no | [control_music] | nothing | free | answered |
| direct dispatch, acting, sent, no answer | pause | That's taking too long to confirm. I've already sent the request, so it may still go through. | POST /music/control | no | [control_music] | nothing | free | answered |
| mirror update sent, no answer | update the magic mirror | That's taking too long to confirm. I've already sent the request, so it may still go through. | POST /magicmirror/update | no | [update_magicmirror] | nothing | free | answered |
| streamed writer, nothing sent yet | what is the capital of France | Sorry, that took too long, so I stopped. Please try again. | none | no | [] | nothing | free | answered |
| streamed writer, text already sent | what is the capital of France | the text already sent, then: Sorry, that took too long, so I stopped. Please try again. | none | no | [] | nothing | free | answered |
| streamed writer, acting tool sent, no answer | play some jazz | That's taking too long to confirm. I've already sent the request, so it may still go through. | POST /music | no | [play_music] | nothing | free | answered |
| streamed writer, waiting for the lock | what is the capital of France | Sorry, that took too long, so I stopped. Please try again. | none | no | [] | nothing | free | answered |
| **`news_brief` with `refresh: true`, the refresh sent, no answer** | get me fresh news please | **Sorry, that took too long, so I stopped. Please try again.** | **POST /news/refresh** | no | [news_brief] | nothing | free | answered |

Every row: root span `kronk.ended = deadline`, status ERROR; one
`request_ended`. The last row is finding B1. Display routes (`/api/chat`,
`/v1`, `/message`, streaming and not) with a 1.0 s generation under a 0.4 s
voice deadline answer in full; `deadline_s: null` on the voice mount
answers in full. A client that leaves before the deadline ends once as
`client_gone`; one that closes after the deadline reply ends once as
`deadline`.

`READ_ONLY_TOOLS` against all 22 handlers in `tools._HANDLERS`, each read
with the tool_service endpoint it calls:

| Tool | What it sends | Changes something | On the list | Verdict |
|---|---|---|---|---|
| get_weather | `GET /weather` | no | yes | right |
| web_search | `GET /search` | no | yes | right |
| fetch_url | `GET /fetch` | no | yes | right |
| shopping_list_view | `GET /shopping_list` | no | yes | right |
| shopping_list_add | `POST /shopping_list` | yes | no | right |
| shopping_list_remove | `DELETE /shopping_list/{item}` | yes | no | right |
| shopping_list_clear | `DELETE /shopping_list/clear` | yes | no | right |
| get_kronk_context | reads a file, no HTTP | no | yes | right |
| generate_diagram | `POST /diagram` (writes an image) | yes | no | right |
| query_hottub | `GET /hottub` | no | yes | right |
| solar_status, solar_detail, solar_energy | `GET /solar/...` | no | yes | right |
| play_music | `POST /music` | yes | no | right |
| control_music | `POST /music/control` | yes | no | right |
| rate_music | `POST /music/rate` | yes | no | right |
| remember_music_preference | `POST /music/taste` | yes | no | right |
| now_playing | `POST /music/now_playing` (reads player state) | no | yes | right |
| update_magicmirror | `POST /magicmirror/update` | yes | no | right |
| remote_exec | `POST /ops/exec` | can | no | right |
| http_check | `GET /http_check` | no | yes | right |
| news_brief | `GET /news/brief`; with `refresh: true` first `POST /news/refresh` (a model call and a new stored brief) | with refresh: yes | yes | **wrong (B1)** |

Calls that leave the orchestrator without `note_tool_sent`: only
`agents.weather_context` (`GET /weather/cached`, before the lock) and the
model calls. Direct dispatch, terminal tools and delegated specialists all
go through `tools.execute`.

The loader, on a scratch copy of `config/`: `deadline_s` 5, 25, 120, `null`
and `~` accepted; 4, 121, 0, -3, 12.5, 25.0, "25", `fast`, `true` rejected
with file and line; an empty value is read as null (observation O6); the
key absent gives 25. `max_overtakes` 0, 1, 3, 100 accepted; 101, -1, 2.5,
null, "3" rejected; block absent gives 3.

Live on dev:

- One long voice request without sound
  (`docs/bench/m9_e2e_2026-11-17_181021_deadline-live.json`): "search the
  web for the height of the five tallest buildings in the world, then
  search for the year each one opened, then search for the architect of
  each one, and compare them all in detail" on `/dev/voice/api/chat`.
  HTTP 200 at 25.006 s, reply `Sorry, that took too long, so I stopped.
  Please try again.`; `request_ended reason deadline route direct
  tools_sent ['web_search', 'fetch_url'] duration_s 25.0`; rounds
  coordinator plan 2.33 s, research plan 2.25 s, research plan 3.07 s,
  research synthesis 14.1 s, then the coordinator's synthesis was cut;
  trace `374aa49388d3859572093906bfb8a31e`, root `pipeline.shim`,
  `kronk.ended = deadline`, status ERROR "deadline: the request passed its
  voice deadline", `kronk.voice true`. `{ span.kronk.ended = "deadline" }`
  in Tempo returns it.
- Reload on dev (scratch edit of `config/voice.yaml`, then
  `scripts/config_reload.sh dev`; reverted, see the end):
  `deadline_s: 4` answers `422 {"ok":false,"errors":[{"file":"config/voice.yaml","line":45,"message":"deadline_s: Input should be greater than or equal to 5 (got 4)"}],"kept":"last good values (files, loaded 2026-11-17 18:11:56)"}`;
  121 answers 422 likewise; a 5.0 s voice request after both still
  completes (25 in effect). `deadline_s: 5` answers
  `200 {"ok":true,"changed":["voice.yaml:deadline_s: 25 -> 5"]}`; then a
  streamed voice story ends at 5.005 s with `request_ended reason
  deadline`, a buffered voice "play some jazz in the office" ends at
  5.005 s with the "I stopped" sentence and `tools_sent []`, a 2.9 s voice
  weather request completes, and a display story runs 8.2 s to its end.
  `deadline_s: null` answers `200 ... 5 -> None`; a voice story runs 9.3 s
  to its end. Restored: `200 ... None -> 25`.

## Disconnects

Live on dev through nginx (the container's Starlette 0.38.6 and uvicorn
0.30.6; ruling 6), raw sockets to this host's loopback. Request: a
400-word story (coordinator). "mid" is after 300 body bytes on a streamed
shape and at 3 s on a buffered one. After each drop one `/dev/api/chat`
request. `docs/bench/m9_e2e_2026-11-17_174253_disconnects.json`.

| Route shape | Drop | Dropped at s | Bytes received before | Ended (reason) at s | llama.cpp cancel lines | Trace `kronk.ended` | Next request: status, s, lock wait ms |
|---|---|---|---|---|---|---|---|
| message | 0.2s | 0.201 | 316 | request_ended (client_gone) 0.2 | 1 | client_gone | 200, 3.709, [0] |
| message | 1s | 1.001 | 316 | request_ended (client_gone) 1.0 | 1 | client_gone | 200, 0.547, [0] |
| message | mid | 1.308 | 503 | request_ended (client_gone) 1.31 | 1 | client_gone | 200, 1.815, [0] |
| api_chat | 0.2s | 0.2 | 0 | request_ended (client_gone) 0.2 | 1 | client_gone | 200, 0.619, [0] |
| api_chat | 1s | 1.001 | 0 | request_ended (client_gone) 1.0 | 1 | client_gone | 200, 1.996, [0] |
| api_chat | mid | 3.002 | 0 | request_ended (client_gone) 3.0 | 1 | client_gone | 200, 0.462, [0] |
| api_chat_stream | 0.2s | 0.201 | 166 | request_ended (client_gone) 0.2 | 1 | client_gone | 200, 2.366, [0] |
| api_chat_stream | 1s | 1.0 | 753 | request_ended (client_gone) 1.0 | 1 | client_gone | 200, 0.686, [0] |
| api_chat_stream | mid | 2.696 | 612 | request_ended (client_gone) 2.69 | 1 | client_gone | 200, 0.643, [0] |
| v1 | 0.2s | 0.2 | 0 | request_ended (client_gone) 0.2 | 1 | client_gone | 200, 3.225, [0] |
| v1 | 1s | 1.001 | 0 | request_ended (client_gone) 1.0 | 1 | client_gone | 200, 0.961, [0] |
| v1 | mid | 3.003 | 0 | request_ended (client_gone) 3.0 | 1 | client_gone | 200, 0.136, [0] |
| v1_stream | 0.2s | 0.2 | 398 | request_ended (client_gone) 0.2 | 1 | client_gone | 200, 0.89, [0] |
| v1_stream | 1s | 1.001 | 398 | request_ended (client_gone) 1.0 | 1 | client_gone | 200, 0.676, [0] |
| v1_stream | mid | 1.19 | 598 | request_ended (client_gone) 1.19 | 1 | client_gone | 200, 0.139, [0] |
| voice_buffered | 0.2s | 0.201 | 0 | request_ended (client_gone) 0.2 | 1 | client_gone | 200, 1.583, [0] |
| voice_buffered | 1s | 1.001 | 0 | request_ended (client_gone) 1.0 | 1 | client_gone | 200, 0.181, [0] |
| voice_buffered | mid | 3.003 | 0 | request_ended (client_gone) 3.0 | 1 | client_gone | 200, 0.178, [0] |
| voice_streamed | 0.2s | 0.201 | 166 | request_ended (client_gone) 0.2 | 1 | client_gone | 200, 2.576, [0] |
| voice_streamed | 1s | 1.001 | 166 | request_ended (client_gone) 1.0 | 1 | client_gone | 200, 1.71, [0] |
| voice_streamed | mid | 4.408 | 687 | request_ended (client_gone) 4.41 | 1 | client_gone | 200, 0.505, [0] |

21 of 21: `request_ended reason client_gone` at the drop time (to 0.02 s),
no `request_complete`, no generation finished, one `stop: cancel task` line
in llama.cpp's journal, the root span says `client_gone` and is not ERROR,
and the next request is answered with a lock wait of 0 ms. No slot or
context error in the logs.

A disconnect around a dry-run music tool call
(`docs/bench/m9_e2e_2026-11-17_182330_disconnect_tool.json`): 24 drops
swept across the moment the home agent calls `play_music` (2.45 to 2.91 s)
and 16 across the direct-dispatch "pause" (2 to 32 ms). 10 ended as
`client_gone` with the tool recorded as sent (`play_music` once,
`control_music` nine times); the rest ended before the call or completed.
In all 40: never more than one `POST /music...` at `tool_service-dev`
within 1.2 s after, no `tool_error`, no failure event, no second
`tool_call`. (tool_service's access log omits a request whose caller has
gone, so "one POST logged" was seen once; "never two" holds for all.)

False positives
(`docs/bench/m9_e2e_2026-11-17_180122_false_positives.json`): every route
shape, through nginx and straight to the dev container's Docker address.

| Via | Case | Requests | Answered 200 and `request_complete` | Ended as |
|---|---|---|---|---|
| nginx | plain | 7 | 7 | request_complete |
| nginx | slow-body | 7 | 7 | request_complete |
| nginx | large-body-2MB | 7 | 7 | request_complete |
| nginx | expect-100 | 7 | 7 | request_complete |
| nginx | http-1.0 | 7 | 7 | request_complete |
| nginx | connection-close | 7 | 7 | request_complete |
| nginx | half-close | 7 | 0 | None |
| nginx | keep-alive | 9 | 9 | request_complete |
| nginx | chunked-upload | 3 | 3 | request_complete |
| direct | plain | 7 | 7 | request_complete |
| direct | slow-body | 7 | 7 | request_complete |
| direct | large-body-2MB | 7 | 7 | request_complete |
| direct | expect-100 | 7 | 7 | request_complete |
| direct | http-1.0 | 7 | 7 | request_complete |
| direct | connection-close | 7 | 7 | request_complete |
| direct | half-close | 7 | 0 | client_gone |
| direct | keep-alive | 9 | 9 | request_complete |
| direct | chunked-upload | 3 | 3 | request_complete |
| direct | pipelined-pair | 2 | 2 | request_complete |
| nginx | GET /api/agents | 1 | 1 | 200 |
| nginx | GET /api/tags | 1 | 1 | 200 |
| nginx | GET /voice/api/tags | 1 | 1 | 200 |
| nginx | GET /api/version | 1 | 1 | 200 |
| nginx | GET /v1/models | 1 | 1 | 200 |
| nginx | history | 1 | 1 | 200 |
| nginx | files | 1 | 1 | 200 |
| nginx | GET /api/status | 1 | 1 | 200 |
| nginx | POST /api/show | 1 | 1 | 200 |
| nginx | files (upload) | 1 | 1 | 200 |
| nginx | files after upload | 1 | 1 | 200 |
| nginx | DELETE /files/m9e2e-note.txt | 1 | 1 | 200 |
| nginx | files after delete | 1 | 1 | 200 |

- **123 of 123 normal requests were answered with 200 and ended as
  `request_complete`**: plain, body sent in 8 pieces over 2 s, a 2 MB body,
  `Expect: 100-continue`, HTTP/1.0, `Connection: close`, nine requests on
  one keep-alive connection, a chunked upload, and two requests pipelined
  on one socket straight to uvicorn.
- **Half-close** (`shutdown(SHUT_WR)` after the request) is the one case
  not answered: through nginx the client gets 0 bytes at once and the
  request never reaches the orchestrator; straight to uvicorn it ends as
  `client_gone` with 0 bytes. This is not the middleware: the same client
  gets 0 bytes on `GET /api/status` (a route the middleware passes through
  untouched) straight to uvicorn, twice of twice, and on `POST /api/show`
  through nginx. uvicorn 0.30.6 closes the transport when the peer sends
  EOF (`eof_received` returns nothing), and nginx treats it as the client
  closing. A half-closing client was never served on this stack. `curl`
  and HA's client do not half-close.
- The replay through HA's real client on `kronk-dev`: 72 rows executed,
  72 `request` events on dev, 0 `request_ended`.
- The middleware, read and driven by hand offline (`spec_version 2.3`): it
  drains the body itself and replays the same messages in order with their
  `more_body` flags; after the body the handler's `receive()` blocks until
  the client really leaves, or returns `http.disconnect` once the response
  is complete, as uvicorn's own does. A body in 3 chunks, an empty body, a
  5 MB body in 80 chunks arrive intact. On any other path (`GET` routes,
  `/files`, `/history`, websocket and lifespan scopes) the handler is given
  the server's own `receive` and `send`: live, eight `GET` routes,
  `POST /api/show`, and a `/files` upload, list and delete all answer 200.
  A handler exception reaches the server stack (422, 400, 405 reach the
  client); a `CancelledError` the handler raises itself is not marked
  `client_gone`.

## Sessions

Live on dev (`docs/bench/m9_e2e_2026-11-17_180205_sessions.json`): three
rounds of eight `/dev/message` requests at once, six on one session
(`m9e2e-shared-1794938497`) and one each on two others. 24 of 24 HTTP 200
with `[DONE]`. Read back from `data/dev/sessions.db` (read-only): the
shared session holds 36 messages, each user message once, each followed by
that request's own reply, in an order that is a valid interleaving (round
0: Canada, Norway, Japan, Kenya, Egypt, Peru); the other two hold 6 each
in round order. A following request on the shared session ("which of those
capitals did I ask about first?") answered "You first asked about Ottawa,
the capital of Canada.", which is the stored order.

`DELETE /dev/history` during a request: the route clears dev's web UI
session only, so the test used it. It held earlier test turns ("pause the
music please", ...). A story request on it, `DELETE` 1.52 s in: `200
{"status":"cleared"}`; the request finished at 6.23 s with `[DONE]`; the
session then holds exactly that exchange (user, assistant). No traceback,
no `pipeline_error`, no `request_ended` in the log for the whole test.

## Guards under interleaving

Offline through the app, my own run, not the builder's test: 117 requests
(9 x 13 kinds), first one by one, then all at once in shuffled order with
random delays in the model, between tokens and in tool_service; 5 seeds;
both lock modes. Kinds: voice and display coordinator answers; voice and
`/v1` music (origin and `said` in the payload); a failing delegated
`now_playing` on voice and on `/api/chat`; direct "pause" with nine
origins; `/message` on three shared sessions; a model error on `/api/chat`
streamed (debug style) and on voice (friendly style); the pinned mirror
sentence by voice; a devops request whose model then asks for `web_search`,
`fetch_url`, `update_magicmirror` and `play_music`; a streamed-voice music
request whose model calls `update_magicmirror` though the words are not the
mirror sentence.

| Mode | Seeds | Differences from each request alone (replies / tool calls and payloads / model inputs / trace attributes / stored history / guard events) | Mirror POSTs for 9 pinned sentences | Payloads with another request's origin or words | Locks after |
|---|---|---|---|---|---|
| server | 5 | 0 / 0 / 0 / 0 / 0 / 0 in every seed | 9 | 0 | free |
| pipeline | 5 | 0 / 0 / 0 / 0 / 0 / 0 in every seed | 9 | 0 | free |

Alone and together alike: 9 `mm_update_refused`, 36 `tool_rejected` for
the devops agent (9 each of the four tools), no `/search`, `/fetch` or
`/music` call from devops. Styles: `/api/chat`
`[coordinator agent error: LiteLLM returned 503: upstream down]`; voice
`Sorry — I couldn't reach my language model. Try again in a moment.` The
comparison detects a leak: with `origin.current` and `origin.said` made
process-wide, every seed shows 9 to 18 different replies and 18 payloads
carrying another request's origin or words. Dev has no mirror key; the
mirror sentence was not sent live.

## The rollback switch

Live on dev, by scratch compose overrides for `orchestrator-dev`:

- `MODEL_LOCK=pipeline`: the load check, eight at once
  (`docs/bench/m9_e2e_2026-11-17_181125_load-pipeline.json`). One request's
  model phases at a time, in arrival order; direct dispatch does not wait:

```
    0.00 ->   5.74  pipeline  voice    wait      0 ms  "what's the weather like"
    5.74 ->   7.09  pipeline  voice    wait   5731 ms  'what is the capital of Spain'
    7.09 ->  13.52  pipeline  voice    wait   7087 ms  "what's playing in the office"
   13.52 ->  15.17  pipeline  display  wait  13516 ms  'what is the capital of Italy'
   15.17 ->  23.05  pipeline  display  wait  15166 ms  "what's playing in the kitchen"
   23.05 ->  29.98  pipeline  display  wait  23045 ms  'what is the weather going to be like tomorro'
   29.98 ->  36.66  pipeline  display  wait  29969 ms  'search the web for how tall the statue of li'
```

  All HTTP 200; "what song is this" 0.05 s with no lock; last request done
  at 36.7 s against 27.5 s in server mode.
- `MODEL_LOCK=banana`: one log line, `ERROR model_lock: MODEL_LOCK='banana'
  is not server or pipeline; using pipeline`; both requests take the lock
  named `pipeline`.
- Unset (the variable absent in the container): the lock is
  `127.0.0.1:11438`, the per-server default.
- Offline: unset and `server` give server; `pipeline`, `PIPELINE` give
  pipeline; `' Server '` is accepted; `''`, `bogus`, `per-server` give
  pipeline with one error line each however many requests. With `pipeline`
  or an unknown value a second request's generation does not start while
  the first waits in a tool call; with `server` it is answered meanwhile.

## Telemetry, docs and the promotion notes

Telemetry (criterion 16), trace `cd422fec30bbeb2628b2b2ff4f810cb2` from the
load check: `POST /api/chat` > `pipeline.shim` > `agent.coordinator` >
`llm.gemma-4-e4b` (10,689 ms, input tokens 2011, `kronk.cached_tokens`
2005) > `lock.wait` (9,297 ms; `kronk.lock 127.0.0.1:11438`,
`kronk.priority display`, `kronk.wait_ms 9297`) beside the model's `POST`
(98 ms). The wait is separate from the generation, with which lock and
which class. In Tempo over the last hour of the run:
`{ span.kronk.ended = "deadline" }` 1 trace,
`{ span.kronk.ended = "client_gone" }` 28 (my 21 drops and 7 direct
half-closes), `{ name = "lock.wait" && span.kronk.priority = "display" &&
span.kronk.wait_ms > 10000 }` 8. One query each.

Docs (criterion 20): `docs/features/model-locks.md` describes the locks,
the queue, the deadline, disconnects and how to read the wait; its numbers
agree with mine. `TECH_DEBT.md`, the lock's comment in `main.py` and
`README.md` no longer say one request at a time. Expected latency is
stated. Inaccuracies: O2 (the `max_overtakes: 0` comment), O5, and the
rollback wording (O1 below).

The builder's promotion notes against how production is deployed
(`kronk-orchestrator-1` runs from
`kronk-releases/sprint-M8`, with that directory's `config/`,
`litellm/config.yaml` and compose file):

- "The release's `config/` must come with the code": right, and it does by
  construction (`deploy_tag.sh` unpacks the tag, `config/` included).
- "`docker-compose.yml` sets `MODEL_LOCK=server` on production's
  orchestrator": right; the tag's compose file carries it.
- The server names come from `/litellm-config.yaml`, which production
  mounts from the release: present, so production gets `127.0.0.1:11438`
  and `:11440` as dev does.
- Rollback rows "`MODEL_LOCK=pipeline` on the orchestrator service and
  recreate it (`up -d`, no rebuild)" and the feature doc's "env on the
  orchestrator service" do not say where: see O1.
- "`deadline_s: null` in the release's `config/voice.yaml`, then
  `scripts/config_reload.sh prod`": right as written.
- The promotion disconnect check (`curl --max-time 1` a coordinator
  question to production's `/voice/api/chat`) is sound: on dev that shape
  ended as `client_gone` at 1.0 s in 1 of 1.

## Tests, mutants and the replay

### The suite

Run with a recorder plugin that refuses and logs every name lookup and
every IPv4/IPv6 connect or send (O12); three runs of the command the
script wraps in a scratch worktree of HEAD, then the script itself in the
working tree:

```
=========== 4516 passed, 3 skipped, 53 xfailed, 6 warnings in 40.70s ===========
=========== 4516 passed, 3 skipped, 53 xfailed, 6 warnings in 40.71s ===========
=========== 4516 passed, 3 skipped, 53 xfailed, 6 warnings in 42.67s ===========
$ ./scripts/run_tests.sh -p netrecorder -p no:cacheprovider --durations=40
=========== 4516 passed, 3 skipped, 53 xfailed, 6 warnings in 39.88s ===========
```

The claim holds, 4 of 4. Refused attempts per run: 38 to 40, from
`test_agentic_loop.py` (10), `test_error_surfacing.py` (5),
`test_retract.py` (5), `test_render.py` (4), `test_playback_control.py`
(1), `test_tool_service_weather.py` (6 to 8), `test_magicmirror.py` (1),
and 6 in `test_http_check.py` that the repo's own `no_network` fixture
refuses on purpose. None from `tests/test_m9_*.py` or `tests/fixtures/m9`.

Where the 9 seconds went: the six M9 files take 8.4 s (81 tests).
`test_m9_deadline.py` 4.99 s (six tests wait out a real 0.4 s deadline, one
sleeps 0.7 s, eleven disconnect tests wait 0.2 s each),
`test_m9_interleave.py` 2.20 s (54 requests twice with random stub sleeps),
`test_m9_lock.py` 0.73 s, `test_m9_sessions.py` 0.43 s. Slowest tests in
the suite, verbatim:

```
3.00s call     tests/test_music_rate.py::test_thumbs_down_whose_skip_does_not_take_says_so
3.00s call     tests/test_playback_control.py::test_resume_with_nothing_to_resume_is_an_honest_failure
3.00s call     tests/test_playback_control.py::test_no_effect_is_a_failure_not_a_claim
2.22s call     tests/test_ops_exec.py::test_remote_timeout_kills_a_process_that_ignores_term
1.88s call     tests/test_m9_interleave.py::test_many_mixed_requests_at_once_match_each_run_alone
1.51s call     tests/test_config_fix2.py::test_both_loaders_agree_on_generated_texts
1.03s call     tests/test_ops_exec.py::test_remote_timeout_kills_the_whole_pipeline
1.03s call     tests/test_config_hardening.py::test_hostile_yaml_within_the_cap_parses_fast[tool_service]
1.02s call     tests/test_ops_exec.py::test_lexer_agrees_with_bash_on_random_lines
0.91s call     tests/test_config_hardening.py::test_hostile_yaml_within_the_cap_parses_fast[orchestrator]
0.71s call     tests/test_m9_deadline.py::test_display_requests_have_no_deadline
0.62s call     tests/test_speech_stream.py::test_generated_replies_join_back_and_never_withdraw_a_cut
0.50s call     tests/test_routing_pin.py::test_outcome_is_the_first_matching_rule_in_order
0.47s call     tests/test_music_say.py::test_every_corpus_play_is_a_uri_never_the_words
0.43s call     tests/test_routing_pin.py::test_classify_returns_the_pinned_route
```

Waits on a real clock that could flake: only two have a finite margin. The
0.4 s test deadline needs routing, the stubbed generation and (in two
tests) the tool call recorded inside it: about 10 ms of work, a margin of
about 40. The 0.2 s disconnect needs the `/music` POST made before it in
`test_disconnect_during_a_tool_call…`: a margin of about 20. A stall of a
few hundred milliseconds in the test process would fail them with a wrong
sentence, not hide a failure. The other sleeps are followed by asserts on
callbacks the loop runs before any timer fires. Probe: the six files 20
times in a row, 5 times beside four busy loops, and 3 times pinned to one
core shared with four busy loops (11.2 to 12.0 s): `81 passed` 28 of 28.

### Mutation check

A scratch worktree of HEAD, 114 hand-written mutants in `model_lock.py`,
`client_watch.py`, `run_state.py`, `sessions.py`, `llm.py`, and the
deadline and cancellation code in `main.py`, `agents.py`, `errors.py` and
`tools.py`; each run first against the six M9 files, `test_sessions.py`,
`test_direct_dispatch.py`, `test_voice_stream.py`,
`test_spoken_failures.py` and `test_error_style.py`, survivors then
against the whole suite, all with the recorder and `timeout 300`. No
socket was opened (one mutant caused a lookup of `fake-llm`, refused by
the `no_network` fixture). **74 killed (4 by hang), 40 survived.** Killed
include: display before voice, the guard dropped, the off-by-one on the
guard, a granted-then-cancelled waiter not passing the lock on, `held()`
not releasing, one lock for all servers, the priority inverted, the mode
default changed, no lock in server mode, the watcher never cancelling, a
dropped body chunk, each chat path removed, the tool recorded after the
call, the sentence choice inverted, a denying sentence, each of ten acting
tools added to `READ_ONLY_TOOLS`, the deadline applied to display or not
applied to voice, unlocked or split session writes.

Survivors (what they mean is in O7):

| Mutant | Change | Where | Result |
|---|---|---|---|
| L04b | overtakes counted for voice waiters too | `model_lock.py:120` | **SURVIVED** (relevant files and whole suite). Equivalent: a voice waiter's `overtaken` is never read. |
| L06 | cancelled waiter not removed from the queue | `model_lock.py:99` | **SURVIVED** (relevant files and whole suite). Near-equivalent: `release()` skips a done future (L18's line), so the dead waiter is dropped at the next release. Until then `waiting()` lists it. The two lines back each other up; neither is pinned alone. |
| L15 | release of an unheld lock is silent | `model_lock.py:113` | **SURVIVED** (relevant files and whole suite). Unpinned defensive check (release of an unheld lock raises). No caller does it. Low. |
| L17 | newest starving display waiter first | `model_lock.py:108` | **SURVIVED** (relevant files and whole suite). Real, small gap: with two display waiters both past the guard, which goes first (oldest) is not pinned. |
| L18 | release does not skip a dead waiter | `model_lock.py:117` | **SURVIVED** (relevant files and whole suite). Equivalent while L06's removal is in place (a cancelled waiter never stays queued). |
| M02 | llm.stream takes the server lock in pipeline mode too | `llm.py:26` | **SURVIVED** (relevant files and whole suite). Unpinned: that `MODEL_LOCK=pipeline` takes NO per-server lock. With both locks nested nothing visible changes. Low. |
| C01 | cancel even when the response is complete | `client_watch.py:72` | **SURVIVED** (relevant files and whole suite). Real gap, and on the false-positive side: the `complete` flag is not pinned. `test_a_completed_response_is_not_a_disconnect` passes because the app task is already done when the disconnect is read, not because of the flag (see C19). In uvicorn, `receive()` answers `http.disconnect` as soon as the response is complete, while the app task is still unwinding (middleware tails, span export), so the flag is what stops a cancel landing in that tail. The response is already sent by then; effect would be a spurious `client_gone` mark, not a cut-off client. |
| C10 | watcher never cancelled | `client_watch.py:88` | **SURVIVED** (relevant files and whole suite). Unpinned: watcher cancelled at the end. Under uvicorn the watcher ends by itself once the response completes; a leak only with a server whose receive() blocks. Low. |
| C12 | disconnect before the body runs the app anyway | `client_watch.py:40` | **SURVIVED** (relevant files and whole suite). Unpinned: disconnect before the body arrives. Low. |
| C14 | any cancellation of the app is swallowed | `client_watch.py:86` | **SURVIVED** (relevant files and whole suite). Unpinned: a cancellation that is not a client disconnect (server shutdown) is re-raised. Low. |
| C15 | the app is never told of the disconnect | `client_watch.py:75` | **SURVIVED** (relevant files and whole suite). Equivalent in effect: the middleware cancels the app task itself; telling the app is belt and braces. |
| C16 | no shield around the app task | `client_watch.py:80` | **SURVIVED** (relevant files and whole suite). Unpinned: the shield (server-shutdown path). Low. |
| C18 | server shutdown does not cancel the app task | `client_watch.py:82` | **SURVIVED** (relevant files and whole suite). Unpinned: server shutdown cancels the app task. Low. |
| C19 | complete never set (always treated as incomplete) | `client_watch.py:55` | **SURVIVED** (relevant files and whole suite). Same gap as C01: `complete` never being set changes no test. |
| R02 | ensure() always makes a new Run | `run_state.py:34` | **SURVIVED** (relevant files and whole suite). Real gap: every disconnect test runs with `deadline_s` None, so the wrapper's `ensure()` is never reached on a disconnect. With a deadline configured (production's setting) and this mutant, the pipeline would see a different Run from the middleware's and report `cancelled` instead of `client_gone`. The combination disconnect + deadline on is untested offline. |
| R04 | ensure() does not keep the Run it makes | `run_state.py:37` | **SURVIVED** (relevant files and whole suite). Equivalent in production (the middleware always sets the Run); matters only for an in-process caller. |
| D05 | deadline_s null still applies | `main.py:899` | **SURVIVED** (relevant files and whole suite). Real gap: `deadline_s: null` switching the deadline off is not pinned through a request (the disconnect tests set None but end at 0.2 s, long before 25 s). |
| D06 | a stopped turn is stored | `main.py:640` | **SURVIVED** (relevant files and whole suite). Real gap: ruling 3's behaviour (a stopped turn is not stored) is pinned only for a turn with no text. A `/message` turn stopped after tokens streamed could be stored and no test would notice. |
| D08 | _why_stopped ignores run.ended | `main.py:651` | **SURVIVED** (relevant files and whole suite). Unpinned ordering in `_why_stopped` (run.ended first). Same untested combination as R02. |
| D09 | _why_stopped: any stop of a voice request is a deadline | `main.py:655` | **SURVIVED** (relevant files and whole suite). Real gap, same combination: with a deadline configured, any stop of a voice request (a disconnect included, if client_gone were lost) would be reported as `deadline`; not pinned. |
| D09b | _why_stopped: deadline checked before client_gone | `main.py:653` | **SURVIVED** (relevant files and whole suite). Unpinned: client_gone checked before the deadline clock (a client that leaves after the deadline instant). Low. |
| D11 | _with_deadline does not close the pipeline | `main.py:689` | **SURVIVED** (relevant files and whole suite). Unpinned: `_with_deadline` closing the pipeline when its consumer closes early (disconnect with a deadline configured). After a timeout the generator is already finished, so the tested path does not need it. |
| D13 | buffered reply appends the sentence to partial text | `main.py:1112` | **SURVIVED** (relevant files and whole suite). Real gap, user-audible: on the buffered voice path (production's) the reply at the deadline must be the sentence alone. The buffered tests hang before any token, so a mutant that speaks the partial answer and then the sentence passes. Only the streamed writer's partial-text case is pinned. |
| D15 | no space between sent text and the sentence | `main.py:1236` | **SURVIVED** (relevant files and whole suite). Unpinned: the space between already-sent text and the sentence on the streamed path (test uses startswith/endswith). Cosmetic for TTS. |
| D20 | deadline ten times longer | `main.py:673` | **SURVIVED** (relevant files and whole suite). Unpinned: that the stop happens AT `deadline_s` and not later (tests assert the sentence, not when). The run took 62 s instead of 41 s and passed. |
| D25 | _direct_dispatch swallows its cancellation | `main.py:716` | **SURVIVED** (relevant files and whole suite). Unpinned: cancellation during a direct dispatch (no test stops a request inside `_direct_dispatch`). |
| D26 | _kronk_pipeline_tokens does not close its events | `main.py:907` | **SURVIVED** (relevant files and whole suite). Unpinned: `_kronk_pipeline_tokens` closing its events in `finally` (covered in effect by the callers' aclosing and the wrapper). |
| D27 | no deadline check between events | `main.py:677` | **SURVIVED** (relevant files and whole suite). Equivalent: `timeout_at` with a deadline already past raises at once. |
| D29 | pipeline does not handle a closed stream (GeneratorExit) | `main.py:601` | **SURVIVED** (relevant files and whole suite). Real gap: no test closes the pipeline generator from the consumer side (GeneratorExit); every tested stop is a task cancellation. |
| A03 | forced-synthesis handler swallows cancellation | `agents.py:882` | **SURVIVED** (relevant files and whole suite). Unpinned: cancellation during the forced synthesis round. |
| A05 | agents.py: aclosing closes nothing | `agents.py:12` | **SURVIVED** (relevant files and whole suite). Real gap: the `aclosing` wrappers in agents.py are not pinned. Every tested stop is a CancelledError, which unwinds through awaits without them. They matter when a generator is closed between events (GeneratorExit): without them the inner `llm.stream` generator, which holds the server lock, is closed only when the garbage collector finalises it. |
| A06 | main.py: aclosing closes nothing | `main.py:7` | **SURVIVED** (relevant files and whole suite). Same as A05 for main.py's wrappers. |
| A07 | tool span not ended on cancellation | `agents.py:781` | **SURVIVED** (relevant files and whole suite). Unpinned: the tool span is ended on cancellation (telemetry only). |
| S01 | append_exchange in one transaction per message (still locked) | `sessions.py:125` | **SURVIVED** (relevant files and whole suite). Equivalent under the lock unless the process dies between the two transactions (atomicity of the pair on disk is not pinned). |
| S04 | clear without the lock | `sessions.py:139` | **SURVIVED** (relevant files and whole suite). Unpinned: `clear` under the lock. Only matters with threads; production has none. |
| S05 | window reads the live list | `sessions.py:92` | **SURVIVED** (relevant files and whole suite). Equivalent: the slice that follows copies. |
| S06 | window without the lock | `sessions.py:91` | **SURVIVED** (relevant files and whole suite). Unpinned: `window` under the lock. Threads only. |
| S10 | plain Lock instead of RLock | `sessions.py:49` | **SURVIVED** (relevant files and whole suite). Equivalent: nothing re-enters the lock. |
| S11 | pipeline stores the pair as two appends (no await between) | `main.py:641` | **SURVIVED** (relevant files and whole suite). Equivalent in asyncio (no await between the two appends), which is the builder's own check-first finding; `append_exchange` being the pipeline's call is not pinned. |
| S13 | append replaces the cached list object (same contents) | `sessions.py:135` | **SURVIVED** (relevant files and whole suite). Equivalent. |

### The replay

```
$ python3 scripts/voice_replay.py --label m9-e2e
voice_replay: pipeline kronk-dev → http://localhost/dev/voice, dry-run on, 160 rows (0 not selected)
  . pass  checked    plan-anime-pop-radio-wet
  . pass  checked    inc-1108-anime-pop-transcript
  . pass  checked    inc-1108-anime-pop-wet-basement
  . pass  checked    inc-1108-mushroom-jazz-basement
  . pass  checked    inc-1108-artist-portishead-basement
  . pass  checked    inc-1108-pause-basement-player
  . pass  checked    inc-1108-timer-2min
  . pass  executed   plan-orbital-station-youtube
  . pass  executed   inc-1004-tell-kronk-orbital
  . pass  checked    inc-1004-play-orbital-radio
  x xfail executed   plan-like-massive-attack-radio
  x xfail executed   inc-1004-well-play-orbital
  . pass  checked    inc-1004-french-dinner-pandora
  . pass  checked    inc-1004-french-dinner
  . pass  checked    inc-1004-french-cafe-pandora
  . pass  checked    plan-girl-with-the-sun
  . pass  checked    plan-shuffle-anime-bangers
  . pass  checked    plan-put-on-some-jazz
  . pass  executed   plan-stop-bang
  . pass  executed   inc-0910-stop-transcript
  . pass  checked    plan-ninetys-two-thousands
  x xfail executed   plan-mishearing-pasta
  x xfail executed   plan-mishearing-whats-up
  . pass  checked    inc-0915-daisies-album-eels-ytm
  . pass  checked    inc-0915-pause-kitchen
  . pass  checked    inc-0915-skip-kitchen
  x xfail checked    inc-0915-who-is-this
  F fail  executed   inc-0930-search-deep-house-ytm  [tool ['coordinator:ask_research', 'research:web_search']]
  . pass  executed   inc-1019-thumbs-up-dot
  . pass  executed   inc-1019-thumbs-up
  . pass  executed   inc-1019-thumbs-down-dot
  . pass  checked    inc-1019-destiny-waving
  . pass  checked    inc-1019-destiny-waving-ytm-kitchen
  . pass  checked    inc-1017-hotel-costes-albums-atlas
  x xfail executed   inc-1017-pay-the-album-hotel-costes-3
  s skip  skipped    inc-1017-try-playing-it-ytm
  s skip  skipped    inc-1019-ytm-link
  x xfail executed   inc-1021-ninetys-no-verb
  . pass  checked    inc-1021-ninetys-from-pandora
  . pass  checked    inc-1021-woodstock-from-pandora
  . pass  checked    inc-1021-shuffle-old-man-sings-basement
  . pass  checked    inc-1025-ninetys-quoted-kitchen
  . pass  checked    m17-play-some-jazz-office
  . pass  checked    m11-some-deep-house-chill
  . pass  checked    m11-italian-music
  . pass  checked    m11-deep-house
  . pass  checked    m11-portishead
  . pass  checked    m11-play-the-news
  . pass  checked    eval-play-top-pink-floyd-songs-on-the
  . pass  checked    eval-play-k-pop-radio-on-sonos-move
  . pass  executed   eval-shuffle-the-anime-bangers-playlist-on-sonos
  . pass  checked    eval-play-music-by-pink-floyd
  . pass  checked    eval-play-pink-floyd-on-sonos-move
  . pass  checked    eval-play-some-jazz-on-the-sonos-move
  . pass  checked    eval-play-the-beatles-in-the-kitchen
  . pass  checked    eval-put-on-some-mellow-pink-floyd-in
  . pass  executed   eval-pause-sonos-move-dirt
  . pass  checked    eval-play-albuquerque-by-weird-al-yankovic
  . pass  checked    eval-play-top-songs-by-weird-al-yankovic
  . pass  checked    eval-play-journey-on-the-sonos
  . pass  checked    eval-play-pink-floyd-on-the-blue-group
  . pass  checked    eval-play-pink-floyd-on-the-sonos-move
  . pass  executed   eval-stop-sonos-move-derp
  . pass  checked    eval-play-don-t-stop-me-now-by
  . pass  checked    eval-play-the-artist-aerosmith-in-the-office
  . pass  checked    eval-play-toys-in-the-attic-by-aerosmith
  x xfail executed   eval-played-the-album-toys-in-the-attic
  . pass  executed   eval-resume-the-music
  . pass  checked    eval-shuffle-my-youtube-music-playlist-anime-bankers
  . pass  checked    eval-shuffle-my-youtube-playlist-old-man-video
  . pass  checked    eval-shuffle-my-youtube-playlist-anime-bangers
  . pass  checked    eval-shuffle-the-youtube-music-playlist-anime-bangers
  . pass  checked    eval-play-some-aerosmith-please
  . pass  checked    eval-put-on-some-aerosmith
  x xfail executed   eval-oh-play-portishead-radio
  . pass  checked    eval-put-on-the-radio-station-zorblax-fnordwave
  . pass  executed   eval-stop
  . pass  checked    eval-pause
  . pass  executed   eval-resume-the-music-2
  . pass  checked    eval-skip-this-song
  x xfail executed   eval-i-have-to-play-the-radio-station
  x xfail executed   eval-stop-stop
  . pass  executed   eval-what-song-is-this
  . pass  executed   eval-what-s-playing
  . pass  checked    eval-play-groove-armada-radio-from-pandora-in
  . pass  executed   eval-stop-2
  . pass  executed   eval-what-song-is-this-2
  . pass  checked    eval-play-the-albo-daisies-of-the-galaxy
  . pass  checked    eval-shuffle-my-youtube-playlist-video-game-pretty
  . pass  checked    eval-shuffle-the-youtube-playlist-old-man-video
  x xfail executed   eval-skip-the-current-song
  . pass  executed   eval-skip-treat-me-my-wallet-son
  . pass  executed   eval-turn-it-down
  x xfail executed   eval-volume-50
  . pass  executed   eval-what-song-is-this-3
  x xfail executed   eval-i-shuffled-my-youtube-playlist-old-man
  x xfail executed   eval-late-evening-from-radio-on-pandora
  . pass  checked    eval-skip
  x xfail executed   eval-j-envoie-my-youtube-playlist-video-game
  X xpass executed   eval-shuffle-it  []
  . pass  checked    eval-play-deep-chill-house-radio-from-pandora
  x xfail executed   eval-clay-thief-recreperation-radio-on-pandora
  . pass  checked    eval-skip-2
  F fail  executed   eval-look-for-some-good-deep-house-chill  [tool []]
  . pass  executed   eval-search-youtube-music-for-some-deep-chill
  . pass  checked    eval-play-lunchlady-land-by-adam-sandler-in
  . pass  checked    eval-play-lunchlady-land-by-adam-sandler-in-2
  . pass  checked    eval-play-the-song-lunchlady-land-by-adam
  . pass  executed   eval-what-song-is-playing-in-the-kitchen
  . pass  executed   eval-what-s-playing-on-the-kitchen
  . pass  executed   eval-what-s-playing-on-the-kitchen-2
  x xfail executed   eval-i-hope-you-play-anime-pop-radio
  x xfail executed   eval-we-play-justin-johnson-radio-on-pandora
  F fail  executed   eval-start-a-zzqx-florbent-radio-station-on  [tool []]
  . pass  executed   m4b-update-the-magicmirror
  . pass  executed   m4b-okay-update-the-magic-mirror-please
  . pass  executed   m4b-update-the-mirror
  . pass  executed   m4b-update-magic-mirror
  . pass  executed   m4b-upgrade-the-magic-mirror
  . pass  executed   m4b-run-the-mirror-update
  . pass  executed   m4b-upgrade-the-mirror-latest
  s skip  skipped    m4b-fetch-and-run-on-the-mirror
  . pass  executed   inc-1101-ask-kronk-groove-armada-radio
  . pass  executed   inc-1107-ask-kronk-ask-home-french-dinner
  . pass  executed   m19-find-deep-house-and-play-it
  . pass  executed   m19-ask-kronk-to-pause
  . pass  executed   m19-ask-kronk-to-play-some-jazz
  . pass  executed   m19-ask-kronk-play-jazz-in-the-office
  x xfail executed   m19-ask-kronk-what-song-is-this
  . pass  executed   m19-hey-ask-kronk-whats-playing
  . pass  executed   m19-can-you-ask-kronk-to-turn-it-up
  . pass  executed   m19-ask-kronk-search-the-web
  . pass  executed   m19-ask-kronk-update-the-magic-mirror
  X xpass executed   m19-what-happens-if-i-ask-kronk-to-pause  []
  . pass  executed   m19-ask-kronk-alone
  . pass  executed   m14-pause-thank-you
  F fail  executed   m14-resume-get-the-door  [tool ['coordinator:ask_home']]
  x xfail executed   m14-thumbs-up-missed-best-part
  . pass  checked    m14-over-word-limit
  . pass  checked    m14-lookalike-mr-brightside
  . pass  checked    m14-lookalike-st-vincent-radio
  . pass  checked    m14-lookalike-rem
  . pass  checked    m14-lookalike-dr-dre
  . pass  checked    m14-lookalike-u2
  . pass  checked    m14-lookalike-mambo-no-5
  . pass  checked    m14-lookalike-blueprint-room-suffix
  . pass  checked    m14-lookalike-ellipsis
  . pass  checked    m14-lookalike-panic-at-the-disco
  . pass  executed   m14-lookalike-am
  . pass  executed   m14-lookalike-decimal
  . pass  executed   m14-lookalike-time
  . pass  checked    m11-jazz-music-style
  . pass  checked    m11-the-song-jazz-music
  . pass  checked    m11-burning-down-the-house
  . pass  checked    m11-girl-with-the-sun-in-the-office
  . pass  checked    m11-toys-in-the-attic-in-the-kitchen
  . pass  checked    m11-handcuffed-in-mississippi-radio
  . pass  checked    m11-some-jazz-on-the-basement-player
  . pass  checked    m11-play-a-game-with-me
  . pass  checked    m11-play-the-movie-inception
voice_replay: {'pass': 130, 'xfail': 21, 'fail': 4, 'skip': 3, 'xpass': 2}; executed 72, checked read-only 85; players unchanged (17 checked)
  skipped inc-1017-try-playing-it-ytm: follow-up that needs conversation history; a replay has none
  skipped inc-1019-ytm-link: typed link; a spoken request carries no URL
  skipped m4b-fetch-and-run-on-the-mirror: typed link; a spoken request carries no URL
voice_replay: wrote /home/drew/git-repos/drawsmcgraw/kronk/docs/bench/voice_replay_2026-11-17_181219_m9-e2e.json
exit=1
```

Against `voice_replay_2026-11-16_012824_m10-baseline.json` (114 rows): 111
rows have the baseline's status. The three that differ: `eval-shuffle-it`
xfail to xpass (known flapper); `eval-look-for-some-good-deep-house-chill`
pass to fail (a "YouTube Music" search row, known flapper; failed in 5
earlier result files); `eval-start-a-zzqx-florbent-radio-station-on` pass
to fail (O8). `inc-0930-search-deep-house-ytm` is fail in both. 46 rows
are newer than the baseline: 41 pass, 2 xfail, 1 skip, 1 xpass
(`m19-what-happens-if-i-ask-kronk-to-pause`, as in the build's run), 1 fail
(`m14-resume-get-the-door`, O8). Exit status 1 because of the four fails.
All players were idle at the start (the kitchen speaker had stopped at
17:24); no row was skipped or stopped for a busy player, so the one rerun
the brief allows for that was not needed. The two O8 rows alone, six
times:

```
$ python3 scripts/voice_replay.py --label m9-e2e-rerunN --id eval-start-a-zzqx-florbent-radio-station-on --id m14-resume-get-the-door      (N = 1..6, the same each time)
  . pass  executed   eval-start-a-zzqx-florbent-radio-station-on
  . pass  executed   m14-resume-get-the-door
voice_replay: {'pass': 2}; executed 2, checked read-only 0; players unchanged (17 checked)
```

During the full replay dev logged 72 `request` events and no
`request_ended`: no deadline fired and no request through HA's client was
ended as `client_gone`.

## Before and after

| Check | Before any live request (17:19 UTC) | At the end (18:40 UTC) |
|---|---|---|
| `orchestrator-dev` code against the tree (md5, 23 `.py` files) | identical | identical |
| `tool_service-dev` code against the tree (md5, 12 `.py` files; the empty `__init__.py` is not in the image) | identical | identical |
| `MUSIC_DRY_RUN` in `tool_service-dev` | 1 | 1 |
| `MODEL_LOCK`, `VOICE_STREAM` in `orchestrator-dev` | server, on | server, on |
| `tool_service-dev` mounts | `/shared`, `/data/hottub`, `/ops`, `/config`, `/data`; no `/keys` | the same; the container was not recreated |
| `ops/` | `hosts.example.json` only | the same |
| Dev pair | up, healthy | up, healthy |
| `config/` | identical to HEAD | identical to HEAD; reload answers `200 {"ok":true,"changed":[]}` on both services |
| `git status` | clean | the result files and this report only |
| Media players and satellites (20 entities) | recorded | the same, apart from three changes that are not mine: the kitchen Voice PE and its media player went from playing to idle at 17:24:16, straight after that puck heard a spoken command (17:24:13 listening, 17:24:16 processing); `media_player.xbr_49x800d` went from unavailable to off at 17:47:40 by itself |
| Production containers | created 15:57 UTC | the same creation times |

## What I changed temporarily and put back

- `orchestrator-dev` recreated about twenty times with scratch compose
  override files (`MODEL_LOCK=pipeline`, `=banana`, and the variable
  absent), each time with `docker compose --profile dev … up -d --no-deps
  --no-build orchestrator-dev`; after each one the ids of every other
  container were compared and none had changed. Last recreate: the repo's
  compose file alone, `MODEL_LOCK=server`. No image was built. `.env.dev`
  was not edited.
- `config/voice.yaml` `deadline_s` edited to 4, 121, 5, null and back to
  25, each followed by `scripts/config_reload.sh dev`; `git diff` on
  `config/` is empty and the last reload reports no change.
- Dev's web UI session was cleared by the `DELETE /dev/history` test (it
  held earlier testers' turns) and now holds the one story exchange. Dev's
  session store also keeps my `m9e2e-*` sessions (four ids, 94 messages);
  I did not edit `data/dev` by hand to remove them.
- One file uploaded to `/dev/files` and deleted through the same route.
- Two scratch worktrees (`b762fe9`, and HEAD for the mutants), removed
  with `git worktree remove`; mutated files were restored first. Scratch
  scripts, logs and the override files were removed by name at the end.
- One run of my false-positive script was stopped and repeated: my raw
  client had mistaken nginx's `100 Continue` for the response and waited
  out the keep-alive on each such case. The requests of that partial run
  completed normally (no `request_ended` on dev).

Not done, and why: the web-search route has 5 runs per mount and mode, not
10 (web searches kept few); the mirror sentence was not sent live (offline
only); a model failure was not provoked live; `news_brief` with a refresh
was not sent live (B1); the dev and production processes together were out
of scope and nothing was sent to production.
