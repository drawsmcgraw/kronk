"""
LiteLLM pre-call hook: normalize message arrays before they reach llama.cpp.

Fixes the "expecting alternating user/assistant" Jinja template error that
occurs when Zed sends conversation history after a model switch. The two
problems this covers:

  1. Consecutive messages with the same role — merged into one message.
  2. Message array ending on an assistant turn — a short placeholder user
     message is appended so the model has something to respond to.
"""
from litellm.integrations.custom_logger import CustomLogger


def _text(content) -> str:
    """Extract plain text from a message content value (str or content-block list)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
        return "\n".join(parts)
    return str(content) if content is not None else ""


def _normalize(messages: list) -> list:
    if not messages:
        return messages

    # Merge consecutive same-role messages
    merged = []
    for msg in messages:
        if merged and merged[-1]["role"] == msg["role"]:
            merged[-1]["content"] = _text(merged[-1]["content"]) + "\n\n" + _text(msg["content"])
        else:
            merged.append({"role": msg["role"], "content": _text(msg["content"])})

    # llama.cpp templates require the final turn to be a user message
    if merged and merged[-1]["role"] == "assistant":
        merged.append({"role": "user", "content": "Please continue."})

    return merged


class MessageNormalizer(CustomLogger):
    async def async_pre_call_hook(self, user_api_key_dict, cache, data, call_type):
        if call_type == "completion" and "messages" in data:
            data["messages"] = _normalize(data["messages"])
        return data


proxy_handler_instance = MessageNormalizer()
