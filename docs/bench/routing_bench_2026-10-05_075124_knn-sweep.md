# Routing bench — knn-sweep (2026-10-05_075124)

test items 118 (first seen ≥ 2026-09-01, bench prompts excluded); exemplars 363; coverage pool = home-labelled test items the pins send to the coordinator (26)

| Arm | wrong-lane | named wrong | coverage | abstain | median ms | p95 ms |
|---|---|---|---|---|---|---|
| regex | 1 | 0 | 0/26 | 76 | 0.0 | 0.1 |
| regex+knn@0 | 1 | 0 | 1/26 | 75 | 4.8 | 11.8 |
| regex+knn@2 | 1 | 0 | 1/26 | 75 | 2.4 | 5.8 |
| regex+knn@5 | 1 | 0 | 2/26 | 74 | 2.4 | 7.5 |

Pass rule: wrong-lane ≤ regex baseline AND named wrong = 0 AND coverage ≥ 50% AND p95 < 50 ms. `@N` = thresholds fit allowing N% wrong-lane on the exemplars (leave-one-out).

## regex: wrong-lane rows
- 'Convert the energy produced by the solar panels last week into dollars' label=direct pred=research (search_phrase)

## regex+knn@0: wrong-lane rows
- 'Convert the energy produced by the solar panels last week into dollars' label=direct pred=research (pin:search_phrase)

## regex+knn@2: wrong-lane rows
- 'Convert the energy produced by the solar panels last week into dollars' label=direct pred=research (pin:search_phrase)

## regex+knn@5: wrong-lane rows
- 'Convert the energy produced by the solar panels last week into dollars' label=direct pred=research (pin:search_phrase)
