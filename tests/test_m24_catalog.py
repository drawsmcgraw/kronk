"""The model catalog matches the model servers the repo ships (M24).

litellm/config.yaml is the catalog LiteLLM serves and /api/servers lists.
Each entry's api_base names a loopback port; a llama.cpp unit in systemd/
must serve that port, or the catalog lists a server that does not exist
(before M24 it listed four retired ones: bonsai-8b, mistral-nemo,
devstral-2512, gemma-3-4b, and /settings showed gemma-3-4b as the
"Router" though there has been no LLM router since 2026-08-18).

Reads repo files only; no socket is opened.
"""
import re
from pathlib import Path

import yaml

import servers

REPO = Path(__file__).resolve().parent.parent
SYSTEMD = REPO / "systemd"
CATALOG = REPO / "litellm" / "config.yaml"


def _unit_ports() -> dict[int, str]:
    """Port → unit name, from each llama-*.service ExecStart's --port."""
    ports: dict[int, str] = {}
    for unit in sorted(SYSTEMD.glob("llama-*.service")):
        m = re.search(r"--port\s+(\d+)", unit.read_text())
        if m:
            ports[int(m.group(1))] = unit.stem
    return ports


def _catalog() -> list[dict]:
    return (yaml.safe_load(CATALOG.read_text()) or {})["model_list"]


def test_catalog_loads_with_the_three_models():
    names = [e["model_name"] for e in _catalog()]
    assert names == ["devstral-2512-q4", "gemma-4-e4b", "talkie"]


def test_every_catalog_port_is_served_by_a_unit_in_systemd():
    ports = _unit_ports()
    orphans = []
    for e in _catalog():
        api_base = e["litellm_params"]["api_base"]
        m = re.match(r"http://127\.0\.0\.1:(\d+)/", api_base)
        assert m, f"{e['model_name']}: api_base {api_base!r} is not a loopback port"
        if int(m.group(1)) not in ports:
            orphans.append(f"{e['model_name']} ({api_base})")
    assert not orphans, f"catalog names models no unit in systemd/ serves: {orphans}"


def test_every_llama_server_unit_has_a_catalog_entry():
    catalog_ports = {int(re.search(r":(\d+)/", e["litellm_params"]["api_base"]).group(1))
                     for e in _catalog()}
    unlisted = {unit for port, unit in _unit_ports().items() if port not in catalog_ports}
    assert not unlisted, f"units in systemd/ the catalog does not list: {unlisted}"


def test_systemd_holds_only_the_three_llama_servers_and_the_props_export():
    """The retired units' reference copies are gone (M24 criterion 1). The
    port check above passes on the tree before M24, which still carried
    the four units and their catalog entries; this one fails there."""
    units = sorted(p.name for p in SYSTEMD.glob("llama-*"))
    assert units == [
        "llama-devstral-q4.service",
        "llama-gemma4-e4b.service",
        "llama-props-export.service",
        "llama-props-export.timer",
        "llama-talkie.service",
    ]


def test_talkie_unit_runs_on_the_cpu_only():
    text = (SYSTEMD / "llama-talkie.service").read_text()
    exec_start = text.split("ExecStart=", 1)[1].split("\nRestart", 1)[0]
    assert re.search(r"--device\s+none\b", exec_start)
    assert re.search(r"(?:-ngl|--n-gpu-layers)\s+0\b", exec_start)
    assert not re.search(r"(?:-ngl|--n-gpu-layers)\s+[1-9]", exec_start)
    assert "--port 11441" in exec_start


def test_no_router_label_even_with_the_old_env_var(monkeypatch):
    """ROUTER_MODEL is gone from compose and code; a stale value in an env
    file (.env.dev carried one) must not bring the label back."""
    monkeypatch.setenv("ROUTER_MODEL", "gemma-3-4b")
    mapping = servers.agents_by_model()
    assert all("Router" not in labels for labels in mapping.values())
    assert "gemma-3-4b" not in mapping


# ── fix round 1 ──────────────────────────────────────────────────────────────

def test_upgrade_rules_name_exactly_the_llama_server_units_in_systemd():
    """scripts/upgrade_rules.toml's llama.cpp `units` are what the quarterly
    report reads; a unit that is not in systemd/ prints "unit not installed"
    (M24-e2e.md "Found" 2: the four retired units were still listed)."""
    import tomllib
    rules = tomllib.loads((REPO / "scripts" / "upgrade_rules.toml").read_text())
    llama = [c for c in rules["component"] if c["kind"] == "llama-build"]
    assert len(llama) == 1
    servers_in_systemd = sorted(p.stem for p in SYSTEMD.glob("llama-*.service")
                                if "llama-server" in p.read_text())
    assert sorted(llama[0]["units"]) == servers_in_systemd
    assert (REPO / llama[0]["file"]).is_file()


def test_router_model_is_not_a_settings_env_row():
    """In place of criterion 2's grep (mutant M24)."""
    import settings_view
    assert "ROUTER_MODEL" not in [name for name, _, _ in settings_view.ENV]


def _compose_environment(service: dict) -> list[str]:
    env = service.get("environment") or []
    if isinstance(env, dict):
        return list(env)
    return [str(e).split("=", 1)[0] for e in env]


def test_router_model_is_not_in_docker_compose():
    """In place of criterion 2's grep (mutant M26): no service's environment
    sets it, and the file does not name it at all (an args: or comment line
    would bring it back too)."""
    text = (REPO / "docker-compose.yml").read_text()
    compose = yaml.safe_load(text)
    for name, service in compose["services"].items():
        assert "ROUTER_MODEL" not in _compose_environment(service), name
    assert "ROUTER_MODEL" not in text


def test_router_model_is_named_nowhere_in_orchestrator_or_config():
    """Criterion 2's grep over orchestrator/ and config/, as a test."""
    hits = [str(p.relative_to(REPO))
            for top in ("orchestrator", "config")
            for p in (REPO / top).rglob("*")
            if p.is_file() and "__pycache__" not in p.parts
            and "ROUTER_MODEL" in p.read_text(errors="replace")]
    assert not hits
