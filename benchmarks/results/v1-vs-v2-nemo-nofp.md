# Comparison — baseline `v1-baseline-20260530-095908.jsonl` vs candidate `v2-coord-nemo-nofp-20260530-172438.jsonl`

## Per-query comparison

| query_id | endpoint | base p50 | cand p50 | Δ | base quality | cand quality |
|---|---|---|---|---|---|---|
| `ambig_cellulitis` | api_chat |  10.87s |  27.78s | +16.91s | P=3 | P=3 |
| `ambig_cellulitis` | message |   9.95s |   5.72s | -4.23s | P=3 | P=3 |
| `ambig_health_general` | api_chat |   7.51s |  16.33s | +8.82s | P=3 | P=2 S=1 |
| `ambig_health_general` | message |   7.55s |   8.65s | +1.10s | P=3 | P=3 |
| `code_email_validator` | api_chat |  19.56s |  54.05s | +34.49s | P=3 | P=3 |
| `code_email_validator` | message |  23.34s |  20.71s | -2.63s | P=3 | P=3 |
| `cross_weather_run` | api_chat |  18.37s |  26.52s | +8.15s | P=3 | P=3 |
| `cross_weather_run` | message |  13.35s |  13.42s | +0.07s | P=3 | P=3 |
| `direct_math` | api_chat |   0.92s |   1.04s | +0.13s | P=3 | P=3 |
| `direct_math` | message |   0.95s |   0.37s | -0.57s | P=3 | P=3 |
| `direct_pong` | api_chat |   0.22s |   0.22s | -0.00s | P=3 | P=3 |
| `direct_pong` | message |   0.24s |   0.32s | +0.08s | P=3 | P=3 |
| `finance_grocery_spend` | api_chat |   2.74s |   5.89s | +3.15s | P=3 | P=3 |
| `finance_grocery_spend` | message |   3.40s |   2.21s | -1.19s | P=3 | P=3 |
| `health_rhr_month` | api_chat |   6.18s |   7.92s | +1.74s | P=3 | P=3 |
| `health_rhr_month` | message |   4.23s |   6.05s | +1.82s | P=3 | P=3 |
| `health_sleep_week` | api_chat |   5.69s |   7.95s | +2.26s | P=3 | P=3 |
| `health_sleep_week` | message |   4.71s |   6.81s | +2.10s | P=3 | P=3 |
| `hottub_status` | api_chat |   4.31s |   8.73s | +4.42s | P=3 | P=3 |
| `hottub_status` | message |   5.12s |   7.85s | +2.73s | P=3 | P=3 |
| `kronk_self_arch` | api_chat |  15.80s |   5.30s | -10.50s | P=3 | P=3 |
| `kronk_self_arch` | message |  11.70s |  49.32s | +37.61s | P=3 | P=1 S=2 |
| `lookup_avgo_close` | api_chat |  36.80s |   6.63s | -30.17s | P=2 F=1 | P=3 |
| `lookup_avgo_close` | message |  19.25s |  19.26s | +0.01s | P=2 F=1 | P=1 F=2 |
| `lookup_conan_quote` | api_chat |   8.37s |   9.09s | +0.72s | S=3 | P=3 |
| `lookup_conan_quote` | message |   8.42s |   7.94s | -0.48s | P=2 S=1 | P=1 S=2 |
| `lookup_news_brief` | api_chat |   8.26s |  17.07s | +8.81s | P=2 F=1 | P=3 |
| `lookup_news_brief` | message |  13.40s |  11.42s | -1.98s | P=3 | P=3 |
| `research_voter_guide` | api_chat |  14.09s |  21.45s | +7.36s | P=3 | P=2 S=1 |
| `research_voter_guide` | message |  10.47s |  11.72s | +1.25s | P=3 | P=3 |
| `shopping_list_add` | api_chat |   4.82s | 240.06s | +235.24s | P=2 S=1 | P=1 F=2 |
| `shopping_list_add` | message |   3.23s |   4.17s | +0.94s | P=1 S=2 | P=3 |
| `shopping_list_view` | api_chat |   1.75s |   5.69s | +3.94s | P=3 | P=3 |
| `shopping_list_view` | message |   3.79s |   3.08s | -0.72s | P=3 | P=3 |
| `talkie_explicit` | api_chat |   5.02s |  17.33s | +12.31s | P=3 | P=3 |
| `talkie_explicit` | message |   1.76s |   4.49s | +2.73s | P=3 | P=3 |
| `weather_default` | api_chat |   8.45s |  26.55s | +18.10s | P=3 | P=3 |
| `weather_default` | message |  12.33s |  16.93s | +4.59s | P=3 | P=2 S=1 |
| `weather_specific_location` | api_chat |  18.48s |  16.76s | -1.72s | P=2 F=1 | P=3 |
| `weather_specific_location` | message |  14.68s |  25.60s | +10.91s | P=3 | P=2 S=1 |

## Aggregate p50/p95 by endpoint

| endpoint | base p50 | cand p50 | base p95 | cand p95 |
|---|---|---|---|---|
| `message` |   7.98s |   7.89s |  19.25s |  25.60s |
| `api_chat` |   7.88s |  12.71s |  19.56s |  54.05s |
