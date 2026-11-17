"""The wrong-action signal (sprint M12): pure logic, no I/O.

A voice run is marked when the same satellite, within WINDOW_S seconds
after it ended, issues a correction:

- "not_that"  : the music_another form ("not that", "something else",
                "a different station", ...);
- "rate_down" : a thumbs down / "I don't like this song" (the rate form
                with thumb down);
- "stop"      : the playback form with action pause ("stop", "pause",
                "shut up", "stop the music");
- "near_repeat": the same request again (near_repeat() below).

The forms are routing's own (orchestrator/command_forms.py), not new
patterns (stopping rule, Drew 2026-11-11). Which form a later run was is
read, in this order:

1. Kronk's routing decision, when Kronk answered the run: the rule name
   and its fixed-vocabulary choice (`kronk.rule`, `kronk.direct_action`
   on the routing.decide span). No transcript text needed.
2. The transcript, matched against the same forms, only when
   TELEMETRY_RECORD_TEXT is on. This is the only way to see a stop that
   Home Assistant handled itself: HA's intent-end event names the targets
   (a media player) but not the intent, so "pause" and "resume" look the
   same without the words.

Near-repeat needs both transcripts and is skipped, never guessed, when
text recording is off (`voice.mark_basis = "rule_only"`).

The mark goes on the earlier run's `voice.run` span: voice_tracer holds a
satellite's run back from export until its correction window has closed
(ready_to_emit), so the mark is set before the span leaves. A dev run
(pipeline in VOICE_TRACER_DEV_PIPELINES) only ever pairs with dev runs.
"""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass

try:  # the voice_tracer image copies orchestrator/command_forms.py next to this file
    import command_forms
except ImportError:  # pragma: no cover - tests put orchestrator/ on sys.path
    from orchestrator import command_forms

WINDOW_S = 60.0          # a correction counts when it starts this soon after the run ended
GRACE_S = 90.0           # after the window: a run that started at its edge ends and is fetched
HOLD_MAX_S = 480.0       # the longest a run waits for a later run's classification, counted
                         # from when this process fetched it
AGE_LIMIT_S = 720.0      # never hold a run older than this (12 min): Tempo's ingestion slack is
                         # 15 m and a span older than it breaks TraceQL metrics
                         # (docs/features/telemetry.md, Gotchas)
NEAR_REPEAT_MIN = 0.75   # word-level similarity at or above which two requests are one request

CORRECTIONS = ("not_that", "rate_down", "stop")
CONTROL = "control"      # a playback or thumbs form that is not a correction (skip, louder,
                         # resume, thumbs up): never a near-repeat either


@dataclass
class RunFacts:
    """What the mark logic needs from one voice run."""
    run_id: str
    dev: bool
    satellite: str | None        # ha.satellite_id, else the device id; None → never marked
    start_s: float
    end_s: float
    transcript: str | None = None    # set only when text recording is on
    kronk_rule: str | None = None    # routing.decide kronk.rule of the joined Kronk trace
    kronk_action: str | None = None  # routing.decide kronk.direct_action
    trace_id: str | None = None      # this run's voice trace id (hex)
    fetched_at: float | None = None  # when this process fetched the run (HOLD_MAX_S counts from it)


@dataclass
class Mark:
    earlier_run_id: str
    later_run_id: str
    kind: str                    # one of CORRECTIONS or "near_repeat"
    after_s: float               # later.start - earlier.end
    earlier_trace_id: str | None = None
    later_trace_id: str | None = None


def facts_from_specs(specs: list, run_id: str, dev: bool, record_text: bool,
                     fetched_at: float | None = None) -> RunFacts | None:
    """RunFacts from a pipeline run's span tree (convert.pipeline_run_spans).
    The transcript is taken only when text recording is on."""
    if not specs:
        return None
    root = specs[0]
    a = root.attrs
    return RunFacts(
        run_id=run_id, dev=dev,
        satellite=a.get("ha.satellite_id") or a.get("kronk.origin.device_id"),
        start_s=root.start_ns / 1e9, end_s=root.end_ns / 1e9,
        transcript=(a.get("voice.transcript") if record_text else None),
        trace_id=format(root.trace_id, "032x") if root.trace_id else None,
        fetched_at=fetched_at,
    )


def _kind_of(cmd: tuple[str, str | None] | None) -> str | None:
    if cmd is None:
        return None
    rule, value = cmd
    if rule == "music_another":
        return "not_that"
    if rule == "rate":
        return "rate_down" if value == "down" else (CONTROL if value else None)
    if rule == "playback":
        return "stop" if value == "pause" else (CONTROL if value else None)
    return None


def correction_kind(f: RunFacts) -> str | None:
    """"not_that" | "rate_down" | "stop" | "control" | None for a run.

    Kronk's routing decision first; the transcript (only present with text
    recording on) when Kronk did not answer or its decision lacks the
    choice (a trace from before M12 has no kronk.direct_action)."""
    if f.kronk_rule in ("playback", "rate", "music_another"):
        kind = _kind_of((f.kronk_rule, f.kronk_action))
        if kind is not None:
            return kind
    if f.transcript:
        return _kind_of(command_forms.command_of(f.transcript))
    return None


_NON_WORD = re.compile(r"[^\w\s]+")


def words(text: str | None) -> list[str]:
    """Lowercase words with punctuation removed."""
    return _NON_WORD.sub(" ", (text or "").lower()).split()


def near_repeat(a: str | None, b: str | None) -> bool:
    """The one definition of near-repeat: both transcripts have words, and
    the word-level similarity of the two (difflib.SequenceMatcher ratio over
    the lowercased, punctuation-free word lists: 2 x matching words / total
    words) is at least NEAR_REPEAT_MIN (0.75). "Play anime pop radio" and
    "play anime pop radio station" (0.89) are one request; "play jazz" and
    "play blues" (0.5) are two."""
    wa, wb = words(a), words(b)
    if not wa or not wb:
        return False
    return difflib.SequenceMatcher(None, wa, wb, autojunk=False).ratio() >= NEAR_REPEAT_MIN


def find_mark(recent: list[RunFacts], later: RunFacts, text_on: bool,
              window_s: float = WINDOW_S) -> Mark | None:
    """The mark `later` puts on the run just before it, or None.

    The earlier run is the latest run on the same satellite (and the same
    side, dev or production) that started before `later`; only that one can
    be corrected. The gap is later.start - earlier.end, inclusive at
    window_s. A stop minutes after a request (a long song, then "stop") is
    outside the window and marks nothing."""
    if later.satellite is None:
        return None
    prev = [r for r in recent
            if r.run_id != later.run_id and r.dev == later.dev
            and r.satellite == later.satellite and r.start_s < later.start_s]
    if not prev:
        return None
    earlier = max(prev, key=lambda r: (r.start_s, r.end_s))
    gap = max(0.0, later.start_s - earlier.end_s)
    if gap > window_s:
        return None
    kind = correction_kind(later)
    if kind == CONTROL:
        return None
    if kind is None:
        if not (text_on and near_repeat(earlier.transcript, later.transcript)):
            return None
        kind = "near_repeat"
    return Mark(earlier.run_id, later.run_id, kind, round(gap, 1),
                earlier.trace_id, later.trace_id)


def apply_mark(earlier_root, later_root, mark: Mark) -> None:
    """Set the mark's attributes on the two runs' voice.run root specs."""
    earlier_root.attrs.update({
        "voice.corrected_by": mark.kind,
        "voice.corrected_after_s": mark.after_s,
        "voice.corrected_by_run_id": mark.later_run_id,
    })
    if mark.later_trace_id:
        earlier_root.attrs["voice.corrected_by_trace_id"] = mark.later_trace_id
    if later_root is not None:
        later_root.attrs.update({
            "voice.correction_kind": mark.kind,
            "voice.correction_of_run_id": mark.earlier_run_id,
        })
        if mark.earlier_trace_id:
            later_root.attrs["voice.correction_of_trace_id"] = mark.earlier_trace_id


def ready_to_emit(f: RunFacts, now: float, unclassified: list[RunFacts],
                  window_s: float = WINDOW_S, grace_s: float = GRACE_S,
                  hold_max_s: float = HOLD_MAX_S, age_limit_s: float = AGE_LIMIT_S) -> bool:
    """True once nothing can still mark `f`: its window plus grace has
    passed and no later run from the same satellite that started inside the
    window is still waiting to be classified. A wait for such a run is
    capped at hold_max_s from when this process fetched `f` (so after a
    restart the re-fetched runs get their turn), and no run is held past
    age_limit_s after it ended."""
    if f.satellite is None:
        return True
    if now - f.end_s >= age_limit_s:
        return True
    if now < f.end_s + window_s + grace_s:
        return False
    waiting = any(u.run_id != f.run_id and u.dev == f.dev and u.satellite == f.satellite
                  and f.start_s < u.start_s <= f.end_s + window_s for u in unclassified)
    if not waiting:
        return True
    since = f.fetched_at if f.fetched_at is not None else f.end_s
    return now - since >= hold_max_s


# ── Kronk's trace (Tempo trace-by-id) ───────────────────────────────────────

def _attr_value(v: dict):
    if not isinstance(v, dict) or not v:
        return None
    if "arrayValue" in v:
        return [_attr_value(x) for x in (v["arrayValue"] or {}).get("values") or []]
    (kind, val), = list(v.items())[:1]
    if kind == "intValue":
        try:
            return int(val)
        except (TypeError, ValueError):
            return val
    return val


def routing_from_trace(payload: dict) -> dict:
    """From a Tempo trace-by-id payload (/api/v2/traces/<id> → {"trace":
    {"resourceSpans": ...}}, or /api/traces/<id> → {"batches": ...}): the
    routing rule, its direct choice, and the tools called, in start order.
    Text attributes are never read."""
    rs = ((payload or {}).get("trace") or {}).get("resourceSpans") or (payload or {}).get("batches") or []
    spans = []
    for r in rs:
        for ss in r.get("scopeSpans") or r.get("instrumentationLibrarySpans") or []:
            spans.extend(ss.get("spans") or [])
    out: dict = {"rule": None, "direct_action": None, "tools": []}
    tools = []
    for sp in spans:
        attrs = {a.get("key"): _attr_value(a.get("value")) for a in sp.get("attributes") or []}
        name = sp.get("name") or ""
        if name == "routing.decide":
            out["rule"] = attrs.get("kronk.rule")
            out["direct_action"] = attrs.get("kronk.direct_action")
        elif name.startswith("tool."):
            tools.append((int(sp.get("startTimeUnixNano") or 0), attrs.get("kronk.tool") or name[5:]))
    out["tools"] = [t for _, t in sorted(tools)]
    return out
