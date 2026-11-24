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
