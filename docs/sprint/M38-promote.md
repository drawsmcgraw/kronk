# M38 promotion checks

Coordinator, 2026-11-27 15:55 to 16:00 EST, on production after
`scripts/deploy_tag.sh deploy item-M38` (tag at `33673fe`: build
`57f3df4`, the test's PASSES).

| Check | Result |
| :-- | :-- |
| Suite on the tagged tree | `8593 passed, 3 skipped, 62 xfailed` |
| `deploy_tag.sh` | `/api/status` and the `/api/chat` shim ok; six Kronk containers on `item-M38` |
| `/api/status` | 200, `status` ok; `llm_service.servers` gemma-4-e4b up, talkie up; `litellm` up; `tool_service` `{"status": "ok", "music_dry_run": false}` |
| Criterion 6, production `/api/chat`, home route, the Boston follow-up, 15:59:01 to 15:59:20, no Voice PE playing | first rounds cached 0 (first after the deploy), 4,038, 4,038 of 4,039; ten `/api/status` calls (all 200, 157 ms in all); the fourth request cached **4,038 of 4,039**, first round 0.97 s. Before (dev, `M38-build.md`): 0 of 4,073 |
| `llama-talkie` journal, the two minutes around it | 0 `launch_slot_` lines |
| Home Assistant, Music Assistant | not touched |

The chat page's dot and `/services`' chat card read this route; on
production they are now the 0.01 s answer above.

Testing cost: one build, one test (`M38-e2e.md`, PASSES, no blocker or
should-fix), no fix round. Leftovers #105 (dev chat page talks to
production) and #106 (nginx's upgrade check reads `/api/status`).
