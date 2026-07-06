"""User-facing error rendering — the ERROR_STYLE toggle.

The toggle governs RENDERING only, never capture: logs and Langfuse always
receive full detail (status codes, bodies, rids) regardless of style. What
changes is the last mile — what gets spoken or displayed.

Styles:
  debug    — full detail: error types, causes, HTTP statuses, rid. The
             default while the system is being built and refined; these
             strings are pinned by tests and asserted by the voice smoke
             test (which always runs in debug).
  friendly — one natural sentence, no technical identifiers. For when the
             kitchen audience matters more than the operator.

Config (read at call time, so flipping is a compose env edit + `up -d`):
  ERROR_STYLE        — global default: debug | friendly   (default: debug)
  ERROR_STYLE_VOICE  — override for the shim transport (/api/chat + /v1;
                       HA voice is the primary shim client)
"""
import os

DEBUG = "debug"
FRIENDLY = "friendly"
_VALID = {DEBUG, FRIENDLY}

_FRIENDLY_GENERIC = (
    "Sorry — something went wrong on my end. I've noted the details."
)


def style_for(transport: str) -> str:
    """Resolve the error style for one request. Unknown values fall back to
    debug — a typo in the env must not silently hide detail forever."""
    style = os.getenv("ERROR_STYLE", DEBUG).strip().lower()
    if transport == "shim":
        style = os.getenv("ERROR_STYLE_VOICE", style).strip().lower()
    return style if style in _VALID else DEBUG


def render(kind: str, detail: str, rid: str, style: str) -> str:
    """Render a code-authored pipeline failure for the user.

    kinds: routing | pipeline | llm. The debug renderings are contracts —
    tests pin them and the operator greps for them; change deliberately.
    """
    if style == FRIENDLY:
        if kind == "llm":
            return "Sorry — I couldn't reach my language model. Try again in a moment."
        return _FRIENDLY_GENERIC
    if kind == "routing":
        return f"Error: routing failed: {detail} [rid {rid}]"
    if kind == "pipeline":
        return f"Error: the pipeline failed unexpectedly ({detail}) [rid {rid}]"
    return detail  # "llm": run_stream's error messages are already specific


def specialist_failed_block(agent_name: str, error: str, style: str) -> str:
    """Model-facing context for the coordinator after a specialist failure.

    Full error detail in BOTH styles — the model needs it to decide what to
    do; only the phrasing instruction changes. The invented-answer ban is
    style-independent: friendliness must never reintroduce lying."""
    if style == FRIENDLY:
        instruction = (
            "If you can fully answer the user's question from your own "
            "knowledge, do so. Otherwise, tell the user it didn't work in "
            "one natural sentence — never mention HTTP status codes, error "
            "codes, or other technical identifiers — and do NOT invent an "
            "answer or claim an action succeeded."
        )
    else:
        instruction = (
            "If you can fully answer the user's question from your own "
            "knowledge, do so. Otherwise, report the failure and its "
            "cause to the user plainly — do NOT invent an answer or "
            "claim an action succeeded."
        )
    return (
        f"[The {agent_name} specialist FAILED with this error:\n"
        f"{error}\n{instruction}]"
    )


# Appended to agent system prompts in friendly mode so tool-failure detail
# (which stays fully verbose model-side) is paraphrased, not recited.
FRIENDLY_TOOL_PHRASING = (
    "When a tool fails, tell the user it didn't work and why in one natural "
    "sentence — never mention HTTP status codes, error codes, or other "
    "technical identifiers."
)
