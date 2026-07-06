# Investigation 2026-07-05 — "what is tomorrow's forecast?" misroute + tool-budget exhaustion

**Symptom (as the user saw it):** asked the chat UI for tomorrow's forecast;
got "I could not look up tomorrow's forecast because the tool budget for this
question has been exhausted, and no weather data was available in the
previous conversation context."

**Trace:** Langfuse `451ad1062e2d66e87a39b7bbdcbc3f34` (2026-07-06T00:35 UTC,
rid `11ad7a9a`, transport webui). System was running `main` (pre
`p0-correctness-fixes`).

## Timeline / what the trace shows

1. `routing.decide` → `rule: "llm"` (no shortcut fired), route **research**.
   The router LLM (gemma-3-4b) chose research despite "forecast" appearing
   verbatim in home's routing hint. Contributing pulls: research's expansive
   hint ("any factual lookup where verbatim precision matters…") and prior
   music-related history in the router window.
2. `agent.research` then spent **all five tool rounds on near-identical
   `web_search` calls** — "Laurel MD weather forecast for tomorrow" reworded
   five ways. The exact-duplicate dedup (`seen_calls`) never fired because
   the args differed each time. It never called `fetch_url` on any result,
   despite its prompt explicitly instructing exactly that.
3. Forced synthesis (the 2026-06-12 budget-cliff guardrail) then produced an
   honest refusal — correct behavior given the context it had; the search
   snippets contained links but no actual forecast data.

## Hypotheses considered

- **"Increase the research tool budget" (operator's initial theory):**
  evidence is against this as the fix for *this* failure — round 5's search
  was the same as round 1's; three more rounds would have bought three more
  searches. Budget was raised anyway (5 → 8, cheap and useful for genuinely
  multi-part questions) but only alongside the repeat-call guardrail below.
- **"Fix the prompts":** both prompts were already correct (home's hint says
  "forecast"; research's prompt says "then call fetch_url on the single most
  relevant URL"). The models ignored them. Tenet 5: change the loop, not the
  prompt.

## Root causes

1. **Router:** 4B LLM classification is unreliable for weather phrasings
   that omit the word "weather" — and weather had no deterministic shortcut,
   unlike URLs and explicit search phrases.
2. **Agent loop:** nothing structural stopped a model from re-issuing the
   same tool with reworded args until the budget died.

## Fixes (branch `p0-correctness-fixes`)

1. `routing.py`: deterministic `_WEATHER_RE` (`weather|forecast`) → `home`,
   checked **after** `_SEARCH_PHRASES` so "look up the weather in Tokyo"
   still reaches research (NWS is US-only; that pinned behavior is why the
   ordering matters). Known accepted limitation: "AMD's revenue forecast"
   now also lands on home — pinned in `test_routing_shortcuts.py`.
2. `agents.py`: repeat-call guardrail — the Nth call (N=3,
   `REPEAT_TOOL_NUDGE_AT`) to the same tool in one turn gets a stop order
   appended to its tool result ("Do not call web_search again — answer from
   the results above, or use a different tool"). Structural sibling of
   terminal tools and the budget-cliff closure message.
3. `agents.py`: research `max_rounds` 5 → 8 via `RESEARCH_MAX_ROUNDS`, now a
   single source shared by the config and the budget stated in the prompt
   (they were two hardcoded literals before — tenet 8).

Tests: 11 new (`test_routing_shortcuts.py` weather pins,
`test_agentic_loop.py` deterministic-route + ordering + nudge +
budget-single-source). Suite 149 passed post-fix.

## What would have caught it sooner

- The planned voice regression smoke test (ROADMAP item 8) includes a
  weather utterance with a tier assertion — it would have caught the
  misroute class on the first run. This incident adds the case "weather
  phrasing *without* the word weather" to its battery.
- A router-classification eval set (utterance → expected route, run against
  the live router model on update day) would catch hint/model drift
  generally. Not built; noted for the item-8/9 design.

## Open questions

- Whether gemma-4-e4b respects the stop-nudge reliably — verify on deploy
  by re-asking a forecast question with the home agent disabled (or watch
  the next organic research query in Langfuse for repeated-search behavior).
- Whether "rain/snow/temperature" phrasings misroute too. Deliberately NOT
  added to the regex yet (single-variable changes); widen only on evidence.
