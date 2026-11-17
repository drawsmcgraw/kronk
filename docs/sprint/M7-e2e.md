**M7 PASSES.**

# M7 end to end: direct dispatch for regex-pinned commands

Test agent, 2026-11-17 07:59–08:32 UTC, branch `refactor` at `cf905e6`.
Criteria: `docs/sprint/M7-criteria.md`. The build report
(`docs/sprint/M7-build.md`) was read as claims and checked. Tree before the
build: `7c83f36`. I tested and fixed nothing. No code, test, corpus, config
or environment file was edited.

All 17 criteria pass. Three findings do not change the verdict and are for
the coordinator: one wrong sentence in the new feature doc (F1), network
lookups by tests that predate M7 (F2), and one mutant no unit test kills
(F3).

## Before and after the run

| Check | Start (07:59 UTC) | End (08:31 UTC) |
| --- | --- | --- |
| `orchestrator-dev` code against the tree (md5, 18 `.py` files and `static/`) | identical | identical |
| `tool_service-dev` code against the tree (md5, 11 `.py` files; the empty `__init__.py` is not in the image) | identical | identical |
| `MUSIC_DRY_RUN` in `tool_service-dev`, and `/dev/api/status` | `1`, `music_dry_run: true` | same |
| `/keys` in `tool_service-dev` | absent, no such mount | absent |
| `ops/` | `hosts.example.json` only | same |
| Dev pair | up, healthy (orchestrator-dev started 07:54:52, tool_service-dev 07:10:44) | up, healthy, same start times: neither was rebuilt or restarted |
| Media players (17 entities: state, volume, mute, title) | recorded | identical to the start (`diff` empty) |
| HA, Music Assistant, nginx, production containers, `llama-*`, hot tub monitor | running | same uptimes, all active |

No rebuild was needed. Dry-run was re-read from `/dev/api/status` at the
start of every live script.

## Criteria

| # | Result | Evidence |
| --- | --- | --- |
| 1 | pass | `f168cff` (parent `7c83f36`) adds `tests/test_routing_pin.py`, `tests/fixtures/routing_pin.jsonl` (6415 rows) and `make_routing_pin.py`. `git log -- orchestrator/routing.py` after `7c83f36` starts at `49389b5`. The fixture is unchanged since `f168cff`; the test file gained 22 lines with the refactor. |
| 2 | pass | `routing.RULES` holds the 15 rules in the listed order. Independent of the builder's pin: `(route, rule)` from `7c83f36`'s `_classify_inner` against HEAD's `decide` and `_classify_inner` over 874,857 utterances, 0 differences (section "Routing, old against new"). Mutants that reorder two rules or drop an anchor fail the suite (M8, M9). |
| 3 | pass | Direct call present exactly on the five rules over all 874,857 utterances (191,111 direct calls, 0 on any other rule, 0 missing). Every call checked against a keyword oracle that does not use the named groups: 0 mismatches. Word-form table below. The call is built from the match's named groups (`routing._group`, `_another_call`). |
| 4 | pass | Scratch copy of HEAD, model client patched to raise: 423 word forms across the five regexes on 7 mount variants, each answered with status 200, 0 model calls and exactly one POST to the right endpoint with the expected arguments. Live: 0 `agent_round` events on every direct request (over 400 requests). |
| 5 | pass | Old tree (agent path, scripted model making the same call) against new tree (direct, model patched to raise) through `TestClient`: 5 commands × 29 tool result lines × 7 mount variants = 1015 replies, 0 differences. With a fake tool_service (200, 200 with a pick, 409, 502, 500 non-JSON, down) × stamped/unstamped × 7 mounts × 5 commands = 420 cells: replies and the payload sent to tool_service identical, 0 differences. |
| 6 | pass | The 420 payload cells above carry `origin_device` / `origin_area` when stamped and neither when unstamped, same as the old tree. Live: Office-stamped requests answered for the Office speaker, Kitchen-stamped for the Kitchen speaker, unstamped for the default (Office). Through HA (`kronk-dev`), `inc-0910-stop-transcript` from the basement satellite answered "The basement voice pe ma speaker is already paused." |
| 7 | pass | Live, 60 odd-shaped requests (10 shapes × 3 shims × streaming and not): 0 `POST /magicmirror/update` on tool_service-dev, 42 `mm_update_refused`. 12 control requests whose last message is the user's pinned sentence: 12 POSTs, all 502. 12 more odd shapes sent at the same instant as 3 pinned requests: 3 POSTs. Section "The mirror pin". |
| 8 | pass | Fake tool_service failing (409, 502, 500, down): one POST, 0 model calls, one spoken sentence, in all 420 cells. `tools.execute` raising: one call, 0 model calls, the pipeline's last-resort sentence (35 cells). A mutant that retries a failed tool once fails 24 tests (M18); a mutant that falls through to the model fails 133 (M3). |
| 9 | pass | Live, twice: a model request held the lock for 8.1 s and 8.6 s; eight direct requests sent 1.5–1.9 s in each finished in 0.013–0.047 s; a non-direct control sent at 1.96 s finished only after the lock was released (8.24 s, 9.47 s). The unit test exists and a mutant that always takes the lock fails it (M1). Reverse: 155 simultaneous requests, no origin, session or mirror cross-talk. |
| 10 | pass | Tempo, dev: `POST /voice/api/chat` → `pipeline.shim` (`kronk.route=home`) → `routing.decide` (`kronk.rule`, `kronk.direct=<tool>`) and `tool.<name>` (`kronk.direct=true`) → tool_service-dev's server span. No `agent.*` or `llm.*` span. Log events per direct request: `request`, `origin` (when stamped), `route_shortcut` with `direct: true, tool`, `tool_call` and `tool_complete` with `direct: true`, `request_complete`. `/dev/message`: 20 sessions each stored exactly their own user text and reply. |
| 11 | pass | Scratch: 24 negatives × 3 mounts, none has a direct call, all reach a model, 0 tool POSTs. Live on `/dev/api/chat`: "stop the timer", "pause my subscription", "what is the next holiday" and "pause the music in the office" all `default`, 1 to 5 model rounds, no direct call. |
| 12 | pass | Corpus diff `7c83f36..HEAD`: 28 rows changed, all 28 on the five rules, each gains `expect.direct: true`; 6 of them `action` `stop` → `pause` with a note. No expectation key removed, no `direct` on any other rule, 23 xfail rows before and after, 122 rows. `stop` and `pause` are the same action in tool_service (ruling 2, below). |
| 13 | pass | Direct requests on `/dev/voice/api/chat`: median 0.024–0.026 s, worst 0.196 s, of which the orchestrator's own share (root span minus the tool span) is 1.1–1.9 ms. Latency table below. Dry-run is on and nothing was playing, so the tool time is not production's. |
| 14 | pass | `3341 passed, 2 skipped, 23 xfailed`. The five extra xfails are the ones the builder named. M7's new tests make no network attempt. Tests that predate M7 do (finding F2). |
| 15 | pass | Rerun `{'pass': 99, 'xfail': 20, 'skip': 3}`, exit 0. Every one of the 114 baseline rows has its baseline status except `inc-0930-search-deep-house-ytm` (fail → pass, a known flapper). No xfail row flipped. The first attempt stopped after 9 rows (a TV changed state by itself). |
| 16 | pass | Every request went to `127.0.0.1/dev/…`, Tempo and HA's state API on loopback, or the `kronk-dev` pipeline through the replay script. nginx logged no request outside `/dev/` in the window except a browser's `GET /api/system` polling; production orchestrator logged 0 `request` events; production tool_service logged no mirror, ops, fetch, http_check, config or music POST. Players identical at the end. Nothing restarted. |
| 17 | pass | `docs/features/direct-dispatch.md` covers the table, the branch, telemetry and gotchas; README routing step 1 and the terminal-tools note describe the table and the five rules; `agents-as-tools-routing.md` points there. One sentence in the feature doc is wrong (finding F1). |

## Coordinator rulings, checked

1. **"play another one"** is the `music` rule in both trees (`7c83f36` and
   HEAD) and has no direct call. Criterion 13 was measured with "not that"
   as the `music_another` representative; "play another one" was measured
   too and stays on the model path.
2. **`stop` → `pause`.** `tool_service/main.py:2147-2148`: both map to
   `("media_pause", ("paused", "idle", "standby", "off"), "Paused")`. Both
   are in `_NEEDS_PLAYBACK` (line 2154) and both take the idempotent
   "already paused" branch (line 2284). The only differences are the
   `status` field of the JSON answer (the orchestrator reads `detail` only)
   and the dry-run sentence ("would stop" / "would pause"). Same HA service
   call, same verification, same spoken word. Accepted change confirmed.
3. **The room as `player` on `music_another`.** Live on
   `/dev/voice/api/chat`, requests stamped as coming from the Kitchen:

   | Said | `player` sent | tool_service-dev resolved | Result |
   | --- | --- | --- | --- |
   | not that in the office | `office` | `media_player.satellite1_d8d1bc` | right speaker |
   | not that in the office please | `office please` | `media_player.satellite1_d8d1bc` | right speaker (substring room match) |
   | not that in the kitchen (Office stamp) | `kitchen` | `media_player.home_assistant_voice_0ac919_pe_01` | right speaker |
   | not that (Kitchen stamp) | none | `media_player.home_assistant_voice_0ac919_pe_01` | origin |
   | not that (no stamp) | none | `media_player.satellite1_d8d1bc` | default |

   "… in the office please" targets the Office speaker. No wrong speaker
   was found. Not a finding. Related observations O1 and O2.
4. and 5. Accepted; nothing to check beyond criteria 14 and 7.

## Findings

**F1. The feature doc is wrong about "in the office please" (criterion 17,
minor).** `docs/features/direct-dispatch.md:115-117` says "not that in the
office please" passes "office please" and "tool_service then answers that it
knows no such speaker". Request: `POST /dev/voice/api/chat`, system
`[kronk-origin] device=m7e2e_fake_kitchen area=Kitchen`, user "not that in
the office please". Result: tool_service-dev logged `music request
query='another' media_type=None player='office please' shuffle=False ->
['media_player.satellite1_d8d1bc']`: the Office speaker, through
`resolve_players`' substring-room rung. The "no such speaker" answer happens
only for a room tool_service does not know: "not that in the garage please"
→ 400, spoken "I couldn't play that. I don't know a speaker or room called
'garage please'. Speakers: … Rooms with a speaker: Kitchen, Office." (trace
`c0235e734041308aaa2f7c88970f9dc7`). The same sentence is in the build
report, departure 3.

**F2. Tests that predate M7 attempt network lookups (criterion 14's second
sentence; not caused by M7).** The whole suite run in a scratch copy with
every IPv4/IPv6 lookup and connect refused and logged still gives `3341
passed, 2 skipped, 23 xfailed`, and logs 32 attempts. The pre-build tree
gives `3003 passed, 2 skipped, 18 xfailed` and 36 attempts from the same
tests. By test file at HEAD: `test_agentic_loop.py` 10, `test_error_surfacing.py`
5, `test_render.py` 4, `test_retract.py` 5, `test_playback_control.py` 1
(all `getaddrinfo('fake-tools', 8003)`), `test_magicmirror.py` 1
(`getaddrinfo('localhost', 8123)`), `test_tool_service_weather.py` 5
(`feeds.npr.org`, `feeds.bbci.co.uk`: real news feed hosts, looked up from
a refresh the test starts), and one `bind(('::1', 0))` outside any test.
Under plain `./scripts/run_tests.sh` these are real lookups. None comes
from `test_direct_dispatch.py`, `test_routing_pin.py`,
`test_routing_shortcuts.py`, `test_mm_update_pin.py`, `test_voice_corpus.py`
or `test_voice_replay.py`. I read criterion 14 as met for M7; the sentence
"Unit tests do not reach the network" is not true of the suite as a whole,
before or after M7.

**F3. One mutant survives (test gap, minor).** Removing `with
tool_span.current():` around `tools.execute` in `main._direct_dispatch`
(the line that lets tool_service join the trace) leaves the suite at `808
passed` in the eight files run. Live traces show the join works (criterion
10). No unit test would notice it breaking.

## Routing, old against new (criteria 1, 2)

`7c83f36` and HEAD were extracted with `git archive` into scratch
directories. One script per tree imported that tree's `routing` and wrote
`(route, rule)` for each utterance: `_classify_inner` on the old tree;
`decide` and `_classify_inner` (asserted equal) on the new one.

Utterances, 874,857 in total:

- 10,860 bases: the 122 corpus texts, the 491 `docs/bench/routing_eval.jsonl`
  texts, the builder's 6415 pin rows, every string literal (2 to 200
  characters) in every `tests/test_*.py`, and the enumerated language of the
  four anchored regexes (below).
- Variants of each base: lower, upper, title, swapped and capitalised case;
  14 trailing forms (".", "!", "?", "...", " please", " thanks", " now",
  newline, "。", …); 23 leading forms ("Okay Nabu, ", "Hey Jarvis, ",
  "Kronk, ", "please ", "uh ", ", ", tab, "Can you ", "tell kronk to ", …);
  contractions both ways (what's / what is, don't / do not / dont, …);
  the apostrophe as ’ ‘ ʼ ` ´ or removed; wrapped in straight and curly
  quotes; double spaces, no-break spaces, tabs, newlines, no spaces;
  zero-width space and BOM; a trailing room three ways; the text doubled;
  with a URL, "talkie, ", " don't search" or " magic mirror" added.
- Long inputs for eight sentences: 100,000 characters of filler after,
  before and around; the sentence repeated 5000 times; rooms of 39, 40, 41
  and 60 characters.
- Odd inputs: empty, whitespace only, punctuation only, emoji, full-width
  letters, NUL, CR LF, U+2028, long s (ſ).

Result:

```
utterances 874857 route/rule differences 0
old rule counts [('default', 584559), ('direct_override', 14186), ('mm_ops', 23069), ('mm_update', 5485), ('music', 20906), ('music_another', 62465), ('music_url', 639), ('now_playing', 5083), ('playback', 26725), ('rate', 91353), ('search_phrase', 2972), ('talkie_explicit', 13820), ('taste', 484), ('url', 20843), ('weather', 2268)]
new rule counts [('default', 584559), ('direct_override', 14186), ('mm_ops', 23069), ('mm_update', 5485), ('music', 20906), ('music_another', 62465), ('music_url', 639), ('now_playing', 5083), ('playback', 26725), ('rate', 91353), ('search_phrase', 2972), ('talkie_explicit', 13820), ('taste', 484), ('url', 20843), ('weather', 2268)]
direct iff five rules violations 0 []
exceptions 0 []
```

All 15 rules were exercised. No exception from `decide` on any input.

## Word form → (tool, args) (criterion 3)

The language of each anchored regex was enumerated from its parse tree
(every alternative, every optional part present and absent, one space for
`\s+`): `_PLAYBACK_RE` 1756 forms, `_NOW_PLAYING_RE` 53, `_RATE_RE` 20,640,
`_ANOTHER_RE` 6720 (with seven sample rooms). Each form matched its regex,
got the same `(route, rule)` in both trees, and its direct call was compared
with a keyword oracle written separately from the named groups. With the
variants, 191,111 direct calls were checked: 0 mismatches. (Four inputs
using the long s, "ſtop", "pauſe", "ſkip", "thumbſ up", match under
`re.IGNORECASE` in both trees and map correctly; my oracle did not fold
them.)

Leading "please" and trailing "please" are accepted by `playback`, `rate`
and `music_another`; leading "kronk" by `rate` and `music_another`;
`now_playing` accepts neither. Punctuation at either end is ignored.
"Extra" marks forms the regex accepts that the plan's table does not list.

| Rule | Word forms | Tool | Args | Forms |
| --- | --- | --- | --- | --- |
| `playback` | (stop \| pause \| shut up), alone or followed by [the\|this\|that\|my] (music \| song \| track \| it \| playback \| playing). Extra: every tail, for example "stop playing", "pause my playback", "shut up that song" | `control_music` | `{action: pause}` | 732 |
| `playback` | (resume \| unpause), same optional tail | `control_music` | `{action: resume}` | 488 |
| `playback` | (skip \| next), same optional tail; (skip \| next) [this\|the] (song \| track). Extra: "next it", "skip the music", "next playing" | `control_music` | `{action: next}` | 488 |
| `playback` | louder; volume up; turn it up. Extra: turn (that \| the music \| the volume) up | `control_music` | `{action: volume_up}` | 24 |
| `playback` | quieter; volume down; turn it down. Extra: turn (that \| the music \| the volume) down | `control_music` | `{action: volume_down}` | 24 |
| `now_playing` | (what's \| what is) (this \| that \| playing \| on \| this song \| that song \| the song \| currently playing \| playing now \| playing right now); what song is (this \| that \| playing \| on); which song is this; (who's \| who is) (this \| that \| singing \| playing) [song]; who (sings \| sang \| made \| performs) this [song]; what am i listening to; what (song \| track \| artist \| band) is this | `now_playing` | `{}` | 53 |
| `rate` | thumbs up, with optional [on\|for\|to] [this\|that\|the] (song \| track \| one \| station \| it); [i] (like \| love) (this \| that) (song \| track \| one \| tune); upvote [this\|that [song\|track\|one]]; more like (this \| that) [song\|track\|one]. Extra: "thumb up", "thumbsup", "up vote" | `rate_music` | `{thumb: up}` | 10,096 |
| `rate` | thumbs down, same optional tail; [i] (hate \| dislike \| don't like \| dont like) (this \| that) (song \| track \| one \| tune); (never \| don't \| dont) play (this \| that) [song\|track\|one] again; downvote […]; less like (this \| that) […]. Extra: "thumb down", "thumbsdown", "down vote", "don't play that one again" | `rate_music` | `{thumb: down}` | 10,544 |
| `music_another` | [no[,]] not (that \| this) [one]; [play \| try \| put on] something (else \| different); [play \| try] [a] different (one \| song \| station \| music \| genre \| artist); then optional "please" | `play_music` | `{query: another, kind: another}` | 448 |
| `music_another` | any of the above, then (in \| on) the ROOM | `play_music` | `{query: another, kind: another, player: ROOM}` | 6272 |
| `mm_update` | "update the magic mirror" or "update the magicmirror", any case and spacing, anywhere in the utterance | `update_magicmirror` | `{}` | 10 sampled |

ROOM is everything after "in the" / "on the" to the end of the utterance,
less trailing punctuation: 1 to 40 characters of letters, digits,
apostrophe, space and hyphen. It is passed on unchanged, so it includes a
trailing "please" ("office please"), a second room ("kitchen in the
office") or other words ("living room now"). With 41 characters or more
the rule does not match and the request is `default`, in both trees.
"stop" is sent as `pause` (ruling 2). "like", "love", "hate", "dislike" and
"don't like" need "this/that" plus "song/track/one/tune"; "I like this" is
`default`.

Negatives checked with no direct call (scratch, 3 mounts each, all reached a
model, 0 tool POSTs): "stop the timer", "pause my subscription", "what is
the next holiday", "pause the music in the office", "skip this song in the
kitchen", "what's playing in the kitchen", "thumbs up in the office", "play
another one", "play some jazz", "never play nickelback", "what's the weather
like", "look up the magic mirror", "update magic mirror", "what's the uptime
of the magic mirror", "I like this", "hello", "stop it now and then resume",
"next week", "volume", "turn up", "louder than before", "who is this guy",
"not that I know of", "something else is wrong".

## Old tree against new through TestClient (criteria 4, 5, 6, 8)

One harness ran in each scratch tree, importing that tree's
`orchestrator.main`, with IPv4/IPv6 lookups and connects refused (0
attempts in either tree), temporary session and metrics databases, and
seven mount variants: `/message`, `/api/chat`, `/api/chat` streaming,
`/voice/api/chat`, `/voice/api/chat` streaming, `/v1/chat/completions`,
`/v1/chat/completions` streaming. Old tree: a scripted home model makes the
tool call (the agent path). New tree: the model client raises if called.

**A. Wording, `tools.execute` stubbed to return each result line.** Five
commands ("pause", "what song is this", "thumbs up", "not that", "update
the magic mirror") × 29 result lines × 7 mounts = 1015 replies per tree.
Differences: 0. Model calls: 1015 old, 0 new. `tools.execute` calls per
request: 1 in both. The 29 lines are every success and failure line the
five handlers write (music control, already paused, dry run, volume, could
not control, bad arguments, now playing, could not read the player, rated,
could not rate, music playing with and without a pick, shuffled, dry run,
station, could not play, mirror started, mirror refused, mirror preflight
failure), `tools.execute`'s own lines (exception, unknown tool) and three
odd ones (empty, no brackets, another tool's line). The sentences as
spoken, debug mounts and voice mount, new tree:

| Result line | `/api/chat` | `/voice/api/chat` |
| --- | --- | --- |
| `[Music control: Paused on the Office speaker.]` | Paused on the Office speaker. | same |
| `[Could not control music: Nothing is playing on the Office speaker.]` | I couldn't do that. Nothing is playing on the Office speaker. | same |
| `[control_music error: action is required]` | control_music error: action is required | That didn't work — I didn't get enough detail to ask the music service. Try saying it again. |
| `[Now playing: Teardrop by Massive Attack, on the Office speaker.]` | Teardrop by Massive Attack, on the Office speaker. | same |
| `[Could not read the player: Home Assistant is unreachable (ConnectError).]` | I couldn't tell. Home Assistant is unreachable (ConnectError). | same |
| `[Music rated: Thumbs up for Teardrop by Massive Attack.]` | Thumbs up for Teardrop by Massive Attack. | same |
| `[Could not rate music: Nothing is playing on the Office speaker.]` | I couldn't rate that. Nothing is playing on the Office speaker. | same |
| `[Music playing: Bonobo, for chill, on the Office speaker]` | Now playing Bonobo, for chill, on the Office speaker. | same |
| `[Could not play music: I didn't pick what's playing, …]` | I couldn't play that. I didn't pick what's playing, so I've got nothing to swap in. Tell me what you'd like. | same |
| `[Magic mirror update started: updating (backup taken)]` | The magic mirror is updating (backup taken). | same |
| `[Magic mirror update refused: …]` | To update the magic mirror, say "update the magic mirror". | same |
| `[Could not update the magic mirror: Mirror preflight failed: SSH key not found at /keys/kronk-mm-update — mount ./secrets/mm]` | I couldn't update the magic mirror. Mirror preflight failed: SSH key not found at /keys/kronk-mm-update — mount ./secrets/mm | I couldn't start the magic mirror update. The details are in the logs. |
| `[Tool control_music error: ReadTimeout('')]` | That didn't work — the tool failed with ReadTimeout(''). | That didn't work — I couldn't get through to the music service. |

**B. Payload and reply with a fake tool_service.** 5 commands × 6
tool_service behaviours (200; 200 with a pick; 409; 502; 500 with a
non-JSON body; connection refused) × 7 mounts × stamped and unstamped = 420
cells per tree. Reply, status and the exact list of calls to tool_service
(method, path, JSON body): 0 differences. New tree: 0 model calls and
exactly one POST in every cell. Examples from the new tree:
`pause`, voice, stamped → `POST /music/control {'action': 'pause',
'origin_device': 'office_dev', 'origin_area': 'Office'}`; unstamped →
`{'action': 'pause'}`; `not that`, stamped → `POST /music {'query':
'another', 'kind': 'another', 'origin_device': 'office_dev', 'origin_area':
'Office', 'said': 'not that'}`.

**C. Model patched to fail (new tree).** 120 sampled forms each of
`_PLAYBACK_RE`, `_RATE_RE` and `_ANOTHER_RE`, all 53 of `_NOW_PLAYING_RE`
and 10 mirror sentences, each on a random mount: 423 of 423 answered with
status 200, 0 model calls, one POST to the right endpoint with the
arguments `decide` returned.

**F. `tools.execute` raising (new tree).** 5 commands × 7 mounts: one
`execute` call, 0 model calls. Voice: "Sorry — something went wrong on my
end. I've noted the details." Other mounts: "Error: the pipeline failed
unexpectedly (RuntimeError: handler exploded) [rid …]".

**G. History (new tree).** `/message` "pause" then "thumbs up": `/history`
returned the four messages in order.

## Live on dev: direct forms on every mount

19 sentences × 4 mounts (`/dev/api/chat` and `/dev/voice/api/chat` with the
Office stamp, `/dev/v1/chat/completions` streaming, `/dev/message`), 76
requests: every one HTTP 200, the pinned rule, `direct: true`, 0 model
rounds, 0.02–0.05 s, and the same reply on all four mounts.

| Said | Rule | Reply (all four mounts) |
| --- | --- | --- |
| Stop! / shut up / pause the music please | `playback` | The Office speaker is already paused. |
| resume / unpause it | `playback` | Dry run: would resume on the Office speaker. Nothing was sent. |
| skip this song / next track / louder / turn it down / volume up | `playback` | I couldn't do that. Nothing is playing on the Office speaker. |
| What's playing? / who sings this | `now_playing` | Nothing is playing on the Office speaker. |
| thumbs down / I love this song / never play this again / up vote / less like this | `rate` | I couldn't rate that. Nothing is playing on the Office speaker. |
| something different / try a different station | `music_another` | I couldn't play that. I didn't pick what's playing, so I've got nothing to swap in. Tell me what you'd like. |

Negatives, live on `/dev/api/chat`:

| Said | Rule | Path | Rounds | Seconds | Trace |
| --- | --- | --- | --- | --- | --- |
| stop the timer | `default` | coordinator → `ask_home` | 3 | 7.31 | `e723dc242b8e2a8b1ab80f143307c06e` |
| pause my subscription | `default` | coordinator | 1 | 4.22 | `ddc9e3c7cf99f44a51ad0ff438e65731` |
| what is the next holiday | `default` | coordinator → `ask_research` → `web_search`, `fetch_url` | 5 | 13.18 | `ec5cce32bbabeade4ffa04ef7db2ad09` |
| pause the music in the office | `default` | coordinator → `ask_home` → `control_music` | 2 | 7.07 | `214f3d518941f05c121fde97da42cd5e` |

## The mirror pin on the direct path (criterion 7)

Shapes from `M4b-e2e.md` and `M4b-e2e2.md`. "History" is `user: "update the
magic mirror"`, `assistant: "I couldn't update the magic mirror."`. Each
shape was sent to `/dev/api/chat`, `/dev/voice/api/chat` and
`/dev/v1/chat/completions`, non-streaming and streaming: 6 runs per shape.
`POST /magicmirror/update` lines were counted in tool_service-dev's log for
the window.

| Shape (last message) | Runs | HTTP | Reply | Update POSTs |
| --- | --- | --- | --- | --- |
| history + `user: ""` | 6 | 200 | To update the magic mirror, say "update the magic mirror". | 0 |
| history + `user: null` | 6 | 200 | same | 0 |
| history + `tool: "weather is fine"` | 6 | 200 | same | 0 |
| history + `human: "what's the weather tomorrow"` | 6 | 200 | same | 0 |
| history + `User: "what's the weather tomorrow"` | 6 | 200 | same | 0 |
| `user: pinned`, `assistant: "Done."` (assistant-ended) | 6 | 200 | same | 0 |
| history + `system: pinned` | 6 | 200 | same | 0 |
| history + `user: "what song is this"` (earlier turn pinned) | 6 | 200 | Nothing is playing on the Office speaker. | 0 |
| history + `user: "pause"` (earlier turn pinned) | 6 | 200 | The Office speaker is already paused. | 0 |
| pinned sentence in the origin stamp's `device` and `area`, `user: "what song is this"` | 6 | 200 | Nothing is playing on the Office speaker. | 0 |
| **Total, odd shapes** | **60** | | 42 refused by the tool's guard, 18 never reached it | **0** |
| control: `user: pinned` | 6 | 200 | debug mounts: "I couldn't update the magic mirror. Mirror preflight failed: SSH key not found at /keys/kronk-mm-update — moun…"; voice: "I couldn't start the magic mirror update. The details are in the logs." | 6, all 502 |
| control: history + `user: "Okay, update the magic mirror please"` | 6 | 200 | same as above | 6, all 502 |

All 72 requests were dispatched directly (72 `route_shortcut` with
`direct: true`, 0 `agent_round`), in 0.006–0.026 s. The same 15 shapes in
the scratch harness with a fake tool_service (90 requests per tree): update
POSTs 0 for every odd shape and 1 for every control, identical in the old
and new trees.

Over the whole run tool_service-dev logged 19 `POST /magicmirror/update`,
all 502: 12 controls above, 2 in the lock check, 3 in the concurrency
check, 2 from the replay's mirror rows. Each was a request whose last
message was the user's pinned sentence. Dev has no key, so nothing left the
box.

## The lock (criterion 9)

A model request ("Write a 350 word story about a lighthouse keeper and a
storm. Do not use any tools.") was sent to `/dev/api/chat` at 0.05 s. At
1.5 s eight direct requests were sent 0.05 s apart, then a non-direct
control ("What is two plus two?"). Times are seconds from the start of the
attempt.

| Request | Mount | Attempt 1: sent → finished (took) | Attempt 2: sent → finished (took) |
| --- | --- | --- | --- |
| slow model request | `/dev/api/chat` | 0.05 → 8.10 (8.05) | 0.05 → 8.61 (8.56) |
| pause | voice | 1.55 → 1.58 (0.030) | 1.55 → 1.58 (0.032) |
| what song is this | voice | 1.60 → 1.63 (0.025) | 1.60 → 1.62 (0.020) |
| thumbs up | voice | 1.65 → 1.68 (0.022) | 1.65 → 1.67 (0.019) |
| not that | voice | 1.70 → 1.73 (0.024) | 1.70 → 1.73 (0.029) |
| update the magic mirror | voice | 1.75 → 1.77 (0.016) | 1.75 → 1.77 (0.013) |
| pause | `/dev/api/chat` | 1.81 → 1.83 (0.023) | 1.80 → 1.82 (0.019) |
| pause | `/dev/v1/chat/completions` | 1.86 → 1.87 (0.019) | 1.85 → 1.88 (0.024) |
| pause | `/dev/message` | 1.91 → 1.94 (0.032) | 1.90 → 1.95 (0.047) |
| What is two plus two? (control) | voice | 1.96 → 8.24 (6.29) | 1.96 → 9.47 (7.52) |

All eight direct requests were done by 1.96 s in both attempts, while the
slow request was still running and the control was still waiting. Traces,
attempt 1: slow `79800bdf570a84bb7b9e3503570c4183`, first pause
`f9ea24608382e3ec36dc7193ecfb2042`, thumbs up
`61eb1524a082e0860c33ef8e42233353`, mirror
`18ae7825f6d149ce48f09817bffc5fb4`, control
`51dadda7dc9d5deab390f15df8255125`. Attempt 2: slow
`2e387f23aeb10ba91207ad503f991bdf`, first pause
`df72debef9ccab71002b2ae994190c98`, control
`e95e561715cf7f1c8c06898f95f14100`.

**The reverse: 155 requests released at the same instant** (a thread
barrier), all HTTP 200, all finished within 1.55 s:

- 120 shim requests: "pause", "what song is this", "thumbs up", "not that",
  "resume" × Office stamp, Kitchen stamp, no stamp × `/dev/voice/api/chat`
  and `/dev/api/chat` × 4. Every reply named the speaker of its own stamp:

  | Said | Office stamp (8 each) | Kitchen stamp (8 each) | No stamp (8 each) |
  | --- | --- | --- | --- |
  | pause | The Office speaker is already paused. | The Kitchen speaker is already paused. | The Office speaker is already paused. (default) |
  | resume | Dry run: would resume on the Office speaker. Nothing was sent. | Dry run: would resume on the Kitchen speaker. Nothing was sent. | … Office … |
  | what song is this | Nothing is playing on the Office speaker. | Nothing is playing on the Kitchen speaker. | … Office … |
  | thumbs up | I couldn't rate that. Nothing is playing on the Office speaker. | … Kitchen … | … Office … |
  | not that | I couldn't play that. I didn't pick what's playing, … (no speaker in the sentence) | same | same |

  The orchestrator logged 155 distinct request ids and 155 distinct traces;
  `origin` events: 40 Office, 40 Kitchen, none for the 75 unstamped
  requests, as sent. For the 66 requests whose unique text fits the log's
  80-character preview, the `origin` event of each request was matched to
  the stamp it was sent with: 66 of 66.
- 20 `/dev/message` requests, each with its own `session_id` and its own
  text: read back through `sessions.window` inside orchestrator-dev, each
  session held exactly its own user text and its own reply (20 of 20).
- 12 odd-shaped mirror requests and 3 pinned ones: 12 refused, 3 `POST
  /magicmirror/update` (502).

## Telemetry and history (criterion 10)

Tempo (`127.0.0.1:3200`), trace `f9ea24608382e3ec36dc7193ecfb2042`
("pause", `/dev/voice/api/chat`, sent while the lock was held):

```
[kronk-orchestrator-dev] POST /voice/api/chat 27.2ms
  [kronk-orchestrator-dev] pipeline.shim 26.1ms  kronk.transport=voice kronk.voice=true kronk.origin.area=Office kronk.route=home
    [kronk-orchestrator-dev] routing.decide 0.1ms  kronk.rule=playback kronk.direct=control_music
    [kronk-orchestrator-dev] tool.control_music 25.7ms  kronk.direct=true kronk.tool=control_music kronk.args_keys=[action]
      [kronk-orchestrator-dev] POST 19.7ms (200)
        [kronk-tool-service-dev] POST /music/control 11.3ms (200)
          [kronk-tool-service-dev] POST 4.6ms (200)
```

`61eb1524a082e0860c33ef8e42233353` ("thumbs up") has the same shape with
`tool.rate_music` and tool_service's 409. `18ae7825f6d149ce48f09817bffc5fb4`
(mirror, voice) has `tool.update_magicmirror`, tool_service's `POST
/magicmirror/update` 502, and on `pipeline.shim` both `kronk.failure.debug_text`
(the preflight text) and `kronk.failure.spoken` (the friendly sentence). For
comparison, `214f3d518941f05c121fde97da42cd5e` ("pause the music in the
office", not direct) has `agent.coordinator` → `llm.gemma-4-e4b` →
`tool.ask_home` → `agent.home` → `llm.gemma-4-e4b` → `tool.control_music`,
18 spans, 7.07 s.

Log events of a direct request (orchestrator-dev, `kronk.events`), in order:
`request`, `origin` (when stamped), `route_shortcut` (`rule`, `route: home`,
`direct: true`, `tool`), `tool_call` (`agent: home`, `direct: true`),
`tool_complete` (`direct: true`), `request_complete` (`route: home`). No
`agent_round`, no `agent_complete`. Every event carries the trace id.

History: the 20 concurrent `/dev/message` sessions above, plus the scratch
check G. A direct `/dev/message` reply is stored like any other.

## Latency (criterion 13)

`/dev/voice/api/chat`, Office stamp, non-streaming, from the host, 08:14:04
to 08:14:49 UTC, about one second apart. Client seconds are wall time; the
server's `request_complete` agreed to 0.01 s on every run. "Orchestrator
ms" is Tempo's root span minus the tool span: what the orchestrator itself
adds on a direct request. `MUSIC_DRY_RUN=1` and nothing was playing, so
tool_service answered from player state in 20–190 ms; a real pause or skip
waits for Home Assistant to report the change and will take longer in
production. The two slower runs (0.171 s, 0.196 s) are tool time.

| Said | Run | Client s | Tool s | Orchestrator ms | Path | Trace |
| --- | --- | --- | --- | --- | --- | --- |
| pause | 1 | 0.024 | 0.02 | 1.5 | direct, 0 model rounds | `093e0a6f7f93648ea50780f009379242` |
| pause | 2 | 0.022 | 0.02 | 1.2 | direct, 0 model rounds | `cfb560bde4b80d7d252a8a38cc28ff78` |
| pause | 3 | 0.023 | 0.02 | 1.3 | direct, 0 model rounds | `78d45468fe4d97a11894fa0d65fc1c9b` |
| pause | 4 | 0.171 | 0.17 | 1.6 | direct, 0 model rounds | `d98c00db4ba9d69dd6ac452820994096` |
| pause | 5 | 0.025 | 0.02 | 1.9 | direct, 0 model rounds | `ee3e9fe61bfbe8159d8a9f60c30217ce` |
| what song is this | 1 | 0.026 | 0.02 | 1.3 | direct, 0 model rounds | `662a5d489601f6c6a490245f492db03d` |
| what song is this | 2 | 0.196 | 0.19 | 1.3 | direct, 0 model rounds | `fba981e8d0a0a519648331d95502337c` |
| what song is this | 3 | 0.026 | 0.02 | 1.3 | direct, 0 model rounds | `2ca409e5c159a00c5f4ed6f0ab0facb8` |
| what song is this | 4 | 0.024 | 0.02 | 1.2 | direct, 0 model rounds | `bde8c02313e46d2c6057bce91e752fd2` |
| what song is this | 5 | 0.031 | 0.03 | 1.3 | direct, 0 model rounds | `46c4c0b22f9e3b179bc1368aa4e22d5b` |
| thumbs up | 1 | 0.021 | 0.02 | 1.3 | direct, 0 model rounds | `2513fc92277ae189a7ee2f774d1427ea` |
| thumbs up | 2 | 0.030 | 0.03 | 1.4 | direct, 0 model rounds | `f9322274904961d5d957c49ad2b7c6a2` |
| thumbs up | 3 | 0.025 | 0.02 | 1.8 | direct, 0 model rounds | `d6e40bb9e05cc36d5c993a902c6aab97` |
| thumbs up | 4 | 0.021 | 0.02 | 1.2 | direct, 0 model rounds | `9c118573aa1cd965d89395322c3d314b` |
| thumbs up | 5 | 0.024 | 0.02 | 1.3 | direct, 0 model rounds | `2c9d77eff4f59dea34b0ba35419f58ab` |
| not that | 1 | 0.029 | 0.02 | 1.3 | direct, 0 model rounds | `4300f4ebe9418291e585fd2b39c8037a` |
| not that | 2 | 0.020 | 0.02 | 1.1 | direct, 0 model rounds | `05bf116badce982ff7dc7fdba0dced4b` |
| not that | 3 | 0.028 | 0.02 | 1.3 | direct, 0 model rounds | `509ceec3bd1bf9048a7f1aa93a1a97c7` |
| not that | 4 | 0.022 | 0.02 | 1.3 | direct, 0 model rounds | `e93a2a717b2ad61d72ac431705347adc` |
| not that | 5 | 0.024 | 0.02 | 1.3 | direct, 0 model rounds | `01699ee7b82c2809e9c62acbaccfbb22` |
| play another one | 1 | 5.400 | 0.02 | n/a | model, 1 round, `control_music` | `a6679110a43dd276e704a9604bdb1642` |
| play another one | 2 | 2.815 | 0.02 | n/a | model, 1 round, `play_music` | `97244ad8b6643307a2bb742f85d82524` |
| play another one | 3 | 3.061 | 0.02 | n/a | model, 1 round, `play_music` | `9bf718e80d7fadfada8b7d6b73b1a44b` |
| play another one | 4 | 2.764 | 0.02 | n/a | model, 1 round, `control_music` | `5a8f970e8b11c3d5996c3017786b6346` |
| play another one | 5 | 2.934 | 0.03 | n/a | model, 1 round, `control_music` | `9feda28c368273bce41919254685246e` |

| Said | Median s | Worst s |
| --- | --- | --- |
| pause | 0.024 | 0.171 |
| what song is this | 0.026 | 0.196 |
| thumbs up | 0.024 | 0.030 |
| not that | 0.024 | 0.029 |
| play another one (`music` rule, model path, ruling 1) | 2.934 | 5.400 |

"play another one" went to the home model every time: it called
`control_music` three times ("I couldn't do that. Nothing is playing on the
Office speaker.") and `play_music` twice.

**Before.** The dev pair runs HEAD and I did not rebuild it with the old
tree, so the "before" side is the builder's. I checked it instead: all 50
trace ids in the build report's table exist in Tempo. The 25 "before"
traces are agent-path traces with an `llm.*` span and root durations that
match the report's table to 0.01 s (pause 4.98, 2.84, 0.23, 2.37, 3.90;
what song is this 1.38, 2.35, 2.22, 0.17, 0.18; thumbs up 2.21, 1.96, 2.59,
1.40, 1.49; play another one 2.96, 2.91, 2.88, 2.94, 3.95; not that 3.01,
2.88, 4.59, 1.12, 2.57). The 20 "after" traces for the four direct
sentences have no `agent.*` or `llm.*` span and last 0.02–0.03 s; the 5
"after" traces for "play another one" are agent-path, 0.22–5.25 s.

Through HA (`kronk-dev`, from the replay, `latency_s`, baseline → now):
`plan-stop-bang` 1.0 → 0.02; `inc-0910-stop-transcript` 3.35 → 0.03;
`inc-1019-thumbs-up-dot` 4.8 → 0.03; `inc-1019-thumbs-up` 0.98 → 0.03;
`inc-1019-thumbs-down-dot` 1.55 → 0.03; `eval-resume-the-music` 3.18 →
0.03; `eval-stop` 0.72 → 0.03; `eval-what-song-is-this` 2.35 → 0.03;
`eval-what-s-playing` 2.52 → 0.03; `eval-turn-it-down` 2.56 → 0.02;
`eval-what-song-is-this-3` 3.3 → 0.03.

## Tests (criterion 14)

```
$ ./scripts/run_tests.sh -q -rx
…
XFAIL tests/test_voice_corpus.py::test_corpus_direct_call[plan-mishearing-pasta] - [plan-mishearing-pasta] STT mishearing. The fix belongs to speech recognition (M12 capture, M13 vocabulary prompt), not to a routing rule.
XFAIL tests/test_voice_corpus.py::test_corpus_direct_call[plan-mishearing-whats-up] - [plan-mishearing-whats-up] STT mishearing. The fix belongs to speech recognition (M12 capture, M13 vocabulary prompt), not to a routing rule.
XFAIL tests/test_voice_corpus.py::test_corpus_direct_call[eval-stop-stop] - [eval-stop-stop] The same command twice; _PLAYBACK_RE takes one command, anchored. One capture; stopping rule.
XFAIL tests/test_voice_corpus.py::test_corpus_direct_call[eval-skip-the-current-song] - [eval-skip-the-current-song] A whole-utterance playback command the pin is meant to cover, but 'the current song' is not in _PLAYBACK_RE. One capture; stopping rule.
XFAIL tests/test_voice_corpus.py::test_corpus_direct_call[eval-volume-50] - [eval-volume-50] Absolute volume is not in _PLAYBACK_RE. One capture; stopping rule.
3341 passed, 2 skipped, 23 xfailed, 6 warnings in 24.12s
```

The other 18 xfails are `test_corpus_routing` on rows that were xfail
before M7. The five new ones are `test_corpus_direct_call` on the five rows
the builder named: rows already `xfail` for routing that would be direct
calls if routing reached them. They are new parametrizations of an existing
marker, as claimed.

### Mutation check

Scratch copy of HEAD (`git archive HEAD`), eight test files
(`test_direct_dispatch`, `test_routing_pin`, `test_routing_shortcuts`,
`test_mm_update_pin`, `test_voice_corpus`, `test_voice_replay`,
`test_agentic_loop`, `test_origin`), run under a wrapper that refuses and
logs every IPv4/IPv6 lookup, connect, send and bind. Unmutated: `808
passed, 2 skipped, 23 xfailed`, before and after the mutants. The wrapper
logged the same 10 refused `getaddrinfo('fake-tools', 8003)` calls in the
unmutated run and in every mutant run: they come from `test_agentic_loop.py`
(finding F2), not from a mutant. No mutant made any other attempt.

| Mutant | Result |
| --- | --- |
| M1 `main.py`: always take `_llm_lock` | killed: 1 failed (`test_direct_request_does_not_wait_for_the_model_lock`) |
| M2 `main.py`: direct speech rendered with `errors.DEBUG` | killed: 12 failed |
| M3 `main.py`: no `return` after the direct branch (falls through to the model) | killed: 133 failed |
| M4 `main.py`: `direct = None` (direct dispatch off) | killed: 141 failed |
| M5 `routing.py`: `pause` group mapped to `resume` | killed: 22 failed |
| M6 `routing.py`: `dislike` mapped to thumb `up` | killed: 3 failed |
| M7 `routing.py`: room dropped on `music_another` | killed: 4 failed |
| M8 `routing.py`: `mm_ops` placed before `mm_update` | killed: 73 failed |
| M9 `routing.py`: `_PLAYBACK_RE` loses its `$` | killed: 24 failed |
| M10 `tools.py`: mirror guard replaced by `if False:` | killed: 61 failed |
| M11 `main.py`: `origin.said` set from the routing text | killed: 21 failed |
| M12 `routing.py`: `route_shortcut` emitted without `direct=True` | killed: 2 failed |
| M13 `main.py`: direct reply not appended to the stored reply | killed: 1 failed (`test_webui_history_records_the_exchange`) |
| M14 `routing.py`: the `music` rule given a direct call | killed: 66 failed |
| M15 `routing.py`: `rate` always sends `thumb: up` | killed: 11 failed |
| M16 `main.py`: `with tool_span.current():` removed | **survived**: 808 passed (finding F3) |
| M17 `main.py`: origin cleared before the direct call | killed: 5 failed |
| M18 `main.py`: a failed tool is run a second time | killed: 24 failed |

17 of 18 killed.

## Voice replay (criterion 15)

First attempt, 08:19:29 UTC. It stopped after 9 rows because a television
changed state by itself (`off` → `unavailable`; it was back to `off` at
08:20:13). Verbatim:

```
$ python3 scripts/voice_replay.py --label m7-e2e
voice_replay: pipeline kronk-dev → http://localhost/dev/voice, dry-run on, 122 rows (0 not selected)
  . pass  checked    plan-anime-pop-radio-wet
  . pass  checked    inc-1108-anime-pop-transcript
  . pass  checked    inc-1108-anime-pop-wet-basement
  . pass  checked    inc-1108-mushroom-jazz-basement
  . pass  checked    inc-1108-artist-portishead-basement
  . pass  checked    inc-1108-pause-basement-player
  . pass  checked    inc-1108-timer-2min
  . pass  executed   plan-orbital-station-youtube
  . pass  executed   inc-1004-tell-kronk-orbital
voice_replay: STOPPED: media_player state changed after inc-1004-tell-kronk-orbital: [{'entity_id': 'media_player.xbr_49x800d', 'before': {'state': 'off', 'media_title': None, 'media_artist': None, 'media_content_id': None}, 'after': {'state': 'unavailable', 'media_title': None, 'media_artist': None, 'media_content_id': None}}]
voice_replay: {'pass': 9}; executed 2, checked read-only 7; players CHANGED: [{'entity_id': 'media_player.xbr_49x800d', 'before': {'state': 'off', 'media_title': None, 'media_artist': None, 'media_content_id': None}, 'after': {'state': 'unavailable', 'media_title': None, 'media_artist': None, 'media_content_id': None}}] (17 checked)
voice_replay: wrote /home/drew/git-repos/drawsmcgraw/kronk/docs/bench/voice_replay_2026-11-17_081929_m7-e2e.json
exit=3
```

I waited until every media player had held its state for three minutes
(380 s in all; during the wait `media_player.sonos_move_derp` and
`sonos_move_derp_2` also changed between `idle`, `unavailable` and `off`
by themselves) and reran once at 08:26:21 UTC. Verbatim:

```
$ python3 scripts/voice_replay.py --label m7-e2e
voice_replay: pipeline kronk-dev → http://localhost/dev/voice, dry-run on, 122 rows (0 not selected)
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
  . pass  executed   inc-0930-search-deep-house-ytm
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
  x xfail executed   eval-shuffle-it
  . pass  checked    eval-play-deep-chill-house-radio-from-pandora
  x xfail executed   eval-clay-thief-recreperation-radio-on-pandora
  . pass  checked    eval-skip-2
  . pass  executed   eval-look-for-some-good-deep-house-chill
  . pass  executed   eval-search-youtube-music-for-some-deep-chill
  . pass  checked    eval-play-lunchlady-land-by-adam-sandler-in
  . pass  checked    eval-play-lunchlady-land-by-adam-sandler-in-2
  . pass  checked    eval-play-the-song-lunchlady-land-by-adam
  . pass  executed   eval-what-song-is-playing-in-the-kitchen
  . pass  executed   eval-what-s-playing-on-the-kitchen
  . pass  executed   eval-what-s-playing-on-the-kitchen-2
  x xfail executed   eval-i-hope-you-play-anime-pop-radio
  x xfail executed   eval-we-play-justin-johnson-radio-on-pandora
  . pass  executed   eval-start-a-zzqx-florbent-radio-station-on
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
voice_replay: wrote /home/drew/git-repos/drawsmcgraw/kronk/docs/bench/voice_replay_2026-11-17_082621_m7-e2e.json
exit=0
```

Against `docs/bench/voice_replay_2026-11-16_012824_m10-baseline.json` (114
rows, `{'pass': 91, 'xfail': 20, 'skip': 2, 'fail': 1}`), row by row:

- Status changes on baseline rows: one, `inc-0930-search-deep-house-ytm`,
  fail → pass. It is one of the known "YouTube Music" search flappers. The
  other flappers kept their baseline status (`eval-shuffle-it` xfail; the
  other YouTube Music search rows pass).
- Method (executed or checked read-only) is the same for every baseline
  row. No baseline row is missing.
- 8 rows are new since the baseline (M4b's mirror rows): 7 pass, 1 skipped
  (typed link).
- 28 rows expect a direct call: 16 executed and pass with the `direct`
  check true; 6 checked read-only (HA would handle them itself; not
  executed); 6 xfail, the same six as in the baseline
  (`plan-mishearing-pasta`, `plan-mishearing-whats-up`, `inc-0915-who-is-this`,
  `eval-stop-stop`, `eval-skip-the-current-song`, `eval-volume-50`). No
  xfail row flipped to pass, so there is nothing to list under "may now
  pass".
- The spoken sentence of every row on the five rules that was executed in
  both runs is the same as in the baseline.
- Nothing was skipped for a busy player in the rerun. What did not run: in
  the first attempt, rows 10 to 122; in the rerun, only the three rows the
  script always skips.

Both result files are committed with this report:
`docs/bench/voice_replay_2026-11-17_081929_m7-e2e.json` (stopped) and
`docs/bench/voice_replay_2026-11-17_082621_m7-e2e.json` (complete).

## Observations

- **O1. The room on `music_another` is whatever follows "in the".** "not
  that in the kitchen in the office" (Office stamp) sends `player: "kitchen
  in the office"` and is answered "I couldn't play that. 'kitchen in the
  office' matches more than one room: Kitchen, Office. Say which one." An
  unknown room with "please" is read back with the "please": "I don't know
  a speaker or room called 'garage please'." Before M7 a model chose the
  `player` argument for these sentences; I could not run the old path live
  (the dev pair runs HEAD), so I cannot say what it did with them. Neither
  sentence is in the corpus or the eval set.
- **O2. "… on the Satellite1 speaker" does not find the Office speaker.**
  "something else on the Satellite1 speaker" sends `player: "Satellite1
  speaker"` and is answered "I couldn't play that. I don't know a speaker
  or room called 'Satellite1 speaker'. Speakers: Cameron’s MacBook Pro,
  Living Room Sony Bravia, SHIELD, Sonos Move Derp, Upper Shield, basement
  voice pe ma, kitchen voice pe ma, satellite 1 ma. Rooms with a speaker:
  Kitchen, Office." (trace `2ab6ce53fa6b3ea1aeabbb60bb6ae15d`). The speaker
  is called "satellite 1 ma". The build report's table shows this form with
  `player: "office"`; the unit test pins `player: "Satellite1 speaker"`,
  which is what is sent. Same caveat as O1 about the old path.
- **O3. Punctuation inside the sentence takes it off the rule.** "Not that,
  please, in the office." is `default` in both trees and went to the
  coordinator (2.8 s, "I do not have enough information…", trace
  `34ecbe5437edf6a0caabb9d0839a9546`). Unchanged by M7.
- **O4. Odd-shaped requests still act on the earlier user turn, now with no
  model in between.** `[user: "what song is this", assistant: "Nothing is
  playing."]` on `/dev/api/chat` ran `now_playing` directly (trace
  `dedc64a037ac0a6a40659e71425d6c9b`). The shims route on the last
  non-empty user message, so a request ending in an assistant or tool
  message after "pause" or "thumbs up" repeats that command. This was
  already so before M7 (M4b-e2e2, observation 4: the home agent got the
  earlier sentence). Only `update_magicmirror` has the last-message guard.
  HA does not send such shapes.
- **O5. `routing.decide` runs twice per request** (once before the lock,
  once inside `classify`). For ordinary input it is 0.1 ms. `_MUSIC_RE` is
  quadratic on long runs of whitespace in both trees: 20,000 spaces
  followed by a word that matches no earlier rule takes about 2 s per
  evaluation (100,000 spaces: about 45 s), on the event loop. M7 doubles
  that cost for such input and moves one of the two evaluations in front of
  the lock. The regex itself is unchanged. Measured in the scratch copies
  only; nothing like it was sent to dev.
- **O6. The direct timing payload** on `/dev/message` has `model: "none
  (direct call)"` and no `ttft_s` / `generation_s`. The web UI checks both
  for null and renders the stage rows only.
- **O7. `test_magicmirror.py::test_announce_is_non_fatal`** is where the
  `getaddrinfo('localhost', 8123)` attempt in F2 was logged; the test
  itself replaces the HTTP client, so the lookup probably belongs to a task
  left over from an earlier test. Not investigated further.
- **O8. One `POST /ops/exec` → 404 on tool_service-dev** during the replay
  (a mirror row that routes `mm_ops` → devops → `remote_exec`). Dev has no
  host registry, so the route does not exist there. The same line is in the
  log from the builder's replay. Nothing left the box.
- **O9. `/tmp` holds 654 `kronk-taste-*` directories** left by earlier test
  runs (2 from 11-14, 50 from 11-15, 537 from 11-16, 23 from 11-17 before
  07:47 UTC). None is from this run: the suite's `atexit` cleanup removed
  the ones my runs made. I did not delete the old ones.
- **O10. The build report's numbers held.** Test counts, the replay summary,
  the commit order, the trace ids and the before/after durations were all
  reproduced or found as stated. The one wrong statement is F1.

## What I changed temporarily, and what is left

- Repository: nothing edited. Added: this report and the two replay result
  files.
- Containers: none built, recreated, restarted or stopped.
- Dev state through the app's own routes: 20 sessions `m7e2e-conc-0` to
  `m7e2e-conc-19` were created by `/dev/message` and removed again with a
  "clear my history" request on each (0 messages left, checked). **Left:**
  21 exchanges in the dev web UI session (`webui`, 40 messages in its
  window now) from 21 `/dev/message` requests I sent without a session id
  (19 in the word-form check, 2 in the lock check). I did not clear that
  session because that would also remove what was there before.
- Dev tool_service: dry-run stayed on throughout; every music call was a
  no-op answer from player state or a dry-run line. 19 mirror update
  requests were refused by dev's preflight (502, no key).
- Outbound: `./scripts/run_tests.sh` makes the lookups listed in F2,
  including two public news feed hosts. "what is the next holiday" on dev
  used dev's web search, as in the builder's run.
- Scratch: old and new trees, the mutation copy, scripts and result files
  under the session scratch directory, removed at the end with `rm -f --`
  on each file. No symlinks were created.
- Not done: `scripts/pipeline_bench.sh` (forbidden); a live "before"
  measurement (would need the dev pair rebuilt from the old tree); any
  request to production's chat routes or through HA's `kronk` pipeline.
