# Feature: public-only page fetches, and `http_check` for the LAN

**Built:** 2026-11-16 on branch `refactor` (sprint item M5; promoted to production 2026-11-17 UTC as tag `sprint-M5`) · **Plan:** `../../Kronk evaluation findings and implementation plan.md`, section "M5. Block private addresses in fetch_url" · **Criteria:** `../sprint/M5-criteria.md` · **Build report:** `../sprint/M5-build.md` · **Change 1 (allow-list dropped):** `../sprint/M5-change1.md`

## What it does

`fetch_url` (the research and coding agents' page reader, tool_service
`GET /fetch`) reaches public addresses only. A user sentence or a fetched page
can no longer point it at the router, the solar bridge, Home Assistant, or a
container on the `kronk` network such as `tool_service:8003/ops/exec`.

`http_check` (devops agent only, tool_service `GET /http_check`) answers
"is this reachable from Kronk?" for any http/https address the host can
reach, private or public. It returns the status, time taken, redirect chain,
final URL and four headers, or the cause of failure. It never returns the
page body.

## How the block works (`tool_service/netguard.py`)

1. The URL is parsed with httpx's own parser (`httpx.URL`), so the host that
   is checked is the host the client connects to. Only `http` and `https`;
   a URL with `user:password@` is refused.
2. A literal address is judged by the address it means. `ipaddress` handles
   dotted quads and IPv6 (bracketed, with or without a zone); `inet_aton`
   handles the legacy spellings the C resolver accepts (`2130706433`,
   `0x7f.1`, `017700000001`, `127.1`). IPv4 inside IPv6 (mapped `::ffff:`,
   6to4 `2002::/16`, NAT64 `64:ff9b::/96`, IPv4-compatible `::/96`) is judged
   by the IPv4 address.
3. A single-label hostname (`localhost`, `tool_service`, `litellm`,
   `finance_service`) is refused without a lookup.
4. Any other name is resolved (`getaddrinfo`, 5 s limit). If any resolved
   address is refused, the URL is refused.
5. Refused: unspecified, loopback, link-local (including `169.254.169.254`),
   multicast, RFC 1918, CGNAT (`100.64.0.0/10`), unique local (`fc00::/7`),
   and anything else Python does not call globally routable.
6. `/fetch` turns `follow_redirects` off and follows by hand, at most five
   hops, running the same check before every hop. A refusal names the hop:
   `blocked (redirect 1, to http://192.168.1.1/admin): 192.168.1.1 is a
   private (RFC 1918) address`.

A refusal uses the existing `{"url", "ok": false, "error"}` shape, so the
orchestrator's `fetch_url` handler renders it as an ordinary "Could not fetch"
line and the agent reports it like any other failed page.

## How `http_check` works

- One plain GET per hop: no caller-supplied headers, methods, bodies or
  credentials. TLS verification is on (a self-signed device reports as a TLS
  error). The response is opened as a stream and closed without reading the
  body.
- Five seconds for the whole check, redirects included.
- Returned headers: `server`, `content-type`, `content-length`, `location`,
  when present. Nothing else.
- Failure causes: `dns`, `connection_refused`, `timeout`, `tls`,
  `too_many_redirects`, or `network` with the exception text. A URL with
  another scheme or with credentials is refused before any connection
  (`refused`).
- Exempt from the address block. No host allow-list: it can GET any address
  the host can reach.
- Redirects are followed to any http/https URL, at most five, inside the
  same budget. A redirect that is not followed (another scheme, credentials
  in the target, an unparseable `Location`) is reported in the chain with a
  note naming that cause. `final_url` is always the last URL actually
  requested.
- Every call, allowed or refused, appends one line to the ops audit log
  (`ops.audit_exec`, the same function and fields `/ops/exec` uses) with
  `command` set to `http_check GET <url>` and `exit_code` set to the HTTP
  status.

### No allow-list (operator decision, 2026-11-16)

The M5 build shipped a host allow-list (`ops/http_check_hosts.json`) as the
coordinator's restrictive default while the question was open. Drew decided
to drop it ("drop the list"), and M5 change 1 removed it entirely: the
loader, the `OPS_HTTP_CHECK_HOSTS` variable, the example file and the
`.gitignore` line. There is no dormant switch.

**Warning, from the plan:** some LAN devices change state on a plain GET (a
relay toggled by URL). Without an allow-list, `http_check` can touch any
address on the LAN. Accepted with the decision: a user sentence, or a
fetched page via a research escalation, can lead the coordinator to hand
devops a URL, and `http_check` will send it one GET. What bounds it: GET
only, no caller headers or body, no body returned, five seconds, devops
only, every call in the ops audit log.

## Accepted limits

- **DNS rebinding.** The check resolves the name, then httpx resolves it
  again to connect. A name that answers public first and private second gets
  through. Accepted for a home LAN (plan, M5 "Limit"). Closing it means
  connecting to the checked address while keeping the hostname for TLS SNI
  and the Host header; not done.
- **The coordinator can chain.** Research cannot call `http_check` (it is not
  offered, and the M4 loop check rejects it). But research can escalate, and
  the coordinator can then call `ask_devops` with a URL taken from the
  escalation note, which a fetched page may have shaped. Seen live on dev:
  research `fetch_url` (blocked) → escalate → `ask_devops` → `http_check`.
  Nothing limits which address that path reaches; see "No allow-list".
- **GET is not always harmless.** Some devices change state on a plain GET,
  and `http_check` will send one to any address it is given.
- **Music Assistant.** `play_music` can hand a model-written string to MA as
  a `media_id`; MA, not tool_service, then fetches it. Out of scope here.
- **Tracing header.** The OpenTelemetry httpx instrumentation adds a
  `traceparent` header to every outbound request, `http_check` included.

## Gotchas

- TEST-NET and benchmark ranges (`192.0.2.0/24`, `198.18.0.0/15`, …) are
  refused by `/fetch`, as Python does not call them global.
- Leading-zero dotted quads (`0177.0.0.1`) fail httpx's URL parser and are
  refused as invalid URLs.

## Blog hooks

- SSRF in a home assistant: the LLM is the confused deputy. A page says
  "now read http://tool_service:8003/ops/exec", and before M5 the fetcher
  would have.
- "Judge the address, not the string": `2130706433` is `127.0.0.1`, and so
  is `0x7f.1`. Parse with the client's parser, resolve with the resolver's
  rules.
- Turning off `follow_redirects` is the one-line change that matters; the
  check on hop zero is worth little without it.
- Reachability without readability: `http_check` answers the operator's
  real question (is it up?) without reopening the page-reading hole.
- Built the allow-list, then dropped it: the restrictive default was the
  right call while the question was open, and removing it completely (no
  dormant flag) was the right call once the operator answered.
- Tests that cannot leak: the e2e mutation run sent ARP and mDNS for the
  tests' placeholder hosts. The fix is structural (a fixture that fails any
  real lookup or connect), plus placeholders from documentation ranges.
