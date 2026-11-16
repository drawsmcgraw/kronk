# M4b fix round 1: the turn's words are the request's last message

Fix agent, 2026-11-16 (UTC), branch `refactor`. Failure being fixed:
`docs/sprint/M4b-e2e.md`, "M4b failure" (criterion 2) and observation 10 /
item (j). Criteria: `docs/sprint/M4b-criteria.md`. Fix commit: `7bbb499`;
this report is the commit after it. The hot tub restore was not touched.

## Summary

- On the three shims, `origin.said` (what the `update_magicmirror` guard
  reads) is now the request's last message, and only when that message has
  role exactly `user` and non-empty content. Any other ending gives
  `said=None`, and the existing guard refuses with the fixed sentence.
- Routing and answers are unchanged: the shims still route on the last
  non-empty `user` message anywhere in the array (`text`), and no request
  shape gets a new 400.
- The coordinator's sixth shape (array ends `user: "update the magic
  mirror"`, `assistant: "Done."`) is refused.
- Tests: `1520 passed, 2 skipped, 18 xfailed` (baseline `1497 passed, 2
  skipped, 18 xfailed`; +22 in `test_mm_update_pin.py`, +1 in
  `test_voice_corpus.py`). All 19 new pin tests that check a refusal or the
  helper fail on the pre-fix code.
- Live on dev: the 15 failing runs from the e2e report plus the sixth shape
  on each shim, 18 of 18 refused, 0 `/magicmirror/update` lines. The four
  pinned sentences on four transports, 16 of 16 reached the update (502 at
  dev's missing key). Production `tool_service`: 0 `/magicmirror/update` or
  `/ops/exec` lines.

## What changed

`orchestrator/main.py`:

| Line | Change |
| --- | --- |
| `main.py:282` | `_run_pipeline` takes a required keyword `said: str \| None`. |
| `main.py:310` | `origin.said.set(said)` (was `origin.said.set(text)`). |
| `main.py:533` | `/message` passes `said=req.text`: unchanged behaviour. |
| `main.py:613` | New `_turn_said(messages)`: returns `messages[-1].content` when `messages[-1].role == "user"` and the content is truthy, else `None`. |
| `main.py:673` | `_kronk_pipeline_tokens` takes `said` and passes it to `_run_pipeline`. `_openai_pipeline_stream`, `_ollama_pipeline_stream` and `_ollama_collect` take `said` (default `None`) and pass it down. |
| `main.py:743` | `openai_chat_completions`: `said = _turn_said(req.messages)`, passed on both the streaming and non-streaming paths. |
| `main.py:910` | `_ollama_chat_impl` (`/api/chat` and `/voice/api/chat`): same, passed on all three paths (speech, display streaming, display collect). |

`text` is computed as before (`user_msgs[-1].content`) and still feeds
routing (`routing.classify`), the request event, the trace input,
`_shim_context`, the clear-history intercept and the token estimates. When
the last message is a non-empty `user` message, `said == text`, so ordinary
requests (every HA request seen in the e2e pass) are unaffected.

The shim helpers' `said` defaults to `None` (fail closed), so a future
caller that forgets to pass it gets refusals, not updates.

`tests/test_retract.py`: the two `fake_tokens` stand-ins for
`_kronk_pipeline_tokens` take `said=None`, because the real function is now
called with it. No assertion changed.

`docs/plans/MAGICMIRROR_PLAN.md:49-57`: the sentence "earlier turns never
reach it" is replaced by a description of `_turn_said`, the shapes that get
`None`, and the note that routing still reads the last non-empty user
message.

## Choke point check (brief item 3)

- The only request to `/magicmirror/update` in the orchestrator is
  `tools.py:906`, inside `_tool_update_magicmirror`, after the guard
  (`tools.py:901-905`). The handler is reached only through
  `tools.execute` (`_HANDLERS`). `grep -rn "magicmirror" orchestrator/*.py`
  finds no other caller; `agents.py` and `main.py` only call
  `tool_service`'s `/weather/cached`, `/health` and `/shopping_list` directly.
- The `mm_update` rule (`routing.py:294-297`) reads the same `text`, but it
  only picks the agent (`home`). It does not run the tool. With the fix, a
  request of the failing shapes still routes `mm_update` → home, home calls
  `update_magicmirror`, and the guard refuses. The live table below shows
  exactly that path in all 18 runs.
- `nginx/nginx.conf` exposes no `tool_service` mirror route. Home
  Assistant's `/config` YAML and `.storage` hold no `magicmirror/update`
  (read-only grep in the `homeassistant` container).
- The other reader of `origin.said` is `play_music` (`tools.py:732`), which
  forwards it to `tool_service` for radio-station parsing. For a shim request
  that does not end in a non-empty `user` message it now forwards nothing,
  and `tool_service` falls back to the model's arguments, as it already did
  for any request without `said`. Before the fix it forwarded an earlier
  turn's sentence. The coordinator's ruling sets `said=None` for the
  pipeline, so this follows from it; it is noted here because it is the
  only behaviour change outside the mirror.

### `/message`

Not affected. `/message` takes the user's sentence from `MessageRequest.text`
(a required string in the request body). Earlier turns come from the
session store and go to `history`, never to `text` or `said`. There is no
message array whose last element could be something other than the user's
sentence. `said=req.text` is passed explicitly, the same value as before.

## Tests

New in `tests/test_mm_update_pin.py` (22 tests, `main.py` code driven
through FastAPI's `TestClient` with the scripted model and fake
`tool_service` the file already uses). Each request is `user: "update the
magic mirror"`, `assistant: "I couldn't update the magic mirror. Mirror
preflight failed: …"`, then the trailing message:

| Test | What it asserts |
| --- | --- |
| `test_trailing_message_not_the_users_is_refused[shape-shim]` (×15) | Shapes `empty_user` (`content: ""`), `null_user` (`content: null`), `tool` (`"weather is fine"`), `role_human` and `role_User` (`"what's the weather tomorrow"`), on `/api/chat`, `/voice/api/chat`, `/v1/chat/completions`: no POST to `/magicmirror/update`, reply is the fixed sentence, model log `["home"]` (routing unchanged). |
| `test_request_ending_in_assistant_message_is_refused[shim]` (×3) | The sixth shape: array is `user: "update the magic mirror"`, `assistant: "Done."`. Same three assertions. |
| `test_pinned_sentence_as_last_message_after_history_still_runs[shim]` (×3) | System stamp, an earlier weather turn, then `user: "update the magic mirror"` last: one POST, the 502 answer. Guards against over-tightening; passes before and after the fix. |
| `test_turn_said_reads_only_the_last_message` | `_turn_said` on a pinned last message, the sixth shape, each of the five trailing shapes, and an empty list. |

New in `tests/test_voice_corpus.py`:

| Test | What it asserts |
| --- | --- |
| `test_mirror_rows_carry_the_mirror_tag` | Every corpus row whose text matches `routing._MM_RE` or whose `expect.tool` is `update_magicmirror` has `mirror` in `tags`. |

### Proof the new tests fail before the fix

A scratch copy of `HEAD` before the fix (`6f93c74`, via `git archive`) with
only the new `tests/test_mm_update_pin.py` copied in, run with the repo's
`tests/.venv`:

```
$ pytest tests/test_mm_update_pin.py -q -k "trailing or assistant_message or still_runs or turn_said"
19 failed, 3 passed, 69 deselected, 3 warnings in 0.47s
```

The 19 failures, one line each (`--tb=line`, request list truncated):

```
tests/test_mm_update_pin.py:449: AssertionError: ('api_chat', 'empty_user', [('GET', '…/weather/cached'), ('GET', '…/weather/cached'), ('POST', 'http://fake-tools:8003/magicmirror/update')…
  … the same at line 449 for all 15 shape × shim cases …
tests/test_mm_update_pin.py:461: AssertionError: ('api_chat', [… ('POST', 'http://fake-tools:8003/magicmirror/update')…
tests/test_mm_update_pin.py:461: AssertionError: ('voice_api_chat', […
tests/test_mm_update_pin.py:461: AssertionError: ('openai', […
tests/test_mm_update_pin.py:482: AttributeError: module 'orchestrator.main' has no attribute '_turn_said'
```

All 18 shape tests fail on the first assertion: the pre-fix code POSTed
to `/magicmirror/update`. The 3 that pass are
`test_pinned_sentence_as_last_message_after_history_still_runs`, which
checks that the pinned sentence still works and is expected to pass on both.

`test_mirror_rows_carry_the_mirror_tag` passes on the committed corpus (all
eight `m4b-` rows carry the tag). That the test catches a missing tag: in the
scratch copy, with `mirror` removed from `m4b-update-the-magicmirror` and
`m4b-update-the-mirror`:

```
tests/test_voice_corpus.py:136: AssertionError: rows missing the 'mirror' tag: ['m4b-update-the-magicmirror']
1 failed, 247 deselected in 0.04s
```

`m4b-update-the-mirror` is not reported: its text has no "magic mirror"
and it does not expect `update_magicmirror`, so it is outside the rule as
the brief states it. See "Not done".

## Live on dev

Deploy: `docker compose --profile dev up -d --build tool_service-dev
orchestrator-dev`, after checking that both services' `depends_on` stay
inside the dev pair (`orchestrator-dev` → `tool_service-dev`;
`tool_service-dev` → none). `orchestrator-dev` was rebuilt and recreated.
`tool_service-dev`'s image built identical (no `tool_service` code change),
so compose kept its container (`b47515fdb02e`, same as before). md5 of
`main.py`, `tools.py`, `routing.py`, `agents.py`, `origin.py` in
`kronk-orchestrator-dev-1` equal the working tree's. nginx resolves
`orchestrator-dev` per request (`resolver … valid=10s`, `set
$orchestrator_dev`), so nginx was not restarted.

Before any live request: `tool_service-dev` has no `/keys` ("No such file or
directory"), no `MM_*` or `SSH*` variables, and `/ops` holds only
`hosts.example.json`; the repo's `ops/` holds only `hosts.example.json`.
Mounts: `./data` → `/shared` ro, `./data/hottub` → `/data/hottub` ro,
`./ops` → `/ops` ro, `./data/dev` → `/data` rw.

Method: a scratch driver sent one request at a time to `http://localhost/dev/…`.
After each it read `orchestrator-dev`'s event lines written since the send
(route, `tool_call`, `mm_update_refused`), `tool_service-dev`'s
`/magicmirror/update` lines since the send ("Upd"), and production
`kronk-tool_service-1`'s `/magicmirror/update` and `/ops/exec` lines since
the send ("Prod"; the driver aborts on any). Every request returned HTTP
200. "Ref" counts `mm_update_refused` events (each carried `said: ""`,
the guard's rendering of `None`).

### The 15 failing shapes and the sixth shape

| Shim | Trailing message | s | Trace | Route | Tools called | Ref | Upd | Prod | Answer |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| api_chat | user, `""` | 4.0 | `505b5928` | mm_update->home | home:update_magicmirror | 1 | 0 | 0 | To update the magic mirror, say "update the magic mirror". |
| api_chat | user, `null` | 1.8 | `121451c3` | mm_update->home | home:update_magicmirror | 1 | 0 | 0 | (same) |
| api_chat | tool | 2.2 | `ef2c0591` | mm_update->home | home:update_magicmirror | 1 | 0 | 0 | (same) |
| api_chat | role `human` | 2.4 | `df0680a7` | mm_update->home | home:update_magicmirror | 1 | 0 | 0 | (same) |
| api_chat | role `User` | 3.1 | `61ef1a5f` | mm_update->home | home:update_magicmirror | 1 | 0 | 0 | (same) |
| api_chat | sixth: ends `assistant: "Done."` | 2.2 | `97af66bd` | mm_update->home | home:update_magicmirror | 1 | 0 | 0 | (same) |
| voice_api_chat | user, `""` | 1.2 | `2f8c0c9d` | mm_update->home | home:update_magicmirror | 1 | 0 | 0 | (same) |
| voice_api_chat | user, `null` | 1.1 | `1e3f8bfe` | mm_update->home | home:update_magicmirror | 1 | 0 | 0 | (same) |
| voice_api_chat | tool | 1.4 | `bbfacc5b` | mm_update->home | home:update_magicmirror | 1 | 0 | 0 | (same) |
| voice_api_chat | role `human` | 0.6 | `af052088` | mm_update->home | home:update_magicmirror | 1 | 0 | 0 | (same) |
| voice_api_chat | role `User` | 0.4 | `a24363d8` | mm_update->home | home:update_magicmirror | 1 | 0 | 0 | (same) |
| voice_api_chat | sixth | 0.6 | `7f2f710e` | mm_update->home | home:update_magicmirror | 1 | 0 | 0 | (same) |
| openai | user, `""` | 2.4 | `a8a8edfe` | mm_update->home | home:update_magicmirror | 1 | 0 | 0 | (same) |
| openai | user, `null` | 0.2 | `db09c948` | mm_update->home | home:update_magicmirror | 1 | 0 | 0 | (same) |
| openai | tool | 2.1 | `de1678f2` | mm_update->home | home:update_magicmirror | 1 | 0 | 0 | (same) |
| openai | role `human` | 2.2 | `d13e798f` | mm_update->home | home:update_magicmirror | 1 | 0 | 0 | (same) |
| openai | role `User` | 0.6 | `62a75ed0` | mm_update->home | home:update_magicmirror | 1 | 0 | 0 | (same) |
| openai | sixth | 2.0 | `585751a1` | mm_update->home | home:update_magicmirror | 1 | 0 | 0 | (same) |

18 of 18 refused; 0 `/magicmirror/update` lines in `tool_service-dev`'s log.
The e2e report had 15 of 15 (and 3 of 3 for the sixth shape) reaching it.
The route is the same as before the fix (`mm_update` → home): routing is
unchanged, and the guard is what stops it.

### The four pinned sentences still reach the tool

| Sentence | message | api_chat | voice_api_chat | openai |
| --- | --- | --- | --- | --- |
| "Update the magicmirror" | 502 (`5f45033b`) | 502 (`c0fad92c`) | 502 (`fb3d55cd`) | 502 (`c90530d9`) |
| "Update the magic mirror" | 502 (`e42d96d2`) | 502 (`bae7328f`) | 502 (`be731688`) | 502 (`981f9e8b`) |
| "update the MagicMirror." | 502 (`d278ffc3`) | 502 (`e891a5c9`) | 502 (`a5b88676`) | 502 (`959dd3f6`) |
| "Okay, update the magic mirror please" | 502 (`6d949a21`) | 502 (`7a7056a6`) | 502 (`27588b7b`) | 502 (`a7a39dac`) |

Each cell: rule `mm_update` → `home:update_magicmirror` → one `POST
/magicmirror/update` answered `502 Bad Gateway`, no refusal event, answer
"I couldn't update the magic mirror. Mirror preflight failed: SSH key not
found at /keys/kronk-mm-update …". 16 of 16. That is dev's expected
result: it has no mirror key.

### Extra runs

| Shim | Request | s | Trace | Route | Tools | Upd | Answer |
| --- | --- | --- | --- | --- | --- | --- | --- |
| api_chat | weather turn, then `user: "update the magic mirror"` last | 2.5 | `cfb894c5` | mm_update->home | home:update_magicmirror | 1 (502) | I couldn't update the magic mirror. Mirror preflight failed: … |
| voice_api_chat | same | 1.8 | `b08ca90b` | mm_update->home | home:update_magicmirror | 1 (502) | (same) |
| openai | same | 1.6 | `ea2497c0` | mm_update->home | home:update_magicmirror | 1 (502) | (same) |
| api_chat | `user: "what's the weather tomorrow"`, `assistant`, then a `tool` message last | 2.9 | `f6254f9e` | weather->home | (none) | 0 | Tomorrow will be sunny with a high of 61°F and winds from the Northwest at 5 mph. |
| voice_api_chat | same | 2.8 | `6bb8487c` | weather->home | (none) | 0 | It will be sunny tomorrow with a high near 61°F and a low around 55°F. |
| openai | same | 3.0 | `94d09a83` | weather->home | (none) | 0 | I can't provide the weather for tomorrow because I don't have the forecast available yet. |

The last three show a non-mirror request of a failing shape: still 200, still
routed on the earlier user turn (`weather` → home), no 400. The OpenAI
run's answer is the model declining despite the weather block; the weather
path does not read `origin.said` (only `play_music` and
`update_magicmirror` do), so it is not from this change.

Raw records: 40 requests, kept in the session scratchpad, not committed.

## Test and replay output

```
$ ./scripts/run_tests.sh -q        # before the change, 6f93c74
1497 passed, 2 skipped, 18 xfailed, 3 warnings in 11.68s
$ ./scripts/run_tests.sh -q        # after the main.py change, before the test_retract.py fake update
FAILED tests/test_retract.py::test_voice_collector_drops_retracted_text - Typ...
FAILED tests/test_retract.py::test_streaming_shims_pass_tokens_and_skip_retracts
2 failed, 1495 passed, 2 skipped, 18 xfailed, 3 warnings in 11.66s
$ ./scripts/run_tests.sh -q        # 7bbb499, and again after the live runs
1520 passed, 2 skipped, 18 xfailed, 3 warnings in 11.72s
1520 passed, 2 skipped, 18 xfailed, 3 warnings in 11.73s
```

The two `test_retract.py` failures were `TypeError`s from the stand-in
`fake_tokens` functions, which did not accept the new `said` argument; fixed
by adding `said=None` to them (see "What changed").

Replay on `kronk-dev` after the deploy, dry run on. Nothing was playing or
paused before it started (14:26 UTC).

```
$ python3 scripts/voice_replay.py --label m4b-fix1
voice_replay: pipeline kronk-dev → http://localhost/dev/voice, dry-run on, 122 rows (0 not selected)
  … 114 lines of pass / xfail / skip rows …
  . pass  executed   m4b-update-the-magicmirror
  . pass  executed   m4b-okay-update-the-magic-mirror-please
  . pass  executed   m4b-update-the-mirror
  . pass  executed   m4b-update-magic-mirror
  . pass  executed   m4b-upgrade-the-magic-mirror
  . pass  executed   m4b-run-the-mirror-update
  . pass  executed   m4b-upgrade-the-mirror-latest
  s skip  skipped    m4b-fetch-and-run-on-the-mirror
voice_replay: {'pass': 99, 'xfail': 20, 'skip': 3}; executed 53, checked read-only 66; players unchanged (17 checked)
  skipped inc-1017-try-playing-it-ytm: follow-up that needs conversation history; a replay has none
  skipped inc-1019-ytm-link: typed link; a spoken request carries no URL
  skipped m4b-fetch-and-run-on-the-mirror: typed link; a spoken request carries no URL
voice_replay: wrote /home/drew/git-repos/drawsmcgraw/kronk/docs/bench/voice_replay_2026-11-16_142652_m4b-fix1.json
exit=0
```

No row failed, so the elided lines are all `pass`, `xfail` or `skip`; the
full per-row record is the committed result file
`docs/bench/voice_replay_2026-11-16_142652_m4b-fix1.json`. The replay
did not stop itself; `media_player.sonos_move_derp` did not wake during the
run.

Against `docs/bench/voice_replay_2026-11-16_012824_m10-baseline.json`, row
by row on status and method:

| Row | Baseline | This run |
| --- | --- | --- |
| 113 of the 114 baseline rows | | identical |
| `inc-0930-search-deep-house-ytm` | fail | pass (known YouTube Music flapper; it passed in M4b-pre/post and in two of the e2e's partial runs) |
| `eval-search-youtube-music-for-some-deep-chill` | pass | pass |
| `eval-shuffle-it` | xfail | xfail |
| `m4b-update-the-magicmirror` | new | pass: `home/mm_update`, `update_magicmirror`, not refused (502 at dev's key) |
| `m4b-okay-update-the-magic-mirror-please` | new | pass: same |
| `m4b-update-the-mirror` | new | pass: `direct/default`, refused |
| `m4b-update-magic-mirror` | new | pass: `devops/mm_ops`, devops answered (one `/ops/exec` 404 on `tool_service-dev`) |
| `m4b-upgrade-the-magic-mirror` | new | pass: `devops/mm_ops`, devops answered |
| `m4b-run-the-mirror-update` | new | pass: `direct/default`, refused |
| `m4b-upgrade-the-mirror-latest` | new | pass: `direct/default`, refused |
| `m4b-fetch-and-run-on-the-mirror` | new | skip (typed link) |

`tool_service-dev`'s log from the deploy (14:23:53 UTC) to the end: 21
`/magicmirror/update` lines, all `502 Bad Gateway`: 16 pinned-sentence runs,
3 pinned-after-history runs, and the replay's 2 pinned rows. None from a
refused run. One `/ops/exec`, `404 Not Found` (the replay's
`m4b-update-magic-mirror` devops call).

## Not done, and why

- **No 400 for malformed shapes.** The coordinator ruled it out: it would
  change behaviour for every request of those shapes, not only mirror ones.
- **Routing still reads the last non-empty user message.** The brief keeps
  routing and answers as they are. So a failing-shape request whose earlier
  turn was the pinned sentence still routes `mm_update` → home and is
  refused there (one model call, about 0.2 to 4 s). Not changed.
- **`_MM_UPDATE_RE` not changed; no new routing rule** (brief; stopping
  rule, Drew 2026-11-11).
- **The corpus tag test uses `_MM_RE` ("magic mirror"), as the brief
  states.** A row such as "update the mirror" with no tag would not be caught.
  On production after promotion that row would reach the guard with
  `said = "update the mirror"` and be refused, so it cannot start an update,
  but the replay would execute it. Widening the test to any "mirror" text was
  not asked for and was not done.
- **`scripts/voice_replay.py` not changed** (brief). The skip stays by tag;
  the new test is what keeps the tag on the rows.
- **The other e2e observations (1 to 9) not addressed** (brief).
- **No `docs/features/` change.** The feature doc for the corpus describes
  the tag skip and is still accurate. The plan doc was the one with the false
  sentence.
- **No HA `assist_pipeline/run` rerun.** HA's own requests always end with
  the user's message (e2e: 20 runs), so `said == text` for them and nothing
  changes; the replay below runs the voice path through HA's `kronk-dev`
  pipeline.
- **No incident doc.** The failure was found and specified by the sprint's
  e2e test, which is its record. Nothing broke in production: production
  still runs the pre-M4b code.

## Guard rails

- No command to the MagicMirror Pi or any managed host. Production's
  `/magicmirror/update` and `/ops/exec`: 0 lines in `kronk-tool_service-1`'s
  log (the driver checked after every request).
- Containers: only `kronk-orchestrator-dev-1` has a new id (`542e9b4f2184`
  → `38247a5b0406`). Production, HA, MA, telemetry, `wyoming-piper`,
  `bgutil-pot-provider` and `tool_service-dev` keep theirs. No HA or MA
  restart. nginx not restarted.
- `.env`, `.env.dev` not edited. No `down -v`, no `--remove-orphans`, no
  `pipeline_bench.sh`, no LAN scan. `kronk-hottub-monitor.service` active
  and not touched.
- No sound: nothing was playing or paused before the replay (HA states,
  read-only); the replay ran with dry run on.
- Scratch: a `git archive` copy of the pre-fix tree and the driver, in the
  session scratchpad. No symlinks to repo data or secrets. No background job
  left running.
