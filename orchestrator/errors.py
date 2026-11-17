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

One exit for code-written failure text (sprint M2 fix 1): every place the
orchestrator writes failure text that reaches the user — pipeline errors,
terminal-tool failures, the news brief, an empty model reply — returns it
through failure_text(), which gives the friendly sentence in friendly
style and today's exact text otherwise. speech_backstop() is the last
check on the speech mount: a failed turn whose final text still carries one
of the code's own debug shapes is replaced by the generic sentence.
"""
import logging
import os
import re
from contextvars import ContextVar

import origin
import telemetry
from events import emit

logger = logging.getLogger(__name__)

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
    if kind == "routing":
        debug = f"Error: routing failed: {detail} [rid {rid}]"
        friendly = _FRIENDLY_ROUTING
    elif kind == "pipeline":
        debug = f"Error: the pipeline failed unexpectedly ({detail}) [rid {rid}]"
        friendly = _FRIENDLY_GENERIC
    elif kind == "llm":
        debug = detail  # run_stream's error messages are already specific
        # A 4xx from LiteLLM means the server answered and refused the
        # request (bad model name, context too long); anything else —
        # 5xx, connect error, timeout — means it could not be reached.
        friendly = (_FRIENDLY_LLM_REJECTED if _LLM_CLIENT_ERROR.search(detail or "")
                    else _FRIENDLY_LLM_UNREACHABLE)
    else:
        debug, friendly = detail, _FRIENDLY_GENERIC
    return failure_text(debug, style, friendly)


def failure_text(debug: str, style: str, friendly: str | None) -> str:
    """The one exit for failure text the code writes for the user.

    debug:    today's exact text; returned unchanged unless style is
              friendly, so /message, /api/chat and /v1 keep their wording.
    friendly: the spoken sentence. None means `debug` is already a plain
              sentence fit for a speaker and is used in both styles (the
              music failure sentences, criterion 8).

    Every call also marks the running turn as failed (note_failure), which
    arms speech_backstop. When the friendly sentence replaces the debug
    text, the debug text is logged and put on the current span, so the
    spoken sentence's claim that the details are noted stays true (tenet 7).
    """
    note_failure(debug)
    if style != FRIENDLY or friendly is None or friendly == debug:
        return debug
    emit("failure_spoken_friendly", debug=debug[:300], spoken=friendly)
    telemetry.current().set(**{"kronk.failure.debug_text": debug[:300],
                               "kronk.failure.spoken": friendly})
    return friendly


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
    "news_brief":                "the news service",
}


def _service(tool: str | None) -> str:
    return _SERVICE_BY_TOOL.get(tool or "", "the service that handles that")


def friendly_tool_failure(tool: str | None) -> str:
    """Spoken sentence for a terminal tool that raised (friendly style)."""
    return f"That didn't work — I couldn't get through to {_service(tool)}."


def friendly_tool_bad_request(tool: str | None) -> str:
    """Spoken sentence for a terminal tool that refused its arguments before
    calling anything ("[play_music error: query is required]") — nothing
    was unreachable, so "couldn't get through" would mislead (M2 e2e
    observation 4)."""
    return (f"That didn't work — I didn't get enough detail to ask "
            f"{_service(tool)}. Try saying it again.")


# A failed mirror update: tool_service's preflight detail (key paths, ssh
# targets, ssh output) is debug text and is never spoken (M2 fix 1).
FRIENDLY_MIRROR_NOT_STARTED = (
    "I couldn't start the magic mirror update. The details are in the logs."
)
FRIENDLY_NEWS_UNAVAILABLE = (
    "The news brief isn't available right now. "
    "Ask me to research the news live if you need it sooner."
)
FRIENDLY_NEWS_REFRESH_FAILED = (
    "I couldn't fetch fresh news, so here's the latest brief I have."
)
# The model streamed no token and no tool call.
FRIENDLY_EMPTY_REPLY = (
    "Sorry — my language model didn't come up with an answer. Try asking again."
)


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
# line and unknown-tool line, a handler's argument refusal ("[play_music
# error: query is required]"), a delegated specialist's error or empty reply
# (agents.run_stream / run_delegated), tools._fail()'s HTTP failure, the
# handlers' "Could not …" / "… failed: …" lines, and the hot tub and solar
# energy "no data" lines (M2 e2e observation 5).
_FAILED_RESULT = re.compile(
    r"^\[(?:Tool \S+ error: |\S+ error: |\S+ agent error: "
    r"|\S+ agent returned no response\]|Unknown tool: |no such specialist: "
    r"|Could not |Hot tub status (?:unknown: |unavailable\])"
    r"|Solar energy — [^\]\n]*\] no figure available"
    r"|[^\]\n]* failed \(HTTP \d{3}\): |[^\]\n]* failed: )"
)


def is_failed_tool_result(result) -> bool:
    return isinstance(result, str) and bool(_FAILED_RESULT.match(result))


# ── the turn's failures, and the speech-mount backstop ────────────────────────
#
# A request-scoped list of the failures the running turn has met: every
# failure_text() call, every failed tool result the agent loop saw, and the
# pipeline's own error. main._kronk_pipeline_tokens opens it for a shim turn
# (begin_turn) and reads it at the end; code that runs outside a turn (the
# web UI, unit tests calling helpers directly) notes into nothing. A
# ContextVar holding a list, so a task spawned inside the turn appends to
# the same list.
_turn: ContextVar[list | None] = ContextVar("kronk_turn_failures", default=None)


def begin_turn():
    return _turn.set([])


def end_turn(token) -> None:
    _turn.reset(token)


def note_failure(detail) -> None:
    failures = _turn.get()
    if failures is not None:
        failures.append(str(detail)[:300])


def turn_failures() -> list[str]:
    return list(_turn.get() or [])


# The orchestrator's own debug shapes. Only checked on a failed turn on the
# speech mount, so a successful answer is never touched.
_DEBUG_SHAPES = re.compile(
    r"\[(?:Tool \S+ error|\S+ agent error|\S+ agent returned no response"
    r"|\S+ error: |Unknown tool: |no such specialist: |Could not )"
    r"|\bErrno\b"
    r"|Traceback \(most recent call last\)"
    r"|\b[A-Z]\w*(?:Error|Exception)\b"
    r"|\bhttps?://[^\s/:]+:\d+"
    r"|(?<![\w.])/(?:keys|data)/"
)


def debug_shape(text: str | None) -> str | None:
    """The first debug shape in `text`, or None."""
    m = _DEBUG_SHAPES.search(text or "")
    return m.group(0) if m else None


def speech_backstop(text: str, failures: list[str]) -> str:
    """Last check before text goes to a speaker (the speech mount only).

    A turn with no recorded failure passes untouched, whatever it says. A
    failed turn whose final text still carries one of the code's own debug
    shapes is replaced by the generic friendly sentence; the original is
    logged and put on the current span. Off when ERROR_STYLE_VOICE=debug —
    the operator asked to hear the debug text."""
    if not failures or style_for(voice=True) != FRIENDLY:
        return text
    shape = debug_shape(text)
    if shape is None:
        return text
    logger.warning("speech backstop replaced a failed turn's reply (matched %r): %r",
                   shape, text[:300])
    emit("speech_backstop", matched=shape, original=text[:300],
         failures=[f[:120] for f in failures[:5]])
    telemetry.current().set(**{"kronk.speech_backstop": True,
                               "kronk.speech_backstop.matched": shape,
                               "kronk.speech_backstop.original": text[:300]})
    return _FRIENDLY_GENERIC
