"""Phase-1 request router: deterministic shortcuts; everything else → the
coordinator.

The LLM classifier (gemma-3-4b) was removed 2026-08-18 — see
docs/plans/COORDINATOR_ROUTING_PLAN.md. Routing is now structural: a
narrow shortcut pins a query to its specialist, and every unmatched query
goes to the coordinator, which answers directly or delegates via its
ask_* tools. A shortcut miss costs seconds (one coordinator hop), never a
wrong lane — so shortcuts are precision-first: they pin only on a named
household entity, an explicit user-stated method, or domain vocabulary
with context.
"""
import re

import telemetry
from events import emit

_URL_RE = re.compile(r'https?://')

# Talkie is a vintage 1930s character model — only invoke it when explicitly named.
# Checked first so it takes priority over all other routing.
_TALKIE_PHRASES = re.compile(
    r"\b(ask\s+talkie|talkie[,\s]+|what\s+does\s+talkie\s+(think|say|know|believe)|"
    r"talkie'?s\s+(opinion|view|take|thoughts?|perspective)|"
    r"have\s+talkie|get\s+talkie\s+to|let\s+talkie)\b",
    re.IGNORECASE,
)

# "Clear my history" intent — handled by the transports BEFORE routing (it
# needs the requesting client's session id, which the router doesn't have).
# Lives here beside its sibling patterns so all deterministic phrase-matching
# is in one place. Deliberately strict: bare "start over" alone could be a
# legitimate request inside a task, so every variant names the conversation.
CLEAR_HISTORY_RE = re.compile(
    r"\b(clear|erase|delete|forget|wipe|reset)\s+(my\s+|the\s+|our\s+|this\s+)?"
    r"(history|conversation|chat( history)?|context)\b"
    r"|\bstart\s+(over|fresh)\s+(with\s+a\s+)?(new|clean|fresh)\s+(conversation|chat|history)\b",
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

# Explicit search phrases that reliably indicate a research task.
# Precision rule (COORDINATOR_ROUTING_PLAN, 2026-08-18): a shortcut pins only
# on an explicit user-stated METHOD — so `search` requires its qualifier
# ("search the web/online"); bare "search"/"search for" hijacked local asks
# ("search my shopping list") and "what is the latest" is recency, not
# method. Unmatched phrasings fall to the coordinator: slow-but-right.
_SEARCH_PHRASES = re.compile(
    r'\b(search\s+(the\s+web|online)|web\s+search|look\s+up|look\s+it\s+up|'
    r'find(\s+me)?\s+(online|on\s+the\s+web|on\s+the\s+internet)|'
    r'google|news\s+about)\b',
    re.IGNORECASE,
)

# Weather queries belong to the home agent (NWS tool + prompt-injected cache).
# Contextual forms only (precision rule): a weather word plus a question
# shape or a timeframe. Keeps the 2026-07-05 forecast-misroute fix and the
# frequent voice phrasings while releasing "AMD's revenue forecast" /
# "weather the storm" to the coordinator. Checked AFTER _SEARCH_PHRASES on
# purpose: explicit "look up the weather in Tokyo" keeps routing to
# research, which works for non-US locations (NWS is US-only).
_WEATHER_RE = re.compile(
    r"what'?s?\s+(is\s+)?the\s+weather"
    r"|\bweather\b.{0,40}\b(today|tonight|tomorrow|this\s+week(end)?)\b"
    r"|\b(today|tonight|tomorrow)'?s?\s+(weather|forecast)\b"
    r"|\bforecast\s+for\s+(today|tonight|tomorrow|this\s+week(end)?)\b"
    r"|\bweather\s+forecast\b",
    re.IGNORECASE,
)

# The solar TOPIC pin was removed 2026-08-18 (same day it was narrowed):
# solar queries are frequently composite ("money's worth of solar…"), a
# topic regex can't see compositeness, and the escalation net under the pin
# proved probabilistic at 4B (1-for-2 on identical inputs — traces
# 1e6d1603 vs 143bbc62). All solar questions now ride the coordinator,
# which reaches home via ask_home: composites compose reliably; status
# checks pay one delegation hop (~+4 s). Lesson recorded in
# docs/features/coordinator-default-routing.md: METHOD pins (user states
# the how) are sound; TOPIC pins only survive on frequency + simplicity
# grounds (weather, mirror).

# "magic mirror" is a multi-agent entity: home owns the fast named-safe
# UPDATE (a terminal tool); devops owns arbitrary diagnostics/ops (the
# remote_exec loop). The update pin fires ONLY on the exact command phrase
# — questions *about* updates ("did the magic mirror update break
# anything?") must not reach a mutation tool (2026-08-18 audit). Everything
# else mirror → devops.
_MM_RE        = re.compile(r'\bmagic\s*mirror\b', re.IGNORECASE)
_MM_UPDATE_RE = re.compile(r'\bupdate\s+the\s+magic\s*mirror\b', re.IGNORECASE)


async def classify(text: str, prior_history: list[dict]) -> str:
    """Return a route: a shortcut-pinned specialist, else "direct" (the
    coordinator).

    prior_history is unused since the LLM classifier was removed — kept for
    transport API stability (both shims and /message call this signature).
    """
    span = telemetry.root().child_span("routing.decide", input=text[:200])
    try:
        route, rule = await _classify_inner(text)
        span.end(output=route, metadata={"rule": rule})
        return route
    except Exception as e:
        span.end(level="ERROR", status_message=str(e)[:200])
        raise


async def _classify_inner(text: str) -> tuple[str, str]:
    """Returns (route, rule) where rule names the deciding mechanism."""
    if _TALKIE_PHRASES.search(text):
        emit("route_shortcut", rule="talkie_explicit", route="talkie")
        return "talkie", "talkie_explicit"
    if _DIRECT_OVERRIDE.search(text):
        emit("route_shortcut", rule="direct_override", route="direct")
        return "direct", "direct_override"
    if _URL_RE.search(text):
        emit("route_shortcut", rule="url", route="research")
        return "research", "url"
    if _SEARCH_PHRASES.search(text):
        emit("route_shortcut", rule="search_phrase", route="research")
        return "research", "search_phrase"
    if _WEATHER_RE.search(text):
        emit("route_shortcut", rule="weather", route="home")
        return "home", "weather"
    if _MM_RE.search(text):
        if _MM_UPDATE_RE.search(text):
            emit("route_shortcut", rule="mm_update", route="home")
            return "home", "mm_update"       # fast terminal tool
        emit("route_shortcut", rule="mm_ops", route="devops")
        return "devops", "mm_ops"            # remote_exec diagnostics loop

    # No LLM classifier: everything unmatched goes to the coordinator, which
    # answers directly or delegates via its ask_* tools. Structural routing
    # (tenet 5): a 4B model can't pick the wrong specialist if specialists
    # aren't on its menu.
    emit("route", text_preview=text[:60], route="direct", rule="default")
    return "direct", "default"
