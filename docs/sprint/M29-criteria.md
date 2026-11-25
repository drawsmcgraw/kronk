# M29 pass criteria: tests that run the pages' scripts

Source: the coordinator's report of 2026-11-24 on M27 ("No test runs the
page's JavaScript. The rule that only `https://` links become clickable is
held by one browser run and no test. … `/resources` shares it.") and
Drew's answer: "yes, implement the page-script test harness".

Handoff line: `./scripts/run_tests.sh` loads `/work` and `/resources` in a
real headless browser against fixture data and fails when a page's script
shows a wrong figure, makes a wrong link, or lets data become markup.

Plan doc: this file. Design review before build.

## What the investigation found (coordinator, 2026-11-24)

- The suite (`7684 passed, 3 skipped, 54 xfailed`) tests the pages as
  text: that a string is in the file, that a route returns it. Nothing
  executes a page's script. Every finding about what a page shows (M25
  three test rounds, M27 two) came from a test agent driving a browser by
  hand, and is unrepeatable.
- `orchestrator/static/work.html` (524 lines) builds its DOM with
  `createElement`, `createTextNode`, `textContent`; one place sets `href`
  and `rel`. `orchestrator/static/resources.html` (565 lines) sets
  `innerHTML` in 16 places. Both fetch JSON from `api/...` relative to the
  page and refresh every 30 s by `setInterval(loadAll, 30000)`.
- The host has snap Firefox 159.0.1 and `/snap/bin/geckodriver` 0.37.1. No
  node, no npm, no Chromium, no Selenium or Playwright in `tests/.venv`
  (Python 3.13).
- The test agents' method, which worked eight times in M25's third test
  (`docs/sprint/M25-e2e3.md`, "Headless snap Firefox…"): geckodriver over
  WebDriver, plain HTTP on loopback from Python, no Selenium; the profile
  under `~/snap/firefox/common/` (snap confinement: a profile elsewhere is
  not readable); proxy prefs in the profile's `user.js`. Firefox tried 364
  outside connections of its own during those runs; the proxy refused all
  of them. One run ended with `pkill -f` on a profile name and that was
  recorded as a near miss: Drew's own Firefox runs on this host.
- `tests/conftest.py` blocks sockets for every test unless it opts in.
- The "promoting" step on `/work` shows the literal text `tag item-<id>
  exists` (M27 leftover).

## Plumbing (coordinator's decisions)

1. A real browser, not a script engine with a made DOM. `resources.html`
   writes `innerHTML`; whether data becomes markup is the browser's
   parser's answer, and a stand-in DOM would be testing itself.
2. Headless snap Firefox through geckodriver, spoken to by a small
   WebDriver client in `tests/` written on the standard library. No new
   Python dependency, no node.
3. The page under test is the file in the tree, served by a loopback HTTP
   server the fixture starts on a free port, with the page's `api/...`
   routes answered from fixture JSON held by the test. No orchestrator, no
   container, no production or dev route.
4. The browser reaches nothing but that server: profile prefs set a proxy
   on a closed loopback port with `127.0.0.1` exempt, and turn off
   updates, telemetry, safe browsing, captive-portal and remote-settings
   fetches. A test asserts the fixture server saw no request for a path it
   does not serve and that the profile's prefs were applied.
5. One browser for the whole run (session fixture), a fresh profile under
   `~/snap/firefox/common/kronk-pagetest-<pid>-<random>`, removed at the
   end with a literal-prefix check. The harness stops the browser by the
   process ids it started (geckodriver's and its children), never by name
   or pattern. A leftover profile from a killed run older than an hour is
   removed at the next start, by the same prefix.
6. Tests carry the marker `page` and live in `tests/page/`. They run
   inside `./scripts/run_tests.sh`. When Firefox or geckodriver is absent
   they skip with that reason; on this host they are present, and
   `KRONK_PAGE_TESTS=require` (set by `run_tests.sh` when both binaries
   exist) turns a harness start failure into a failure, not a skip.
   `KRONK_PAGE_TESTS=off` skips them (for an agent that must not start a
   browser).
7. Time and stability budget: the page tier adds at most 60 s to the
   suite, and passes 20 runs in a row on this host. A test never sleeps
   for a fixed time to wait for a render: it polls a condition with a
   limit. The 30 s refresh is exercised by calling the page's `loadAll()`
   from the test, not by waiting.
8. What the first tests pin, on fixture data shaped like the real
   `/api/work`, `/api/servers`, `/api/system`, `/api/activity` answers
   (captured once from dev, reduced, with household text removed):
   - `/work`: a link is clickable only for `https://`, with
     `rel="noopener"`; `javascript:`, `http:`, and a URL with a quote and
     an event handler stay text. Markup in every string field of the
     fixture stays text (no element created, no script run). An opened
     entry stays open across a refresh. The stale banner shows for an old
     export and gives the route's reason. Every count on the page equals
     the fixture's.
   - `/resources`: every figure on the page equals the fixture's. Markup
     in every string field (model name, unit name, file path, source
     line, agent name) creates no element and runs no script. A missing
     or failed source shows its "not measured" word, not a zero.
   - Each of these has a mutant shown to fail it (in the build report).
   A hole found by a new test is fixed in the page in this item, with the
   test left as its proof.
9. The M27 leftover is fixed here and pinned: the "promoting" step shows
   the item's real tag.
10. `docs/features/page-tests.md` (how to write one, the fixture format,
    what the harness guarantees, what it does not: one browser, one
    width unless a test sets it). `CLAUDE.md` "Definition of done" gains
    one line: a change to a page's script lands with a page test.
    README's test section mentions the tier.

## Criteria

1. `./scripts/run_tests.sh` runs the page tests and passes; last lines
   quoted, with the time before and after.
2. Twenty consecutive runs of the page tier pass (`-m page`), times
   listed. No Firefox or geckodriver process and no
   `kronk-pagetest-*` profile is left after any of them, including one
   run interrupted with SIGINT and one whose test raises.
3. A Firefox that is not the harness's (started by the tester, own
   profile) is still running after a harness run.
4. Each pinned behaviour in point 8 fails under its mutant.
5. Outside connections: with the proxy pref pointed at a listening
   loopback port that logs, a run shows what Firefox tried and that
   nothing was answered; with the shipped prefs, the count of attempts is
   reported.
6. With `KRONK_PAGE_TESTS=off`, and with `PATH` lacking geckodriver, the
   tier skips with the reason in the summary and the rest passes.
7. Other tests still open no sockets: the opt-in is scoped to
   `tests/page/`.

## Out of scope

- The other pages (`/`, `/services`, `/settings`, `/performance`,
  `/agents`, `/solar`, shopping list). The harness is built so each is a
  new test file; none is written here.
- Screenshots, visual comparison, any browser but Firefox.
- Running against the dev or production orchestrator.

## Rulings

(none yet)
