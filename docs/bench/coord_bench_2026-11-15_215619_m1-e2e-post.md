# Coordinator model bench — m1-e2e-post (2026-11-15_215619)

## Base battery

| Model | weather_delegate | shopping_delegate | no_spurious | news_brief_terminal | news_refresh_flag | knowledge_prose | markdown_list | composite_solar | total | leaks | med gen tok/s | med composite s | med reasoning chars |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| gemma-4-e4b | 5/5 | 3/3 | 5/5 | 3/3 | 3/3 | 2/2 | 1/2 | 5/5 | 27/28 | 0 | 64.8 | 6.8 | 377 |

## Hard set (2026-10-24)

| Model | officeholder_delegate | mortgage_rate_research | weather_by_riddle | date_math | clock_math | exact_words | composite_hottub | total | leaks | med gen tok/s | med composite s | med reasoning chars |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| gemma-4-e4b | 3/3 | 3/3 | 3/3 | 1/3 | 3/3 | 1/3 | 0/3 | 14/21 | 0 | 74.5 | 5.2 | 591.0 |

## Absent data (2026-11-15)

| Model | no_data_sleep | no_data_networth | total | leaks | med gen tok/s | med composite s | med reasoning chars |
|---|---|---|---|---|---|---|---|
| gemma-4-e4b | 5/5 | 5/5 | 10/10 | 0 | 79.7 | 0 | 835 |

Base rule (2026-09-03): beat the incumbent on correctness, or tie and win >=2x on gen tok/s. Hard-set rule: docs/plans/MODEL_BENCH_GEMMA4_26B_PLAN.md. `leaks` counts runs where deliberation or raw tool-call syntax reached `content`.
