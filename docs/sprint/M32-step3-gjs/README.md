# M32 step 3: the track and its motion, under gjs

The build agent's check of `orchestrator/static/work.html`'s track, flash,
moving station, clock and still class (`docs/sprint/M32-step3-build.md`).
The suite has no JavaScript engine; `gjs` (SpiderMonkey) is on the host.
`run.js` loads step 2's stub DOM (`../M32-step2-gjs/dom.js`) unchanged. The
stub has no style engine, so what it shows is the classes the script sets
(which station is `cur`, `moving`, `working`; which row got `flash`, which
station `fade-in`), the nodes kept, the clock's text and the element and
timer counts. What the CSS does with those classes is the browser's.

Run from this directory, with a copy of a schema-4 export as the fixture:

    cp ../../../data/dev/work.json /tmp/fixture.json
    TZ=UTC gjs run.js ../../../orchestrator/static/work.html /tmp/fixture.json

It names real items of the export of 2026-11-27 (M30, M31, M35, M36 in
Done; M32 under way with `m32-build3` working): on a later export those
lines may need their names changed. The made-up item M99 is walked through
criteria, review, build, test (FAILS, graded fail), fix, test 2 (PASSES,
graded pass) and promoted, in front of M32. A few minutes of wall time
(500 updates for 3g).
