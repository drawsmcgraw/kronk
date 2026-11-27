# M32 step 2: rows that unfold, under gjs

The build agent's check of `orchestrator/static/work.html`'s keyed patching,
folds, unfolded rows, dropped list and "Heard"'s folded line
(`docs/sprint/M32-step2-build.md`). The suite has no JavaScript engine;
`gjs` (SpiderMonkey) is on the host. `dom.js` is step 1's stub DOM
(`../M32-step1-gjs/`) with two changes: a `toggle` reaches capture listeners
on the element's ancestors (the page's one fold listener sits on `<main>`),
and a move of a node within its parent is counted in `DOC.moves`. `run.js`
holds the scenarios (criteria 2a to 2e, 2g to 2j, and note 6); each line it
prints is PASS or FAIL with what it saw.

Run from this directory, with a copy of a schema-4 export as the fixture
(repo data; the "Heard" runs are invented, "fixture words N") and
`item-M32-step1`'s page for 2h:

    cp ../../../data/dev/work.json /tmp/fixture.json
    git show item-M32-step1:orchestrator/static/work.html > /tmp/old-work.html
    TZ=UTC gjs run.js ../../../orchestrator/static/work.html /tmp/fixture.json /tmp/old-work.html

Some checks name items of the export of 2026-11-26 (M32 under way with
its steps, M24's open entries, M29 dropped on 2026-11-24): on a later
export those lines may need their names changed. A few minutes of wall
time. What it cannot show (layout, a real click, `sessionStorage` across a
real reload) is the browser's.
