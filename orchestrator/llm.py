"""Thin OpenAI-compatible client for LiteLLM."""
import json
import os
import uuid
from typing import AsyncIterator

import httpx

LLM_SERVICE_URL = os.getenv("LLM_SERVICE_URL", "http://localhost:8002")


async def stream(messages: list[dict], model: str) -> AsyncIterator[dict]:
    """Streaming chat completion.

    Yields:
      {"token": str}          — incremental content delta
      {"usage": {...}}        — token counts (sent by include_usage, usually last)
    Terminates when the server sends [DONE] or the connection closes.
    """
    payload = {
        "model": model,
        "messages": messages,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    async with httpx.AsyncClient(timeout=None) as client:
        async with client.stream(
            "POST",
            f"{LLM_SERVICE_URL}/v1/chat/completions",
            json=payload,
        ) as resp:
            async for line in resp.aiter_lines():
                if not line.startswith("data:"):
                    continue
                body = line[len("data:"):].strip()
                if not body or body == "[DONE]":
                    return
                try:
                    chunk = json.loads(body)
                except json.JSONDecodeError:
                    continue
                if chunk.get("usage"):
                    yield {"usage": chunk["usage"]}
                    continue
                choices = chunk.get("choices") or []
                if not choices:
                    continue
                delta = choices[0].get("delta", {}).get("content", "")
                if delta:
                    yield {"token": delta}


async def complete(messages: list[dict], tools: list[dict], model: str) -> dict:
    """Non-streaming LLM completion via LiteLLM.

    Returns:
      {
        "message":     raw assistant message (safe to append to history verbatim),
        "content":     convenience string (may be empty when tool_calls is set),
        "tool_calls":  [{"id": str, "function": {"name": str, "arguments": dict}}],
        "usage":       {prompt_tokens, completion_tokens, eval_duration_ns},
      }
    """
    payload: dict = {"model": model, "messages": messages, "stream": False}
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"

    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(f"{LLM_SERVICE_URL}/v1/chat/completions", json=payload)
        resp.raise_for_status()
        data = resp.json()

    message = data["choices"][0]["message"]

    # Ensure every returned tool_call has an id — some models/templates drop it.
    raw_calls = message.get("tool_calls") or []
    for tc in raw_calls:
        if not tc.get("id"):
            tc["id"] = f"call_{uuid.uuid4().hex[:12]}"
        tc.setdefault("type", "function")

    tool_calls = []
    for tc in raw_calls:
        fn = tc.get("function", {})
        args = fn.get("arguments", {})
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except Exception:
                args = {}
        tool_calls.append({
            "id": tc["id"],
            "function": {"name": fn.get("name", ""), "arguments": args},
        })

    usage = data.get("usage", {})
    return {
        "message":    message,
        "content":    message.get("content") or "",
        "tool_calls": tool_calls,
        "usage": {
            "prompt_tokens":     usage.get("prompt_tokens", 0),
            "completion_tokens": usage.get("completion_tokens", 0),
            "eval_duration_ns":  0,
        },
    }
