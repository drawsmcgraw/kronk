#!/usr/bin/env python3
"""Generate the provisioned Grafana dashboards (telemetry/grafana/dashboards/*.json).

The JSON is the artifact Grafana loads; this script is how it is written,
so every panel shares one query/unit/legend convention. Edit here, run
`python3 telemetry/grafana/build_dashboards.py`, commit both.

Queries are TraceQL (Tempo 3.1 — metrics functions over span selectors).
Span names and attributes come from orchestrator/telemetry.py,
tool_service/telemetry.py and voice_tracer/convert.py.
"""
import json
import os

DS = {"type": "tempo", "uid": "tempo"}
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dashboards")

PIPELINE = r'name =~ "pipeline\\..*"'


def q(query, ref="A", **kw):
    # No explicit step: Tempo picks one from the range. A fixed coarse step
    # (1h over a 3h window) dropped buckets at the window edges (2026-11-08).
    t = {"refId": ref, "datasource": DS, "queryType": "traceql", "query": query,
         "filters": [], "limit": 20, "tableType": "traces",
         "metricsQueryType": "range", "step": kw.pop("step", "")}
    t.update(kw)
    return t


def instant(query, ref="A"):
    return q(query, ref, metricsQueryType="instant")


def search(query, ref="A", limit=50, table="traces"):
    return q(query, ref, limit=limit, tableType=table, spss=3)


_id = [0]


def panel(title, targets, x, y, w, h, ptype="timeseries", unit=None, desc=None, **extra):
    _id[0] += 1
    p = {"id": _id[0], "type": ptype, "title": title, "datasource": DS,
         "gridPos": {"x": x, "y": y, "w": w, "h": h}, "targets": targets,
         "fieldConfig": {"defaults": {}, "overrides": []}, "options": {}}
    if unit:
        p["fieldConfig"]["defaults"]["unit"] = unit
    if desc:
        p["description"] = desc
    if ptype == "timeseries":
        p["options"] = {"legend": {"displayMode": "list", "placement": "bottom", "showLegend": True},
                        "tooltip": {"mode": "multi", "sort": "desc"}}
        p["fieldConfig"]["defaults"]["custom"] = {"lineWidth": 1, "fillOpacity": 8, "spanNulls": True,
                                                   "showPoints": "auto", "pointSize": 4}
    if ptype == "stat":
        p["options"] = {"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
                        "colorMode": "none", "graphMode": "none", "textMode": "value_and_name"}
    if ptype == "table":
        p["options"] = {"showHeader": True, "sortBy": [{"displayName": "Duration", "desc": True}]}
    p.update(extra)
    return p


def text(md, x, y, w, h, title=""):
    _id[0] += 1
    return {"id": _id[0], "type": "text", "title": title, "gridPos": {"x": x, "y": y, "w": w, "h": h},
            "options": {"mode": "markdown", "content": md}}


def dashboard(uid, title, panels, tags=("kronk",), refresh="1m", time_from="now-24h", templating=None):
    _id[0] = 0
    return {
        "uid": uid, "title": title, "tags": list(tags), "timezone": "browser", "editable": True,
        "graphTooltip": 1, "schemaVersion": 39, "version": 1, "refresh": refresh,
        "time": {"from": time_from, "to": "now"},
        "templating": {"list": templating or []},
        "panels": panels,
        "links": [{"title": "Kronk dashboards", "type": "dashboards", "tags": ["kronk"], "asDropdown": True,
                   "includeVars": False, "keepTime": True}],
    }


def p50p95(selector, by=None):
    s = f"{{ {selector} }} | quantile_over_time(duration, .5, .95)"
    return s + (f" by ({by})" if by else "")


def count(selector, by=None):
    s = f"{{ {selector} }} | count_over_time()"
    return s + (f" by ({by})" if by else "")


# ── Overview ────────────────────────────────────────────────────────────────
overview = dashboard("kronk-overview", "Kronk — Overview", [
    text(
        "**Every interaction, end to end.** Chat UI, OpenAI shim, Ollama shim and voice "
        "(`pipeline.*` spans from the orchestrator) plus HA voice runs (`voice.run`, from "
        "voice_tracer) and the music blueprint's fast path (`blueprint.run`).\n\n"
        "Voice is measured from the moment HA received the satellite's stream (wake-word "
        "detection runs on the satellite and is not visible to HA) to the TTS request. "
        "Traces become searchable about one to two minutes after the request (Tempo cuts a "
        "block every 30 s; voice runs wait for their Kronk join). "
        "Latency budget (CLAUDE.md tenet 12): ~2 s local tier, 15–25 s Kronk tier.",
        0, 0, 24, 4),
    panel("Requests (range)", [instant(count(PIPELINE))], 0, 4, 4, 4, "stat", desc="Kronk pipeline runs, all transports"),
    panel("p50 / p95 end-to-end", [instant(p50p95(PIPELINE))], 4, 4, 6, 4, "stat", unit="s"),
    panel("Errors", [instant(count(PIPELINE + " && status = error"))], 10, 4, 4, 4, "stat"),
    panel("Voice runs", [instant(count('name = "voice.run"'))], 14, 4, 4, 4, "stat", desc="HA Assist pipeline runs (voice_tracer)"),
    panel("Voice p50 / p95", [instant(p50p95('name = "voice.run"'))], 18, 4, 6, 4, "stat", unit="s"),

    panel("Requests over time by transport", [q(count(PIPELINE, "span.kronk.transport"))],
          0, 8, 12, 8, desc="webui = chat page, openai = /v1 shim, ollama = /api/chat, voice = /voice/api/chat (HA)"),
    panel("End-to-end p50 / p95 by transport", [q(p50p95(PIPELINE, "span.kronk.transport"))],
          12, 8, 12, 8, unit="s", desc="pipeline root span: request in → last token"),

    panel("Voice runs over time by who answered", [q(count('name = "voice.run"', "span.voice.handled_by"))],
          0, 16, 12, 8, desc="blueprint = MA fast path; ha_local = HA built-in intent; kronk = Kronk; blueprint_then_kronk = catch-all hand-off"),
    panel("Voice end-to-end p50 / p95 by who answered", [q(p50p95('name = "voice.run"', "span.voice.handled_by"))],
          12, 16, 12, 8, unit="s"),

    panel("Errors over time by transport", [q(count(PIPELINE + " && status = error", "span.kronk.transport"))],
          0, 24, 12, 8),
    panel("Routes over time", [q(count(PIPELINE, "span.kronk.route"))], 12, 24, 12, 8,
          desc="direct = coordinator answered or delegated; a name = shortcut-pinned specialist"),
])

# ── Stages ──────────────────────────────────────────────────────────────────
stages = dashboard("kronk-stages", "Kronk — Stages", [
    text("**p50 / p95 per stage over time.** Voice front half (HA), blueprint, Kronk routing, "
         "LLM rounds (time to first token and generation separately), tools, and tool internals "
         "(Music Assistant commands, HA service calls, the playback-verify wait).", 0, 0, 24, 3),
    panel("Voice stages: STT / intent / TTS", [q(p50p95(r'name =~ "voice\\.(stt|intent|tts)"', "name"))],
          0, 3, 12, 8, unit="s", desc="voice.stt = stream start → transcript (listen + transcribe); voice.intent = HA intent match, blueprint or Kronk; voice.tts = TTS request (streamed, so near zero)"),
    panel("STT: listening vs transcribing", [q(p50p95(r'name =~ "voice\\.stt\\.(listen|transcribe)"', "name"))],
          12, 3, 12, 8, unit="s", desc="listen = speech + trailing silence until VAD end; transcribe = Whisper"),
    panel("Blueprint fast path by trigger", [q(p50p95('name = "blueprint.run"', "span.bp.trigger_id"))],
          0, 11, 12, 8, unit="s"),
    panel("Kronk routing decision", [q(p50p95('name = "routing.decide"'))], 12, 11, 12, 8, unit="s",
          desc="deterministic shortcuts; should stay in the milliseconds"),

    panel("LLM round duration by model and phase", [q(p50p95(r'name =~ "llm\\..*"', "span.gen_ai.request.model, span.kronk.phase"))],
          0, 19, 12, 8, unit="s", desc="plan_N = deciding/calling tools, synthesis = writing the answer"),
    panel("LLM time to first token by model", [q(r'{ name =~ "llm\\..*" } | quantile_over_time(span.kronk.ttft_ms, .5, .95) by (span.gen_ai.request.model)')],
          12, 19, 12, 8, unit="ms", desc="prompt processing + queueing (+ hidden reasoning); tool-call-only rounds have no TTFT"),
    panel("LLM generation tokens/s by model", [q(r'{ name =~ "llm\\..*" } | avg_over_time(span.kronk.tokens_per_sec) by (span.gen_ai.request.model)')],
          0, 27, 12, 8, desc="completion tokens / (end − first token)"),
    panel("Agent loops by agent", [q(p50p95(r'name =~ "agent\\..*"', "name"))], 12, 27, 12, 8, unit="s"),

    panel("Tools by name", [q(p50p95(r'name =~ "tool\\..*"', "name"))], 0, 35, 12, 8, unit="s",
          desc="orchestrator side: tool call → result, including ask_* delegations"),
    panel("Tool internals (tool_service)", [q(p50p95(r'resource.service.name = "kronk-tool-service" && name =~ "(ma|ha|music|ssh)\\..*"', "name"))],
          12, 35, 12, 8, unit="s"),
    panel("Music Assistant commands", [q(p50p95('name = "ma.command"', "span.ma.command"))], 0, 43, 12, 8, unit="s"),
    panel("HA service calls (websocket)", [q(p50p95('name = "ha.call_service"', "span.ha.domain, span.ha.service"))],
          12, 43, 12, 8, unit="s", desc="music_assistant.play_media blocks until MA has resolved and queued the media"),
    panel("Outbound HTTP from tool_service (HA REST, NWS, SearXNG, pages)",
          [q(p50p95('resource.service.name = "kronk-tool-service" && kind = client', "span.http.status_code"))],
          0, 51, 12, 8, unit="s"),
    panel("tool_service endpoints", [q(p50p95('resource.service.name = "kronk-tool-service" && kind = server', "span.http.route"))],
          12, 51, 12, 8, unit="s"),
])

# ── Slowest requests ────────────────────────────────────────────────────────
slow_tpl = [{
    "name": "min", "label": "Slower than", "type": "textbox", "query": "5s",
    "current": {"text": "5s", "value": "5s"}, "options": [], "hide": 0,
}]
slowest = dashboard("kronk-slowest", "Kronk — Slowest requests", [
    text("Traces slower than **$min** in the time range (Tempo returns the first matches, so "
         "tighten the range or raise the threshold for a true top-N). Click a trace ID to open "
         "the waterfall. `kronk.rid` matches the `rid` in the orchestrator's `kronk.events` log "
         "lines, which also carry `trace_id`.", 0, 0, 24, 3),
    panel("Slow Kronk requests", [search(f"{{ {PIPELINE} && duration > $min }}")], 0, 3, 24, 10, "table",
          desc="pipeline root spans (one per request)"),
    panel("Slow voice runs (HA)", [search('{ name = "voice.run" && duration > $min }')], 0, 13, 12, 10, "table"),
    panel("Slow blueprint runs", [search('{ name = "blueprint.run" && duration > $min }')], 12, 13, 12, 10, "table"),
    panel("Slow spans inside requests (which stage)",
          [search(r'{ duration > $min && name !~ "pipeline\\..*|voice\\.run|POST .*|GET .*" } | select(span.kronk.rid, span.kronk.route, span.kronk.transport)',
                  table="spans")],
          0, 23, 24, 10, "table", desc="agent, llm, tool, ma, ha, music, voice.* spans over the threshold"),
    panel("Errored requests", [search(f"{{ {PIPELINE} && status = error }}")], 0, 33, 24, 10, "table"),
], templating=slow_tpl, refresh="")

# ── Music ───────────────────────────────────────────────────────────────────
music = dashboard("kronk-music", "Kronk — Music", [
    text("**`play_music` and the blueprint fast path.** Kronk path: `tool.play_music` → "
         "tool_service `POST /music` → `music.resolve` (MA search + scoring) / "
         "`music.pandora_station` / `music.genre_plan` → `music.play_verified` (HA `play_media` "
         "over websocket, then polling the player until it plays something new). Fast path: "
         "`blueprint.run` (HA sentence trigger → MA directly), joined to its voice run by time window.",
         0, 0, 24, 3),
    panel("Kronk path: play_music breakdown p50 / p95", [q(p50p95(r'resource.service.name = "kronk-tool-service" && name =~ "music\\..*"', "name"))],
          0, 3, 12, 8, unit="s"),
    panel("Kronk path vs fast path, p50 / p95", [
        q(p50p95('name = "tool.play_music"'), "A"),
        q(p50p95('name = "blueprint.run" && span.bp.outcome = "played"'), "B"),
    ], 12, 3, 12, 8, unit="s", desc="A = tool.play_music (Kronk, from the tool call); B = blueprint.run that played"),
    panel("play_media inside HA/MA (ha.call_service)", [q(p50p95('name = "ha.call_service" && span.ha.service = "play_media"'))],
          0, 11, 12, 8, unit="s", desc="HA blocks until MA resolved and queued; the 61 s of the Anime Pop incident lived here"),
    panel("Verify wait outcomes over time", [q(count('name = "music.play_verified"', "span.music.outcome"))],
          12, 11, 12, 8, desc="started / started_after_failure / failed / resolve_miss / no_start"),
    panel("Resolver outcomes over time", [q(count('name = "music.resolve"', "span.music.outcome"))],
          0, 19, 12, 8, desc="winner / below_threshold / nothing"),
    panel("Resolver: candidates and best score", [
        q(r'{ name = "music.resolve" } | avg_over_time(span.music.candidates)', "A"),
        q(r'{ name = "music.resolve" } | avg_over_time(span.music.best_score)', "B"),
    ], 12, 19, 12, 8),
    panel("MA commands p50 / p95", [q(p50p95('name = "ma.command"', "span.ma.command"))], 0, 27, 12, 8, unit="s"),
    panel("Blueprint runs over time by trigger and outcome", [q(count('name = "blueprint.run"', "span.bp.trigger_id, span.bp.outcome"))],
          12, 27, 12, 8),
    panel("Recent Kronk music requests", [search('{ name = "tool.play_music" }')], 0, 35, 12, 10, "table"),
    panel("Recent blueprint runs", [search('{ name = "blueprint.run" }')], 12, 35, 12, 10, "table"),
])

if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    for d in (overview, stages, slowest, music):
        path = os.path.join(OUT, d["uid"].replace("kronk-", "") + ".json")
        with open(path, "w") as f:
            json.dump(d, f, indent=1)
            f.write("\n")
        print("wrote", path, len(d["panels"]), "panels")
