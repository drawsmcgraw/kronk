"""Model servers for /resources and /services (M25), and their health for
/api/status (M38): the catalog's names and ports, joined by port with what
the two host exporters measured.

- litellm/config.yaml gives each model's name and port, nothing else.
- data/gpu_mem.json (scripts/gpu_mem_export.py, every 30 s): each GPU
  process's GPU memory (DRM fdinfo) and RAM. A server is running when its
  port is in a fresh file; absent from a fresh file, it is configured and
  not running; with the file stale or missing its state is unknown.
- data/llama_props.json (scripts/llama_props_export.py, every 60 s): what
  each running llama.cpp server answered on GET /props (context, slots),
  its model and draft files with their sizes, and the filesystems holding
  them. A server answering there is the health signal; stale, health is
  unknown.

No fact has a default. A fact with no fresh source is null, and
"reasons" says why. Nothing here calls LiteLLM or a model server: the
containers cannot reach the servers' loopback ports, and LiteLLM's /health
runs a completion on every backend (docs/sprint/M25-review.md, point 2).
See docs/features/resources-live-memory.md.
"""
import json
import logging
import os
import re
import time
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

LITELLM_CONFIG_PATH = Path(os.getenv("LITELLM_CONFIG", "/litellm-config.yaml"))
GPU_MEM_PATH = Path(os.getenv("GPU_MEM_PATH", "/data/gpu_mem.json"))
# The exporter fires every 30 s; older than this, the file is stale.
GPU_MEM_MAX_AGE_S = 120
GPU_MEM_EVERY_S = 30
LLAMA_PROPS_PATH = Path(os.getenv("LLAMA_PROPS_PATH", "/data/llama_props.json"))
# Written every 60 s by scripts/llama_props_export.py; older than this, stale.
LLAMA_PROPS_MAX_AGE_S = 300
LLAMA_PROPS_EVERY_S = 60
# A file this far in the future means a wrong clock, not fresh data.
FUTURE_SLACK_S = 60
DRM_ROOT = Path("/sys/class/drm")

RUNNING, NOT_RUNNING, UNKNOWN = "running", "not_running", "unknown"
ANSWERING, NOT_ANSWERING = "answering", "not_answering"

MEMORY_FACTS = ("gpu_bytes", "gtt_bytes", "vram_bytes", "rss_bytes")
PROPS_FACTS = ("n_ctx", "total_slots")
FILE_FACTS = ("model_file", "model_bytes", "draft_file", "draft_bytes")


def _parse(text: str) -> tuple[dict, float]:
    raw = json.loads(text)
    if not isinstance(raw, dict):
        raise ValueError("not a JSON object")
    return raw, float(raw.get("ts"))


def _parsed(path: Path, cache: dict | None) -> tuple[str, object]:
    """("ok", (raw, ts)) or ("bad", why); FileNotFoundError when there is
    no file. With a cache (a dict the caller keeps), the parse is kept keyed
    by the open file's device, inode, mtime and size, so an unchanged file
    costs an open and an fstat and is read once per change (M32 step 1:
    /work polls every 3 s). The key is taken from the file that is read, so
    a file renamed into place between two polls is never paired with the
    other's parse. cache["reads"] counts the reads."""
    if cache is None:
        try:
            return "ok", _parse(path.read_text())
        except (ValueError, TypeError) as e:
            return "bad", f"{type(e).__name__}: {e}"
    with open(path, "rb") as f:
        st = os.fstat(f.fileno())
        key = (st.st_dev, st.st_ino, st.st_mtime_ns, st.st_size)
        if cache.get("key") != key:
            try:
                cache["value"] = ("ok", _parse(f.read().decode("utf-8")))
            except (ValueError, TypeError) as e:          # UnicodeDecodeError is a ValueError
                cache["value"] = ("bad", f"{type(e).__name__}: {e}")
            cache["key"] = key
            cache["reads"] = cache.get("reads", 0) + 1
    return cache["value"]


def read_source(path: Path, max_age_s: float, every_s: int, timer: str, now: float | None = None,
                cache: dict | None = None) -> dict:
    """One exporter file: {"file", "state", "age_s", "max_age_s", "reason",
    "data"}. state is fresh, stale, missing or unreadable; age_s is known
    whenever the file parses; reason is null only when fresh. With a cache
    (see _parsed), data is the kept parse: the caller must not change it."""
    now = time.time() if now is None else now
    src = {"file": path.name, "state": "missing", "age_s": None, "max_age_s": max_age_s,
           "every_s": every_s, "reason": None, "data": None}
    try:
        kind, val = _parsed(path, cache)
    except FileNotFoundError:
        src["reason"] = (f"{path.name} does not exist: the host exporter ({timer}) is not installed "
                         "or has not run")
        return src
    except OSError as e:
        kind, val = "bad", f"{type(e).__name__}: {e}"
    if kind == "bad":
        src["state"] = "unreadable"
        src["reason"] = f"{path.name} cannot be read: {val}"
        return src
    raw, ts = val
    age = now - ts
    src["age_s"] = round(age, 1)
    src["data"] = raw
    if age < -FUTURE_SLACK_S:
        src["state"] = "stale"
        src["reason"] = f"{path.name} is dated {round(-age)} s in the future: the host clock or the file is wrong"
    elif age > max_age_s:
        src["state"] = "stale"
        src["reason"] = (f"stale: {path.name} was written {round(age)} s ago (the exporter runs every "
                         f"{every_s} s; older than {max_age_s} s is stale)")
    else:
        src["state"] = "fresh"
    return src


def gpu_mem_source(now: float | None = None) -> dict:
    return read_source(GPU_MEM_PATH, GPU_MEM_MAX_AGE_S, GPU_MEM_EVERY_S, "systemd/gpu-mem-export.timer", now)


def llama_props_source(now: float | None = None) -> dict:
    return read_source(LLAMA_PROPS_PATH, LLAMA_PROPS_MAX_AGE_S, LLAMA_PROPS_EVERY_S,
                       "systemd/llama-props-export.timer", now)


def load_llama_props() -> dict:
    """What each running llama.cpp server reports (sprint M21 step 2, for
    /settings): n_ctx, slots and sampling from its GET /props, and the flags
    of its running process. Written on the HOST by
    scripts/llama_props_export.py (the servers listen on the host's
    loopback, which the containers cannot reach). Returns {"servers": [...],
    "_age_s": n} or {"servers": [], "_error": why}."""
    try:
        raw = json.loads(LLAMA_PROPS_PATH.read_text())
    except FileNotFoundError:
        return {"servers": [], "_error": f"{LLAMA_PROPS_PATH} does not exist: the host-side exporter "
                                         "(systemd/llama-props-export.timer) is not installed"}
    except (OSError, ValueError) as e:
        return {"servers": [], "_error": f"{LLAMA_PROPS_PATH} cannot be read: {e}"}
    age = time.time() - (raw.get("ts") or 0)
    servers_ = [s for s in raw.get("servers", []) if isinstance(s, dict)]
    if not (-60 <= age <= LLAMA_PROPS_MAX_AGE_S):
        return {"servers": servers_, "_age_s": round(age, 1),
                "_error": f"stale: written {round(age)} s ago (the exporter runs every 60 s)"}
    return {"servers": servers_, "_age_s": round(age, 1)}


def load_catalog() -> list[dict]:
    """One entry per configured model: its name, api_base and port. The
    catalog holds names and addresses only (M25); every other fact about
    a server is measured."""
    if not LITELLM_CONFIG_PATH.exists():
        logger.warning("litellm config not found at %s", LITELLM_CONFIG_PATH)
        return []
    try:
        cfg = yaml.safe_load(LITELLM_CONFIG_PATH.read_text()) or {}
    except yaml.YAMLError as e:
        logger.warning("Could not parse litellm config: %s", e)
        return []

    catalog: list[dict] = []
    for entry in cfg.get("model_list", []) or []:
        name = entry.get("model_name")
        if not name:
            continue
        params = entry.get("litellm_params") or {}
        api_base = params.get("api_base", "")
        port_match = re.search(r":(\d+)", api_base)
        port = int(port_match.group(1)) if port_match else None
        catalog.append({"name": name, "api_base": api_base, "port": port})
    return catalog


def agents_by_model() -> dict[str, list[str]]:
    """Map model_name → list of agent labels that use it (the roster only:
    there has been no LLM router since 2026-08-18, M24)."""
    import agents  # local import to avoid circular
    mapping: dict[str, list[str]] = {}
    for item in agents.roster():
        mapping.setdefault(item["model"], []).append(item["name"])
    return mapping


# ── joining ───────────────────────────────────────────────────────────────

def _processes(gpu: dict) -> list[dict]:
    data = gpu.get("data") or {}
    return [p for p in data.get("processes") or [] if isinstance(p, dict)]


def _props_servers(props: dict) -> list[dict]:
    data = props.get("data") or {}
    return [s for s in data.get("servers") or [] if isinstance(s, dict)]


def _memory(proc: dict) -> dict:
    gtt, vram = proc.get("gtt_bytes"), proc.get("vram_bytes")
    gpu = gtt + vram if isinstance(gtt, int) and isinstance(vram, int) else None
    return {"gpu_bytes": gpu, "gtt_bytes": gtt, "vram_bytes": vram, "rss_bytes": proc.get("rss_bytes")}


def _files(entry: dict, out: dict, reasons: dict) -> None:
    """Model and draft file facts from a llama_props.json server entry."""
    flags = entry.get("flags") or {}
    for fact, flag, size_key in (("model", "model", "model_bytes"), ("draft", "model_draft", "model_draft_bytes")):
        name = flags.get(flag)
        if name is None:
            why = "the server runs no draft model" if fact == "draft" else "no -m on the command line"
            out[f"{fact}_file"] = out[f"{fact}_bytes"] = None
            reasons[f"{fact}_file"] = reasons[f"{fact}_bytes"] = why
            continue
        out[f"{fact}_file"] = name
        if size_key not in entry:
            out[f"{fact}_bytes"] = None
            reasons[f"{fact}_bytes"] = f"llama_props.json has no {size_key} (written by an exporter older than M25)"
        else:
            out[f"{fact}_bytes"] = entry[size_key]
            if entry[size_key] is None:
                reasons[f"{fact}_bytes"] = entry.get(f"{size_key}_error") or "the exporter could not stat the file"


def _null(out: dict, reasons: dict, facts, why: str) -> None:
    for f in facts:
        out[f] = None
        reasons[f] = why


def _health(port: int | None, props: dict, pentry: dict | None) -> tuple[str, str | None]:
    """(health, reason) of one catalog server: whether it answered GET /props
    when llama_props.json was written. The one definition, for /api/servers
    and /api/status (M38). unknown when the file is not fresh."""
    if props["state"] != "fresh":
        return UNKNOWN, props["reason"]
    if pentry and not pentry.get("error"):
        return ANSWERING, None
    if pentry:
        return NOT_ANSWERING, pentry["error"]
    return NOT_ANSWERING, (f"not answering: port {port} is not in llama_props.json "
                           f"(written {round(props['age_s'])} s ago)")


def _public(src: dict) -> dict:
    return {k: v for k, v in src.items() if k != "data"}


def model_server_health(catalog: list[dict], props: dict) -> dict:
    """The llm_service block's model servers for /api/status (M38): each
    catalog server's health from llama_props.json, by name, with the file's
    age. Pure: no file or network access, no request to a model server or
    LiteLLM. "down" names every server not answering or of unknown health;
    an empty catalog is itself a reason the block is down."""
    props_by_port = {s["port"]: s for s in _props_servers(props) if s.get("port")}
    rows, down = [], []
    for entry in catalog:
        port = entry.get("port")
        health, reason = _health(port, props, props_by_port.get(port) if port else None)
        rows.append({"name": entry["name"], "port": port, "health": health, "up": health == ANSWERING,
                     "reason": reason})
        if health != ANSWERING:
            down.append(f"{entry['name']}: {reason}")
    if not catalog:
        down.append(f"no model server in the catalog ({LITELLM_CONFIG_PATH} missing, empty or unreadable)")
    healthy = sum(r["up"] for r in rows)
    return {"servers": rows, "source": _public(props), "healthy_count": healthy,
            "unhealthy_count": len(rows) - healthy, "down": down}


def build_servers(catalog: list[dict], gpu: dict, props: dict, by_model: dict[str, list[str]]) -> dict:
    """The /api/servers payload from the catalog and the two sources (as
    read_source returns them). Pure: no file or network access."""
    gpu_fresh, props_fresh = gpu["state"] == "fresh", props["state"] == "fresh"
    procs_by_port = {p["port"]: p for p in _processes(gpu) if p.get("port")}
    props_by_port = {s["port"]: s for s in _props_servers(props) if s.get("port")}
    props_by_unit = {str(s.get("unit", "")).removesuffix(".service"): s for s in _props_servers(props)}
    catalog_ports = {e.get("port") for e in catalog if e.get("port")}

    rows = []
    for entry in catalog:
        port = entry.get("port")
        out: dict = {"name": entry["name"], "port": port, "agents": by_model.get(entry["name"], []),
                     "unit": None}
        reasons: dict = {}
        proc = procs_by_port.get(port) if port else None
        pentry = props_by_port.get(port) if port else None

        # State: the one definition (criteria, "Running has one definition").
        if not port:
            out["state"], out["state_reason"] = UNKNOWN, "the catalog entry has no port in its api_base"
        elif not gpu_fresh:
            out["state"], out["state_reason"] = UNKNOWN, gpu["reason"]
        elif proc:
            out["state"], out["state_reason"] = RUNNING, None
        else:
            out["state"] = NOT_RUNNING
            out["state_reason"] = (f"configured, not running: port {port} is not in gpu_mem.json "
                                   f"(written {round(gpu['age_s'])} s ago)")

        out["health"], out["health_reason"] = _health(port, props, pentry)

        # Memory.
        if out["state"] == RUNNING:
            out.update(_memory(proc))
            out["unit"] = proc.get("unit")
            for f in MEMORY_FACTS:
                if out[f] is None:
                    reasons[f] = f"gpu_mem.json has no {f} for this process"
            out["memory_age_s"] = gpu["age_s"]
        else:
            _null(out, reasons, MEMORY_FACTS, out["state_reason"])
            out["memory_age_s"] = None
            reasons["memory_age_s"] = out["state_reason"]

        # Context, slots, files.
        if out["state"] == NOT_RUNNING:
            _null(out, reasons, PROPS_FACTS + FILE_FACTS, out["state_reason"])
            out["props_age_s"] = None
            reasons["props_age_s"] = out["state_reason"]
        elif not props_fresh:
            _null(out, reasons, PROPS_FACTS + FILE_FACTS, props["reason"])
            out["props_age_s"] = None
            reasons["props_age_s"] = props["reason"]
        elif not pentry:
            _null(out, reasons, PROPS_FACTS + FILE_FACTS, out["health_reason"])
            out["props_age_s"] = None
            reasons["props_age_s"] = out["health_reason"]
        else:
            out["props_age_s"] = props["age_s"]
            out["unit"] = out["unit"] or str(pentry.get("unit", "")).removesuffix(".service") or None
            if pentry.get("error"):
                _null(out, reasons, PROPS_FACTS, pentry["error"])
            else:
                for f in PROPS_FACTS:
                    out[f] = pentry.get(f)
                    if out[f] is None:
                        reasons[f] = f"the server's /props answer has no {f}"
            _files(pentry, out, reasons)
        out["reasons"] = reasons
        rows.append(out)

    others = []
    if gpu_fresh:
        for proc in _processes(gpu):
            if proc.get("port") in catalog_ports and proc.get("port"):
                continue
            unit = str(proc.get("unit", ""))
            kind = ("llama unit not in the catalog" if unit.startswith("llama-")
                    else "speech to text" if unit.startswith("wyoming-whisper") else "other")
            out = {"unit": unit, "kind": kind, "port": proc.get("port"), **_memory(proc)}
            reasons = {}
            if unit.startswith("llama-"):
                pentry = props_by_unit.get(unit)
                if not props_fresh:
                    _null(out, reasons, FILE_FACTS, props["reason"])
                elif not pentry:
                    _null(out, reasons, FILE_FACTS, f"{unit} is not in llama_props.json")
                else:
                    _files(pentry, out, reasons)
            out["reasons"] = reasons
            others.append(out)

    return {
        "sources": {"gpu_mem": _public(gpu), "llama_props": _public(props)},
        # Kept from before M25 (same meaning): the memory file's age when fresh.
        "measured_age_s": gpu["age_s"] if gpu_fresh else None,
        "servers": rows,
        "other_processes": others,
    }


# ── host block for /api/system ─────────────────────────────────────────────

def read_gpu(drm_root: Path | None = None) -> dict:
    """GTT total and used and gpu_busy_percent of the first DRM card with
    GTT counters, read now from sysfs (readable inside the container).
    Each value is null with a reason when it cannot be read."""
    root = DRM_ROOT if drm_root is None else drm_root
    out = {"card": None, "gtt_total": None, "gtt_used": None, "gpu_busy_percent": None, "reasons": {}}
    for card_dir in sorted(root.glob("card[0-9]*")):
        dev = card_dir / "device"
        try:
            total = int((dev / "mem_info_gtt_total").read_text().strip())
            used = int((dev / "mem_info_gtt_used").read_text().strip())
        except (OSError, ValueError):
            continue
        out.update(card=card_dir.name, gtt_total=total, gtt_used=used)
        try:
            out["gpu_busy_percent"] = int((dev / "gpu_busy_percent").read_text().strip())
        except (OSError, ValueError) as e:
            out["reasons"]["gpu_busy_percent"] = f"{card_dir.name}/device/gpu_busy_percent: {type(e).__name__}"
        return out
    why = f"no card under {root} has mem_info_gtt_total and mem_info_gtt_used"
    out["reasons"] = {k: why for k in ("gtt_total", "gtt_used", "gpu_busy_percent")}
    return out


def disk_block(props: dict) -> dict:
    """The disk figures from llama_props.json: the total size of the loaded
    model files and each filesystem holding one. Null with a reason when
    the file is not fresh or has no disk block."""
    data = props.get("data") or {}
    if props["state"] != "fresh":
        return {"disk": None, "disk_reason": props["reason"]}
    disk = data.get("disk")
    if not isinstance(disk, dict):
        return {"disk": None, "disk_reason": "llama_props.json has no disk block (written by an exporter older than M25)"}
    return {"disk": disk, "disk_reason": None}
