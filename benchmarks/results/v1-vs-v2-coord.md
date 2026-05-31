# Comparison — baseline `v1-baseline-20260530-095908.jsonl` vs candidate `v2-coord-20260530-102134.jsonl`

## Per-query comparison

| query_id | endpoint | base p50 | cand p50 | Δ | base quality | cand quality |
|---|---|---|---|---|---|---|
| `ambig_cellulitis` | api_chat |  10.87s |  18.61s | +7.75s | P=3 | P=3 |
| `ambig_cellulitis` | message |   9.95s |   4.84s | -5.11s | P=3 | P=3 |
| `ambig_health_general` | api_chat |   7.51s |   6.57s | -0.94s | P=3 | P=3 |
| `ambig_health_general` | message |   7.55s |   8.12s | +0.57s | P=3 | P=3 |
| `code_email_validator` | api_chat |  19.56s |   5.80s | -13.77s | P=3 | P=3 |
| `code_email_validator` | message |  23.34s |  19.13s | -4.21s | P=3 | P=3 |
| `cross_weather_run` | api_chat |  18.37s |   3.51s | -14.86s | P=3 | S=2 F=1 |
| `cross_weather_run` | message |  13.35s |  16.98s | +3.63s | P=3 | P=3 |
| `direct_math` | api_chat |   0.92s |   5.97s | +5.06s | P=3 | P=3 |
| `direct_math` | message |   0.95s |   0.98s | +0.04s | P=3 | P=3 |
| `direct_pong` | api_chat |   0.22s |   0.09s | -0.13s | P=3 | P=3 |
| `direct_pong` | message |   0.24s |   0.25s | +0.01s | P=3 | P=3 |
| `finance_grocery_spend` | api_chat |   2.74s |   2.69s | -0.05s | P=3 | P=3 |
| `finance_grocery_spend` | message |   3.40s |   2.89s | -0.50s | P=3 | P=3 |
| `health_rhr_month` | api_chat |   6.18s |   7.66s | +1.47s | P=3 | P=3 |
| `health_rhr_month` | message |   4.23s |   7.35s | +3.12s | P=3 | P=3 |
| `health_sleep_week` | api_chat |   5.69s |   7.31s | +1.62s | P=3 | P=3 |
| `health_sleep_week` | message |   4.71s |   9.32s | +4.61s | P=3 | P=3 |
| `hottub_status` | api_chat |   4.31s |   1.07s | -3.24s | P=3 | P=1 F=2 |
| `hottub_status` | message |   5.12s |   9.55s | +4.44s | P=3 | P=3 |
| `kronk_self_arch` | api_chat |  15.80s |  10.21s | -5.59s | P=3 | P=3 |
| `kronk_self_arch` | message |  11.70s |  11.47s | -0.23s | P=3 | P=3 |
| `lookup_avgo_close` | api_chat |  36.80s |  30.09s | -6.71s | P=2 F=1 | P=2 S=1 |
| `lookup_avgo_close` | message |  19.25s |  32.84s | +13.59s | P=2 F=1 | P=3 |
| `lookup_conan_quote` | api_chat |   8.37s |  13.30s | +4.93s | S=3 | P=1 S=2 |
| `lookup_conan_quote` | message |   8.42s |  18.20s | +9.78s | P=2 S=1 | P=2 S=1 |
| `lookup_news_brief` | api_chat |   8.26s |   9.09s | +0.83s | P=2 F=1 | P=3 |
| `lookup_news_brief` | message |  13.40s |  11.83s | -1.57s | P=3 | P=3 |
| `research_voter_guide` | api_chat |  14.09s |  42.88s | +28.79s | P=3 | P=2 S=1 |
| `research_voter_guide` | message |  10.47s |  16.12s | +5.65s | P=3 | P=3 |
| `shopping_list_add` | api_chat |   4.82s |   0.80s | -4.02s | P=2 S=1 | S=3 |
| `shopping_list_add` | message |   3.23s |   4.36s | +1.13s | P=1 S=2 | P=1 S=2 |
| `shopping_list_view` | api_chat |   1.75s |   0.82s | -0.93s | P=3 | F=3 |
| `shopping_list_view` | message |   3.79s |   3.72s | -0.08s | P=3 | P=3 |
| `talkie_explicit` | api_chat |   5.02s |   6.40s | +1.38s | P=3 | P=3 |
| `talkie_explicit` | message |   1.76s |   4.58s | +2.82s | P=3 | P=3 |
| `weather_default` | api_chat |   8.45s |   1.64s | -6.81s | P=3 | F=3 |
| `weather_default` | message |  12.33s |  13.48s | +1.15s | P=3 | P=3 |
| `weather_specific_location` | api_chat |  18.48s |   6.59s | -11.90s | P=2 F=1 | P=1 S=2 |
| `weather_specific_location` | message |  14.68s |  20.75s | +6.06s | P=3 | P=3 |

## Aggregate p50/p95 by endpoint

| endpoint | base p50 | cand p50 | base p95 | cand p95 |
|---|---|---|---|---|
| `message` |   7.98s |   9.44s |  19.25s |  20.75s |
| `api_chat` |   7.88s |   6.49s |  19.56s |  30.09s |
