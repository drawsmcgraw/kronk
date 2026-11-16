# M5 change 1: drop the `http_check` allow-list

Build agent, 2026-11-16 (UTC), branch `refactor`. Code commit `59c0cec`;
this report and the replay file are the commit after it.

Decision (Drew, 2026-11-16): "drop the list". `http_check` gets no host
allow-list and may check any http/https URL the host can reach, private or
public. Criterion 12 and the allow-list parts of 15 are replaced by that.
The rest of section B stands. Section A (`/fetch`) is not changed.

## What changed

| File | Change |
| --- | --- |
| `tool_service/netguard.py` | Removed `HTTP_CHECK_HOSTS_FILE` (and the `OPS_HTTP_CHECK_HOSTS` env lookup), `load_http_check_allowlist`, `normalize_host`, and the now-unused `json`, `os`, `Path` imports. Docstring :11-14 states the exemption and the decision. `/fetch`'s functions are untouched |
| `tool_service/main.py` | Comment block :522-531 rewritten: any address, no list, the GET warning. `/http_check` :576. Allow-list check removed; `host = u.host` :588 is the audit host. `last` :593 / :605 tracks the last URL requested. Redirect handling: an unparseable `Location` is caught :620; every redirect target goes through `netguard.parse_url` :626 (http/https, no credentials, has a host); the too-many check :632 returns `final_url=str(current)` :636, the URL just requested; a target that is not followed gets a note naming that cause :638. Budget spent between hops: `final_url` is the last URL requested :604, and the note names the URL not requested |
| `orchestrator/tools.py` | `http_check` description :439: "Works on any http or https address the Kronk host can reach, on the LAN or public." Handler docstring :912 |
| `orchestrator/agents.py` | Devops prompt :356: "it works on LAN and public addresses" (was "only hosts on the operator's allow-list"). Comment :365. `tool_names` and the devops `description` (what the coordinator reads) are unchanged |
| `ops/http_check_hosts.example.json` | Deleted |
| `.gitignore` | The `ops/http_check_hosts.json` lines removed; the file is byte-identical to `91e29a6` (pre-M5) |
| `tests/conftest.py` | `no_network` fixture :29-80 and `NetworkBlocked` :43 (see "Tests cannot reach the network") |
| `tests/test_fetch_address_block.py` | `pytestmark` uses `no_network` :24. Stub-resolved private names moved from `*.example.net` to `*.home.test` :80-83. Docstring says why the criterion-7 literals stay |
| `tests/test_http_check.py` | Rewritten for no list; `no_network` :30; placeholders `nas.test`, `192.0.2.10`, `2001:db8::10` :32-34, loopback for the not-public cases |
| `README.md` | :218 devops row; :432-436 says `http_check` reaches any address, that it was the operator's decision, and the GET warning |
| `kronk-context.md` | :108 devops row |
| `docs/features/fetch-address-block.md` | Header links this report. "How `http_check` works" covers redirects and `final_url`. New section "No allow-list (operator decision, 2026-11-16)" :70 with the plan's warning :78. "Accepted limits" updated; the shared-`ops/` gotcha removed (it was about the scratch list). Two blog hooks added |

`grep -rn "http_check_hosts\|OPS_HTTP_CHECK_HOSTS\|load_http_check_allowlist"`
outside `docs/sprint/`, `docs/bench/` and the plan finds only the feature
doc's history paragraph and the test that asserts the loader is gone. No
dormant switch: `test_no_allow_list_is_consulted` sets
`OPS_HTTP_CHECK_HOSTS` to a file naming no hosts and the check still runs.
The `./ops:/ops:ro` mount stays; `/ops/exec` uses it.

The coordinator's tool menu (`agents._coordinator_tool_defs()`, sha256
prefix `26c8acf887c313b1`) is the same at `936429c` and `59c0cec`.

## The e2e observations

- **4 (a), fixed.** A redirect target with credentials or a non-http(s)
  scheme is not followed, and the note now gives that cause:
  `redirect to ftp://… not followed: only http and https URLs are allowed, not 'ftp'`,
  `redirect to http://user:pw@… not followed: URLs with credentials (user:password@) are not allowed`.
  Both use `netguard.parse_url`, the same check as the first URL. An
  unparseable `Location` (`http://[::1`) used to raise `InvalidURL` and give
  a 500; it is now reported, not followed, with `invalid URL: …`.
- **4 (b), fixed.** On `too_many_redirects`, `final_url` is the sixth URL
  requested (`/chain/5` live, below), not the unrequested seventh. The same
  bug was in the budget-spent path (the next, unrequested URL); fixed there
  too.
- **4 (c), gone.** The trailing-dot mismatch came from the list match
  stripping the dot while the request kept it. With no list, nothing
  normalises the host: `http://172.18.0.1.:18770/` is requested as given
  and fails as `dns`, which is the true cause.
- **6, fixed.** `test_unfollowable_redirect_names_its_own_cause` has two
  credential cases (`user:pw@`, `user@`). The mutant that drops the check on
  redirect targets (C1 below) now fails 5 tests.

## Tests cannot reach the network

The e2e tester's mutants H5 and H13 built a real `httpx.get` behind the fake
client's back. The tests' placeholder hosts were `192.168.1.10` (ARP on the
LAN) and `nas.local` (mDNS). Two layers now stop that:

1. **Structural.** `no_network` replaces `socket.getaddrinfo`,
   `gethostbyname`, `gethostbyname_ex`, `gethostbyaddr`, `getnameinfo` and
   `create_connection` with functions that raise `NetworkBlocked`, and wraps
   `socket.socket.connect`, `connect_ex`, `sendto`, `sendmsg` to raise for
   `AF_INET`/`AF_INET6` sockets. Name lookups go through `socket.getaddrinfo`
   in httpx's sync path (`create_connection`) and in asyncio
   (`loop.getaddrinfo` runs it in a thread); literal addresses go straight
   to `sock.connect`. So no lookup and no connect syscall happens, for any
   host. `NetworkBlocked` is an `AssertionError`, not an `OSError`, so httpx
   and httpcore do not wrap it into an ordinary `ConnectError`. Both
   `test_fetch_address_block.py` and `test_http_check.py` opt in with
   `pytestmark`. It is not applied suite-wide; no other module tests
   outbound fetches. `test_the_network_guard_is_on_for_this_module` checks
   the patches are in place before it calls anything, then shows that
   `getaddrinfo`, `create_connection`, a raw `connect`, `httpx.get` and
   `httpx.AsyncClient().get` all raise `NetworkBlocked`.
2. **Placeholders.** In `test_http_check.py`: `nas.test` (RFC 6761),
   `192.0.2.10` (RFC 5737), `2001:db8::10` (RFC 3849), and `127.0.0.1:9`,
   `[::1]:9`, `localhost:9` for "a non-public host is checked". No RFC 1918
   address is used there: a leaked request to one would route to the
   default gateway. `example.com` stays for the public case. In
   `test_fetch_address_block.py`, the stub-resolved private names are now
   `*.home.test`. The literal private addresses stay (`192.168.1.1`,
   `10.0.0.1`, `169.254.169.254`, …): criterion 7 names them, they are
   refused before any request, and an unexpected request fails in the fake
   client.

All `/fetch` and `/http_check` tests already went through `FakeClient`
(`patch.object(ts.httpx, "AsyncClient", client)`). The orchestrator-side
tests use their own fake `get`. `tests/test_mm_update_pin.py` fakes
`/fetch` at the orchestrator, so it never reaches tool_service.

### Mutation check

Scratch copy of the working tree (`rsync --no-links`, no symlinks), repo
`tests/.venv`. Each run went under an outer net: a `sys.addaudithook` hook
that raises on the `socket.getaddrinfo`, `gethostbyname`, `gethostbyaddr`,
`getnameinfo` audit events, and on `socket.connect`, `sendto`, `sendmsg`
for inet sockets. CPython raises these events before the lookup or
syscall, so even a hole in the fixture sends nothing. One edit per mutant,
restored after each, on `test_fetch_address_block.py`, `test_http_check.py`
and `test_agent_tool_separation.py`:

```
baseline: 133 passed in 0.87s; audit hook caught 0 network event(s): []
KILLED   C1 redirect target not checked (credentials/scheme followed): 5 failed, 128 passed in 1.28s; NetworkBlocked raised: False; audit hook caught 0 network event(s): []
KILLED   C2 unfollowable-redirect note blames the host (old wording): 6 failed, 127 passed in 0.73s; NetworkBlocked raised: False; audit hook caught 0 network event(s): []
KILLED   C3 too_many_redirects final_url is the unrequested next URL: 1 failed, 132 passed in 0.67s; NetworkBlocked raised: False; audit hook caught 0 network event(s): []
KILLED   C4 budget-spent final_url is the unrequested next URL: 1 failed, 132 passed in 0.68s; NetworkBlocked raised: False; audit hook caught 0 network event(s): []
KILLED   C5 a private host is refused (block applied to http_check): 9 failed, 124 passed in 0.73s; NetworkBlocked raised: False; audit hook caught 0 network event(s): []
KILLED   C6 invalid Location crashes (no catch): 1 failed, 132 passed in 0.78s; NetworkBlocked raised: False; audit hook caught 0 network event(s): []
KILLED   G1 real httpx.get in the success path (e2e H5), guard on: 17 failed, 116 passed in 2.75s; NetworkBlocked raised: True; audit hook caught 0 network event(s): []
KILLED   G2 real AsyncClient in /fetch success path, guard on: 4 failed, 129 passed in 1.33s; NetworkBlocked raised: True; audit hook caught 0 network event(s): []
KILLED   G3 = G1 with the no_network fixture removed (audit hook is the only net): 18 failed, 115 passed in 2.92s; NetworkBlocked raised: False; audit hook caught 18 network event(s): [('socket.getaddrinfo', ('192.0.2.10', 80)), ('socket.getaddrinfo', ('192.0.2.10', 5000)), ('socket.getaddrinfo', ('nas.test', 443)), ('socket.getaddrinfo', ('2001:db8::10', 8080)), ('socket.getaddrinfo', ('example.com', 443)), ('socket.getaddrinfo', ('127.0.0.1', 9)), ('socket.getaddrinfo', ('::1', 9)), ('socket.getaddrinfo', ('localhost', 9)), ('socket.getaddrinfo', ('192.0.2.10', 80)), ('socket.getaddrinfo', ('nas.test', 80))]
9 of 9 killed; restored baseline: 133 passed in 0.66s; audit hook caught 0 network event(s): []
```

G1 is the e2e tester's H5: with the fixture on, it fails fast with
`NetworkBlocked` and the outer hook sees nothing. G3 removes the fixture
and shows what would have been tried: only documentation addresses, `.test`
names, loopback and `example.com`, each stopped by the hook before any
lookup. The upstream resolver here is `192.168.1.2` (`resolvectl status`),
so a lookup that got through would have been a LAN request. That is why
the outer net was used and the fixture-off run was not done bare. A first
version of G2 was a dud (`AttributeError` on the patched name, killed for
the wrong reason). It was rewritten to build `httpx._client.AsyncClient()`,
which is the result shown.

## Live checks on dev

Deploy: `docker compose --profile dev up -d --build tool_service-dev orchestrator-dev`.
Only `kronk-tool_service-dev-1` and `kronk-orchestrator-dev-1` were
recreated (23:26:51Z, 23:26:52Z). The other 12 containers kept their IDs
and creation times. md5 of `main.py`, `netguard.py` (in tool_service-dev)
and `agents.py`, `tools.py` (in orchestrator-dev) match the working tree.
tool_service-dev's `/ops` holds `hosts.example.json` only, and no
`OPS_HTTP_CHECK*` variable is set. The recreate also removed the e2e
tester's leftover `/tmp` files in the old container. nginx resolves
`orchestrator-dev` per request (`set $orchestrator_dev`), so it was not
restarted.

Scratch server: `python3 -I`, `BaseHTTPRequestHandler`, bound to
`172.18.0.1:18765` (the `kronk_kronk` bridge gateway, this host). `/ok`
serves a body marked `SCRATCH-BODY-MUST-NOT-LEAK` with `X-Secret` and
`Set-Cookie` headers. Port 18766 had no listener (checked with `ss`).
Requests went to `127.0.0.1:8103` only, at 23:27:33Z.

```
### http_check
$ http://172.18.0.1:18765/ok
{"url":"http://172.18.0.1:18765/ok","ok":true,"elapsed_ms":7,"status":200,"redirects":[],"final_url":"http://172.18.0.1:18765/ok","headers":{"server":"BaseHTTP/0.6 Python/3.13.7","content-type":"text/html","content-length":"52"}}
$ http://172.18.0.1:18765/redir
{"url":"http://172.18.0.1:18765/redir","ok":true,"elapsed_ms":8,"status":200,"redirects":[{"url":"http://172.18.0.1:18765/redir","status":302,"location":"/ok"}],"final_url":"http://172.18.0.1:18765/ok","headers":{"server":"BaseHTTP/0.6 Python/3.13.7","content-type":"text/html","content-length":"52"}}
$ http://172.18.0.1:18765/to-ftp
{"url":"http://172.18.0.1:18765/to-ftp","ok":true,"elapsed_ms":7,"status":302,"redirects":[{"url":"http://172.18.0.1:18765/to-ftp","status":302,"location":"ftp://172.18.0.1:18765/file"}],"final_url":"http://172.18.0.1:18765/to-ftp","headers":{"server":"BaseHTTP/0.6 Python/3.13.7","content-length":"0","location":"ftp://172.18.0.1:18765/file"},"note":"redirect to ftp://172.18.0.1:18765/file not followed: only http and https URLs are allowed, not 'ftp'"}
$ http://172.18.0.1:18765/to-creds
{"url":"http://172.18.0.1:18765/to-creds","ok":true,"elapsed_ms":6,"status":302,"redirects":[{"url":"http://172.18.0.1:18765/to-creds","status":302,"location":"http://user:pw@172.18.0.1:18765/ok"}],"final_url":"http://172.18.0.1:18765/to-creds","headers":{"server":"BaseHTTP/0.6 Python/3.13.7","content-length":"0","location":"http://user:pw@172.18.0.1:18765/ok"},"note":"redirect to http://user:pw@172.18.0.1:18765/ok not followed: URLs with credentials (user:password@) are not allowed"}
$ http://172.18.0.1:18765/chain/0
{"url":"http://172.18.0.1:18765/chain/0","ok":false,"elapsed_ms":13,"cause":"too_many_redirects","error":"too many redirects (more than 5)","redirects":[{"url":"http://172.18.0.1:18765/chain/0","status":302,"location":"/chain/1"},{"url":"http://172.18.0.1:18765/chain/1","status":302,"location":"/chain/2"},{"url":"http://172.18.0.1:18765/chain/2","status":302,"location":"/chain/3"},{"url":"http://172.18.0.1:18765/chain/3","status":302,"location":"/chain/4"},{"url":"http://172.18.0.1:18765/chain/4","status":302,"location":"/chain/5"},{"url":"http://172.18.0.1:18765/chain/5","status":302,"location":"/chain/6"}],"final_url":"http://172.18.0.1:18765/chain/5"}
$ http://172.18.0.1:18766/
{"url":"http://172.18.0.1:18766/","ok":false,"elapsed_ms":8,"cause":"connection_refused","error":"connection refused","redirects":[],"final_url":"http://172.18.0.1:18766/"}
$ https://example.com/
{"url":"https://example.com/","ok":true,"elapsed_ms":93,"status":200,"redirects":[],"final_url":"https://example.com/","headers":{"server":"cloudflare","content-type":"text/html; charset=utf-8"}}
### fetch
$ http://127.0.0.1:8103/health
{"url":"http://127.0.0.1:8103/health","ok":false,"error":"blocked: 127.0.0.1 is a loopback address"}
$ http://tool_service:8003/health
{"url":"http://tool_service:8003/health","ok":false,"error":"blocked: 'tool_service' is a single-label hostname (a local or Docker service name), not a public site"}
$ http://192.168.1.1/
{"url":"http://192.168.1.1/","ok":false,"error":"blocked: 192.168.1.1 is a private (RFC 1918) address"}
```

Scratch server log, complete:

```
REQ GET /ok headers=['Accept', 'Accept-Encoding', 'Connection', 'Host', 'User-Agent', 'traceparent']
REQ GET /redir headers=['Accept', 'Accept-Encoding', 'Connection', 'Host', 'User-Agent', 'traceparent']
REQ GET /ok headers=['Accept', 'Accept-Encoding', 'Connection', 'Host', 'User-Agent', 'traceparent']
REQ GET /to-ftp headers=['Accept', 'Accept-Encoding', 'Connection', 'Host', 'User-Agent', 'traceparent']
REQ GET /to-creds headers=['Accept', 'Accept-Encoding', 'Connection', 'Host', 'User-Agent', 'traceparent']
REQ GET /chain/0 headers=['Accept', 'Accept-Encoding', 'Connection', 'Host', 'User-Agent', 'traceparent']
REQ GET /chain/1 headers=['Accept', 'Accept-Encoding', 'Connection', 'Host', 'User-Agent', 'traceparent']
REQ GET /chain/2 headers=['Accept', 'Accept-Encoding', 'Connection', 'Host', 'User-Agent', 'traceparent']
REQ GET /chain/3 headers=['Accept', 'Accept-Encoding', 'Connection', 'Host', 'User-Agent', 'traceparent']
REQ GET /chain/4 headers=['Accept', 'Accept-Encoding', 'Connection', 'Host', 'User-Agent', 'traceparent']
REQ GET /chain/5 headers=['Accept', 'Accept-Encoding', 'Connection', 'Host', 'User-Agent', 'traceparent']
```

Six requests for the chain (`/chain/0` to `/chain/5`), and `final_url` is
`/chain/5`. No `ftp` or credentialed request arrived. The three `/fetch`
refusals appear in tool_service-dev's log as `fetch refused: …` warnings
and nothing else; the refusal returns before the client is used. The
markers (`MUST-NOT-LEAK`) appear 0 times in the responses and the audit
lines. Only `GET` was received.

Dev audit log (`data/dev/ops_audit.log`, 107 → 114 lines), the seven new lines:

```
{"ts": 1794871653.254, "host": "172.18.0.1", "command": "http_check GET http://172.18.0.1:18765/ok", "allowed": true, "exit_code": 200, "out_len": 0, "note": "HTTP 200"}
{"ts": 1794871653.268, "host": "172.18.0.1", "command": "http_check GET http://172.18.0.1:18765/redir", "allowed": true, "exit_code": 200, "out_len": 0, "note": "HTTP 200"}
{"ts": 1794871653.281, "host": "172.18.0.1", "command": "http_check GET http://172.18.0.1:18765/to-ftp", "allowed": true, "exit_code": 302, "out_len": 0, "note": "redirect to ftp://172.18.0.1:18765/file not followed: only http and https URLs are allowed, not 'ftp'"}
{"ts": 1794871653.291, "host": "172.18.0.1", "command": "http_check GET http://172.18.0.1:18765/to-creds", "allowed": true, "exit_code": 302, "out_len": 0, "note": "redirect to http://user:pw@172.18.0.1:18765/ok not followed: URLs with credentials (user:password@) are not allowed"}
{"ts": 1794871653.309, "host": "172.18.0.1", "command": "http_check GET http://172.18.0.1:18765/chain/0", "allowed": true, "exit_code": null, "out_len": 0, "note": "too many redirects (more than 5)"}
{"ts": 1794871653.322, "host": "172.18.0.1", "command": "http_check GET http://172.18.0.1:18766/", "allowed": true, "exit_code": null, "out_len": 0, "note": "connection_refused: connection refused"}
{"ts": 1794871653.42, "host": "example.com", "command": "http_check GET https://example.com/", "allowed": true, "exit_code": 200, "out_len": 0, "note": "HTTP 200"}
```

The scratch server was stopped afterwards; nothing listens on 18765.

## Test output

`./scripts/run_tests.sh -q` at `59c0cec`:

```
2215 passed, 2 skipped, 18 xfailed, 3 warnings in 16.13s
```

Baseline 2208 passed. The difference, +7, is all in
`tests/test_http_check.py` (40 → 47 collected; `test_fetch_address_block.py`
stays at 69):

- Removed (19, the allow-list): `test_absent_file_refuses_everything_and_names_the_file` (1),
  `test_empty_file_refuses_everything` (6), `test_unreadable_json_refuses_and_says_so` (1),
  `test_unlisted_host_is_refused_before_any_connection` (1),
  `test_listed_private_host_is_reached` (1),
  `test_allow_list_matching_is_exact_host_case_insensitive` (4),
  `test_list_is_read_on_every_call` (1),
  `test_redirect_to_an_unlisted_host_is_reported_not_followed` (1),
  `test_schemes_and_credentials_are_refused_even_when_listed` (3).
- Replaced under new names (2): `test_redirect_loop_stops_after_five` →
  `…_and_reports_the_last_url_requested`; `test_redirect_to_a_listed_host_is_followed_and_reported`
  → `test_redirect_to_another_host_is_followed_and_reported`.
- Added (28): `test_any_http_or_https_host_is_checked` (8),
  `test_no_allow_list_is_consulted` (1),
  `test_schemes_and_credentials_are_refused_before_any_connection` (5),
  `test_redirect_to_another_host_is_followed_and_reported` (1),
  `test_redirects_are_followed_to_any_http_host` (3),
  `test_unfollowable_redirect_names_its_own_cause` (4),
  `test_redirect_with_an_unparseable_location_is_reported_not_followed` (1),
  `test_five_redirects_are_followed` (1),
  `test_redirect_loop_stops_after_five_and_reports_the_last_url_requested` (1),
  `test_budget_spent_between_hops_reports_the_last_url_requested` (1),
  `test_tool_shows_a_redirect_that_was_not_followed` (1),
  `test_the_network_guard_is_on_for_this_module` (1).

40 − 19 − 2 + 28 = 47. Kept and moved to the new placeholders: the header
filter, the body-never-read test, one GET with the 5 s budget, the nine
failure causes, the audit test (now: scheme, credentials, success, timeout,
redirect not followed), the orchestrator formatting tests and the M4 loop
rejection for research, coding, home and assistant.

## Voice replay

`python3 scripts/voice_replay.py --label m5-chg1`, pipeline `kronk-dev`,
dry run on, after the dev rebuild. Exit 0. Every row that is not a pass,
and the closing lines, verbatim:

```
voice_replay: pipeline kronk-dev → http://localhost/dev/voice, dry-run on, 122 rows (0 not selected)
  x xfail executed   plan-like-massive-attack-radio
  x xfail executed   inc-1004-well-play-orbital
  x xfail executed   plan-mishearing-pasta
  x xfail executed   plan-mishearing-whats-up
  x xfail checked    inc-0915-who-is-this
  x xfail executed   inc-1017-pay-the-album-hotel-costes-3
  s skip  skipped    inc-1017-try-playing-it-ytm
  s skip  skipped    inc-1019-ytm-link
  x xfail executed   inc-1021-ninetys-no-verb
  x xfail executed   eval-played-the-album-toys-in-the-attic
  x xfail executed   eval-oh-play-portishead-radio
  x xfail executed   eval-i-have-to-play-the-radio-station
  x xfail executed   eval-stop-stop
  x xfail executed   eval-skip-the-current-song
  x xfail executed   eval-volume-50
  x xfail executed   eval-i-shuffled-my-youtube-playlist-old-man
  x xfail executed   eval-late-evening-from-radio-on-pandora
  x xfail executed   eval-j-envoie-my-youtube-playlist-video-game
  x xfail executed   eval-shuffle-it
  x xfail executed   eval-clay-thief-recreperation-radio-on-pandora
  x xfail executed   eval-i-hope-you-play-anime-pop-radio
  x xfail executed   eval-we-play-justin-johnson-radio-on-pandora
  s skip  skipped    m4b-fetch-and-run-on-the-mirror
voice_replay: {'pass': 99, 'xfail': 20, 'skip': 3}; executed 53, checked read-only 66; players unchanged (17 checked)
  skipped inc-1017-try-playing-it-ytm: follow-up that needs conversation history; a replay has none
  skipped inc-1019-ytm-link: typed link; a spoken request carries no URL
  skipped m4b-fetch-and-run-on-the-mirror: typed link; a spoken request carries no URL
voice_replay: wrote /home/drew/git-repos/drawsmcgraw/kronk/docs/bench/voice_replay_2026-11-16_232804_m5-chg1.json
```

Against `voice_replay_2026-11-16_012824_m10-baseline.json` (114 rows): 113
identical. One differs: `inc-0930-search-deep-house-ytm` (a named "YouTube
Music" flapper), baseline fail, now pass. The baseline rows give 92 pass /
20 xfail / 2 skip; the baseline had 91 / 20 / 1 fail / 2. The 8 `m4b-*`
rows added since the baseline are 7 pass, 1 skip. `eval-shuffle-it` is
xfail in both. `media_player.sonos_move_derp` did not wake, so no rerun was
needed.

## Not done, and why

- **No agent runs through `/dev/api/chat`.** With no list, a model-written
  URL goes wherever the model points it. A devops run could make up a LAN
  address, and the guard rails allow none outside this host's loopback and
  Docker network. The orchestrator change is two strings (the devops prompt
  sentence and the tool description) and is covered by the unit tests and
  by the replay through the voice shim. The coordinator's menu is
  unchanged.
- **`/fetch` and an unparseable `Location`.** `netguard.redirect_target`
  calls `httpx.URL.join`, which raises `InvalidURL` on a value like
  `http://[::1`. `/fetch` does not catch it, so such a redirect gives a 500
  instead of `{"ok": false}`. It is a section A path and the brief says
  section A is not changed. Fixed for `http_check` only (in its handler,
  not in `redirect_target`). For the coordinator: a one-line catch in
  `/fetch`, if wanted.
- **Credentials in a `Location` header are echoed.** When a redirect target
  carries `user:pw@`, the response's `location` header, the redirect chain
  and the note show it as received (criterion 10 returns `location`
  verbatim), and the audit note holds it too. Nothing is sent with it. Not
  changed.
- **`traceparent`** still goes to every checked host (M5 departure 2,
  accepted).
- **The coordinator chain** (M5 departure 1, e2e observations 1 and 2) is
  now unbounded by any list, as the decision accepts. Recorded in the
  feature doc's "No allow-list" section and in the plan's decision line.
- `ROADMAP.md` has no M5 line; nothing to move. The feature doc header
  still says "not yet promoted".

## Guard rails

- Requests: `127.0.0.1:8103` (tool_service-dev) only. Through it, to
  `172.18.0.1:18765`/`18766` (this host's Docker bridge) and
  `https://example.com/`. `127.0.0.1`, `tool_service` and `192.168.1.1` were
  refused by `/fetch` before any request. The voice replay through HA's
  `kronk-dev` pipeline. No other LAN address, no scan, no discovery, no
  `.local` lookup. During the mutation check no lookup or connect happened
  (audit hook: 0 events, apart from the 18 refused in G3).
- Production untouched: `data/ops_audit.log` md5
  `a84455d0a6427ecc0a7e57a5ace8f635` (as in the M5 reports); production
  `tool_service` logged 0 `/fetch`, `/http_check`, `/ops/exec` or
  `/magicmirror/update` requests since 23:14Z. No call to the Pi or any
  managed host.
- Only `tool_service-dev` and `orchestrator-dev` were rebuilt, by name. HA,
  MA and every production container kept their IDs and creation times. No
  `down -v`, no `--remove-orphans`, no `pipeline_bench.sh`, no sound (dry
  run, 17 players unchanged). `.env` and `.env.dev` not edited;
  `ops/hosts.json` not created. `kronk-hottub-monitor.service` active.
- No `rm` was run outside git (`git rm` for the example file). The scratch
  directory holds no symlinks. The scratch server is stopped and no
  background job is left.
