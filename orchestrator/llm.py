"""Thin OpenAI-compatible client for LiteLLM."""
import json
import os
import uuid
from typing import AsyncIterator

import httpx

LLM_SERVICE_URL = os.getenv("LLM_SERVICE_URL", "http://localhost:8002")


async def stream(
    messages: list[dict],
    model: str,
    tools: list[dict] | None = None,
) -> AsyncIterator[dict]:
    """Streaming chat completion.

    Yields:
      {"token": str}          — incremental content delta
      {"tool_calls": [...]}   — accumulated tool_calls (once, at end of stream, if any)
      {"usage": {...}}        — token counts (sent by include_usage, usually last)
    Terminates when the server sends [DONE] or the connection closes.

    The tool_call accumulator merges streamed deltas keyed by their `index`:
    `id` and `function.name` arrive in the first delta(s) for an index; subsequent
    deltas append to `function.arguments`. The arguments JSON string is parsed
    only at end of stream (partial JSON is never valid).
    """
    payload: dict = {
        "model": model,
        "messages": messages,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"

    # index -> {"id": str|None, "function": {"name": str, "arguments": str}}
    tool_acc: dict[int, dict] = {}

    async with httpx.AsyncClient(timeout=None) as client:
        async with client.stream(
            "POST",
            f"{LLM_SERVICE_URL}/v1/chat/completions",
            json=payload,
        ) as resp:
            if resp.status_code >= 400:
                # Error responses carry a JSON body, not an SSE stream. Read it
                # so the raised message is actionable, then fail loudly — the
                # caller turns this into a user-visible error / coordinator fallback.
                raw = await resp.aread()
                detail = raw.decode("utf-8", errors="replace")[:500]
                try:
                    detail = json.loads(detail)["error"]["message"]
                except (json.JSONDecodeError, KeyError, TypeError):
                    pass
                raise RuntimeError(f"LiteLLM returned {resp.status_code}: {detail}")
            async for line in resp.aiter_lines():
                if not line.startswith("data:"):
                    continue
                body = line[len("data:"):].strip()
                if not body or body == "[DONE]":
                    break
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
                delta = choices[0].get("delta") or {}

                content = delta.get("content")
                if content:
                    yield {"token": content}

                for tc_delta in delta.get("tool_calls") or []:
                    idx = tc_delta.get("index")
                    if idx is None:
                        continue
                    entry = tool_acc.setdefault(
                        idx,
                        {"id": None, "function": {"name": "", "arguments": ""}},
                    )
                    if tc_delta.get("id"):
                        entry["id"] = tc_delta["id"]
                    fn_delta = tc_delta.get("function") or {}
                    if fn_delta.get("name"):
                        entry["function"]["name"] += fn_delta["name"]
                    if fn_delta.get("arguments"):
                        entry["function"]["arguments"] += fn_delta["arguments"]

    if tool_acc:
        finalized = []
        for idx in sorted(tool_acc.keys()):
            entry = tool_acc[idx]
            args_str = entry["function"]["arguments"]
            try:
                args = json.loads(args_str) if args_str else {}
            except json.JSONDecodeError:
                args = {}
            finalized.append({
                "id": entry["id"] or f"call_{uuid.uuid4().hex[:12]}",
                "function": {
                    "name": entry["function"]["name"],
                    "arguments": args,
                },
            })
        yield {"tool_calls": finalized}


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

    async with httpx.AsyncClient(timeout=300) as client:
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
