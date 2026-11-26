# M32 step 1: the page's scheduler under gjs

The build agent's check of `orchestrator/static/work.html`'s script
(`docs/sprint/M32-build.md`). The suite has no JavaScript engine; `gjs`
(SpiderMonkey) is on the host. `dom.js` is a stub DOM built from the page's
own `<body>` markup, a fake clock (`performance.now`, `setTimeout`), a
scripted `fetch` (answers, 502, refused, hang, not JSON), and storage that
can throw. `run.js` holds the scenarios; each line it prints is PASS or
FAIL with what it saw.

Run from this directory, with a copy of an export as the fixture (repo
data, no household speech; the "Heard" runs are invented) and item-M34's
page for the old-page check:

    cp ../../../data/dev/work.json /tmp/fixture.json
    git show item-M34:orchestrator/static/work.html > /tmp/old-work.html
    TZ=UTC gjs run.js ../../../orchestrator/static/work.html /tmp/fixture.json /tmp/old-work.html

About two minutes of wall time. What it cannot show is listed in
`M32-build.md` ("What a real browser must still confirm").
