"""Structured JSON-line event logging for ops visibility."""
import json
import logging
import time
import uuid
from contextvars import ContextVar

_request_id: ContextVar[str] = ContextVar("request_id", default="-")

event_logger = logging.getLogger("kronk.events")

try:
    import telemetry as _telemetry   # trace id on every line (logs ↔ traces join)
except Exception:  # pragma: no cover
    _telemetry = None


def new_request_id() -> str:
    rid = uuid.uuid4().hex[:8]
    _request_id.set(rid)
    return rid


def current_request_id() -> str:
    return _request_id.get()


def emit(event: str, **fields) -> None:
    """Emit a single JSON line with a stable shape."""
    payload = {
        "ts": round(time.time(), 3),
        "rid": _request_id.get(),
        "event": event,
        **fields,
    }
    if _telemetry is not None and (tid := _telemetry.current_trace_id()):
        payload["trace_id"] = tid
    event_logger.info(json.dumps(payload, default=str))
