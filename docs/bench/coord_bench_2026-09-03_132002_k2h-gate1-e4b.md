# Coordinator model bench — k2h-gate1-e4b (2026-09-03_132002)

| Model | weather_delegate | shopping_delegate | no_spurious | news_brief_terminal | news_refresh_flag | knowledge_prose | markdown_list | composite_solar | leaks | med gen tok/s | med composite s | med reasoning chars |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| gemma-4-e4b | 5/5 | 3/3 | 5/5 | 3/3 | 3/3 | 2/2 | 2/2 | 5/5 | 0 | 103.8 | 3.4 | 712.5 |

Rule: beat the incumbent on correctness, or tie and win >=2x on gen tok/s. `leaks` counts runs where deliberation or raw tool-call syntax reached `content`.
