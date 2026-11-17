"""User-facing error rendering — the ERROR_STYLE toggle.

The toggle governs RENDERING only, never capture: logs and traces always
receive full detail (status codes, bodies, rids) regardless of style. What
changes is the last mile — what gets spoken or displayed.

Styles:
  debug    — full detail: error types, causes, HTTP statuses, rid. The
             default for every mount except the speech mount; these
             strings are pinned by tests and the operator greps for them.
  friendly — one short spoken sentence, no technical identifiers, still
             naming what kind of thing failed where the code knows. The
             default on the speech mount (/voice/api/chat).

Which style a request gets is keyed on origin.voice, the request-scoped
"this came in on the speech mount" value (sprint M2) — not on the
transport: /api/chat and /v1 are shims too, but they are not spoken.

Config (read at call time, so flipping is a compose env edit + `up -d`):
  ERROR_STYLE        — every non-voice request: debug | friendly
                       (default: debug)
  ERROR_STYLE_VOICE  — requests on the speech mount: debug | friendly
                       (default: friendly). Set ERROR_STYLE_VOICE=debug to
                       hear the full debug wording on a speaker again.
"""
import os
import re

import origin

DEBUG = "debug"
FRIENDLY = "friendly"
_VALID = {DEBUG, FRIENDLY}

# Unexpected exceptions: the cause is truly unknown to the code, so this is
# the one friendly sentence allowed to be generic (tenet 7).
_FRIENDLY_GENERIC = (
    "Sorry — something went wrong on my end. I've noted the details."
)
_FRIENDLY_ROUTING = (
    "Sorry — I couldn't work out where to send that request. I've noted the details."
)
_FRIENDLY_LLM_UNREACHABLE = (
    "Sorry — I couldn't reach my language model. Try again in a moment."
)
_FRIENDLY_LLM_REJECTED = (
    "Sorry — my language model couldn't handle that request."
)
# llm.stream raises "LiteLLM returned <status>: …" for an HTTP error answer.
_LLM_CLIENT_ERROR = re.compile(r"returned 4\d\d\b")


def style_for(voice: bool) -> str:
    """Resolve the error style for one request. Unknown values fall back to
    debug — a typo in the env must not silently hide detail forever."""
    if voice:
        style = os.getenv("ERROR_STYLE_VOICE", FRIENDLY)
    else:
        style = os.getenv("ERROR_STYLE", DEBUG)
    style = style.strip().lower()
    return style if style in _VALID else DEBUG


def current_style() -> str:
    """The style for the request running now (origin.voice), for code that
    is not handed one explicitly — e.g. a specialist the coordinator
    delegates to via ask_*."""
    return style_for(origin.voice.get())


def render(kind: str, detail: str, rid: str, style: str) -> str:
    """Render a code-authored pipeline failure for the user.

    kinds: routing | pipeline | llm. The debug renderings are contracts —
    tests pin them and the operator greps for them; change deliberately.
    """
    if style == FRIENDLY:
        if kind == "llm":
            # A 4xx from LiteLLM means the server answered and refused the
            # request (bad model name, context too long); anything else —
            # 5xx, connect error, timeout — means it could not be reached.
            if _LLM_CLIENT_ERROR.search(detail or ""):
                return _FRIENDLY_LLM_REJECTED
            return _FRIENDLY_LLM_UNREACHABLE
        if kind == "routing":
            return _FRIENDLY_ROUTING
        return _FRIENDLY_GENERIC
    if kind == "routing":
        return f"Error: routing failed: {detail} [rid {rid}]"
    if kind == "pipeline":
        return f"Error: the pipeline failed unexpectedly ({detail}) [rid {rid}]"
    return detail  # "llm": run_stream's error messages are already specific


# The service a terminal tool talks to, for the friendly sentence spoken when
# the tool raised (tools.execute's "[Tool <name> error: …]") instead of
# returning one of its own result lines.
_SERVICE_BY_TOOL = {
    "play_music":                "the music service",
    "control_music":             "the music service",
    "now_playing":               "the music service",
    "rate_music":                "the music service",
    "remember_music_preference": "the music service",
    "update_magicmirror":        "the magic mirror updater",
}


def friendly_tool_failure(tool: str | None) -> str:
    """Spoken sentence for a terminal tool that raised (friendly style)."""
    service = _SERVICE_BY_TOOL.get(tool or "", "the service that handles that")
    return f"That didn't work — I couldn't get through to {service}."


def specialist_failed_block(agent_name: str, error: str, style: str) -> str:
    """Model-facing context for the coordinator after a specialist failure.

    Full error detail in BOTH styles — the model needs it to decide what to
    do; only the phrasing instruction changes. The invented-answer ban is
    style-independent: friendliness must never reintroduce lying."""
    if style == FRIENDLY:
        instruction = (
            "If you can fully answer the user's question from your own "
            "knowledge, do so. Otherwise, tell the user it didn't work in "
            "one short natural sentence that says what kind of thing failed "
            "— never mention HTTP status codes, error names, URLs, ports, "
            "model names or other technical identifiers — and do NOT invent "
            "an answer or claim an action succeeded."
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


# Friendly style only: appended to a FAILED tool result (never to the system
# prompt), so the prompt of every successful request is unchanged and the
# instruction sits right next to the failure it is about. The result itself
# keeps full detail — the model needs it to act.
FRIENDLY_TOOL_PHRASING = (
    "[This tool failed. Tell the user it didn't work and what kind of thing "
    "failed in one short natural sentence — never mention HTTP status codes, "
    "error names, URLs, ports, model names or other technical identifiers.]"
)

# The code's own failure shapes for a tool result: tools.execute's exception
# line, a delegated specialist's error (agents.run_stream), tools._fail()'s
# HTTP failure, and the handlers' "Could not …" / "… failed: …" lines.
_FAILED_RESULT = re.compile(
    r"^\[(?:Tool \S+ error: |\S+ agent error: |Could not "
    r"|[^\]\n]* failed \(HTTP \d{3}\): |[^\]\n]* failed: )"
)


def is_failed_tool_result(result) -> bool:
    return isinstance(result, str) and bool(_FAILED_RESULT.match(result))
