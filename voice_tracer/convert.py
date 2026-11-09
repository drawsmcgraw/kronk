"""Turn what Home Assistant already records into span trees.

Pure functions, no I/O: tested against recorded payloads in tests/fixtures/.

Two sources (docs/plans/TELEMETRY_V2_PLAN.md → "Voice listener"):

- An Assist **pipeline run** from `assist_pipeline/pipeline_debug/get`: a
  list of timestamped events (run-start, stt-*, intent-*, tts-*, run-end,
  error). One trace per run, root `voice.run`, one child per stage.
- A blueprint **automation trace** from `trace/get`: timestamped steps keyed
  by path. One trace per run, root `blueprint.run`, a child per service
  call / wait / Kronk hand-off.

Ids are derived from HA's own run ids (sha256), so emitting the same run
twice — after a restart, say — yields the same trace and span ids and
Tempo de-duplicates instead of showing two traces.

Text (transcripts, spoken answers, media names) stays off the spans unless
record_text=True; lengths are always recorded.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

STATUS_OK = "ok"
STATUS_ERROR = "error"


@dataclass
class SpanSpec:
    key: str                       # unique within the tree; parent refers to it
    name: str
    start_ns: int
    end_ns: int
    attrs: dict = field(default_factory=dict)
    parent: str | None = None
    status: str = STATUS_OK
    status_message: str | None = None
    links: list[str] = field(default_factory=list)   # trace ids (hex) to link to
    span_id: int = 0               # filled by assign_ids
    trace_id: int = 0


def _ns(ts: str | None) -> int | None:
    if not ts:
        return None
    dt = datetime.fromisoformat(ts)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1_000_000_000)


def _clean_attrs(attrs: dict) -> dict:
    return {k: v for k, v in attrs.items() if v is not None}


def _digest(*parts: str) -> bytes:
    return hashlib.sha256(":".join(parts).encode()).digest()


def trace_id_for(source: str, run_id: str) -> int:
    return int.from_bytes(_digest("kronk-voice-tracer", source, run_id)[:16], "big") or 1


def assign_ids(source: str, run_id: str, specs: list[SpanSpec]) -> list[SpanSpec]:
    tid = trace_id_for(source, run_id)
    for s in specs:
        s.trace_id = tid
        s.span_id = int.from_bytes(_digest(source, run_id, s.key)[:8], "big") or 1
    return specs


# ── Assist pipeline runs ────────────────────────────────────────────────────

def _events_by_type(run: dict) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for e in run.get("events") or []:
        out.setdefault(e.get("type", "?"), []).append(e)
    return out


def _first(ev: dict[str, list[dict]], t: str) -> dict | None:
    lst = ev.get(t)
    return lst[0] if lst else None


def handled_by(intent_start: dict | None, intent_end: dict | None) -> str:
    """Who answered: kronk (the conversation agent), ha_local (a built-in
    intent or the sentence-trigger blueprint), or unknown. A blueprint run
    inside the intent window refines ha_local → blueprint (see join_blueprint)."""
    data_end = (intent_end or {}).get("data") or {}
    data_start = (intent_start or {}).get("data") or {}
    if "processed_locally" in data_end:
        return "ha_local" if data_end["processed_locally"] else "kronk"
    if data_start.get("engine", "").startswith("conversation.ollama"):
        return "kronk"
    return "unknown"


def pipeline_run_spans(run: dict, run_id: str, pipeline_name: str | None = None,
                       record_text: bool = False) -> list[SpanSpec]:
    """Span tree for one pipeline-debug run. Empty list if there is no
    run-start event (nothing to time)."""
    ev = _events_by_type(run)
    start = _first(ev, "run-start")
    if not start:
        return []
    all_ts = [_ns(e["timestamp"]) for e in run.get("events") or [] if e.get("timestamp")]
    end_ev = _first(ev, "run-end")
    t0 = _ns(start["timestamp"])
    t_end = _ns(end_ev["timestamp"]) if end_ev else max(all_ts)

    sd = start.get("data") or {}
    stt_start, stt_end = _first(ev, "stt-start"), _first(ev, "stt-end")
    vad_start, vad_end = _first(ev, "stt-vad-start"), _first(ev, "stt-vad-end")
    int_start, int_end = _first(ev, "intent-start"), _first(ev, "intent-end")
    tts_start, tts_end = _first(ev, "tts-start"), _first(ev, "tts-end")
    wake_start, wake_end = _first(ev, "wake_word-start"), _first(ev, "wake_word-end")
    error = _first(ev, "error")

    # A stage with no end event (the run died inside it) runs to the error
    # event when there is one, else to the run end.
    t_fallback = _ns(error["timestamp"]) if error and error.get("timestamp") else t_end

    def _stage_end(end_event, *nexts):
        for e in (end_event, *nexts):
            if e is not None and e.get("timestamp"):
                return _ns(e["timestamp"])
        return t_fallback

    isd = (int_start or {}).get("data") or {}
    ied = (int_end or {}).get("data") or {}
    transcript = (((stt_end or {}).get("data") or {}).get("stt_output") or {}).get("text")
    if transcript is None:
        transcript = isd.get("intent_input")
    response = ((ied.get("intent_output") or {}).get("response") or {})
    speech = (((response.get("speech") or {}).get("plain") or {}).get("speech"))

    root = SpanSpec(
        key="run", name="voice.run", start_ns=t0, end_ns=t_end,
        attrs=_clean_attrs({
            "ha.pipeline_id": sd.get("pipeline"),
            "ha.pipeline_name": pipeline_name,
            "ha.pipeline_run_id": run_id,
            "ha.conversation_id": sd.get("conversation_id") or isd.get("conversation_id"),
            "ha.satellite_id": sd.get("satellite_id") or isd.get("satellite_id"),
            "kronk.origin.device_id": isd.get("device_id"),
            "voice.language": sd.get("language"),
            "voice.handled_by": handled_by(int_start, int_end),
            "voice.response_type": response.get("response_type"),
            "voice.transcript_chars": len(transcript) if transcript is not None else None,
            "voice.speech_chars": len(speech) if speech is not None else None,
            "voice.transcript": transcript.strip() if (record_text and transcript) else None,
            "voice.speech": speech if (record_text and speech) else None,
            # Wake-word detection runs on the satellite; HA only sees the
            # stream that follows it. Stated, not faked (plan: known gap).
            "voice.wake_detection": "satellite" if not wake_start else "ha",
            "voice.stages": ",".join(t for t in ("wake_word", "stt", "intent", "tts")
                                     if _first(ev, f"{t}-start")),
        }),
    )
    specs = [root]

    if wake_start:
        specs.append(SpanSpec("wake", "voice.wake", _ns(wake_start["timestamp"]),
                              _ns((wake_end or wake_start)["timestamp"]), parent="run",
                              attrs=_clean_attrs({"voice.engine": (wake_start.get("data") or {}).get("entity_id")})))

    if stt_start:
        s0 = _ns(stt_start["timestamp"])
        s1 = _stage_end(stt_end, int_start)
        std = stt_start.get("data") or {}
        specs.append(SpanSpec("stt", "voice.stt", s0, s1, parent="run", attrs=_clean_attrs({
            "voice.engine": std.get("engine"),
            "voice.vad_start_ms": ((vad_start or {}).get("data") or {}).get("timestamp"),
            "voice.vad_end_ms": ((vad_end or {}).get("data") or {}).get("timestamp"),
            "voice.transcript_chars": len(transcript) if transcript is not None else None,
        })))
        if vad_end:
            v1 = _ns(vad_end["timestamp"])
            specs.append(SpanSpec("stt.listen", "voice.stt.listen", s0, v1, parent="stt",
                                  attrs=_clean_attrs({"voice.vad_start_ms": ((vad_start or {}).get("data") or {}).get("timestamp"),
                                                      "voice.vad_end_ms": (vad_end.get("data") or {}).get("timestamp")})))
            specs.append(SpanSpec("stt.transcribe", "voice.stt.transcribe", v1, s1, parent="stt",
                                  attrs=_clean_attrs({"voice.engine": std.get("engine")})))

    if int_start:
        i0 = _ns(int_start["timestamp"])
        i1 = _stage_end(int_end, tts_start)
        specs.append(SpanSpec("intent", "voice.intent", i0, i1, parent="run", attrs=_clean_attrs({
            "voice.engine": isd.get("engine"),
            "voice.prefer_local_intents": isd.get("prefer_local_intents"),
            "voice.processed_locally": ied.get("processed_locally"),
            "voice.handled_by": handled_by(int_start, int_end),
            "voice.response_type": response.get("response_type"),
            "kronk.origin.device_id": isd.get("device_id"),
            "voice.speech_chars": len(speech) if speech is not None else None,
            "voice.speech": speech if (record_text and speech) else None,
        })))

    if tts_start:
        t_0 = _ns(tts_start["timestamp"])
        t_1 = _stage_end(tts_end, end_ev)
        ttd = tts_start.get("data") or {}
        specs.append(SpanSpec("tts", "voice.tts", t_0, t_1, parent="run", attrs=_clean_attrs({
            "voice.engine": ttd.get("engine"),
            "voice.voice": ttd.get("voice"),
            # HA streams TTS: this span is the request, not the synthesis.
            "voice.streamed": bool((sd.get("tts_output") or {}).get("stream_response")),
        })))

    if error:
        ed = error.get("data") or {}
        msg = f"{ed.get('code', 'error')}: {ed.get('message', '')}".strip(": ")
        root.status, root.status_message = STATUS_ERROR, msg[:200]
        root.attrs["voice.error_code"] = ed.get("code")
        # the stage that was open when the error fired
        t_err = _ns(error["timestamp"])
        for s in specs[1:]:
            if s.start_ns <= t_err <= s.end_ns + 1_000_000 and s.parent == "run":
                s.status, s.status_message = STATUS_ERROR, msg[:200]
    return specs


def intent_window(specs: list[SpanSpec]) -> tuple[str | None, float, float] | None:
    """(device_id, start_s, end_s) of the intent stage — the window in which
    a Kronk pipeline root for the same device must have started."""
    spec = next((s for s in specs if s.key == "intent"), None)
    if spec is None:
        return None
    dev = spec.attrs.get("kronk.origin.device_id")
    return dev, spec.start_ns / 1e9, spec.end_ns / 1e9


def apply_kronk_join(specs: list[SpanSpec], kronk_trace_ids: list[str]) -> None:
    """Record the Kronk trace(s) that served this run on the intent span (and
    the root): attribute + span link. More than one match is kept and
    flagged — report it, don't guess."""
    intent = next((s for s in specs if s.key == "intent"), None)
    root = specs[0] if specs else None
    if intent is None or root is None:
        return
    n = len(kronk_trace_ids)
    for s in (intent, root):
        s.attrs["kronk.join_matches"] = n
        if n:
            s.attrs["kronk.trace_id"] = kronk_trace_ids[0]
            s.links = list(dict.fromkeys(s.links + kronk_trace_ids))
        if n > 1:
            s.attrs["kronk.join_ambiguous"] = True
            s.attrs["kronk.trace_ids"] = list(kronk_trace_ids)


# ── Blueprint automation traces ─────────────────────────────────────────────

_SERVICE_RE = re.compile(r"^[a-z_]+\.[a-z_]+$")


def _step_kind(path: str, step: dict, config_aliases: dict[str, str]) -> tuple[str, dict] | None:
    """(span name, attrs) for a step worth a span, else None."""
    result = step.get("result") or {}
    params = result.get("params") or {}
    cv = step.get("changed_variables") or {}
    alias = config_aliases.get(path) or ""
    domain, service = params.get("domain"), params.get("service")
    if domain and service:
        name = f"ha.{domain}.{service}"
        if domain == "conversation" and service == "process":
            name = "bp.handoff_kronk"
        sd = params.get("service_data") or {}
        return name, _clean_attrs({
            "ha.domain": domain, "ha.service": service,
            "ha.entity_id": (params.get("target") or {}).get("entity_id") or sd.get("entity_id"),
            "bp.media_type": sd.get("media_type"),
            "bp.agent_id": sd.get("agent_id"),
        })
    if "wait" in result:                 # the wait_template / wait_for_trigger step itself
        w = result.get("wait") or {}
        return "bp.wait", _clean_attrs({"bp.wait_completed": w.get("completed"),
                                        "bp.wait_remaining_s": w.get("remaining")})
    if step.get("error"):
        return "bp.step", {"bp.alias": alias or None}
    return None


def _aliases(config: dict) -> dict[str, str]:
    """path → alias for every action node in the automation config, using
    HA's trace path grammar (action/N, then/else/sequence/choose/N/sequence)."""
    out: dict[str, str] = {}

    def walk(node, path):
        if isinstance(node, list):
            for i, n in enumerate(node):
                walk(n, f"{path}/{i}")
        elif isinstance(node, dict):
            if node.get("alias"):
                out[path] = node["alias"]
            for k in ("then", "else", "sequence", "default"):
                if k in node:
                    walk(node[k], f"{path}/{k}")
            for i, c in enumerate(node.get("choose") or []):
                walk(c.get("sequence"), f"{path}/choose/{i}/sequence")

    walk((config or {}).get("actions") or (config or {}).get("action") or [], "action")
    return out


def automation_trace_spans(tr: dict, record_text: bool = False) -> list[SpanSpec]:
    """Span tree for one automation trace (HA `trace/get`)."""
    ts = tr.get("timestamp") or {}
    t0, t1 = _ns(ts.get("start")), _ns(ts.get("finish"))
    if t0 is None:
        return []
    steps: list[tuple[int, str, dict]] = []
    for path, lst in (tr.get("trace") or {}).items():
        for st in lst:
            if st.get("timestamp"):
                steps.append((_ns(st["timestamp"]), path, st))
    steps.sort(key=lambda x: x[0])
    if t1 is None:
        t1 = steps[-1][0] if steps else t0

    # Variables the blueprint computed up front (action/0) and at the end.
    variables: dict = {}
    for _, _, st in steps:
        variables.update(st.get("changed_variables") or {})
    handed = any((st.get("result") or {}).get("params", {}).get("domain") == "conversation"
                 for _, _, st in steps)
    outcome = ("handed_to_kronk" if handed else
               "played" if variables.get("started") else
               "failed" if tr.get("error") or variables.get("play_ok") is False else "finished")
    action_data = variables.get("action_data") if isinstance(variables.get("action_data"), dict) else {}
    media_name = action_data.get("media_id")
    root = SpanSpec(
        key="run", name="blueprint.run", start_ns=t0, end_ns=t1,
        attrs=_clean_attrs({
            "ha.automation": tr.get("item_id"),
            "ha.run_id": tr.get("run_id"),
            "ha.context_id": (tr.get("context") or {}).get("id"),
            "bp.trigger_id": variables.get("trigger_id"),
            "bp.version": variables.get("version"),
            "bp.state": tr.get("state"),
            "bp.script_execution": tr.get("script_execution"),
            "bp.last_step": tr.get("last_step"),
            "bp.outcome": outcome,
            "bp.target": variables.get("verify_entity"),
            "bp.player": variables.get("mass_player_name"),
            "bp.media_type": action_data.get("media_type"),
            "kronk.origin.device_id": variables.get("assist_device_id") or None,
            "bp.media_name_chars": len(media_name) if isinstance(media_name, str) else None,
            "bp.media_name": media_name if (record_text and isinstance(media_name, str)) else None,
            "bp.area_or_player": variables.get("area_or_player_name") if record_text else None,
            "bp.steps": len(steps),
        }),
    )
    if tr.get("error"):
        root.status, root.status_message = STATUS_ERROR, str(tr["error"])[:200]
    specs = [root]

    aliases = _aliases(tr.get("config") or {})
    for i, (t, path, st) in enumerate(steps):
        kind = _step_kind(path, st, aliases)
        if not kind:
            continue
        name, attrs = kind
        end = steps[i + 1][0] if i + 1 < len(steps) else t1
        attrs["bp.path"] = path
        if aliases.get(path):
            attrs["bp.alias"] = aliases[path]
        spec = SpanSpec(key=path, name=name, start_ns=t, end_ns=max(end, t), parent="run", attrs=attrs)
        if st.get("error"):
            spec.status, spec.status_message = STATUS_ERROR, str(st["error"])[:200]
        specs.append(spec)
    return specs


def join_blueprint(voice_specs: list[SpanSpec], bp_specs: list[SpanSpec]) -> bool:
    """If the blueprint run started inside this voice run's intent stage,
    link both ways and mark the voice run as handled by the blueprint.
    Returns True when joined."""
    win = intent_window(voice_specs)
    if not win or not bp_specs:
        return False
    _, s, e = win
    bp_root = bp_specs[0]
    if bp_root.attrs.get("ha.pipeline_run_id"):
        return False                       # already joined to another run
    # A sentence-trigger automation starts inside the intent stage that
    # matched it — never before it. 50 ms covers clock jitter between
    # HA's event timestamps; the next utterance is seconds away.
    bp_start = bp_root.start_ns / 1e9
    if not (s <= bp_start <= e + 0.05):
        return False
    v_root, v_intent = voice_specs[0], next(x for x in voice_specs if x.key == "intent")
    bp_tid = format(bp_root.trace_id, "032x") if bp_root.trace_id else None
    v_tid = format(v_root.trace_id, "032x") if v_root.trace_id else None
    handed = bp_root.attrs.get("bp.outcome") == "handed_to_kronk"
    for s_ in (v_root, v_intent):
        if handed or s_.attrs.get("voice.handled_by") == "kronk":
            s_.attrs["voice.handled_by"] = "blueprint_then_kronk"
        else:
            s_.attrs["voice.handled_by"] = "blueprint"
        s_.attrs["ha.blueprint_run_id"] = bp_root.attrs.get("ha.run_id")
        if bp_tid:
            s_.attrs["ha.blueprint_trace_id"] = bp_tid
            s_.links = list(dict.fromkeys(s_.links + [bp_tid]))
    bp_root.attrs["ha.pipeline_run_id"] = v_root.attrs.get("ha.pipeline_run_id")
    if v_tid:
        bp_root.attrs["voice.trace_id"] = v_tid
        bp_root.links = list(dict.fromkeys(bp_root.links + [v_tid]))
    return True
