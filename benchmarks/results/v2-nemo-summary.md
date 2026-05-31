# v2-coord-nemo-20260530-164920 — `v2-coord-nemo-20260530-164920.jsonl`

_120 trials across 20 queries_

## Aggregate timing by endpoint

| endpoint | n queries | p50 | p95 | min | max |
|---|---|---|---|---|---|
| `api_chat` | 20 | 8.63s | 36.28s | 0.05s | 240.10s |
| `message` | 20 | 4.14s | 33.23s | 0.33s | 37.68s |

## Quality flag distribution

| flag | count | pct |
|---|---|---|
| pass | 88 | 73.3% |
| suspect | 14 | 11.7% |
| fail | 18 | 15.0% |
| unknown | 0 | 0.0% |

## Per-query results

| query_id | endpoint | n | duration p50 | quality | sample response |
|---|---|---|---|---|---|
| `ambig_cellulitis` | api_chat | 3 |  21.60s | P=3 | Cellulitis is a common bacterial skin infection that affects the deeper layers o |
| `ambig_cellulitis` | message | 3 |   4.35s | P=3 | Cellulitis is a common bacterial skin infection that causes skin redness, swelli |
| `ambig_health_general` | api_chat | 3 |   7.35s | P=3 | I couldn't find any health data for the last 30 days. Would you like me to check |
| `ambig_health_general` | message | 3 |   2.19s | F=3 | <unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49> |
| `code_email_validator` | api_chat | 3 |  23.15s | P=3 | Here is a simple Python function to validate an email address using regular expr |
| `code_email_validator` | message | 3 |  19.64s | P=3 | Here's a Python function to validate an email address using regular expressions: |
| `cross_weather_run` | api_chat | 3 |   0.60s | S=3 | [coordinator fast-path synth error: LiteLLM returned 500: {"error":{"message":"l |
| `cross_weather_run` | message | 3 |  12.24s | P=3 | Your current shopping list contains: milk  Considering the weather tomorrow and  |
| `direct_math` | api_chat | 3 |   1.06s | P=3 | The result of 47 times 8 is 376. |
| `direct_math` | message | 3 |   0.40s | P=3 | 376 |
| `direct_pong` | api_chat | 3 |   0.24s | P=3 | Pong. |
| `direct_pong` | message | 3 |   0.33s | P=3 | pong |
| `finance_grocery_spend` | api_chat | 3 |   7.87s | P=3 | Sorry, I couldn't find any information about your grocery expenses last month in |
| `finance_grocery_spend` | message | 3 |   2.48s | P=3 | I am sorry forms unable to find specific data regarding your grocery spending fo |
| `health_rhr_month` | api_chat | 3 |   9.39s | P=3 | I couldn't find your resting heart rate data for this month. Would you like to c |
| `health_rhr_month` | message | 3 |   7.91s | P=3 | I am unable to show a resting heart rate trend for this month because no data wa |
| `health_sleep_week` | api_chat | 3 |   7.88s | P=3 | I couldn't find your sleep data for last week. Would you like to add a reminder  |
| `health_sleep_week` | message | 3 |   6.42s | P=3 | I was unable to retrieve your average sleep for last week. The system reports no |
| `hottub_status` | api_chat | 3 |   0.05s | S=3 | [coordinator fast-path synth error: LiteLLM returned 500: {"error":{"message":"l |
| `hottub_status` | message | 3 |   6.10s | P=3 | Hi there! The hot tub is currently offline, and it seems like the breaker might  |
| `kronk_self_arch` | api_chat | 3 |  13.33s | P=3 | I have agents for the following tasks:  - get_weather: Get current weather and f |
| `kronk_self_arch` | message | 3 |  37.68s | P=1 S=2 | Here is the diagram of the kronk system architecture: ![diagram](/static/generat |
| `lookup_avgo_close` | api_chat | 3 |  10.72s | P=3 | The last closing price of AVGO was $472.8. |
| `lookup_avgo_close` | message | 3 |   2.16s | F=3 | <unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49> |
| `lookup_conan_quote` | api_chat | 3 |  11.96s | P=3 | The iconic quote from Conan the Barbarian, spoken by the title character, is:  > |
| `lookup_conan_quote` | message | 3 |   2.15s | F=3 | <unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49> |
| `lookup_news_brief` | api_chat | 3 |  11.38s | P=3 | Based on the top news articles, here's a two-sentence summary:  - The top news s |
| `lookup_news_brief` | message | 3 |   2.18s | F=3 | <unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49> |
| `research_voter_guide` | api_chat | 3 |  36.28s | P=3 | **Maryland Governor Election 2026: Housing and Transit Positions**  **Democratic |
| `research_voter_guide` | message | 3 |   2.18s | F=3 | <unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49> |
| `shopping_list_add` | api_chat | 3 |   6.50s | P=3 | I've added milk to your shopping list. Is there anything else you need? |
| `shopping_list_add` | message | 3 |   3.93s | P=3 | Item added! What else can I assist you with today? |
| `shopping_list_view` | api_chat | 3 |   0.05s | S=3 | [coordinator fast-path synth error: LiteLLM returned 500: {"error":{"message":"l |
| `shopping_list_view` | message | 3 |   5.46s | P=3 | The shopping list currently has: - milk |
| `talkie_explicit` | api_chat | 3 | 240.10s | F=3 |  |
| `talkie_explicit` | message | 3 |   2.98s | P=3 | I think modern automobiles are very fine things. They enable a man to get quickl |
| `weather_default` | api_chat | 3 |   0.60s | S=3 | [coordinator fast-path synth error: LiteLLM returned 500: {"error":{"message":"l |
| `weather_default` | message | 3 |  33.23s | P=3 | Here's the weather in Laurel, MD:  **Current conditions:** 70°F, Sunny, wind 9 m |
| `weather_specific_location` | api_chat | 3 |  21.41s | P=3 | Here's the current weather and forecast for Baltimore, MD:  ### Current conditio |
| `weather_specific_location` | message | 3 |  18.43s | P=3 | Based on the forecast from AccuWeather, tomorrow (Sunday, May 31st) in Baltimore |

## Failures and suspects (full responses)

- **`weather_default` / api_chat / t0** (suspect: regex no match: (?i)(°|degree|temp|rain|cloud|sun|wind|forecast))
  ```
  [coordinator fast-path synth error: LiteLLM returned 500: {"error":{"message":"litellm.InternalServerError: InternalServerError: OpenAIException - \n------------\nWhile executing CallExpression at line 81, column 32 in source:\n... != 9 %}↵            {{- raise_exception(\"Tool call IDs should be alphanumeric s...\n                                           ^\nError: Jinja Exception: Tool call IDs should be alphanumeric strings with length 9!. Received Model Group=mistral-nemo\nAvailable Model Group Fallbacks=None","type":null,"param":null,"code":"500"]
  ```
- **`weather_default` / api_chat / t1** (suspect: regex no match: (?i)(°|degree|temp|rain|cloud|sun|wind|forecast))
  ```
  [coordinator fast-path synth error: LiteLLM returned 500: {"error":{"message":"litellm.InternalServerError: InternalServerError: OpenAIException - \n------------\nWhile executing CallExpression at line 81, column 32 in source:\n... != 9 %}↵            {{- raise_exception(\"Tool call IDs should be alphanumeric s...\n                                           ^\nError: Jinja Exception: Tool call IDs should be alphanumeric strings with length 9!. Received Model Group=mistral-nemo\nAvailable Model Group Fallbacks=None","type":null,"param":null,"code":"500"]
  ```
- **`weather_default` / api_chat / t2** (suspect: regex no match: (?i)(°|degree|temp|rain|cloud|sun|wind|forecast))
  ```
  [coordinator fast-path synth error: LiteLLM returned 500: {"error":{"message":"litellm.InternalServerError: InternalServerError: OpenAIException - \n------------\nWhile executing CallExpression at line 81, column 32 in source:\n... != 9 %}↵            {{- raise_exception(\"Tool call IDs should be alphanumeric s...\n                                           ^\nError: Jinja Exception: Tool call IDs should be alphanumeric strings with length 9!. Received Model Group=mistral-nemo\nAvailable Model Group Fallbacks=None","type":null,"param":null,"code":"500"]
  ```
- **`shopping_list_view` / api_chat / t0** (suspect: regex no match: (?i)(shopping|list|empty|items|nothing))
  ```
  [coordinator fast-path synth error: LiteLLM returned 500: {"error":{"message":"litellm.InternalServerError: InternalServerError: OpenAIException - \n------------\nWhile executing CallExpression at line 81, column 32 in source:\n... != 9 %}↵            {{- raise_exception(\"Tool call IDs should be alphanumeric s...\n                                           ^\nError: Jinja Exception: Tool call IDs should be alphanumeric strings with length 9!. Received Model Group=mistral-nemo\nAvailable Model Group Fallbacks=None","type":null,"param":null,"code":"500"]
  ```
- **`shopping_list_view` / api_chat / t1** (suspect: regex no match: (?i)(shopping|list|empty|items|nothing))
  ```
  [coordinator fast-path synth error: LiteLLM returned 500: {"error":{"message":"litellm.InternalServerError: InternalServerError: OpenAIException - \n------------\nWhile executing CallExpression at line 81, column 32 in source:\n... != 9 %}↵            {{- raise_exception(\"Tool call IDs should be alphanumeric s...\n                                           ^\nError: Jinja Exception: Tool call IDs should be alphanumeric strings with length 9!. Received Model Group=mistral-nemo\nAvailable Model Group Fallbacks=None","type":null,"param":null,"code":"500"]
  ```
- **`shopping_list_view` / api_chat / t2** (suspect: regex no match: (?i)(shopping|list|empty|items|nothing))
  ```
  [coordinator fast-path synth error: LiteLLM returned 500: {"error":{"message":"litellm.InternalServerError: InternalServerError: OpenAIException - \n------------\nWhile executing CallExpression at line 81, column 32 in source:\n... != 9 %}↵            {{- raise_exception(\"Tool call IDs should be alphanumeric s...\n                                           ^\nError: Jinja Exception: Tool call IDs should be alphanumeric strings with length 9!. Received Model Group=mistral-nemo\nAvailable Model Group Fallbacks=None","type":null,"param":null,"code":"500"]
  ```
- **`hottub_status` / api_chat / t0** (suspect: regex no match: (?i)(hot.tub|spa|online|offline|temp))
  ```
  [coordinator fast-path synth error: LiteLLM returned 500: {"error":{"message":"litellm.InternalServerError: InternalServerError: OpenAIException - \n------------\nWhile executing CallExpression at line 81, column 32 in source:\n... != 9 %}↵            {{- raise_exception(\"Tool call IDs should be alphanumeric s...\n                                           ^\nError: Jinja Exception: Tool call IDs should be alphanumeric strings with length 9!. Received Model Group=mistral-nemo\nAvailable Model Group Fallbacks=None","type":null,"param":null,"code":"500"]
  ```
- **`hottub_status` / api_chat / t1** (suspect: regex no match: (?i)(hot.tub|spa|online|offline|temp))
  ```
  [coordinator fast-path synth error: LiteLLM returned 500: {"error":{"message":"litellm.InternalServerError: InternalServerError: OpenAIException - \n------------\nWhile executing CallExpression at line 81, column 32 in source:\n... != 9 %}↵            {{- raise_exception(\"Tool call IDs should be alphanumeric s...\n                                           ^\nError: Jinja Exception: Tool call IDs should be alphanumeric strings with length 9!. Received Model Group=mistral-nemo\nAvailable Model Group Fallbacks=None","type":null,"param":null,"code":"500"]
  ```
- **`hottub_status` / api_chat / t2** (suspect: regex no match: (?i)(hot.tub|spa|online|offline|temp))
  ```
  [coordinator fast-path synth error: LiteLLM returned 500: {"error":{"message":"litellm.InternalServerError: InternalServerError: OpenAIException - \n------------\nWhile executing CallExpression at line 81, column 32 in source:\n... != 9 %}↵            {{- raise_exception(\"Tool call IDs should be alphanumeric s...\n                                           ^\nError: Jinja Exception: Tool call IDs should be alphanumeric strings with length 9!. Received Model Group=mistral-nemo\nAvailable Model Group Fallbacks=None","type":null,"param":null,"code":"500"]
  ```
- **`kronk_self_arch` / message / t0** (suspect: regex no match: (?i)(agent|home|research|health|finance|coord))
  ```
  Here is the diagram of the kronk system architecture: ![diagram](/static/generated/diagram-55c9640e.png)
  ```
- **`kronk_self_arch` / message / t2** (suspect: regex no match: (?i)(agent|home|research|health|finance|coord))
  ```
  Here is your Kronk system diagram: ![diagram](/static/generated/diagram-e5ec24d4.png)
  
  This diagram shows how the user interacts with the system, how the nginx server acts as a reverse proxy, and how the orchestrator communicates with the litellm server to access the llama.cpp models.
  ```
- **`lookup_conan_quote` / message / t0** (fail: garbage tokens)
  ```
  <unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49>
  ```
- **`lookup_conan_quote` / message / t1** (fail: garbage tokens)
  ```
  <unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49>
  ```
- **`lookup_conan_quote` / message / t2** (fail: garbage tokens)
  ```
  <unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49>
  ```
- **`lookup_avgo_close` / message / t0** (fail: garbage tokens)
  ```
  <unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49>
  ```
- **`lookup_avgo_close` / message / t1** (fail: garbage tokens)
  ```
  <unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49>
  ```
- **`lookup_avgo_close` / message / t2** (fail: garbage tokens)
  ```
  <unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49>
  ```
- **`lookup_news_brief` / message / t0** (fail: garbage tokens)
  ```
  <unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49>
  ```
- **`lookup_news_brief` / message / t1** (fail: garbage tokens)
  ```
  <unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49>
  ```
- **`lookup_news_brief` / message / t2** (fail: garbage tokens)
  ```
  <unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49>
  ```
- **`research_voter_guide` / message / t0** (fail: garbage tokens)
  ```
  <unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49>
  ```
- **`research_voter_guide` / message / t1** (fail: garbage tokens)
  ```
  <unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49>
  ```
- **`research_voter_guide` / message / t2** (fail: garbage tokens)
  ```
  <unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49>
  ```
- **`talkie_explicit` / api_chat / t0** (fail: error: ReadTimeout: )
  *error: ReadTimeout: *
- **`talkie_explicit` / api_chat / t1** (fail: error: ReadTimeout: )
  *error: ReadTimeout: *
- **`talkie_explicit` / api_chat / t2** (fail: error: ReadTimeout: )
  *error: ReadTimeout: *
- **`cross_weather_run` / api_chat / t0** (suspect: regex no match: (?i)(weather|rain|sleep|run|outside|recommend))
  ```
  [coordinator fast-path synth error: LiteLLM returned 500: {"error":{"message":"litellm.InternalServerError: InternalServerError: OpenAIException - \n------------\nWhile executing CallExpression at line 81, column 32 in source:\n... != 9 %}↵            {{- raise_exception(\"Tool call IDs should be alphanumeric s...\n                                           ^\nError: Jinja Exception: Tool call IDs should be alphanumeric strings with length 9!. Received Model Group=mistral-nemo\nAvailable Model Group Fallbacks=None","type":null,"param":null,"code":"500"]
  ```
- **`cross_weather_run` / api_chat / t1** (suspect: regex no match: (?i)(weather|rain|sleep|run|outside|recommend))
  ```
  [coordinator fast-path synth error: LiteLLM returned 500: {"error":{"message":"litellm.InternalServerError: InternalServerError: OpenAIException - \n------------\nWhile executing CallExpression at line 81, column 32 in source:\n... != 9 %}↵            {{- raise_exception(\"Tool call IDs should be alphanumeric s...\n                                           ^\nError: Jinja Exception: Tool call IDs should be alphanumeric strings with length 9!. Received Model Group=mistral-nemo\nAvailable Model Group Fallbacks=None","type":null,"param":null,"code":"500"]
  ```
- **`cross_weather_run` / api_chat / t2** (suspect: regex no match: (?i)(weather|rain|sleep|run|outside|recommend))
  ```
  [coordinator fast-path synth error: LiteLLM returned 500: {"error":{"message":"litellm.InternalServerError: InternalServerError: OpenAIException - \n------------\nWhile executing CallExpression at line 81, column 32 in source:\n... != 9 %}↵            {{- raise_exception(\"Tool call IDs should be alphanumeric s...\n                                           ^\nError: Jinja Exception: Tool call IDs should be alphanumeric strings with length 9!. Received Model Group=mistral-nemo\nAvailable Model Group Fallbacks=None","type":null,"param":null,"code":"500"]
  ```
- **`ambig_health_general` / message / t0** (fail: garbage tokens)
  ```
  <unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49>
  ```
- **`ambig_health_general` / message / t1** (fail: garbage tokens)
  ```
  <unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49>
  ```
- **`ambig_health_general` / message / t2** (fail: garbage tokens)
  ```
  <unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49><unused49>
  ```
