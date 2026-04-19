"""Model server catalog — parsed from litellm's config.yaml + live health from LiteLLM."""
import logging
import os
import re
from pathlib import Path

import httpx
import yaml

logger = logging.getLogger(__name__)

LITELLM_CONFIG_PATH = Path(os.getenv("LITELLM_CONFIG", "/litellm-config.yaml"))


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


async def fetch_health(llm_service_url: str) -> dict[str, bool]:
    """Ask LiteLLM which upstream endpoints are healthy. Returns {api_base: ok}."""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"{llm_service_url}/health")
            if resp.status_code != 200:
                return {}
            data = resp.json()
    except Exception as e:
        logger.warning("Could not fetch LiteLLM /health: %s", e)
        return {}
    status: dict[str, bool] = {}
    for ep in data.get("healthy_endpoints") or []:
        if api := ep.get("api_base"):
            status[api] = True
    for ep in data.get("unhealthy_endpoints") or []:
        if api := ep.get("api_base"):
            status[api] = False
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
