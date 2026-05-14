"""Phase-1 request router: deterministic pre-checks, then LLM classifier."""
import logging
import os
import re

import agents
import llm
import metrics
from events import emit

logger = logging.getLogger(__name__)

ROUTER_MODEL = os.getenv("ROUTER_MODEL", "gemma-3-4b")

_MAX_ROUTER_HISTORY  = 4    # messages (≈2 turns)
_MAX_ASSISTANT_CHARS = 200  # truncated before sending to router

_URL_RE = re.compile(r'https?://')

# Talkie is a vintage 1930s character model — only invoke it when explicitly named.
# Checked first so it takes priority over all other routing.
_TALKIE_PHRASES = re.compile(
    r"\b(ask\s+talkie|talkie[,\s]+|what\s+does\s+talkie\s+(think|say|know|believe)|"
    r"talkie'?s\s+(opinion|view|take|thoughts?|perspective)|"
    r"have\s+talkie|get\s+talkie\s+to|let\s+talkie)\b",
    re.IGNORECASE,
)

# If the user explicitly says not to search / use the research agent, route direct
# regardless of other signals. Checked before search phrases so "don't search for
# that" doesn't accidentally match the search-phrase pattern.
_DIRECT_OVERRIDE = re.compile(
    r"don'?t\s+(use\s+)?(the\s+)?(research|search|web|internet)(\s+agent)?"
    r"|no\s+(web\s+|internet\s+)?search"
    r"|from\s+your\s+(own\s+)?(knowledge|training|memory)"
    r"|generate\s+your\s+own\s+answer"
    r"|answer\s+(it\s+)?yourself"
    r"|without\s+(searching|the\s+web|internet)"
    r"|no\s+answer\s+online"
    r"|there'?s?\s+no\s+answer\s+online",
    re.IGNORECASE,
)

# Explicit search phrases that reliably indicate a research task. Small classifier
# models miss these consistently, so we pre-check before the LLM call.
# Note: bare "find" is intentionally excluded — "what can you find about X" is
# colloquial and should route through the LLM classifier, not shortcut to research.
_SEARCH_PHRASES = re.compile(
    r'\b(search(\s+online|\s+the\s+web|\s+for)?|look\s+up|'
    r'find(\s+me)?\s+(online|on\s+the\s+web|on\s+the\s+internet)|'
    r'look\s+it\s+up|google|what\s+is\s+the\s+latest|news\s+about)\b',
    re.IGNORECASE,
)


async def classify(text: str, prior_history: list[dict]) -> str:
    """Return one of agents.VALID_ROUTES.

    prior_history: conversation turns *before* the current user message.
    """
    if _TALKIE_PHRASES.search(text):
        emit("route_shortcut", rule="talkie_explicit", route="talkie")
        return "talkie"
    if _DIRECT_OVERRIDE.search(text):
        emit("route_shortcut", rule="direct_override", route="direct")
        return "direct"
    if _URL_RE.search(text):
        emit("route_shortcut", rule="url", route="research")
        return "research"
    if _SEARCH_PHRASES.search(text):
        emit("route_shortcut", rule="search_phrase", route="research")
        return "research"

    # Build a short, alternation-safe history window for the classifier.
    router_history: list[dict] = []
    if prior_history:
        recent = prior_history[-_MAX_ROUTER_HISTORY:]
        while recent and recent[0]["role"] != "user":
            recent = recent[1:]
        for m in recent:
            content = m["content"]
            if m["role"] == "assistant" and len(content) > _MAX_ASSISTANT_CHARS:
                content = content[:_MAX_ASSISTANT_CHARS] + "…"
            router_history.append({"role": m["role"], "content": content})

    # Gemma-family chat templates reject system messages via LiteLLM, so embed
    # the routing prompt inside the user turn.
    router_query = f"{agents.ROUTING_PROMPT}\n\nClassify this request: {text}"
    messages = router_history + [{"role": "user", "content": router_query}]

    completion = await llm.complete(messages, [], ROUTER_MODEL)

    route_text = (completion.get("content") or "").strip().lower()
    route = route_text.split()[0] if route_text else "direct"
    if route not in agents.VALID_ROUTES:
        emit("route_invalid", raw=route_text[:40], fallback="direct")
        logger.warning("Router returned unexpected route %r, defaulting to direct", route_text[:40])
        route = "direct"

    usage = completion.get("usage") or {}
    metrics.record(
        agent="router",
        model=ROUTER_MODEL,
        prompt_tokens=usage.get("prompt_tokens", 0),
        completion_tokens=usage.get("completion_tokens", 0),
        eval_duration_ns=usage.get("eval_duration_ns", 0),
    )
    emit("route", text_preview=text[:60], route=route, model=ROUTER_MODEL)
    return route
