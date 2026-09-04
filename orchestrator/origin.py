"""Where a request came from — the voice satellite that heard it.

HA stamps one tagged line onto the system prompt it sends every voice
request (the Ollama conversation instructions are a Jinja template with
`llm_context.device_id` in scope — docs/plans/VOICE_MUSIC_ORIGIN_KRONK_PLAN.md):

    [kronk-origin] device=<HA device id> area=<area name>

The shim reads that line (and still discards the rest of HA's system
prompt — Kronk owns its persona). The origin is then held in a
request-scoped ContextVar for the duration of `_run_pipeline`, so the
coordinator → ask_home → home agent → tools.execute chain reaches
`play_music` without a new parameter at every hop. One producer, one
consumer, three async layers apart; asyncio copies the context per task,
so concurrent requests cannot see each other's origin (tests/test_origin.py).

Requests without the stamp (web UI, OpenAI shim, other clients) carry
None and behave as before.
"""
import re
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass


@dataclass(frozen=True)
class Origin:
    device_id: str | None   # HA device id of the satellite that heard the request
    area: str | None        # its HA area name, if it has one


current: ContextVar[Origin | None] = ContextVar("kronk_origin", default=None)

_STAMP = re.compile(r"\[kronk-origin\]\s*device=(?P<device>\S*)\s*area=(?P<area>[^\n]*)")


def _clean(v: str | None) -> str | None:
    v = (v or "").strip()
    return None if v in ("", "None", "none") else v


def parse(text: str | None) -> Origin | None:
    """The origin stamped in `text`, or None if absent or empty."""
    if not text:
        return None
    m = _STAMP.search(text)
    if not m:
        return None
    o = Origin(device_id=_clean(m.group("device")), area=_clean(m.group("area")))
    return o if (o.device_id or o.area) else None


def from_messages(messages) -> Origin | None:
    """First stamp found in the system messages of a chat request.
    Accepts dicts or objects with .role/.content."""
    for m in messages or []:
        role = m.get("role") if isinstance(m, dict) else getattr(m, "role", None)
        if role != "system":
            continue
        content = m.get("content") if isinstance(m, dict) else getattr(m, "content", None)
        o = parse(content)
        if o:
            return o
    return None


@contextmanager
def scope(origin: Origin | None):
    """Set the current origin for the enclosed block (sync or async code)."""
    token = current.set(origin)
    try:
        yield origin
    finally:
        current.reset(token)
