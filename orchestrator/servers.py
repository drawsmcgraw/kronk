"""Model server catalog — parsed from litellm's config.yaml + live health
from LiteLLM + measured per-model GPU memory from the host-side exporter."""
import json
import logging
import os
import re
import time
from pathlib import Path

import httpx
import yaml

logger = logging.getLogger(__name__)

LITELLM_CONFIG_PATH = Path(os.getenv("LITELLM_CONFIG", "/litellm-config.yaml"))
GPU_MEM_PATH = Path(os.getenv("GPU_MEM_PATH", "/data/gpu_mem.json"))
# Exporter fires every 30 s; past this age the measurement is treated as
# gone and the UI falls back to static estimates (labeled as such).
GPU_MEM_MAX_AGE_S = 120


def load_gpu_mem() -> dict:
    """Measured GPU memory per model server, keyed by port.

    Written on the HOST by scripts/gpu_mem_export.py (systemd timer) —
    the container can't read host /proc, so the file is the bridge. See
    docs/features/resources-live-memory.md. Returns {} when the file is
    missing, unparsable, or stale, plus an "_age_s" key when fresh."""
    try:
        raw = json.loads(GPU_MEM_PATH.read_text())
    except (OSError, ValueError):
        return {}
    age = time.time() - (raw.get("ts") or 0)
    if not (-60 <= age <= GPU_MEM_MAX_AGE_S):
        return {}
    out: dict = {"_age_s": round(age, 1)}
    for p in raw.get("processes", []):
        if p.get("port"):
            out[p["port"]] = p
    return out


def load_catalog() -> list[dict]:
    """Return one entry per configured model with its kronk metadata."""
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
        kronk = entry.get("kronk") or {}
        catalog.append({
            "name":     name,
            "api_base": api_base,
            "port":     port,
            "params":   kronk.get("params", ""),
            "quant":    kronk.get("quant", ""),
            "vram_gb":  kronk.get("vram_gb"),
            "ctx_k":    kronk.get("ctx_k"),
        })
    return catalog


def _health_key(ep: dict) -> str | None:
    """Join key for a LiteLLM health endpoint entry.

    LiteLLM v1.88's /health reports api_base as null and identifies
    backends as model="openai/<name>" — keying by api_base silently
    produced healthy=None for every server (found 2026-07-06; the page's
    health dots had been dead, not gray-by-choice). Prefer the model name,
    fall back to api_base for older/other payload shapes."""
    model = ep.get("model") or ""
    if model.startswith("openai/"):
        return model[len("openai/"):]
    return ep.get("api_base") or model or None


async def fetch_health(llm_service_url: str) -> dict[str, bool]:
    """Ask LiteLLM which upstreams are healthy. Returns {model_name: ok}
    (with api_base keys as fallback for old payload shapes)."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{llm_service_url}/health")
            if resp.status_code != 200:
                logger.warning("LiteLLM /health returned %s", resp.status_code)
                return {}
            data = resp.json()
    except Exception as e:
        logger.warning("Could not fetch LiteLLM /health: %s", e)
        return {}
    status: dict[str, bool] = {}
    for ep in data.get("healthy_endpoints") or []:
        if key := _health_key(ep):
            status[key] = True
    for ep in data.get("unhealthy_endpoints") or []:
        if key := _health_key(ep):
            status[key] = False
    return status


def agents_by_model() -> dict[str, list[str]]:
    """Map model_name → list of agent labels that use it (includes Router)."""
    import agents  # local import to avoid circular
    mapping: dict[str, list[str]] = {}
    for item in agents.roster():
        mapping.setdefault(item["model"], []).append(item["name"])
    router_model = os.getenv("ROUTER_MODEL")
    if router_model:
        mapping.setdefault(router_model, []).append("Router")
    return mapping
