"""devstral-2512-q4 is retired; coding and devops run on gemma-4-e4b (M31).

Before M31 both agents ran on the Devstral Q4 server (port 11440, about
34 GiB of GPU memory), and the code defaults named devstral-2512, a model
no server had served since the Q8 unit was retired in M24. Every model a
compose service, the dev env example or a code default names is now one
the catalog serves (docs/sprint/M31-criteria.md).

Reads repo files only; no socket is opened.
"""
import ast
import re
from pathlib import Path

import yaml

import servers

REPO = Path(__file__).resolve().parent.parent
MODEL_VARS = ("COORDINATOR_MODEL", "RESEARCH_AGENT_MODEL", "CODING_AGENT_MODEL",
              "DEVOPS_AGENT_MODEL", "TALKIE_MODEL")
NOW = 1_795_547_481.0


def _catalog_names() -> set[str]:
    cfg = yaml.safe_load((REPO / "litellm" / "config.yaml").read_text())
    return {e["model_name"] for e in cfg["model_list"]}


def _env(service: dict) -> dict[str, str]:
    env = service.get("environment") or []
    if isinstance(env, dict):
        return {k: str(v) for k, v in env.items()}
    return dict(str(e).split("=", 1) for e in env if "=" in str(e))


def test_devstral_is_gone_from_the_catalog():
    assert not {n for n in _catalog_names() if "devstral" in n}
    assert "11440" not in (REPO / "litellm" / "config.yaml").read_text()


def test_both_compose_orchestrators_run_coding_and_devops_on_gemma():
    """Production's block sets them; dev's block sets them too, because
    environment: wins over env_file: and a pre-M31 .env.dev names devstral."""
    services = yaml.safe_load((REPO / "docker-compose.yml").read_text())["services"]
    for name in ("orchestrator", "orchestrator-dev"):
        env = _env(services[name])
        assert env.get("CODING_AGENT_MODEL") == "gemma-4-e4b", name
        assert env.get("DEVOPS_AGENT_MODEL") == "gemma-4-e4b", name


def test_every_model_compose_and_the_dev_example_name_is_in_the_catalog():
    catalog = _catalog_names()
    services = yaml.safe_load((REPO / "docker-compose.yml").read_text())["services"]
    named = {(svc, k, v) for svc, s in services.items() for k, v in _env(s).items() if k in MODEL_VARS}
    example = (REPO / ".env.dev.example").read_text()
    named |= {(".env.dev.example", k, v) for k, v in re.findall(r"^([A-Z_]+_MODEL)=(\S+)$", example, re.M)}
    assert {k for where, k, _ in named if where == ".env.dev.example"} >= {"CODING_AGENT_MODEL",
                                                                           "DEVOPS_AGENT_MODEL"}
    missing = sorted(f"{where}: {k}={v}" for where, k, v in named if v not in catalog)
    assert not missing, f"models the catalog does not serve: {missing}"


def test_agent_model_defaults_follow_the_coordinator():
    """As RESEARCH_AGENT_MODEL does: no default names its own server."""
    tree = ast.parse((REPO / "orchestrator" / "agents.py").read_text())
    defaults = {}
    for node in tree.body:
        if (isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)
                and isinstance(node.value, ast.Call) and getattr(node.value.func, "attr", "") == "getenv"):
            defaults[node.targets[0].id] = node.value.args[1]
    for var in ("RESEARCH_AGENT_MODEL", "CODING_AGENT_MODEL", "DEVOPS_AGENT_MODEL"):
        assert isinstance(defaults[var], ast.Name) and defaults[var].id == "COORDINATOR_MODEL", var


def test_settings_page_shows_the_same_defaults():
    import settings_view
    rows = {name: default for name, default, _ in settings_view.ENV}
    for var in ("CODING_AGENT_MODEL", "DEVOPS_AGENT_MODEL"):
        assert rows[var] == "COORDINATOR_MODEL", var


def test_the_dead_devstral_proxy_route_is_gone():
    conf = (REPO / "nginx" / "nginx.conf").read_text()
    assert "/devstral/" not in conf and "11436" not in conf


def test_no_devstral_unit_or_upgrade_rule():
    assert not list((REPO / "systemd").glob("*devstral*"))
    assert "devstral" not in (REPO / "scripts" / "upgrade_rules.toml").read_text()


# ── criterion 3: the pages once the unit is stopped ─────────────────────────

def _real_catalog():
    import unittest.mock
    with unittest.mock.patch.object(servers, "LITELLM_CONFIG_PATH", REPO / "litellm" / "config.yaml"):
        return servers.load_catalog()


def _gpu(procs):
    return {"file": "gpu_mem.json", "state": "fresh", "age_s": 10.0, "max_age_s": 120, "every_s": 30,
            "reason": None, "data": {"ts": NOW - 10, "processes": procs}}


def _props(entries):
    return {"file": "llama_props.json", "state": "fresh", "age_s": 20.0, "max_age_s": 300, "every_s": 60,
            "reason": None, "data": {"ts": NOW - 20, "servers": entries}}


GEMMA_PROC = {"unit": "llama-gemma4-e4b", "pid": 2, "port": 11438, "gtt_bytes": 5_475_282_944,
              "vram_bytes": 4_829_184, "rss_bytes": 11_023_216_640}
TALKIE_PROC = {"unit": "llama-talkie", "pid": 3, "port": 11441, "gtt_bytes": 6_328_320,
               "vram_bytes": 57_344, "rss_bytes": 24_559_325_184}
WHISPER_PROC = {"unit": "wyoming-whisper", "pid": 4, "port": None, "gtt_bytes": 2_130_194_432,
                "vram_bytes": 2_211_840, "rss_bytes": 781_197_312}
DEVSTRAL_PROC = {"unit": "llama-devstral-q4", "pid": 1, "port": 11440, "gtt_bytes": 36_518_264_832,
                 "vram_bytes": 1_085_440, "rss_bytes": 9_078_099_968}


def _entry(unit, port, model, n_ctx, slots):
    return {"unit": f"{unit}.service", "port": port, "n_ctx": n_ctx, "total_slots": slots,
            "flags": {"model": model, "port": str(port)}, "model_bytes": 1}


PROPS = [_entry("llama-gemma4-e4b", 11438, "gemma.gguf", 32768, 4),
         _entry("llama-talkie", 11441, "talkie.gguf", 2048, 1)]


def test_with_the_unit_stopped_two_servers_and_no_fault():
    by_model = {"gemma-4-e4b": ["coordinator", "research", "coding", "devops"]}
    out = servers.build_servers(_real_catalog(), _gpu([GEMMA_PROC, TALKIE_PROC, WHISPER_PROC]),
                                _props(PROPS), by_model)
    rows = {r["name"]: r for r in out["servers"]}
    assert list(rows) == ["gemma-4-e4b", "talkie"]
    for r in rows.values():
        assert (r["state"], r["health"]) == ("running", "answering"), r["name"]
        assert r["reasons"] == {} or set(r["reasons"]) == {"draft_file", "draft_bytes"}, r["reasons"]
    assert rows["gemma-4-e4b"]["agents"] == ["coordinator", "research", "coding", "devops"]
    assert [p["unit"] for p in out["other_processes"]] == ["wyoming-whisper"]


def test_between_the_deploy_and_the_stop_devstral_shows_as_an_uncatalogued_unit():
    """The deploy drops the catalog entry before the coordinator stops the
    unit: for that window the server is listed apart, with its memory, and
    is not a catalog row."""
    props = PROPS + [_entry("llama-devstral-q4", 11440, "devstral.gguf", 131072, 4)]
    out = servers.build_servers(_real_catalog(), _gpu([DEVSTRAL_PROC, GEMMA_PROC, TALKIE_PROC]),
                                _props(props), {})
    assert [r["name"] for r in out["servers"]] == ["gemma-4-e4b", "talkie"]
    stray = [p for p in out["other_processes"] if p["unit"] == "llama-devstral-q4"]
    assert stray and stray[0]["kind"] == "llama unit not in the catalog"
    assert stray[0]["gpu_bytes"] == 36_518_264_832 + 1_085_440
