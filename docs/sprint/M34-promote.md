# M34 promotion checks

Coordinator, 2026-11-26 05:11 to 05:14 EST. Tag `item-M34`.

The exporter's part went live without a deploy at 04:31 EST, when the
build agent's commit `86584fe` put the new `scripts/work_export.py` in
the working tree (the host timer runs that file). The page and the route
went live with `scripts/deploy_tag.sh deploy item-M34` at 05:11 EST.

| Check | Result |
| :-- | :-- |
| `deploy_tag.sh` | `/api/status` ok, `/api/chat` shim ok; six Kronk containers on `item-M34` |
| `/work`, `/api/work`, `/api/voice_runs?limit=0`, `/resources` | 200; `/work` still holds "Heard" |
| `/api/work` envelope | fresh; file schema 2, `schema_known` 2, no schema note |
| The database's state in the file | `ok`, dump 10 s old |
| Under way, file against `work.py resume` | both: M34 only, stage "promoting" (the tag existed, the promotion was not yet recorded) |
| Queue | both: M32, held by M34 |
| A promotion with no item | the chat-buttons deploy recorded (`work.py promote --label "Chat page buttons" --tag chat-buttons-2026-11-25 --verified docs/sprint/PROMOTIONS.md --at 2026-11-25T22:19:00-05:00`), in the file under `promotions_no_item` a minute later |
| Decisions owed | 11, each with id, item, date, whether it blocks; no text |
| `/voice/api/chat` | answered ("ready.") |
| Installed unit files | `kronk-work-export.{service,timer}` copied from `systemd/`, `daemon-reload`; the timer's next run finished |
| PROMOTIONS.md | frozen by a note at its end |
| WORKFLOW.md, CLAUDE.md, ROADMAP.md | say a promotion is recorded with `work.py promote` and its checks in `<item>-promote.md` |
| Suite, after those doc changes | `8435 passed, 3 skipped, 62 xfailed` |

Home Assistant and Music Assistant not touched. The page in a browser on
production was not looked at by the coordinator; the test agent ran both
the new page and the page of `item-M30` in a browser on dev
(`M34-e2e.md`).

Drew's words at M33, to be said back at this promotion ("it would
simplify the file structure"): the report files stay, one per step;
what ends is the hand-kept log (`PROMOTIONS.md`) and reading prose to
learn an item's state.
