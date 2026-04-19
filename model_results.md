# Kronk model benchmark

_Last updated: 2026-04-06 21:38_

## Summary

| Model | Avg TTFT | Avg generation | Theatrical flags |
|---|---|---|---|
| `mistral-nemo:12b` | 0.17s | 2.33s | 0 / 7 |
| `devstral:24b` | 0.63s | 7.88s | 1 / 7 |

## Per-prompt timing

_TTFT / generation (seconds)_

| Model | factual | weather | theatrical | code | reasoning | math | code_agent |
|---|---|---|---|---|---|---|---|
| `mistral-nemo:12b` | 0.30s / 0.37s | 0.10s / 1.66s | 0.10s / 0.40s | 0.11s / 2.79s | 0.17s / 1.40s | 0.12s / 0.10s | 0.28s / 9.61s |
| `devstral:24b` | 2.77s / 0.74s | 0.20s / 2.19s | 0.21s / 0.80s | 0.20s / 5.21s | 0.38s / 10.06s | 0.24s / 1.08s | 0.41s / 35.05s |

## Full responses

### Basic factual

**Prompt:** What year did World War II end?

**`mistral-nemo:12b`** — ttft 0.30s · gen 0.37s

> World War II ended in 1945.

**`devstral:24b`** — ttft 2.77s · gen 0.74s

> World War II ended in 1945.

### Tool use (weather)

**Prompt:** What's the weather like?

**`mistral-nemo:12b`** — ttft 0.10s · gen 1.66s

> Current temperature: 72°F (22.2°C). Weather conditions: Partly cloudy with a light breeze. Forecast for tomorrow: Mostly sunny, high of 81°F (27.2°C).

**`devstral:24b`** — ttft 0.20s · gen 2.19s

> The current weather is sunny with a high of 75°F (24°C) and a low of 60°F (15°C).

### Instruction following / theatrical

**Prompt:** Tell me a joke.

**`mistral-nemo:12b`** — ttft 0.10s · gen 0.40s

> What do you call a fake noodle? An impasta

**`devstral:24b`** — ttft 0.21s · gen 0.80s

> What do you call fake spaghetti? An impasta

### Code generation

**Prompt:** Write a Python function that checks if a string is a palindrome.

**`mistral-nemo:12b`** — ttft 0.11s · gen 2.79s

> Here's a Python function that checks if a given string is a palindrome:
> 
> ```python
> def is_palindrome(s: str) -> bool:
>     return s == s[::-1]
> ```
> 
> You can use it like this:
> 
> ```python
> print(is_palindrome("racecar"))  # Outputs: True
> print(is_palindrome("hello"))   # Outputs: False
> ```

**`devstral:24b`** — ttft 0.20s · gen 5.21s

> ```python
> def is_palindrome(s):
>     return s == s[::-1]
> ```
> 
> This function takes a string `s` as input and returns `True` if the string is a palindrome, and `False` otherwise. The slicing operation `s[::-1]` creates a reversed version of the string, which is then compared to the original string.

### Practical multi-step reasoning

**Prompt:** I'm driving from Laurel to New York tomorrow. What should I think about before leaving?

**`mistral-nemo:12b`** — ttft 0.17s · gen 1.40s

> Check traffic conditions on route. Plan fuel stops. Consider rest areas for breaks. Pack necessary items: maps/GPS, phone charger, snacks, water. Inform someone of your travel plans and estimated arrival time.

**`devstral:24b`** — ttft 0.38s · gen 10.06s ⚠️ theatrical language detected

> Before leaving:
> 
> 1. **Route Planning**: Check the best route and traffic conditions. Consider using a navigation app.
> 2. **Vehicle Check**: Ensure your car is in good condition: check tires, fluids, brakes, and lights.
> 3. **Supplies**: Pack water, snacks, emergency kit, and any necessary documents (ID, insurance, etc.).
> 4. **Weather**: Be aware of the forecasted weather conditions for the duration of your trip.
> 5. **Rest Stops**: Plan rest stops along the way to avoid fatigue.
> 6. **Tolls**: Be prepared for toll roads with cash or a compatible transponder.
> 7. **Communication**: Ensure your phone is fully charged and you have a car charger.
> 
> Safe travels!

### Math

**Prompt:** What is 17 times 38?

**`mistral-nemo:12b`** — ttft 0.12s · gen 0.10s

> 646

**`devstral:24b`** — ttft 0.24s · gen 1.08s

> The product of 17 and 38 is 646.

### Coding agent task

**Prompt:** I have a FastAPI app. Add a POST /summarize endpoint that accepts {"text": "...", "max_sentences": 3} and returns {"summary": "..."}. Implement the summarization by extracting the first N sentences. Include input validation and a test using httpx.AsyncClient.

**`mistral-nemo:12b`** — ttft 0.28s · gen 9.61s

> Here's how you can add the `/summarize` endpoint to your FastAPI app with input validation and a simple extract-based summarization:
> 
> ```python
> from fastapi import FastAPI, HTTPException
> from pydantic import BaseModel
> from typing import Optional
> 
> app = FastAPI()
> 
> class SummarizeRequest(BaseModel):
>     text: str
>     max_sentences: Optional[int] = 3
> 
> @app.post("/summarize")
> async def summarize(req: SummarizeRequest):
>     if req.max_sentences <= 0:
>         raise HTTPException(status_code=400, detail="Max sentences must be a positive integer")
> 
>     sentences = req.text.split('. ')
>     summary = '\n'.join(sentences[:req.max_sentences])
> 
>     return {"summary": summary}
> 
> import httpx
> async with httpx.AsyncClient() as client:
>     response = await client.post("http://localhost:8000/summarize", json={"text": "This is a test. This is only a test."})
>     print(response.json())
> ```
> 
> In this example, the `summarize` function extracts the first `max_sentences` sentences from the input text and returns them as a summary. The input validation checks that `max_sentences` is a positive integer.
> 
> The last part of the code is an example test using `httpx.AsyncClient`.

**`devstral:24b`** — ttft 0.41s · gen 35.05s

> To add a `POST /summarize` endpoint to your FastAPI app, follow these steps:
> 
> 1. **Install necessary dependencies**:
>    ```bash
>    pip install fastapi httpx
>    ```
> 
> 2. **Create the FastAPI app with the new endpoint**:
> 
>    ```python
>    from typing import Any
>    from pydantic import BaseModel
>    from fastapi import FastAPI, HTTPException
> 
>    app = FastAPI()
> 
>    class SummarizeRequest(BaseModel):
>        text: str
>        max_sentences: int
> 
>    def summarize_text(text: str, max_sentences: int) -> str:
>        sentences = text.split('. ')
>        summary_sentences = '. '.join(sentences[:max_sentences])
>        return summary_sentences + '.' if summary_sentences else ""
> 
>    @app.post("/summarize", response_model=dict)
>    async def summarize(request: SummarizeRequest) -> dict[str, str]:
>        if not request.text:
>            raise HTTPException(status_code=400, detail="Text is required.")
>        if not (1 <= request.max_sentences <= len(request.text.split('. '))):
>            raise HTTPException(status_code=400, detail="Invalid max_sentences value.")
> 
>        summary = summarize_text(request.text, request.max_sentences)
>        return {"summary": summary}
> 
>    if __name__ == "__main__":
>        import uvicorn
>        uvicorn.run(app, host="127.0.0.1", port=8000)
>    ```
> 
> 3. **Create a test using `httpx.AsyncClient`**:
> 
>    ```python
>    import pytest
>    from httpx import AsyncClient
> 
>    @pytest.mark.asyncio
>    async def test_summarize():
>        async with AsyncClient(app=app, base_url="http://test") as ac:
>            response = await ac.post("/summarize", json={
>                "text": "This is a sentence. This is another sentence. Here is the third one.",
>                "max_sentences": 2
>            })
>            assert response.status_code == 200
>            assert response.json() == {"summary": "This is a sentence. This is another sentence."}
> 
>    if __name__ == "__main__":
>        pytest.main([__file__])
>    ```
> 
> This code defines the FastAPI app with input validation and includes a test using `httpx.AsyncClient`. The summarization function extracts the first N sentences from the provided text.


## Recommendation

### Best fit: `qwen2.5:14b`

**Reasoning:**

- **No thinking overhead.** Unlike qwen3:14b (avg TTFT 14s+), qwen2.5:14b responds
  immediately. The difference is stark on the code and reasoning prompts where qwen3
  spent 22-29s in its thinking phase before generating a single token.

- **Zero theatrical flags.** Respected the system prompt across all prompts. qwen3:14b,
  phi4:14b, and mistral-small:24b all produced `*emote*` patterns on the reasoning prompt.

- **Strong tool use.** The weather prompt completed with a 0.24s TTFT — essentially no
  latency introduced by the model itself after the tool result was injected.

- **Quality ceiling at 14B.** On the reasoning prompt, qwen2.5:14b produced a detailed,
  well-structured response in 12.67s. The faster models (llama3.1:8b at 1.55s,
  mistral-nemo:12b at 2.50s) were noticeably shallower on multi-step tasks.

### Strong alternative: `mistral-nemo:12b`

The surprise of the extended Mistral testing. It posted the fastest raw numbers of any
model tested: sub-0.15s TTFT on most prompts, zero theatrical flags, and no theatrical
issues. The tradeoff is depth — at 12B it will be shallower than qwen2.5:14b on complex
reasoning. If responsiveness matters more than answer depth in practice, this is worth
a real-world trial.

### Mistral family summary

- **`mistral-nemo:12b`** — best Mistral option. Fast, clean, well-instruction-tuned.
- **`mistral-small:24b`** — more capable than the 22b revision but adds latency and
  produced a theatrical flag on reasoning. Not a clear win over qwen2.5:14b.
- **`mistral-small:22b`** — solid but outclassed by the 24b revision and mistral-nemo.
- **`mistral:7b`** — reliable fallback, but shows its age vs newer 7-8B options.

### Runner-up (speed-first): `llama3.1:8b`

If the 14B models feel slow in daily use, llama3.1:8b is the best smaller option.
Meta built tool use directly into this model's training, it's consistently fast, and
produced zero theatrical flags. Ceiling is lower but it punches above its weight.

### Avoid: `qwen3:14b` without `/no_think`

14s+ average TTFT is unacceptable for a home assistant. If you want to keep it, add
`/no_think` to the system prompt — but at that point qwen2.5:14b is a better choice.
