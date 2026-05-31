# v1-baseline-20260530-095908 — `v1-baseline-20260530-095908.jsonl`

_120 trials across 20 queries_

## Aggregate timing by endpoint

| endpoint | n queries | p50 | p95 | min | max |
|---|---|---|---|---|---|
| `api_chat` | 20 | 7.88s | 19.56s | 0.22s | 36.80s |
| `message` | 20 | 7.98s | 19.25s | 0.24s | 23.34s |

## Quality flag distribution

| flag | count | pct |
|---|---|---|
| pass | 109 | 90.8% |
| suspect | 7 | 5.8% |
| fail | 4 | 3.3% |
| unknown | 0 | 0.0% |

## Per-query results

| query_id | endpoint | n | duration p50 | quality | sample response |
|---|---|---|---|---|---|
| `ambig_cellulitis` | api_chat | 3 |  10.87s | P=3 | Cellulitis is a bacterial infection of the skin and the deeper layers of the tis |
| `ambig_cellulitis` | message | 3 |   9.95s | P=3 | I will search for information on cellulitis.  |
| `ambig_health_general` | api_chat | 3 |   7.51s | P=3 | I am unable to provide a detailed overview of your fitness goals as there is no  |
| `ambig_health_general` | message | 3 |   7.55s | P=3 | I was unable to retrieve any general health data for the past 30 days. It looks  |
| `code_email_validator` | api_chat | 3 |  19.56s | P=3 | Here's a Python function to validate an email address using regular expressions: |
| `code_email_validator` | message | 3 |  23.34s | P=3 | Here's a Python function to validate an email address using regular expressions: |
| `cross_weather_run` | api_chat | 3 |  18.37s | P=3 | The weather tomorrow (Sunday) is expected to be sunny with a high near 76°F and  |
| `cross_weather_run` | message | 3 |  13.35s | P=3 | Tomorrow looks like a great day for a run. The forecast calls for a high near 76 |
| `direct_math` | api_chat | 3 |   0.92s | P=3 | 376 |
| `direct_math` | message | 3 |   0.95s | P=3 | 376 |
| `direct_pong` | api_chat | 3 |   0.22s | P=3 | pong |
| `direct_pong` | message | 3 |   0.24s | P=3 | pong |
| `finance_grocery_spend` | api_chat | 3 |   2.74s | P=3 | I was unable to find any financial documents matching "groceries spending last m |
| `finance_grocery_spend` | message | 3 |   3.40s | P=3 | I could not find any financial documents that show your total grocery spending f |
| `health_rhr_month` | api_chat | 3 |   6.18s | P=3 | I was unable to retrieve a resting heart rate trend for this month. The system i |
| `health_rhr_month` | message | 3 |   4.23s | P=3 | I could not find any resting heart rate data for this month. It looks like you m |
| `health_sleep_week` | api_chat | 3 |   5.69s | P=3 | I was unable to retrieve your average sleep data for last week (May 23 - May 30, |
| `health_sleep_week` | message | 3 |   4.71s | P=3 | As Kronk's health specialist, I need to let you know that I could not find any s |
| `hottub_status` | api_chat | 3 |   4.31s | P=3 | The hot tub is offline. The breaker may have tripped, and it has been offline si |
| `hottub_status` | message | 3 |   5.12s | P=3 | The hot tub is offline. The breaker may have tripped and it has been offline sin |
| `kronk_self_arch` | api_chat | 3 |  15.80s | P=3 | I have seven specialized agents available in the Kronk system. These agents hand |
| `kronk_self_arch` | message | 3 |  11.70s | P=3 | Based on the Kronk system context, I have the following specialized agents avail |
| `lookup_avgo_close` | api_chat | 3 |  36.80s | P=2 F=1 | The last closing price of AVGO (Broadcom Inc.) was **$446.77**.  This informatio |
| `lookup_avgo_close` | message | 3 |  19.25s | P=2 F=1 | <\|tool_call>call:fetch_url{url:<\|"\|>https://www.marketwatch.com/investing/sto |
| `lookup_conan_quote` | api_chat | 3 |   8.37s | S=3 | I could not find a specific quote from Conan the Barbarian about "what is best i |
| `lookup_conan_quote` | message | 3 |   8.42s | P=2 S=1 | A famous quote attributed to Conan the Barbarian is: "What is best in life? To c |
| `lookup_news_brief` | api_chat | 3 |   8.26s | P=2 F=1 | <\|tool_call>call:fetch_url{url:<\|"\|>https://abcnews.com/<\|"\|>}<tool_call\|> |
| `lookup_news_brief` | message | 3 |  13.40s | P=3 | Current news highlights the situation in Gaza, where the conflict has been linke |
| `research_voter_guide` | api_chat | 3 |  14.09s | P=3 | I was unable to find a brief summary of the candidates running for Governor of M |
| `research_voter_guide` | message | 3 |  10.47s | P=3 | Based on the current information available from the searches, there is no specif |
| `shopping_list_add` | api_chat | 3 |   4.82s | P=2 S=1 | [home agent returned no response] |
| `shopping_list_add` | message | 3 |   3.23s | P=1 S=2 | [home agent returned no response] |
| `shopping_list_view` | api_chat | 3 |   1.75s | P=3 | The shopping list currently has milk. |
| `shopping_list_view` | message | 3 |   3.79s | P=3 | The shopping list contains: milk. |
| `talkie_explicit` | api_chat | 3 |   5.02s | P=3 | I think the modern automobile is a great improvement on the old type, and will b |
| `talkie_explicit` | message | 3 |   1.76s | P=3 | I think, sir, that the modern automobile has come to stay. It is a great improve |
| `weather_default` | api_chat | 3 |   8.45s | P=3 | The weather in Laurel, MD is currently 61°F and sunny, with a wind of 15 mph fro |
| `weather_default` | message | 3 |  12.33s | P=3 | It's 72°F and Sunny today in Laurel, MD, with a high near 72. The wind will be 1 |
| `weather_specific_location` | api_chat | 3 |  18.48s | P=2 F=1 | Based on the information from AccuWeather, the forecast for Sunday, May 31st (wh |
| `weather_specific_location` | message | 3 |  14.68s | P=3 | Based on the weather forecast for tomorrow in Baltimore, MD (Sunday, May 31), th |

## Failures and suspects (full responses)

- **`weather_specific_location` / api_chat / t1** (fail: garbage tokens)
  ```
  <|tool_call>call:web_search{query:<|"|>Baltimore MD tomorrow weather forecast<|"|>}<tool_call|>
  ```
- **`shopping_list_add` / message / t0** (suspect: regex no match: (?i)(milk|added|shopping))
  ```
  [home agent returned no response]
  ```
- **`shopping_list_add` / message / t2** (suspect: regex no match: (?i)(milk|added|shopping))
  ```
  [home agent returned no response]
  ```
- **`shopping_list_add` / api_chat / t0** (suspect: regex no match: (?i)(milk|added|shopping))
  ```
  [home agent returned no response]
  ```
- **`lookup_conan_quote` / message / t2** (suspect: regex no match: (?i)(crush|enemies|driven|lamentation))
  ```
  I could not find a specific famous Conan the Barbarian quote about "what is best in life" in the search results. The results provided general information about the character Conan the Barbarian and related media.
  ```
- **`lookup_conan_quote` / api_chat / t0** (suspect: regex no match: (?i)(crush|enemies|driven|lamentation))
  ```
  I could not find a specific quote from Conan the Barbarian about "what is best in life" in the search results.
  ```
- **`lookup_conan_quote` / api_chat / t1** (suspect: regex no match: (?i)(crush|enemies|driven|lamentation))
  ```
  The search results did not provide a specific famous quote from Conan the Barbarian about what is best in life.
  ```
- **`lookup_conan_quote` / api_chat / t2** (suspect: regex no match: (?i)(crush|enemies|driven|lamentation))
  ```
  The search results did not provide a specific famous quote from Conan the Barbarian about "what is best in life." The results provided general information about the character, such as that he was created by Robert E. Howard, and mentioned various modern adaptations, games, and software packages related to the name Conan.
  ```
- **`lookup_avgo_close` / message / t0** (fail: garbage tokens)
  ```
  <|tool_call>call:fetch_url{url:<|"|>https://www.marketwatch.com/investing/stock/avgo<|"|>}<tool_call|>
  ```
- **`lookup_avgo_close` / api_chat / t1** (fail: garbage tokens)
  ```
  <|tool_call>call:fetch_url{url:<|"|>https://www.marketwatch.com/investing/stock/avgo<|"|>}<tool_call|>
  ```
- **`lookup_news_brief` / api_chat / t0** (fail: garbage tokens)
  ```
  <|tool_call>call:fetch_url{url:<|"|>https://abcnews.com/<|"|>}<tool_call|>
  ```
