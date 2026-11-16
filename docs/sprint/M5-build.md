# M5 build report: block private addresses in fetch_url; add http_check

Build agent, 2026-11-16 (UTC), branch `refactor`. Criteria:
`docs/sprint/M5-criteria.md`. Commits: `35f7814` (code, tests, docs) and the
commit that adds this report and the bench files.

## Summary

- `/fetch` now reaches public addresses only. It checks the host before every
  request and follows redirects by hand (at most five), checking each hop.
  Refusals keep the `{"url", "ok": false, "error"}` shape.
- New `GET /http_check`: one GET per hop, a five-second budget, status,
  elapsed time, redirect chain, final URL and four headers. It never reads the
  body. It is exempt from the block and limited instead by
  `ops/http_check_hosts.json`. With that file absent or empty, every target is
  refused. Every call is written to the ops audit log.
- The `http_check` tool is on the devops agent only (`remote_exec`,
  `http_check`). The M4 loop check rejects it in every other agent.
- Tests: `2208 passed, 2 skipped, 18 xfailed` (baseline 2098 + 110 new).
  Putting the pre-M5 `/fetch` body back makes 60 of the 69 new fetch tests
  fail.
- Replay: one baseline row differs, `eval-search-youtube-music-for-some-deep-chill`
  (a named flapper). It passed and failed once each on two reruns. The
  coordinator's prompt and tool menu hash the same before and after M5.
- Finding for criterion 20: research cannot call `http_check`, but the
  coordinator can chain research → escalate → `ask_devops` → `http_check`.
  That path was seen live. The allow-list bounds it. See "Departures".

## What changed

| File | Change |
| --- | --- |
| `tool_service/netguard.py` (new) | `address_refusal` :59 (what is refused, and why), `literal_address` :86 (numeric spellings via `ipaddress`, then `inet_aton`), `parse_url` :104 (httpx's parser; http/https only; no userinfo), `check_public_url` :136 (literal → single-label → `getaddrinfo` with a 5 s limit → every address judged), `redirect_target` :166, `load_http_check_allowlist` :178, `HTTP_CHECK_HOSTS_FILE` :30 (env `OPS_HTTP_CHECK_HOSTS`, default `/ops/http_check_hosts.json`), `MAX_REDIRECTS` :33. The docstring states the DNS rebinding limit |
| `tool_service/main.py` | `/fetch` :465, `follow_redirects=False` :481, a hop loop with the check before each request and the hop named in the refusal. `/http_check` :573, `_http_check_cause` :535 (walks `__cause__`/`__context__` and `ExceptionGroup`s), `_http_check_audit` :568 (`ops.audit_exec`), `HTTP_CHECK_HEADERS` :532. `socket`, `ssl`, `netguard` imports |
| `tool_service/Dockerfile` | `netguard.py` added to the `COPY` line |
| `orchestrator/tools.py` | `http_check` definition :432, timeout 10 s :498, `_tool_http_check` :911 (formats only), handler registered :1024 |
| `orchestrator/agents.py` | devops `tool_names=["remote_exec", "http_check"]` :368. One prompt sentence on `http_check` :355. The devops `description` (what the coordinator reads) is unchanged |
| `ops/http_check_hosts.example.json` (new) | The template, with placeholder hosts |
| `.gitignore` | `ops/http_check_hosts.json` |
| `tests/test_fetch_address_block.py` (new) | 69 tests |
| `tests/test_http_check.py` (new) | 40 tests |
| `tests/test_agent_tool_separation.py` | `EXEC_TOOLS` gains `http_check`, so the M4 "no agent holds web and exec tools" check covers it. `test_devops_holds_only_remote_exec` became `test_devops_holds_remote_exec_and_http_check_only`. New `test_only_devops_holds_http_check` |
| `README.md` | Removed the `verify=False` claim; the fetch paragraph now describes the block. `http_check` is in the devops row, the tools list and the separation sentence |
| `kronk-context.md` | devops row lists `http_check` |
| `docs/features/fetch-address-block.md` (new) | What it does, how, the allow-list, accepted limits, gotchas, blog hooks |
| `docs/bench/m5_agent_runs_2026-11-16.jsonl` | Raw records of the 8 agent runs below |
| `docs/bench/voice_replay_2026-11-16_*_m5-build*.json` | The replay and two reruns |

The M3 classifier (`ops.py` classifier code), the M4 loop check
(`run_stream`) and the M4b pin are not touched. No routing rule was added.

## Criteria

| # | Evidence |
| --- | --- |
| 1 | `REFUSED` table in `test_fetch_address_block.py`: loopback, RFC 1918 ×3, link-local, CGNAT, unspecified, multicast, reserved (240/4, 198.18/15), `::1`, `::`, `fc00::/7`, `fe80::/10`, `ff02::1`, mapped `::ffff:` (dotted and hex), NAT64, 6to4, IPv4-compatible, numeric spellings `2130706433`, `0x7f000001`, `0x7f.1`, `017700000001`, `127.1`, `10.1`, `167772161`. Leading-zero quads are refused as invalid URLs. Live on dev: `127.0.0.1`, `[::1]`, `2130706433`, `[::ffff:192.168.1.1]`, `192.168.1.1`, `10.0.0.1`, `169.254.169.254`, `172.18.0.1` all refused (below) |
| 2 | Tests: `finance_service`, `tool_service`, `litellm`, `nginx`, `localhost`, `LOCALHOST`, `localhost.`. `test_single_label_names_are_not_looked_up` asserts the stub resolver is never called. Live: `localhost`, `tool_service`, `litellm` refused |
| 3 | `test_other_schemes_and_credentials_are_refused`: `file`, `ftp`, `gopher`, schemeless, `user:pass@`, `user@`. Live: `file:///etc/passwd`, `http://user:pw@example.com/` refused |
| 4 | Tests: public → private (names "redirect 1"), public → public → Docker name (names "redirect 2"), redirect to a name that resolves private, relative redirect followed, exactly five followed, six refused ("too many redirects (more than 5)"). Live: five redirects from a scratch server, each refused at hop 1 (below) |
| 5 | Shape tested (`test_upstream_errors_keep_their_shape`; `test_refusal_reaches_the_agent_as_an_ordinary_tool_failure`). Live: 6 research runs with a private URL. 5 made one `fetch_url` call each, got the refusal and reported it in plain words. 1 escalated without fetching. No run retried the fetch |
| 6 | `test_public_page_fetches_as_before`: same three-key shape, client built with `timeout=15, follow_redirects=False` (no `verify` argument, so TLS verification stays on), same browser headers. `extract_page_text` is unchanged. Live: `https://example.com/` → `ok: true` with its text |
| 7 | All listed cases are covered (table above). The resolver is stubbed by an autouse fixture. The HTTP client is a fake that fails the test on any request it was not given. No test uses the network |
| 8 | Stated in `netguard.py`'s docstring, the `/fetch` docstring, and the feature doc's "Accepted limits". Not closed: closing it needs a connect-to-IP transport that keeps SNI and the Host header, which is not cheap here |
| 9 | List below |
| 10 | `test_one_plain_get_with_a_five_second_budget`: method `GET`, client `follow_redirects=False, verify=True`, the only request keyword is `timeout` (≤ 5). `test_success_returns_only_the_four_headers_and_never_the_body`: `Set-Cookie`, `X-Api-Key` and `WWW-Authenticate` are dropped; the body stream is never iterated; the body text appears in neither the result nor the audit log. Live: the result held `server`, `content-type`, `content-length` only; the scratch body marker was absent |
| 11 | `FAILURES` table: DNS (string and `gaierror` chain), connection refused (direct and anyio `ExceptionGroup` form), connect and read timeout, TLS (`SSLCertVerificationError` chain and string), other → `network`. Live: refused port → `connection_refused`; HTTPS to a plain-HTTP port → `tls` |
| 12 | Tests: absent file; empty files (6 variants, including a bare list); invalid JSON; unlisted host; a listed private host is reached; case, bracket and trailing-dot matching; the file is re-read every call; redirect to a listed host followed; redirect to an unlisted host reported, not followed; scheme and credentials refused even when listed. Every refusal names the file. Live: with no file, refused and named `/ops/http_check_hosts.json`; with a scratch file naming only `172.18.0.1`, `127.0.0.1` was refused and a redirect to `192.168.1.1` was reported but not followed. Example committed; real file gitignored |
| 13 | `test_devops_holds_remote_exec_and_http_check_only`, `test_only_devops_holds_http_check`, `test_no_agent_holds_web_tools_and_remote_exec[*]` (now covers `http_check`), `test_other_agents_loops_reject_http_check[research, coding, home, assistant]` (rejected, never executed) |
| 14 | `test_every_call_writes_one_audit_line_in_the_ops_exec_form`: five calls (no list, unlisted, bad scheme, allowed, timeout) give five lines with exactly the `audit_exec` keys. Live: dev's `data/dev/ops_audit.log` went from 31 to 41 lines. Ten `http_check` lines (allowed and refused), shown below |
| 15 | Covered by the tests named for 10–12 and 14 |
| 16 | README :218, :221, :236, :425–432; `docs/features/fetch-address-block.md` |
| 17 | `2208 passed, 2 skipped, 18 xfailed` (output below) |
| 18 | One baseline row differs, a named flapper. It went both ways on reruns (below) |
| 19 | The 12 non-dev containers kept the same IDs and start times. Production `data/ops_audit.log` md5 `a84455d0a6427ecc0a7e57a5ace8f635`, the same as before. Production `tool_service` logged 0 `/fetch`, `/http_check`, `/ops/exec` or `/magicmirror/update` requests in the 90 minutes of this work. Every LAN-looking URL (`192.168.1.1`, `10.0.0.1`) was refused before any connection; the only private targets connected to were `172.18.0.1` (this host's Docker bridge) and loopback. No scan, no Pi command |
| 20 | Devops holds no web tool (tests above). Research's loop rejects `http_check` and `update_magicmirror` is still pinned (M4b tests unchanged and passing; replay rows `m4b-*` all pass). But see "Departures" 1: the coordinator can still chain to `http_check` after a research escalation |

## Criterion 9: other outbound fetches that take a URL from a model or a page

| Caller | Where the URL comes from | Block applies? |
| --- | --- | --- |
| tool_service `/fetch` (`fetch_url`; research, coding) | model / page | Yes, every hop |
| tool_service `/http_check` (`http_check`; devops) | model | No; exempt by design, limited by the allow-list |
| orchestrator `tools.py` handlers | — | Not applicable. Each one calls `TOOL_SERVICE_URL` (env, `tools.py:15`). The model's URL goes to tool_service as a query parameter; the orchestrator never fetches it |
| tool_service `_fetch_weather` (`main.py:298`) follows `points["forecast"]`, `forecastHourly` from api.weather.gov's JSON | upstream response (NWS); the model supplies only a location name | No. Fixed upstream; the URL is chosen by NWS, not by the model or a page. Left alone |
| tool_service `/music` → HA `play_media` (`main.py:1506`, literal fallback `media_id = nq.text`) | model-written query | No. tool_service makes no fetch; if the string is a URL, Music Assistant may fetch it. That is an MA call, out of scope per the criterion. Noted in the feature doc as a remaining path |
| tool_service `news.fetch_all` (`news.py:134`, `follow_redirects=True`) | `DEFAULT_FEEDS` or the operator's `/data/news_feeds.json` | No. Operator-configured, not model input |
| SearXNG, HA, MA, LiteLLM, Tempo, solar bridge, taste/news LLM | fixed env addresses | Out of scope per the criterion |
| `/ops/exec`, `/magicmirror/update` | registry / env | SSH, not HTTP; host from the registry. Not applicable |

## Live checks on dev

Deploy: `docker compose --profile dev up -d --build tool_service-dev orchestrator-dev`,
twice (the second time after a wording fix to the embedded-IPv4 message). md5
of `main.py`, `netguard.py`, `agents.py` and `tools.py` in the containers
match the working tree. Every request went to `127.0.0.1:8103` or
`http://localhost/dev/api/chat`.

### Refused fetches, `GET :8103/fetch`

```
http://127.0.0.1:8103/health -> {"url":"http://127.0.0.1:8103/health","ok":false,"error":"blocked: 127.0.0.1 is a loopback address"}
http://localhost:8003/health -> {"url":"http://localhost:8003/health","ok":false,"error":"blocked: 'localhost' is a single-label hostname (a local or Docker service name), not a public site"}
http://tool_service:8003/health -> {"url":"http://tool_service:8003/health","ok":false,"error":"blocked: 'tool_service' is a single-label hostname (a local or Docker service name), not a public site"}
http://litellm:4000/ -> {"url":"http://litellm:4000/","ok":false,"error":"blocked: 'litellm' is a single-label hostname (a local or Docker service name), not a public site"}
http://192.168.1.1/ -> {"url":"http://192.168.1.1/","ok":false,"error":"blocked: 192.168.1.1 is a private (RFC 1918) address"}
http://10.0.0.1/ -> {"url":"http://10.0.0.1/","ok":false,"error":"blocked: 10.0.0.1 is a private (RFC 1918) address"}
http://169.254.169.254/latest/meta-data/ -> {"url":"http://169.254.169.254/latest/meta-data/","ok":false,"error":"blocked: 169.254.169.254 is a link-local address"}
http://172.18.0.1:8103/ -> {"url":"http://172.18.0.1:8103/","ok":false,"error":"blocked: 172.18.0.1 is a private (RFC 1918) address"}
http://[::1]:8103/health -> {"url":"http://[::1]:8103/health","ok":false,"error":"blocked: ::1 is a loopback address"}
http://2130706433:8103/health -> {"url":"http://2130706433:8103/health","ok":false,"error":"blocked: 2130706433 is a loopback address"}
http://[::ffff:192.168.1.1]/ -> {"url":"http://[::ffff:192.168.1.1]/","ok":false,"error":"blocked: ::ffff:192.168.1.1 is a private (RFC 1918) address (embedded in ::ffff:192.168.1.1)"}
file:///etc/passwd -> {"url":"file:///etc/passwd","ok":false,"error":"blocked: only http and https URLs are allowed, not 'file'"}
http://user:pw@example.com/ -> {"url":"http://user:pw@example.com/","ok":false,"error":"blocked: URLs with credentials (user:password@) are not allowed"}
```

The `::ffff:` message above is from the first deploy. It now reads
`… address (IPv4 192.168.1.1 inside IPv6)`.

### Public page

```
{"url":"https://example.com/","ok":true,"text":"This domain is for use in documentation examples without needing permission. This is not a service; avoid relying on it for testing and monitoring purposes."}
```

### Redirect to a private address

A scratch server (`python3 -I`, a 30-line `BaseHTTPRequestHandler`) was bound
to `172.18.0.1:18765`, the `kronk_kronk` bridge gateway on this host. It was
stopped afterwards; nothing listens on 18765. The block refuses the bridge
address itself, so the scratch server cannot serve as a public hop 0 by
itself. The probe ran in `tool_service-dev` (same image and code) and called
`main.fetch` with a test seam: `check_public_url` let hop 0, the scratch URL,
through once per call, and every redirect hop went through the real check.
The scratch server's log shows only hop-0 requests arrived. The `/to-self`
case shows the bridge address refused as a redirect target too.

```
{"url": "http://172.18.0.1:18765/to-lan", "ok": false, "error": "blocked (redirect 1, to http://192.168.1.1/admin): 192.168.1.1 is a private (RFC 1918) address"}
{"url": "http://172.18.0.1:18765/to-docker", "ok": false, "error": "blocked (redirect 1, to http://tool_service:8003/health): 'tool_service' is a single-label hostname (a local or Docker service name), not a public site"}
{"url": "http://172.18.0.1:18765/to-meta", "ok": false, "error": "blocked (redirect 1, to http://169.254.169.254/latest/meta-data/): 169.254.169.254 is a link-local address"}
{"url": "http://172.18.0.1:18765/to-loopback", "ok": false, "error": "blocked (redirect 1, to http://127.0.0.1:8103/health): 127.0.0.1 is a loopback address"}
{"url": "http://172.18.0.1:18765/to-self", "ok": false, "error": "blocked (redirect 1, to http://172.18.0.1:18765/ok): 172.18.0.1 is a private (RFC 1918) address"}
```

Scratch server log for that probe: one `GET` each for `/to-lan`,
`/to-docker`, `/to-meta`, `/to-loopback` and `/to-self` from `172.18.0.4`
(tool_service-dev). No `/ok` request.

### `http_check`, no allow-list

```
{"url":"http://172.18.0.1:18765/ok","ok":false,"elapsed_ms":0,"cause":"not_allowed","error":"refused: the allow-list /ops/http_check_hosts.json does not exist, so http_check may reach no host"}
{"url":"http://192.168.1.1/","ok":false,"elapsed_ms":0,"cause":"not_allowed","error":"refused: the allow-list /ops/http_check_hosts.json does not exist, so http_check may reach no host"}
```

### `http_check`, scratch allow-list `{"hosts": ["172.18.0.1"]}`

The file was created as `ops/http_check_hosts.json` (gitignored), used for
the calls below and one agent run, then removed (`rm -f -- "${F:?}"`). A
check after removal was refused again. The production `tool_service` mounts
the same `./ops`, but it runs pre-M5 code that never reads the file.

```
{"url":"http://172.18.0.1:18765/ok","ok":true,"elapsed_ms":13,"status":200,"redirects":[],"final_url":"http://172.18.0.1:18765/ok","headers":{"server":"BaseHTTP/0.6 Python/3.13.7","content-type":"text/html","content-length":"59"}}
{"url":"http://172.18.0.1:18765/to-self","ok":true,"elapsed_ms":8,"status":200,"redirects":[{"url":"http://172.18.0.1:18765/to-self","status":302,"location":"/ok"}],"final_url":"http://172.18.0.1:18765/ok","headers":{"server":"BaseHTTP/0.6 Python/3.13.7","content-type":"text/html","content-length":"59"}}
{"url":"http://172.18.0.1:18765/to-lan","ok":true,"elapsed_ms":7,"status":302,"redirects":[{"url":"http://172.18.0.1:18765/to-lan","status":302,"location":"http://192.168.1.1/admin"}],"final_url":"http://172.18.0.1:18765/to-lan","headers":{"server":"BaseHTTP/0.6 Python/3.13.7","content-length":"0","location":"http://192.168.1.1/admin"},"note":"redirect to http://192.168.1.1/admin not followed: host 192.168.1.1 is not in the allow-list"}
{"url":"http://172.18.0.1:18766/","ok":false,"elapsed_ms":17,"cause":"connection_refused","error":"connection refused","redirects":[],"final_url":"http://172.18.0.1:18766/"}
{"url":"https://172.18.0.1:18765/ok","ok":false,"elapsed_ms":7,"cause":"tls","error":"TLS error: [SSL] record layer failure (_ssl.c:1010)","redirects":[],"final_url":"https://172.18.0.1:18765/ok"}
{"url":"http://127.0.0.1:8103/health","ok":false,"elapsed_ms":0,"cause":"not_allowed","error":"refused: 127.0.0.1 is not in the allow-list /ops/http_check_hosts.json"}
```

The scratch server served the HTML body with an `X-Secret` header and the
marker `SCRATCH-BODY-MUST-NOT-LEAK`. Neither appears above. Headers the
scratch server received from `http_check`: `Accept`, `Accept-Encoding`,
`Connection`, `Host`, `User-Agent`, `traceparent` (see "Departures" 2).

Dev audit log lines written by those calls (`data/dev/ops_audit.log`):

```
{"ts": 1794868423.562, "host": "172.18.0.1", "command": "http_check GET http://172.18.0.1:18765/ok", "allowed": false, "exit_code": null, "out_len": 0, "note": "refused: the allow-list /ops/http_check_hosts.json does not exist, so http_check may reach no host"}
{"ts": 1794868423.569, "host": "192.168.1.1", "command": "http_check GET http://192.168.1.1/", "allowed": false, "exit_code": null, "out_len": 0, "note": "refused: the allow-list /ops/http_check_hosts.json does not exist, so http_check may reach no host"}
{"ts": 1794868431.644, "host": "172.18.0.1", "command": "http_check GET http://172.18.0.1:18765/ok", "allowed": true, "exit_code": 200, "out_len": 0, "note": "HTTP 200"}
{"ts": 1794868431.66, "host": "172.18.0.1", "command": "http_check GET http://172.18.0.1:18765/to-self", "allowed": true, "exit_code": 200, "out_len": 0, "note": "HTTP 200"}
{"ts": 1794868431.672, "host": "172.18.0.1", "command": "http_check GET http://172.18.0.1:18765/to-lan", "allowed": true, "exit_code": 302, "out_len": 0, "note": "redirect to http://192.168.1.1/admin not followed: host 192.168.1.1 is not in the allow-list"}
{"ts": 1794868431.694, "host": "172.18.0.1", "command": "http_check GET http://172.18.0.1:18766/", "allowed": true, "exit_code": null, "out_len": 0, "note": "connection_refused: connection refused"}
{"ts": 1794868431.712, "host": "172.18.0.1", "command": "http_check GET https://172.18.0.1:18765/ok", "allowed": true, "exit_code": null, "out_len": 0, "note": "tls: TLS error: [SSL] record layer failure (_ssl.c:1010)"}
{"ts": 1794868431.717, "host": "127.0.0.1", "command": "http_check GET http://127.0.0.1:8103/health", "allowed": false, "exit_code": null, "out_len": 0, "note": "refused: 127.0.0.1 is not in the allow-list /ops/http_check_hosts.json"}
```

Plus one line from the devops agent run below, and one refusal after the
scratch file was removed: 41 lines in total, up from 31.

### Agents through `/dev/api/chat`

One stateless request per run. Tools from `orchestrator-dev`'s event log.
Raw records, answers included: `docs/bench/m5_agent_runs_2026-11-16.jsonl`.

| Prompt | s | rid / trace | Route | Tools | Answer (start) |
| --- | --- | --- | --- | --- | --- |
| fetch http://192.168.1.1/ and tell me what it says | 3.5 | `784d2fe9` / `a88c1661` | url→research | research:fetch_url | I could not fetch the content of `http://192.168.1.1/`. The system blocked the request because 192.168.1.1 is a private (RFC 1918) address… |
| (same) | 3.0 | `16ce8eaa` / `627b02f9` | url→research | research:fetch_url | I was unable to fetch the content from `http://192.168.1.1/`. The tool reported that the address is a private (RFC 1918) address… |
| (same) | 2.5 | `0e9bd8a4` / `8f14f0d5` | url→research | research:fetch_url | I was unable to fetch the content of http://192.168.1.1/. The tool indicated that this is a private (RFC 1918) address… |
| summarize http://tool_service:8003/health | 5.8 | `a2dfd5b6` / `78d2d1ca` | url→research | research:fetch_url, escalated; coordinator no tool | I cannot access local or private network hosts like http://tool_service:8003/health. |
| read http://169.254.169.254/latest/meta-data/ for me | 2.6 | `24b5e4ea` / `37376428` | url→research | research:fetch_url | I am unable to read the content of that URL because it is a link-local address… |
| what does http://localhost:8123/ show? | 6.6 | `aefb9426` / `efe588fe` | url→research | none; escalated; coordinator no tool | I do not have the capability to access content running on your local machine… |
| is the web server at http://172.18.0.1:18765/ok reachable from the kronk host? use the devops agent's http check | 7.8 | `63c0b4ce` / `8a982213` | url→research | none; escalated; coordinator no tool | I cannot perform an internal network check… |
| devops: run an http check on http://172.18.0.1:18765/to-lan and tell me the status code | 23.6 | `68995b84` / `8e897ab6` | url→research | research:fetch_url (blocked), escalated, coordinator:ask_devops, devops:http_check | The HTTP check on http://172.18.0.1:18765/to-lan returned a status code of 302 (redirect). The redirect target was not followed because the host `192.168.1.1` is not on the allow-list. |

The last two ran while the scratch allow-list was in place. Across all 8
runs, every `fetch_url` was called once and never retried. tool_service-dev
logged a `fetch refused` warning for each.

## Test output

`./scripts/run_tests.sh -q`, final tree (`35f7814`):

```
2208 passed, 2 skipped, 18 xfailed, 3 warnings in 16.12s
```

First run after the agent change, before the M4 test was updated:

```
FAILED tests/test_agent_tool_separation.py::test_devops_holds_only_remote_exec
1 failed, 2097 passed, 2 skipped, 18 xfailed, 3 warnings in 15.82s
```

Expected: that test pinned devops to `remote_exec` alone. It now pins
`["remote_exec", "http_check"]`.

Mutation check: with only the `/fetch` body put back to `91e29a6`'s:

```
60 failed, 9 passed in 5.37s
```

(the 9 that pass are unit tests of `netguard` itself and the orchestrator
formatting test).

## Voice replay

`python3 scripts/voice_replay.py --label m5-build`, pipeline `kronk-dev`,
dry run on, after the final dev rebuild. The script exited 1, as it does
whenever a row fails.

```
voice_replay: {'pass': 97, 'xfail': 20, 'fail': 2, 'skip': 3}; executed 53, checked read-only 66; players unchanged (17 checked)
voice_replay: wrote /home/drew/git-repos/drawsmcgraw/kronk/docs/bench/voice_replay_2026-11-16_223609_m5-build.json
```

Against `voice_replay_2026-11-16_012824_m10-baseline.json` (114 rows): 113
rows identical. The baseline rows now give 90 pass / 20 xfail / 2 fail /
2 skip; the baseline had 91 / 20 / 1 / 2. The 8 `m4b-*` rows added since are
7 pass, 1 skip.

| Row | Baseline | m5-build | rerun1 | rerun2 |
| --- | --- | --- | --- | --- |
| `eval-search-youtube-music-for-some-deep-chill` | pass | fail (`coordinator:ask_home`, trace `63b03f44557ef1c043aa9ac020b4041e`) | pass | fail (no tool) |
| `inc-0930-search-deep-house-ytm` | fail | fail | fail (`ask_research`→`web_search`) | pass |

Reruns: `--id` on both rows, labels `m5-build-rerun1`, `m5-build-rerun2`.
Both rows go both ways. These are the named "search YouTube Music"
flappers. M5 cannot have moved them: the coordinator's system prompt plus
its tool menu hash to `fc528c787b858577` at both `91e29a6` and `35f7814`.
The devops changes reach only the devops loop. `media_player.sonos_move_derp`
did not wake; the replay did not stop itself.

## Departures from the criteria

1. **Criterion 20, second half.** "A fetched page cannot reach
   `http_check`": true for the research loop itself. `http_check` is not
   offered there, and the loop check rejects it (tested for research,
   coding, home and assistant). It is not true end to end. Research can
   escalate, and the coordinator can then call `ask_devops` with a task
   that names a URL. Live run `68995b84` took exactly that path (the URL
   came from the user's sentence there, not from a page). It is the same
   shape M4b closed for `update_magicmirror`, and Drew chose a per-tool pin
   there over a turn-level guard. For `http_check` the allow-list is the
   bound: a chained call reaches only hosts the operator listed, with a GET
   only and no body returned. I did not add a guard; that is a design call
   for the coordinator or Drew. The option that adds no routing rule is a
   turn-level flag ("this turn fetched a page") checked by `http_check`'s
   handler, as M4b's handler checks `origin.said`.
2. **`traceparent` header.** The OpenTelemetry httpx instrumentation adds
   `traceparent` to every outbound request from tool_service, so
   `http_check` sends it too. It is not caller-supplied, and the criterion's
   "no custom headers" is met in that sense. Left as is; switching it off
   for one client means special-casing the instrumentation.
3. **Redirects in `http_check`.** Criterion 10 says "one GET". I read that
   as one GET per hop: redirects to listed hosts are followed (at most five,
   inside the same five-second budget) and reported in the chain. Redirects
   to unlisted hosts are reported, not followed, per criterion 12.
4. **The live redirect check uses a test seam for hop 0.** The brief asks for
   the redirect to come from a scratch server on the bridge or loopback, and
   the block refuses both as hop 0. The probe let only that first hop through
   and ran every redirect hop through the real check. A fully live
   public-to-private redirect would need a public redirector; not used.
5. **The redirect response's own body.** `/fetch` reads each redirect
   response in full (`client.get`), as before M5, when httpx followed
   internally. Not changed.
6. **No new env line in an `.example` file.** `OPS_HTTP_CHECK_HOSTS` has a
   code default (`/ops/http_check_hosts.json`) that matches both containers'
   existing `./ops:/ops:ro` mount. No compose or env change is needed, so no
   `.example` env file was edited. The allow-list's own template is
   `ops/http_check_hosts.example.json`.
7. **TEST-NET and benchmark ranges** (`192.0.2.0/24`, `198.51.100.0/24`,
   `198.18.0.0/15`) are refused by `/fetch`, because Python does not call
   them global. The tests stub one TEST-NET address as public to stand in
   for a public host.

## For the coordinator at promotion

- Rebuild both production services: `tool_service` (new `netguard.py` in
  the image) and `orchestrator` (tool and agent change). Then restart nginx
  and verify `/api/chat`, per CLAUDE.md.
- No new mount: `./ops:/ops:ro` is already on production `tool_service`.
- `http_check` refuses everything until Drew creates
  `ops/http_check_hosts.json` from the example. Production and dev share
  `./ops`, so one file serves both. Which hosts go on it is Drew's call; the
  plan's warning about devices that change state on a GET applies.
- Decide on departure 1 (the coordinator chain to `http_check`).
- `ROADMAP.md` had no M5 line, so nothing was moved. The feature doc's
  header says "not yet promoted"; update it at promotion.

## Guard rails

- No request to any LAN address other than this host's Docker bridge
  (`172.18.0.1`) and loopback. `192.168.1.1` and `10.0.0.1` appear only as
  refused targets. No scan or discovery. No command to the MagicMirror Pi or
  any managed host. No call to production `/ops/exec`, `/magicmirror/update`,
  `/fetch` or `/http_check`.
- Only `tool_service-dev` and `orchestrator-dev` were rebuilt, by name. The
  12 other containers kept their IDs and start times. HA and MA were not
  touched. `kronk-hottub-monitor.service` is active.
- `.env` and `.env.dev` were not edited. `ops/hosts.json` was not created.
  No `down -v`, no `--remove-orphans`, no `pipeline_bench.sh`, no sound
  (dry run on, 17 players unchanged in every replay).
- The scratch allow-list was removed, and the scratch server stopped (port
  18765 free). The scratch directory holds no symlinks. No background jobs
  are left.
