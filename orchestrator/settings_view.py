"""What the /settings page shows (sprint M21 step 2, criteria 1 to 3), read
only. Built per request from the values in effect, the live files, their
git history and tool_service's /config/effective.

Nothing here writes. Nothing here reads a secret: the environment tables
are allow-lists (ENV below, tool_service's settings_info.ENV), and
SETTINGS_PASSPHRASE_HASH is reported only as "editing on/off".
"""
import json
import os
import time
from pathlib import Path

import agents
import config_files
import config_loader
import errors
import routing
import servers
import settings_git
import settings_store
import tools

WHERE_PROD = "docker-compose.yml (orchestrator environment); commit, tag, scripts/deploy_tag.sh deploy"
WHERE_DEV = ".env.dev or docker-compose.yml (orchestrator-dev); then the dev up line"

# The orchestrator's environment settings that shape behaviour: (name,
# default in code, what it does). An allow-list: nothing else is read.
ENV = (
    ("COORDINATOR_MODEL", "mistral-nemo:12b", "The coordinator's model (and the default for the agents)"),
    ("RESEARCH_AGENT_MODEL", "COORDINATOR_MODEL", "The research agent's model"),
    ("CODING_AGENT_MODEL", "COORDINATOR_MODEL", "The coding agent's model"),
    ("DEVOPS_AGENT_MODEL", "COORDINATOR_MODEL", "The devops agent's model"),
    ("TALKIE_MODEL", "talkie", "Talkie's model"),
    ("VOICE_STREAM", "off", "Sentence streaming on the speech mount (/voice/api/chat)"),
    ("MODEL_LOCK", "server", "server: one lock per model server; pipeline: the pre-M9 lock (rollback)"),
    ("ERROR_STYLE", "debug", "How errors read on /message, /api/chat and /v1 (debug or friendly)"),
    ("ERROR_STYLE_VOICE", "friendly", "How errors read on the speech mount"),
    ("LOCATION", "Laurel, MD", "The household's location in prompts and weather"),
    ("KRONK_TZ", "America/New_York", "The house's zone for the date and time every prompt states"),
    ("HISTORY_MAX_MESSAGES", "40", "Messages kept per chat session"),
    ("ASSISTANT_PROMPT_CHARS", "1500", "Characters of session history the assistant agent sees"),
    ("SESSION_IDLE_PRUNE_DAYS", "30", "Idle sessions are pruned after this many days"),
    ("WEATHER_CTX_MAX_AGE_S", "(tuning.yaml)", "Overrides tuning.yaml weather_context.max_age_s when set"),
    ("TELEMETRY_ENABLED", "false", "OpenTelemetry tracing"),
    ("TELEMETRY_RECORD_TEXT", "false", "What was said and answered goes into spans and logs"),
    ("OTEL_SERVICE_NAME", "kronk-orchestrator", "The service name traces carry (the dev tag)"),
    ("LLM_SERVICE_URL", "http://localhost:8002", "LiteLLM"),
    ("TOOL_SERVICE_URL", "http://localhost:8003", "tool_service"),
    ("SETTINGS_INSTANCE", "production", "Which instance this page belongs to"),
    ("SETTINGS_DEV_URL", "(none)", "The dev orchestrator, for apply to dev first / bring over from dev"),
    ("KRONK_RELEASE", "(unknown)", "The release running, set by scripts/deploy_tag.sh"),
)

# Structural guards: code, never a setting. Named so nobody looks for them
# in a file; an edit on this page cannot loosen any of them (criterion 11).
GUARDS = (
    ("Magic mirror update", "Only the exact sentence 'update the magic mirror' may run update_magicmirror "
     "(routing._MM_UPDATE_RE; tools._tool_update_magicmirror checks the user's own words)",
     "orchestrator/routing.py, orchestrator/tools.py (sprint M4b)"),
    ("Devops tools", "The devops agent is offered only its own tools, never web_search or fetch_url; a call "
     "to a tool not offered is rejected without running", "orchestrator/agents.py (sprint M4)"),
    ("fetch_url address block", "fetch_url refuses private, loopback and link-local addresses, also after "
     "redirects", "tool_service/netguard.py (sprint M5)"),
    ("Read-only exec classifier", "remote_exec runs only commands the classifier calls read-only",
     "tool_service/ops.py (sprint M3)"),
    ("Music dry run", "MUSIC_DRY_RUN: nothing is sent to a player (an environment setting, not a file)",
     "tool_service/main.py"),
    ("config/ caps", "64 KiB per file, nesting depth 10, the known file names only",
     "orchestrator/config_files.py = tool_service/config_files.py, orchestrator/settings_store.py"),
)

HOST_SIDE = (
    ("llama.cpp unit flags", "~/.config/systemd/user/llama-*.service (copies in systemd/)",
     "Edit the unit, systemctl --user daemon-reload, restart the unit. The values in effect are under "
     "Model servers"),
    ("LiteLLM model list", "litellm/config.yaml (in the repo; production reads the release's copy)",
     "Commit, tag, scripts/deploy_tag.sh deploy; LiteLLM restarts with it"),
    ("Whisper vocabulary prompt (dev Whisper only)", "~/services/wyoming-whisper/dev-prompt.env",
     "Rebuilt nightly by kronk-stt-prompt.timer (scripts/build_stt_prompt.py)"),
    ("Household music taste", "data/music_taste.yaml", "Household data with its own writer (taste.py), "
     "changed by voice ('remember that we love ...'), not here"),
)


def instance() -> str:
    return os.getenv("SETTINGS_INSTANCE", "production")


def release() -> str:
    return config_files.safe(os.getenv("KRONK_RELEASE") or "(unknown)", 120)


def env_table() -> list[dict]:
    return [{"name": name, "value": config_files.safe(os.environ[name], config_files.MAX_MESSAGE)
             if name in os.environ else None, "default": default, "what": what,
             "change": {"production": WHERE_PROD, "dev": WHERE_DEV}}
            for name, default, what in ENV]


def tools_table() -> list[dict]:
    offered: dict[str, list[str]] = {}
    for agent in [agents.COORDINATOR, *agents.AGENTS.values()]:
        for d in agent.tool_defs():
            offered.setdefault(d["function"]["name"], []).append(agent.name)
    offered.setdefault("escalate", []).append("any pinned specialist (top-level runs only)")
    defs = {d["function"]["name"]: d for d in tools.TOOL_DEFINITIONS}
    for d in agents.COORDINATOR.tool_defs():
        defs.setdefault(d["function"]["name"], d)
    defs["escalate"] = agents.ESCALATE_TOOL
    return [{"name": name, "description": d["function"].get("description", ""),
             "parameters": d["function"].get("parameters", {}),
             "offered_to": offered.get(name, []),
             "where": "orchestrator/agents.py" if name.startswith("ask_") or name == "escalate"
             else "orchestrator/tools.py TOOL_DEFINITIONS"}
            for name, d in sorted(defs.items())]


def code_text() -> list[dict]:
    out = [
        {"name": "Escalation guidance", "where": "orchestrator/agents.py ESCALATION_GUIDANCE",
         "text": agents.ESCALATION_GUIDANCE, "note": "Added to a pinned specialist's system text"},
        {"name": "Tool rejected", "where": "orchestrator/agents.py TOOL_REJECTED_TEXT",
         "text": agents.TOOL_REJECTED_TEXT, "note": "Tool result when a model calls a tool it was not offered"},
        {"name": "Duplicate call", "where": "orchestrator/agents.py DUPLICATE_CALL_TEXT",
         "text": agents.DUPLICATE_CALL_TEXT, "note": "Tool result for an exact repeat call"},
        {"name": "Repeat-call note", "where": "orchestrator/agents.py REPEAT_CALL_TEXT",
         "text": agents.REPEAT_CALL_TEXT,
         "note": "Appended from tuning.yaml agent_loop.repeat_tool_nudge_at calls to one tool"},
        {"name": "Budget exhausted", "where": "orchestrator/agents.py BUDGET_EXHAUSTED_TEXT",
         "text": agents.BUDGET_EXHAUSTED_TEXT, "note": "Sent before the forced final answer"},
        {"name": "No such specialist", "where": "orchestrator/agents.py NO_SUCH_SPECIALIST_TEXT",
         "text": agents.NO_SUCH_SPECIALIST_TEXT, "note": "Tool result for an unknown ask_* tool"},
        {"name": "Friendly tool phrasing", "where": "orchestrator/errors.py FRIENDLY_TOOL_PHRASING",
         "text": errors.FRIENDLY_TOOL_PHRASING, "note": "Appended to a failed tool result in the friendly style"},
    ]
    for name in sorted(vars(errors)):
        if name.startswith(("FRIENDLY_", "_FRIENDLY_")) and name != "FRIENDLY_TOOL_PHRASING":
            value = getattr(errors, name)
            if isinstance(value, str):
                out.append({"name": f"Friendly error: {name.strip('_').lower().replace('_', ' ')}",
                            "where": f"orchestrator/errors.py {name}", "text": value,
                            "note": "Spoken or shown in place of the failure detail (friendly style)"})
    for name in ("NEWS_UNAVAILABLE", "NEWS_REFRESH_FAILED"):
        out.append({"name": f"News line: {name.lower().replace('_', ' ')}", "where": f"orchestrator/tools.py {name}",
                    "text": getattr(tools, name), "note": "Prefix of the news tool's failure result"})
    for name, where, note in (
            ("Terminal speech", "orchestrator/agents.py _terminal_speech, _news_speech",
             "Maps a tool_service result line to what is spoken; a code path per result prefix"),
            ("Ambient facts", "orchestrator/agents.py kronk_facts",
             "Date, time and location added to every system text, built per request"),
            ("Weather context", "orchestrator/agents.py weather_context",
             "The cached forecast added to the home agent's system text"),
            ("Pipeline blocks", "orchestrator/main.py _run_pipeline",
             "Shared state, attached files and the escalation note, built per request")):
        out.append({"name": name, "where": where, "text": None, "note": note})
    return out


def models() -> dict:
    catalog = servers.load_catalog()
    props = servers.load_llama_props()
    by_port = {p.get("port"): p for p in props.get("servers", [])}
    by_model = servers.agents_by_model()
    rows = []
    for entry in catalog:
        p = by_port.get(entry.get("port")) or {}
        rows.append({"name": entry["name"], "port": entry.get("port"),
                     "agents": by_model.get(entry["name"], []), "running": bool(p),
                     "n_ctx": p.get("n_ctx"), "slots": p.get("total_slots"),
                     "sampling": p.get("sampling"), "flags": p.get("flags"), "unit": p.get("unit")})
    return {"servers": rows, "props_age_s": props.get("_age_s"), "props_error": props.get("_error"),
            "where": "Unit flags: ~/.config/systemd/user/llama-*.service; model names and ports: "
                     "litellm/config.yaml"}


def _read_text(base: Path, rel: str) -> tuple[str | None, bytes | None, str | None]:
    """(normalised text, raw bytes, error) of a live file."""
    try:
        raw = settings_store.read_bytes(base, rel)
    except settings_store.StoreError as e:
        return None, None, str(e)
    if raw is None:
        return None, None, "file is missing"
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as e:
        return None, raw, f"not UTF-8: {e.reason}"
    return text.replace("\r\n", "\n").replace("\r", "\n"), raw, None


def _digest(text: str) -> str:
    import hashlib
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def _commit_ref(entry: dict | None) -> dict | None:
    if not entry:
        return None
    return {k: entry[k] for k in ("commit", "short", "date", "who", "subject")}


def file_entries(base: Path, ts_effective: dict | None, git_ok: bool) -> list[dict]:
    cfg = config_loader.current()
    ts_digests = (ts_effective or {}).get("digests", {})
    out = []
    for rel, info in settings_store.EDITABLE.items():
        text, raw, err = _read_text(base, rel)
        entry = {"name": rel, "owner": info.owner, "kind": info.kind, "description": info.description,
                 "limit_bytes": settings_store.max_bytes(rel), "error": err,
                 "size_bytes": len(raw) if raw is not None else None,
                 "blob": settings_git.blob_sha(raw) if raw is not None else None,
                 "digest_on_disk": _digest(text) if text is not None else None,
                 "digest_in_effect": (cfg.digests if info.owner == "orchestrator" else ts_digests).get(rel)}
        entry["differs_from_effect"] = bool(entry["digest_on_disk"] and entry["digest_in_effect"]
                                            and entry["digest_on_disk"] != entry["digest_in_effect"])
        history: list[dict] = []
        changes: dict = {}
        if git_ok:
            try:
                history = settings_git.file_log(base, rel, limit=settings_git.MAX_LOG)
                entry["uncommitted"] = settings_git.is_dirty(base, rel)
                if info.kind == "yaml":
                    changes = settings_git.key_changes(base, rel)
            except settings_git.GitError as e:
                entry["git_error"] = str(e)
        entry["last_change"] = _commit_ref(history[0] if history else None)
        if info.kind == "prompt":
            name = rel[len("prompts/"):-3]
            entry["prompt_in_effect"] = cfg.prompts.get(name)
            entry["prompt_source"] = "file" if cfg.source == "files" else "code default"
        else:
            comments = {}
            if text is not None:
                try:
                    _, lines = config_loader.parse_yaml(text, rel)
                    comments = config_files.key_comments(text, lines)
                except config_loader.ConfigInvalid:
                    pass
            if info.owner == "orchestrator":
                model = {config_loader.TUNING_FILE: cfg.tuning, config_loader.LEAD_IN_FILE: cfg.lead_in,
                         config_loader.VOICE_FILE: cfg.voice}[rel]
                env = {".".join(path): var for var, path in config_loader.ENV_OVERRIDES.items()
                       if var in cfg.env} if rel == config_loader.TUNING_FILE else {}
                keys = config_files.describe_keys(model, cfg.data.get(rel, {}), env)
                if cfg.source == "defaults":
                    for k in keys:
                        k["source"] = "code default" if k["source"] != "environment" else k["source"]
            else:
                keys = ((ts_effective or {}).get("files") or {}).get(rel)
                if keys is None:
                    entry["keys_error"] = "tool_service did not answer; the values in effect are unknown"
                    keys = []
            for k in keys:
                # A key the live file does not carry (added to the schema
                # after the file was seeded) shows the schema's description.
                k["comment"] = comments.get(k["key"]) or k.get("description")
                parts = k["key"].split(".")
                k["section_comment"] = next((comments[".".join(parts[:i])] for i in range(len(parts) - 1, 0, -1)
                                             if ".".join(parts[:i]) in comments), None)
                k["last_change"] = _commit_ref(changes.get(k["key"]))
            entry["keys"] = keys
        out.append(entry)
    return out


def readonly(ts_effective: dict | None) -> dict:
    ts = ts_effective or {}
    return {
        "routing": routing.rules_table(),
        "routing_where": "orchestrator/routing.py RULES (in code, with its unit tests; not editable here)",
        "env": {"orchestrator": env_table(), "tool_service": ts.get("env", [])},
        "tools": tools_table(),
        "code_text": {"orchestrator": code_text(), "tool_service": ts.get("code_text", [])},
        "models": models(),
        "guards": [{"name": n, "what": w, "where": where} for n, w, where in GUARDS],
        "host_side": [{"name": n, "where": w, "how": h} for n, w, h in HOST_SIDE],
        "agents": [{"name": a.name, "model": a.model, "tools": list(a.tool_names),
                    "rounds": agents.prompt_and_rounds(a)[1]}
                   for a in [agents.COORDINATOR, *agents.AGENTS.values()]],
    }


def dump(obj) -> str:
    return json.dumps(obj, ensure_ascii=False)


def now() -> float:
    return time.time()
