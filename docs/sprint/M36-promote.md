# M36 promotion checks

Coordinator, 2026-11-26 19:48 to 20:10 EST. Dev checks before the tag;
production checks after `scripts/deploy_tag.sh deploy item-M36`.

| Check | Result |
| :-- | :-- |
| Suite, HEAD before the tag | `8521 passed, 3 skipped, 62 xfailed, 6 warnings in 217.28s` |
| `pipeline_bench.sh` on dev (criterion 6) | `m36-post-dev` (1 rep) and `m36-post-dev-x3` (3 reps) against `m36-pre-dev` (1 rep): every prompt answered; each pre value inside the post 3-rep range or within 0.5 s of it (pre `shim_weather` 5.45 s, post max 5.23 s; pre `weather_now` 5.70 s, post max 4.73 s) |
| Home route cached tokens (criterion 6), `/dev/api/chat`, Boston follow-up | same minute: plan round cached 4,183 of 4,184; one minute later: 893 of 4,184 (the clock at token 893, decision #95) |
| Silent replay `m36-post` (criterion 5) | 166 rows: 115 pass, 18 xfail, 2 xpass, 30 skip, 1 fail. No full replay ran on the pre-M36 build; against the last full ones (`m18-e2e3`, `m18-e2e4`) no row moved to fail or changed route or tool. The fail, `inc-1119-get-a-grit-aerosmith-basement`, is HA's sentence-trigger reading (catch-all, not album), compared and not executed; Kronk is not reached. 30 skipped because `media_player.upper_shield` was playing |
| `deploy_tag.sh` | `/api/status` ok, `/api/chat` shim ok; six containers on `item-M36` |
| Both shims, production, "what is the weather" | `/voice/api/chat` and `/api/chat` answered with the live forecast, `get_weather` called |
| Criterion 8, production `/message`, session `m36-promote` | "play the album 'pulse' by pink floyd in the basement": `play_music`, "Now playing Pulse by Pink Floyd on the basement voice pe ma speaker." Then "play that album": rule `music`, route `home`, `play_music` (media_type, player, query), same reply. `media_player.home_assistant_voice_0ab117` playing, artist Pink Floyd, album Pulse. The basement puck was Drew's choice of speaker; it was left playing |
| Home Assistant, Music Assistant | not touched: up 9 days, up 3 weeks |

Not checked: a spoken request (the music blueprint takes a spoken "play …"
as a new conversation; decision #96).

## Testing cost

One design review, one build (stopped before its counts; no report), one
test round (`M36-e2e.md`, 222 requests), no fix round. The test stopped
on the zero-claims rule: one false "now playing" with no tool call in
222 requests (finding 1). Drew accepted it ("1 reply out of 222 is a
failure rate I'm okay with. Promote it and move on.") and ruled
criterion 2's natural leg on the live-form reply (8 of 10). Findings 1
to 3 happen in ordinary use; 2, 3 and 4 are left over (matters #99 to
#101), with the dev sessions the test left (#102).

The test report had no anchored verdict line (the brief did not ask for
one); the suite's `test_real_tree_every_test_report_has_a_verdict`
caught it, and the coordinator added `Verdict: FAILS`, the agent's own
reading, with a pointer to the ruling.
