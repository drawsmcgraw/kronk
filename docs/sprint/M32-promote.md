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
