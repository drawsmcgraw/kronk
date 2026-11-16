# Feature: public-only page fetches, and `http_check` for the LAN

**Built:** 2026-11-16 on branch `refactor` (sprint item M5; not yet promoted to production) · **Plan:** `../../Kronk evaluation findings and implementation plan.md`, section "M5. Block private addresses in fetch_url" · **Criteria:** `../sprint/M5-criteria.md` · **Build report:** `../sprint/M5-build.md`

## What it does

`fetch_url` (the research and coding agents' page reader, tool_service
`GET /fetch`) reaches public addresses only. A user sentence or a fetched page
can no longer point it at the router, the solar bridge, Home Assistant, or a
container on the `kronk` network such as `tool_service:8003/ops/exec`.

`http_check` (devops agent only, tool_service `GET /http_check`) answers
"is this reachable from Kronk?" for hosts the operator has listed. It returns
the status, time taken, redirect chain, final URL and four headers, or the
cause of failure. It never returns the page body.

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
  `too_many_redirects`, or `network` with the exception text.
- Exempt from the address block; limited by the allow-list instead.
- Every call, allowed or refused, appends one line to the ops audit log
  (`ops.audit_exec`, the same function and fields `/ops/exec` uses) with
  `command` set to `http_check GET <url>` and `exit_code` set to the HTTP
  status.

### The allow-list

`ops/http_check_hosts.json` (gitignored; template
`ops/http_check_hosts.example.json`), mounted read-only at `/ops` in both
tool_service containers. Path override: `OPS_HTTP_CHECK_HOSTS`.

```json
{"hosts": ["nas.local", "192.168.1.10"]}
```

- Matched exactly and case-insensitively against the URL's host (brackets
  and a trailing dot ignored). Any port. A name and its IP are separate
  entries.
- File absent, empty, not JSON, or naming no hosts: every target is refused,
  and the refusal names the file.
- Read on every call; edits take effect without a restart.
- Redirects are followed only to listed hosts. A redirect elsewhere is
  reported in the chain with a note and not followed.

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
  The allow-list bounds what that path can reach.
- **GET is not always harmless.** Some devices change state on a plain GET.
  List only hosts where it is.
- **Music Assistant.** `play_music` can hand a model-written string to MA as
  a `media_id`; MA, not tool_service, then fetches it. Out of scope here.
- **Tracing header.** The OpenTelemetry httpx instrumentation adds a
  `traceparent` header to every outbound request, `http_check` included.

## Gotchas

- `ops/` is one bind mount shared by production and dev tool_service. A
  scratch allow-list made for a dev test is visible to production too.
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
  real question (is it up?) without reopening the page-reading hole, and an
  allow-list keeps a GET from flipping a relay.
