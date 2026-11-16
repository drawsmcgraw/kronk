# Coordinator model bench — m1b-post (2026-11-15_225125)

## Base battery

| Model | weather_delegate | shopping_delegate | no_spurious | news_brief_terminal | news_refresh_flag | knowledge_prose | markdown_list | composite_solar | total | leaks | med gen tok/s | med composite s | med reasoning chars |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| gemma-4-e4b | 5/5 | 3/3 | 5/5 | 3/3 | 3/3 | 2/2 | 1/2 | 5/5 | 27/28 | 0 | 71.6 | 5.4 | 526.5 |

## Hard set (2026-10-24)

| Model | officeholder_delegate | mortgage_rate_research | weather_by_riddle | date_math | clock_math | exact_words | total | leaks | med gen tok/s | med composite s | med reasoning chars |
|---|---|---|---|---|---|---|---|---|---|---|---|
| gemma-4-e4b | 3/3 | 3/3 | 3/3 | 1/3 | 3/3 | 0/3 | 13/18 | 0 | 69.5 | 0 | 618 |

## Absent data (2026-11-15)

| Model | no_data_sleep | no_data_networth | no_device_hottub | total | leaks | med gen tok/s | med composite s | med reasoning chars |
|---|---|---|---|---|---|---|---|---|
| gemma-4-e4b | 5/5 | 5/5 | 5/5 | 15/15 | 0 | 72.0 | 0 | 912 |

Base rule (2026-09-03): beat the incumbent on correctness, or tie and win >=2x on gen tok/s. Hard-set rule: docs/plans/MODEL_BENCH_GEMMA4_26B_PLAN.md. `leaks` counts runs where deliberation or raw tool-call syntax reached `content`.
