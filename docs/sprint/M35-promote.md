# M35 promotion checks

Coordinator, 2026-11-25 23:59 to 2026-11-26 00:01 EST, on production
after `scripts/deploy_tag.sh deploy item-M35`.

| Check | Result |
| :-- | :-- |
| `deploy_tag.sh` | `/api/status` ok, `/api/chat` shim ok; six containers on `item-M35` |
| `kronk-orchestrator-1` env | `KRONK_TZ=America/New_York` |
| "what time is it", `/api/chat`, 23:59:59 EST | "It is 11:59 PM EST." |
| "what time is it", `/voice/api/chat`, 00:00:02 EST | "The current time is 12:00 AM EST on Thursday, November 26, 2026." |
| "what is the date tomorrow", both shims, 00:00 EST | Friday, November 27, 2026 (right; UTC agrees at that hour, so the date's proof is dev's at 23:50 and 23:52 EST, `M35-build.md` and `M35-e2e.md`) |
| Forecast in production's tool_service, fetched 23:59 EST | hourly list starts "11 PM" (before: "3 AM") |
| Home Assistant, Music Assistant | not touched: up 8 days, up 3 weeks |
| Live battery | on dev, after the build, one rep: `docs/bench/bench_2026-11-25_235837_m35-post-dev.json`, eight of eight answered; none before the build (see `PROMOTIONS.md`, "M35") |

Not checked: a spoken request (Drew's), and `m35-date-tomorrow` through
Home Assistant's pipeline (a player was active).
