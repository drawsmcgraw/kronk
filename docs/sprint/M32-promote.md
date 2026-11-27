# M32 promotion checks

## Step 1: /work live and safe

Coordinator, 2026-11-26 07:15 to 07:25 EST. Tag `item-M32-step1` at
`f6604b9` (the build, the test's PASSES, fix round 1).

| Check | Result |
| :-- | :-- |
| Fix round 1, the coordinator's reading of the diff | the three ruled jobs and nothing else: `StartLimitIntervalSec=0` on the export service; one encoding of the outline per change; "Heard"'s "as of" in the house's zone |
| Suite on the fixed tree | `8477 passed, 3 skipped, 61 xfailed` |
| `deploy_tag.sh deploy item-M32-step1` | `/api/status` and the `/api/chat` shim ok; six Kronk containers on the tag; HA (up 8 days) and MA (up 3 weeks) not touched |
| What an open page saw through the restart (`/api/work?have=1` twice a second) | 200 from the old container, one 502 at 07:19:49, connection refused until 07:20:06 (17 s), then 200 with page version `7a2332b2f04f` |
| `/work` | 200, `Cache-Control: no-store`, stamped `7a2332b2f04f`; `/api/work` fresh, `schema_known` 3, no schema note, `house_zone` America/New_York; `/api/voice_runs` `no-store` |
| `/voice/api/chat` | answered ("ready.") |
| Units | `kronk-work-export.{service,timer,path}` installed from `systemd/`; the path unit enabled and active; the service shows `StartLimitIntervalUSec=0` |
| 1b, three real `work.py note` writes, from the write to the route's answer | 0.41 s, 0.41 s, 0.36 s (the page polls every 3 s, so on the page within 3.5 s) |
| A burst of eight writes in a row | the last write on the route 4 s later; the path unit still active; the service not failed; 7 exports ran |

Not seen: the page in a real browser through this restart (the test
agent showed the page's side of a 502 and of a refused connection, for
a minute and for an hour, behind a proxy; `M32-e2e.md`). A browser left
open on `/work` across this deploy would have reloaded itself to the
new version: Drew's to confirm if one was open.

Steps 2 (rows that unfold) and 3 (motion and the display) follow; M32
stays under way.

## Step 2: rows that unfold

Coordinator, 2026-11-27 13:10 to 13:20 EST. Tag `item-M32-step2` at
`5b83da8` (build `c5aaf0d`, fix 1 `40cac4b`, the narrow retest's PASSES).

| Check | Result |
| :-- | :-- |
| Suite on the tagged tree | `8563 passed, 3 skipped, 62 xfailed` |
| `deploy_tag.sh deploy item-M32-step2` | `/api/status` and the `/api/chat` shim ok; six Kronk containers on the tag; HA (up 9 days) and MA (up 3 weeks) not touched |
| `/work` | 200, `Cache-Control: no-store`, stamped `b612a3370183` (step 1: `7a2332b2f04f`) |
| `/api/work` | `schema` 4, `schema_known` 4, page version `b612a3370183`, state fresh; 71 open leftovers and decisions with text across the items; 3 of 3 owed decisions with text; M12's Shipped line "Refactor sprint"; M32's source 685 characters, whole |
| `/voice/api/chat`, `/api/chat` | both answered with the live forecast |

Not seen: the page in a browser on production (the tests' browser runs
were on dev). Drew's look, once.

Testing cost: one build, one full test (`M32-step2-e2e.md`: B1 and S1,
both on every load of the live data), one fix round, one narrow retest
(`M32-step2-e2e2.md`, PASSES). Both rounds found something that
happens. Step 3 (motion) follows; M32 stays under way.

## Step 3: motion (M32 promoted)

Coordinator, 2026-11-27 14:50 to 15:00 EST. Tag `item-M32-step3` at
`a1dfac8` (build `cd0dcc1`, fix 1 `9f9a722`, the narrow retest's PASSES).

| Check | Result |
| :-- | :-- |
| Suite on the tagged tree | `8582 passed, 3 skipped, 62 xfailed` |
| `deploy_tag.sh deploy item-M32-step3` | `/api/status` and the `/api/chat` shim ok; six Kronk containers on the tag; HA (up 9 days) and MA (up 3 weeks) not touched |
| `/work` | 200, `Cache-Control: no-store`, stamped `cd6954945009`, the page the retest passed on dev (step 2: `b612a3370183`) |
| `/api/work` | `schema` 4, page version `cd6954945009`, state fresh |
| `/voice/api/chat`, `/api/chat` | both answered with the live forecast |

Not seen: the page in a browser on production. Drew's look, once
(leftover #103).

Testing cost, step 3: one build, one full test (`M32-step3-e2e.md`: B1
and B2, both in the live data), one fix round, one narrow retest
(`M32-step3-e2e2.md`, PASSES, no should-fix). Across M32's three steps:
three builds, three full tests, three fix rounds, two narrow retests;
every full test found something that happens.
