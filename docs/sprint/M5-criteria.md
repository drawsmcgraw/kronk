# M5 pass criteria: block private addresses in fetch_url; add http_check

Written by the sprint coordinator on 2026-11-16, before the build, from
`Kronk evaluation findings and implementation plan.md` (section "M5. Block
private addresses in fetch_url", the M5 line of "Verified on Kronk,
2026-11-15") and the outcome of M4 (devops has `remote_exec` only). The
tester checks against this file, not against the build report.

## Coordinator decision recorded here

The plan leaves one question open for Drew: whether `http_check` gets a host
allow-list. Until he answers, the build takes the restrictive, reversible
default: `http_check` reaches only hosts named in an allow-list file beside
the ops registry. With the file absent or empty the tool refuses every
target and names the file. Drew can widen it later by editing the file or
by asking for the list to be dropped.

## Criteria

### A. The address block on `/fetch`

1. Before any request, `/fetch` resolves the hostname and refuses when any
   resolved address is loopback, RFC 1918, link-local (including
   `169.254.169.254`), CGNAT (`100.64.0.0/10`), unspecified, multicast or
   otherwise not globally routable, in IPv4 and IPv6 (including
   IPv4-mapped IPv6, `::1`, `fc00::/7`, `fe80::/10`). Literal addresses in
   any spelling the HTTP client would accept (decimal, octal, hex, short
   forms, bracketed IPv6) are judged by the address they mean.
2. Single-label hostnames (Docker service names such as `tool_service`,
   `litellm`, `nginx`, `localhost`) are refused without a lookup.
3. Only `http` and `https` are fetched. Credentials in the URL and other
   schemes are refused.
4. `follow_redirects` is off. Redirects are followed by hand, at most five
   hops, with the same check applied to every hop. A public URL that
   redirects to a private address is refused, and the refusal says which
   hop.
5. A refusal returns the existing `{"ok": false, "error": ...}` shape with
   a specific cause, so the research agent treats it as an ordinary tool
   failure. The research agent, given such a URL on dev, reports that it
   could not fetch it and does not retry in a loop.
6. Public pages still fetch as before: same result shape, same size and
   time limits, TLS verification on.
7. Unit tests cover at least: `127.0.0.1`, `10.0.0.1`, `192.168.1.1`,
   `169.254.169.254`, `http://finance_service:8005/`, `localhost`, an IPv6
   loopback, an IPv4-mapped IPv6 private address, a numeric-spelling
   variant, a hostname that resolves to a private address (resolver
   stubbed), a public URL that redirects to a private one, a redirect chain
   longer than five, and a public page that succeeds. No test needs the
   network.
8. The DNS rebinding window (check, then fetch) is stated as an accepted
   limit in the code comment and the feature doc. If the build can close it
   cheaply by connecting to the address it checked, it may; it is not
   required.
9. Every other caller of an outbound fetch in `tool_service` and the
   orchestrator that takes a URL from a model or a page is listed in the
   build report, with a statement of whether the block applies to it.
   SearXNG queries, HA, Music Assistant and LiteLLM calls are fixed
   internal addresses and are not in scope.

### B. `http_check`

10. `tool_service` has `GET /http_check?url=...`: one GET, five-second
    timeout, no custom headers, methods, bodies or credentials. It returns
    status code, elapsed time, the redirect chain, the final URL and only
    these headers when present: `server`, `content-type`,
    `content-length`, `location`. It never returns or logs the body.
11. On failure it names the cause: DNS failure, connection refused,
    timeout, TLS error.
12. It is exempt from the block in section A, and limited by the
    allow-list described above: a host not on the list is refused before
    any connection, with a message that names the file. Redirects are
    reported, not followed to hosts off the list. The repo carries an
    `.example` for the file; the real file is gitignored like
    `ops/hosts.json`.
13. Only the devops agent has the tool (`tool_names` is `remote_exec` and
    `http_check`). The M4 loop check still refuses any tool not offered.
    No agent with web tools has it.
14. Every call, allowed or refused, writes one line to the ops audit log
    in the same form `ops_exec` uses.
15. Unit tests cover each failure cause, the allow-list (absent file,
    empty file, listed host, unlisted host, redirect to an unlisted host),
    the header filter, and that no body is returned.

### C. Docs, regression, safety

16. `README.md` no longer says `fetch_url` uses `verify=False`, and lists
    `http_check` under the devops agent. A `docs/features/` doc (new or
    existing) describes the block, the limit and `http_check`.
17. `./scripts/run_tests.sh` passes: at least `2098 passed, 2 skipped,
    18 xfailed` plus the new tests.
18. The voice replay on the dev path matches the M10 baseline row by row,
    apart from the known flapping rows.
19. Production is untouched by the build. No request is sent to any LAN
    address other than this host's own loopback and Docker network; no
    scan or discovery; no command to the MagicMirror Pi.
20. M4 and M4b still hold on dev: devops has no web tools; a fetched page
    cannot reach `http_check` or `update_magicmirror`.

## Reports

Build: `docs/sprint/M5-build.md`. End to end: `docs/sprint/M5-e2e.md`, one
line per criterion with pass, fail or not testable, and the evidence.
