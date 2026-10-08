# Routing bench — router-gate1 (2026-10-05_074934)

test items 118 (first seen ≥ 2026-09-01, bench prompts excluded); exemplars 363; coverage pool = home-labelled test items the pins send to the coordinator (26)

| Arm | wrong-lane | named wrong | coverage | abstain | median ms | p95 ms |
|---|---|---|---|---|---|---|
| regex | 1 | 0 | 0/26 | 76 | 0.0 | 0.1 |
| regex+knn | 1 | 0 | 1/26 | 75 | 2.5 | 8.2 |

Pass rule: wrong-lane ≤ regex baseline AND named wrong = 0 AND coverage ≥ 50% AND p95 < 50 ms.

## regex: wrong-lane rows
- 'Convert the energy produced by the solar panels last week into dollars' label=direct pred=research (search_phrase)

## regex+knn: wrong-lane rows
- 'Convert the energy produced by the solar panels last week into dollars' label=direct pred=research (pin:search_phrase)
