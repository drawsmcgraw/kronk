# Coordinator model bench — k2h-gate1-k2-7b-q8-high (2026-09-03_133606)

| Model | weather_delegate | shopping_delegate | no_spurious | news_brief_terminal | news_refresh_flag | knowledge_prose | markdown_list | composite_solar | leaks | med gen tok/s | med composite s | med reasoning chars |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| k2-horizon-7b-q8 | 5/5 | 3/3 | 5/5 | 3/3 | 3/3 | 2/2 | 2/2 | 4/5 | 0 | 24.6 | 8.0 | 207.5 |

Rule: beat the incumbent on correctness, or tie and win >=2x on gen tok/s. `leaks` counts runs where deliberation or raw tool-call syntax reached `content`.
