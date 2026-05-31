# v2-coord-nemo-nofp-20260530-172438 — `v2-coord-nemo-nofp-20260530-172438.jsonl`

_120 trials across 20 queries_

## Aggregate timing by endpoint

| endpoint | n queries | p50 | p95 | min | max |
|---|---|---|---|---|---|
| `api_chat` | 20 | 12.71s | 54.05s | 0.22s | 240.06s |
| `message` | 20 | 7.89s | 25.60s | 0.32s | 49.32s |

## Quality flag distribution

| flag | count | pct |
|---|---|---|
| pass | 108 | 90.0% |
| suspect | 8 | 6.7% |
| fail | 4 | 3.3% |
| unknown | 0 | 0.0% |

## Per-query results

| query_id | endpoint | n | duration p50 | quality | sample response |
|---|---|---|---|---|---|
| `ambig_cellulitis` | api_chat | 3 |  27.78s | P=3 | Here's what I found about cellulitis:  **Cellulitis** is a spreading skin infect |
| `ambig_cellulitis` | message | 3 |   5.72s | P=3 | Cellulitis is a common bacterial skin infection that causes redness, swelling, a |
| `ambig_health_general` | api_chat | 3 |  16.33s | P=2 S=1 | I don't have any fitness data from the last month. Do you want me to search for  |
| `ambig_health_general` | message | 3 |   8.65s | P=3 | I was unable to retrieve a comprehensive overview of your fitness goals, as no " |
| `code_email_validator` | api_chat | 3 |  54.05s | P=3 | I've found a simple Python function to validate an email address using regular e |
| `code_email_validator` | message | 3 |  20.71s | P=3 | Here's a Python function to validate an email address using regular expressions. |
| `cross_weather_run` | api_chat | 3 |  26.52s | P=3 | I'm sorry, but I couldn't retrieve your sleep data for the past week. It seems l |
| `cross_weather_run` | message | 3 |  13.42s | P=3 | The weather in Laurel, MD over the next 24 hours is predicted to be mostly clear |
| `direct_math` | api_chat | 3 |   1.04s | P=3 | The answer is 376. |
| `direct_math` | message | 3 |   0.37s | P=3 | 376 |
| `direct_pong` | api_chat | 3 |   0.22s | P=3 | Pong |
| `direct_pong` | message | 3 |   0.32s | P=3 | Pong |
| `finance_grocery_spend` | api_chat | 3 |   5.89s | P=3 | Did you upload any bank statements? I couldn't find any grocery spending from la |
| `finance_grocery_spend` | message | 3 |   2.21s | P=3 | I'm sorryich sorrylin' sorry I couldn't find any documents matching "groceries s |
| `health_rhr_month` | api_chat | 3 |   7.92s | P=3 | It looks like I don't have your resting heart rate data for this month. Would yo |
| `health_rhr_month` | message | 3 |   6.05s | P=3 | I was unable to retrieve your resting heart rate trend for this month. The syste |
| `health_sleep_week` | api_chat | 3 |   7.95s | P=3 | I couldn't find your sleep data for last week. It might be because you didn't sy |
| `health_sleep_week` | message | 3 |   6.81s | P=3 | Based on the data retrieved, there is no sleep data found for the last week (May |
| `hottub_status` | api_chat | 3 |   8.73s | P=3 | The hot tub is offline. The breaker might have tripped. It was last seen on May  |
| `hottub_status` | message | 3 |   7.85s | P=3 | The hot tub is currently offline. It was last seen on May 28th at 7:24 AM and ha |
| `kronk_self_arch` | api_chat | 3 |   5.30s | P=3 | I have the following agents available:  - Get Weather - Web Search - Fetch URL - |
| `kronk_self_arch` | message | 3 |  49.32s | P=1 S=2 | Here's a diagram of the system architecture:  ![diagram](/static/generated/diagr |
| `lookup_avgo_close` | api_chat | 3 |   6.63s | P=3 | The last closing price of AVGO was $450. |
| `lookup_avgo_close` | message | 3 |  19.26s | P=1 F=2 | <\|tool_call>call:web_search{query:<\|"\|>AVGO last closing price<\|"\|>}<tool_c |
| `lookup_conan_quote` | api_chat | 3 |   9.09s | P=3 | To crush your enemies, see them driven before you, and to hear the lamentations  |
| `lookup_conan_quote` | message | 3 |   7.94s | P=1 S=2 | The famous quote from *Conan the Barbarian* about what is best in life is:  **"T |
| `lookup_news_brief` | api_chat | 3 |  17.07s | P=3 | Here are some of today's top news stories:  * **Politics**: The Justice Departme |
| `lookup_news_brief` | message | 3 |  11.42s | P=3 | The search results provided do not contain information about today's top news fo |
| `research_voter_guide` | api_chat | 3 |  21.45s | P=2 S=1 | Here's what I found about the candidates running for governor of Maryland in 202 |
| `research_voter_guide` | message | 3 |  11.72s | P=3 | I am unable to compile a brief summary of the candidates running for governor of |
| `shopping_list_add` | api_chat | 3 | 240.06s | P=1 F=2 | I've added milk to your shopping list. Here's what you have now:  - milk |
| `shopping_list_add` | message | 3 |   4.17s | P=3 | Milk has been added to your shopping list. How else can I help you? |
| `shopping_list_view` | api_chat | 3 |   5.69s | P=3 | Here is your shopping list:  - milk |
| `shopping_list_view` | message | 3 |   3.08s | P=3 | The current shopping list has: milk |
| `talkie_explicit` | api_chat | 3 |  17.33s | P=3 | It seems that there is no information available regarding Talkie's opinion on mo |
| `talkie_explicit` | message | 3 |   4.49s | P=3 | I think, sir, that the modern automobile has become a very efficient and reliabl |
| `weather_default` | api_chat | 3 |  26.55s | P=3 | The current temperature in Laurel, MD, is 70°F, and it's sunny with a wind speed |
| `weather_default` | message | 3 |  16.93s | P=2 S=1 | Sorry, I couldn't retrieve the weather information right now. Could you please t |
| `weather_specific_location` | api_chat | 3 |  16.76s | P=3 | Here's the current weather and forecast for Baltimore, MD:  **Current conditions |
| `weather_specific_location` | message | 3 |  25.60s | P=2 S=1 | Based on the weather forecast for Baltimore, MD, on Sunday, May 31, the probabil |

## Failures and suspects (full responses)

- **`weather_default` / message / t0** (suspect: regex no match: (?i)(°|degree|temp|rain|cloud|sun|wind|forecast))
  ```
  Sorry, I couldn't retrieve the weather information right now. Could you please try again later?
  ```
- **`weather_specific_location` / message / t2** (suspect: regex no match: (?i)(rain|shower|cloud|dry|precipitation|°|degree))
  ```
  I was unable to retrieve the specific weather details for tomorrow in Baltimore, Maryland, from the available sources.
  ```
- **`shopping_list_add` / api_chat / t1** (fail: error: ReadTimeout: )
  *error: ReadTimeout: *
- **`shopping_list_add` / api_chat / t2** (fail: error: ReadTimeout: )
  *error: ReadTimeout: *
- **`kronk_self_arch` / message / t1** (suspect: regex no match: (?i)(agent|home|research|health|finance|coord))
  ```
  I have generated an architecture diagram for you:
  
  ![diagram](/static/generated/diagram-de29518e.png)
  ```
- **`kronk_self_arch` / message / t2** (suspect: regex no match: (?i)(agent|home|research|health|finance|coord))
  ```
  The diagram was generated successfully. Here it is below:
  
  ![diagram](/static/generated/diagram-b36226e3.png)
  ```
- **`lookup_conan_quote` / message / t1** (suspect: regex no match: (?i)(crush|enemies|driven|lamentation))
  ```
  The search results do not contain the specific famous Conan the Barbarian quote about what is best in life.
  ```
- **`lookup_conan_quote` / message / t2** (suspect: regex no match: (?i)(crush|enemies|driven|lamentation))
  ```
  I was unable to find the specific famous Conan the Barbarian quote about what is best in life in the available tool results.
  ```
- **`lookup_avgo_close` / message / t0** (fail: garbage tokens)
  ```
  <|tool_call>call:web_search{query:<|"|>AVGO last closing price<|"|>}<tool_call|>
  ```
- **`lookup_avgo_close` / message / t1** (fail: garbage tokens)
  ```
  <|tool_call>call:fetch_url{url:<|"|>https://finance.yahoo.com/quote/AVGO/<|"|>}<tool_call|>
  ```
- **`research_voter_guide` / api_chat / t1** (suspect: regex no match: (?i)(candidate|governor|maryland|hous|transit))
  ```
  I apologize for the inconvenience.
  ```
- **`ambig_health_general` / api_chat / t1** (suspect: regex no match: (?i)(fitness|sleep|step|heart|activity|health|data))
  ```
  Here is the weather for today and the next few days in Baltimore, MD.
  
  **Today:**
  
  - Temperature: 74°F (23°C)
  - Conditions: Partly cloudy
  - Humidity: 65%
  - Wind: 7 mph (11 km/h) north
  
  **Tomorrow:**
  
  - Temperature: 78°F (26°C)
  - Conditions: Mostly sunny
  - Humidity: 60%
  - Wind: 8 mph (13 km/h) north
  
  **Day after tomorrow:**
  
  - Temperature: 82°F (28°C)
  - Conditions: Sunny
  - Humidity: 55%
  - Wind: 9 mph (14 km/h) north
  
  Would you like to add any of these days to your calendar?
  ```
