"""Langfuse instrumentation helpers — Phase 2 of docs/plans/TELEMETRY_PLAN.md.

All Langfuse SDK interaction lives in this module so the rest of the
orchestrator never imports langfuse directly. Design rules:

- When LANGFUSE_ENABLED != "true", or the SDK import/init fails, every
  helper degrades to a silent no-op. The pipeline must never break or slow
  down because telemetry is misconfigured.
- One *trace* per pipeline run, created via start_pipeline(). Stages attach
  children through root(). A module-level current-root is safe here because
  the orchestrator serialises all pipeline runs behind _llm_lock; if that
  lock ever goes away, switch this to a ContextVar.
- Generations (LLM calls) record completion_start_time on first token so
  Langfuse computes time-to-first-token natively.
- The SDK batches in a background thread; we don't flush per-request.

Env (set on the orchestrator container):
  LANGFUSE_ENABLED     "true" to instrument (default: off)
  LANGFUSE_HOST        e.g. http://host.docker.internal:3000
  LANGFUSE_PUBLIC_KEY  project public key (pk-lf-…)
  LANGFUSE_SECRET_KEY  project secret key (sk-lf-…)
"""
import logging
import os
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

ENABLED = os.getenv("LANGFUSE_ENABLED", "false").lower() == "true"
_client = None

if ENABLED:
    try:
        from langfuse import Langfuse

        _client = Langfuse(
            public_key=os.environ["LANGFUSE_PUBLIC_KEY"],
            secret_key=os.environ["LANGFUSE_SECRET_KEY"],
            host=os.getenv("LANGFUSE_HOST", "http://localhost:3000"),
        )
        logger.info("telemetry: Langfuse tracing enabled → %s",
                    os.getenv("LANGFUSE_HOST", "http://localhost:3000"))
    except Exception as e:  # missing keys, bad import, unreachable host config
        logger.error("telemetry: disabled — Langfuse init failed: %s", e)
        ENABLED = False
        _client = None


def _now() -> datetime:
    return datetime.now(timezone.utc)


class _NoopObs:
    """Absorbs every call. Returned whenever telemetry is off or errored."""
    ended = True

    def child_span(self, *a, **kw):       return self
    def child_generation(self, *a, **kw): return self
    def first_token(self):                pass
    def end(self, *a, **kw):              pass
    def update_trace(self, *a, **kw):     pass


_NOOP = _NoopObs()


class _Obs:
    """Thin wrapper over a Langfuse span/generation object.

    Normalises the v3/v4 SDK surface (start_observation vs start_generation)
    and guarantees no telemetry exception ever propagates into the pipeline.
    """

    def __init__(self, raw):
        self._raw = raw
        self._completion_start: datetime | None = None
        self.ended = False

    def child_span(self, name: str, input=None, metadata: dict | None = None):
        try:
            return _Obs(self._raw.start_observation(
                name=name, as_type="span", input=input, metadata=metadata,
            ))
        except Exception as e:
            logger.debug("telemetry: child_span(%s) failed: %s", name, e)
            return _NOOP

    def child_generation(self, name: str, model: str, input=None,
                         metadata: dict | None = None):
        try:
            return _Obs(self._raw.start_observation(
                name=name, as_type="generation",
                model=model, input=input, metadata=metadata,
            ))
        except Exception as e:
            logger.debug("telemetry: child_generation(%s) failed: %s", name, e)
            return _NOOP

    def first_token(self):
        """Call when the first streamed token arrives → Langfuse derives TTFT."""
        if self._completion_start is None:
            self._completion_start = _now()

    def end(self, output=None, usage: dict | None = None,
            level: str | None = None, status_message: str | None = None,
            metadata: dict | None = None):
        """End the observation. usage: {"input": n, "output": m} token counts."""
        if self.ended:
            return
        self.ended = True
        try:
            kw: dict = {}
            if output is not None:
                kw["output"] = output
            if metadata:
                kw["metadata"] = metadata
            if usage:
                kw["usage_details"] = usage
            if self._completion_start is not None:
                kw["completion_start_time"] = self._completion_start
            if level:
                kw["level"] = level
            if status_message:
                kw["status_message"] = status_message
            if kw:
                self._raw.update(**kw)
            self._raw.end()
        except Exception as e:
            logger.debug("telemetry: end failed: %s", e)

    def update_trace(self, input=None, output=None, metadata: dict | None = None,
                     **_ignored):
        """Set trace-level fields. v4 SDK: trace name comes from the root
        span; trace input/output go through set_trace_io; everything else
        (route, tags) is stamped on the root span as metadata."""
        try:
            if input is not None or output is not None:
                io_kw = {}
                if input is not None:
                    io_kw["input"] = input
                if output is not None:
                    io_kw["output"] = output
                self._raw.set_trace_io(**io_kw)
            if metadata:
                self._raw.update(metadata=metadata)
        except Exception as e:
            logger.debug("telemetry: update_trace failed: %s", e)


# ── pipeline root management ────────────────────────────────────────────────
# Safe as a module global because _llm_lock serialises pipeline runs.
_current_root = _NOOP


def start_pipeline(name: str, input_text: str, rid: str = "-",
                   tags: list[str] | None = None):
    """Open the root span (= trace) for one pipeline run."""
    global _current_root
    if not ENABLED or _client is None:
        _current_root = _NOOP
        return _NOOP
    try:
        obs = _Obs(_client.start_observation(
            name=name, as_type="span", input=input_text,
            metadata={"rid": rid, "tags": ",".join(tags or [])},
        ))
        obs.update_trace(input=input_text)
        _current_root = obs
        return obs
    except Exception as e:
        logger.debug("telemetry: start_pipeline failed: %s", e)
        _current_root = _NOOP
        return _NOOP


def root():
    """The current pipeline's root observation (no-op outside a pipeline)."""
    return _current_root


def end_pipeline(obs, output=None, route: str | None = None,
                 level: str | None = None, status_message: str | None = None):
    """Close the root span and stamp trace-level output/route."""
    global _current_root
    try:
        if route is not None:
            obs.update_trace(metadata={"route": route})
        if output is not None:
            obs.update_trace(output=output)
        obs.end(output=output, level=level, status_message=status_message,
                metadata={"route": route} if route else None)
    finally:
        _current_root = _NOOP
