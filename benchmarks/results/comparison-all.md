# Multi-way comparison

- **v1-baseline** = `v1-baseline-20260530-095908.jsonl`
- **v2-gemma+fp** = `v2-coord-20260530-102134.jsonl`
- **v2-nemo-only** = `v2-coord-nemo-nofp-20260530-172438.jsonl`
- **v2-nemo+fp** = `v2-nemo-fp-20260530-180301.jsonl`

## Aggregate (median per-query) — `api_chat`

| label | p50 | p95 | n_pass | n_suspect | n_fail |
|---|---|---|---|---|---|
| v1-baseline |   7.88s |  19.56s | 53 (88%) | 4 | 3 |
| v2-gemma+fp |   6.49s |  30.09s | 40 (67%) | 11 | 9 |
| v2-nemo-only |  12.71s |  54.05s | 56 (93%) | 2 | 2 |
| v2-nemo+fp |  10.51s |  35.43s | 55 (92%) | 3 | 2 |

## Aggregate (median per-query) — `message`

| label | p50 | p95 | n_pass | n_suspect | n_fail |
|---|---|---|---|---|---|
| v1-baseline |   7.98s |  19.25s | 56 (93%) | 3 | 1 |
| v2-gemma+fp |   9.44s |  20.75s | 57 (95%) | 3 | 0 |
| v2-nemo-only |   7.89s |  25.60s | 52 (87%) | 6 | 2 |
| v2-nemo+fp |   4.82s |  28.97s | 49 (82%) | 2 | 9 |

## Per-query — `api_chat`

| query_id | v1-baseline | v2-gemma+fp | v2-nemo-only | v2-nemo+fp | best | worst |
|---|---|---|---|---|---|---|
| `ambig_cellulitis` |  10.87s |  18.61s |  27.78s |  26.41s |  10.87s |  27.78s |
| `ambig_health_general` |   7.51s |   6.57s |  16.33s ⚠️1 |  11.18s |   6.57s |  16.33s |
| `code_email_validator` |  19.56s |   5.80s |  54.05s |  20.84s |   5.80s |  54.05s |
| `cross_weather_run` |  18.37s |   3.51s ❌1 |  26.52s |  19.01s |   3.51s |  26.52s |
| `direct_math` |   0.92s |   5.97s |   1.04s |   1.27s ⚠️1 |   0.92s |   5.97s |
| `direct_pong` |   0.22s |   0.09s |   0.22s |   0.30s |   0.09s |   0.30s |
| `finance_grocery_spend` |   2.74s |   2.69s |   5.89s |   7.53s |   2.69s |   7.53s |
| `health_rhr_month` |   6.18s |   7.66s |   7.92s |   8.76s |   6.18s |   8.76s |
| `health_sleep_week` |   5.69s |   7.31s |   7.95s |   7.18s |   5.69s |   7.95s |
| `hottub_status` |   4.31s |   1.07s ❌2 |   8.73s |   5.29s |   1.07s |   8.73s |
| `kronk_self_arch` |  15.80s |  10.21s |   5.30s |  10.87s |   5.30s |  15.80s |
| `lookup_avgo_close` |  36.80s ❌1 |  30.09s ⚠️1 |   6.63s |   7.97s |   6.63s |  36.80s |
| `lookup_conan_quote` |   8.37s ⚠️3 |  13.30s ⚠️2 |   9.09s |  10.15s |   8.37s |  13.30s |
| `lookup_news_brief` |   8.26s ❌1 |   9.09s |  17.07s | 240.10s ❌2 |   8.26s | 240.10s |
| `research_voter_guide` |  14.09s |  42.88s ⚠️1 |  21.45s ⚠️1 |  35.43s |  14.09s |  42.88s |
| `shopping_list_add` |   4.82s ⚠️1 |   0.80s ⚠️3 | 240.06s ❌2 |   5.33s |   0.80s | 240.06s |
| `shopping_list_view` |   1.75s |   0.82s ❌3 |   5.69s |   0.31s ⚠️2 |   0.31s |   5.69s |
| `talkie_explicit` |   5.02s |   6.40s |  17.33s |  23.12s |   5.02s |  23.12s |
| `weather_default` |   8.45s |   1.64s ❌3 |  26.55s |  27.70s |   1.64s |  27.70s |
| `weather_specific_location` |  18.48s ❌1 |   6.59s ⚠️2 |  16.76s |  22.70s |   6.59s |  22.70s |

## Per-query — `message`

| query_id | v1-baseline | v2-gemma+fp | v2-nemo-only | v2-nemo+fp | best | worst |
|---|---|---|---|---|---|---|
| `ambig_cellulitis` |   9.95s |   4.84s |   5.72s |   5.37s |   4.84s |   9.95s |
| `ambig_health_general` |   7.55s |   8.12s |   8.65s |   3.12s ❌3 |   3.12s |   8.65s |
| `code_email_validator` |  23.34s |  19.13s |  20.71s |  28.97s |  19.13s |  28.97s |
| `cross_weather_run` |  13.35s |  16.98s |  13.42s |  18.00s |  13.35s |  18.00s |
| `direct_math` |   0.95s |   0.98s |   0.37s |   0.57s |   0.37s |   0.98s |
| `direct_pong` |   0.24s |   0.25s |   0.32s |   0.48s |   0.24s |   0.48s |
| `finance_grocery_spend` |   3.40s |   2.89s |   2.21s |   2.40s |   2.21s |   3.40s |
| `health_rhr_month` |   4.23s |   7.35s |   6.05s |   4.28s |   4.23s |   7.35s |
| `health_sleep_week` |   4.71s |   9.32s |   6.81s |   3.96s |   3.96s |   9.32s |
| `hottub_status` |   5.12s |   9.55s |   7.85s |   6.08s |   5.12s |   9.55s |
| `kronk_self_arch` |  11.70s |  11.47s |  49.32s ⚠️2 |  39.19s ⚠️1 |  11.47s |  49.32s |
| `lookup_avgo_close` |  19.25s ❌1 |  32.84s |  19.26s ❌2 |  21.09s ❌1 |  19.25s |  32.84s |
| `lookup_conan_quote` |   8.42s ⚠️1 |  18.20s ⚠️1 |   7.94s ⚠️2 |   8.29s ⚠️1 |   7.94s |  18.20s |
| `lookup_news_brief` |  13.40s |  11.83s |  11.42s |  16.03s ❌2 |  11.42s |  16.03s |
| `research_voter_guide` |  10.47s |  16.12s |  11.72s |   3.86s ❌3 |   3.86s |  16.12s |
| `shopping_list_add` |   3.23s ⚠️2 |   4.36s ⚠️2 |   4.17s |   3.88s |   3.23s |   4.36s |
| `shopping_list_view` |   3.79s |   3.72s |   3.08s |   3.97s |   3.08s |   3.97s |
| `talkie_explicit` |   1.76s |   4.58s |   4.49s |   2.47s |   1.76s |   4.58s |
| `weather_default` |  12.33s |  13.48s |  16.93s ⚠️1 |  20.35s |  12.33s |  20.35s |
| `weather_specific_location` |  14.68s |  20.75s |  25.60s ⚠️1 |  18.53s |  14.68s |  25.60s |
