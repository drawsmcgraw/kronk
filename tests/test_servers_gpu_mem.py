"""/api/servers and /api/system on measured facts only (M25).

Rewritten by M25. Before it, this file asserted the hand-written vram_gb
estimate was kept as a fallback and that a stale memory file gave a
reasonless {}. Now: the catalog gives names and ports, the two host
exporter files give every other fact, a fact with no fresh source is null
with a reason, and the route calls neither LiteLLM nor any model server
(docs/sprint/M25-criteria.md, criteria 5 to 9).

No socket is opened: the no_network fixture refuses every connect, and a
fake httpx client fails the test if anything builds one.
"""
import json
import time
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
from fastapi.testclient import TestClient

import servers

pytestmark = pytest.mark.usefixtures("no_network")

NOW = 1_795_547_481.0
CATALOG = [
    {"name": "devstral-2512-q4", "api_base": "http://127.0.0.1:11440/v1", "port": 11440},
    {"name": "gemma-4-e4b", "api_base": "http://127.0.0.1:11438/v1", "port": 11438},
    {"name": "talkie", "api_base": "http://127.0.0.1:11441/v1", "port": 11441},
]
BY_MODEL = {"gemma-4-e4b": ["coordinator", "research"], "devstral-2512-q4": ["coding"]}


def _gpu(ts=NOW - 10, extra=()):
    return {"ts": ts, "processes": [
        {"unit": "llama-devstral-q4", "pid": 1, "port": 11440, "gtt_bytes": 36_518_264_832,
         "vram_bytes": 1_085_440, "rss_bytes": 9_078_099_968},
        {"unit": "llama-gemma4-e4b", "pid": 2, "port": 11438, "gtt_bytes": 5_475_282_944,
         "vram_bytes": 4_829_184, "rss_bytes": 11_023_216_640},
        {"unit": "llama-talkie", "pid": 3, "port": 11441, "gtt_bytes": 6_328_320,
         "vram_bytes": 57_344, "rss_bytes": 24_559_325_184},
        {"unit": "wyoming-whisper", "pid": 4, "port": None, "gtt_bytes": 2_130_194_432,
         "vram_bytes": 2_211_840, "rss_bytes": 781_197_312},
        *extra]}


def _server(unit, port, model, model_bytes, n_ctx, slots, draft=None, draft_bytes=None, **kw):
    flags = {"model": model, "port": str(port)}
    entry = {"unit": f"{unit}.service", "flags": flags, "port": port, "n_ctx": n_ctx,
             "total_slots": slots, "model_alias": model, "sampling": {}, "model_bytes": model_bytes}
    if draft:
        flags["model_draft"] = draft
        entry["model_draft_bytes"] = draft_bytes
    entry.update(kw)
    return entry


DISK = {"model_files_bytes": 32_762_820_832, "filesystems": [
    {"mount": "/", "size_bytes": 1_966_736_678_912, "used_bytes": 757_529_047_040,
     "avail_bytes": 1_109_227_380_736, "files": ["a.gguf"]}]}


def _props(ts=NOW - 20, servers_=None, disk=DISK):
    if servers_ is None:
        servers_ = [
            _server("llama-devstral-q4", 11440, "mistralai_Devstral-Small-2-24B-Instruct-2512-Q4_K_M.gguf",
                    14_334_438_272, 131072, 4),
            _server("llama-gemma4-e4b", 11438, "gemma-4-E4B-it-qat-UD-Q4_K_XL-202607.gguf", 4_215_695_776,
                    32768, 4, draft="mtp-gemma-4-E4B-it-Q8_0-202607.gguf", draft_bytes=98_671_936),
            _server("llama-talkie", 11441, "talkie-1930-13b-it-Q8_0.gguf", 14_114_014_848, 2048, 1),
        ]
    out = {"ts": ts, "servers": servers_}
    if disk is not None:
        out["disk"] = disk
    return out


def _sources(tmp_path, gpu=None, props=None):
    g, p = tmp_path / "gpu_mem.json", tmp_path / "llama_props.json"
    if gpu is not None:
        g.write_text(json.dumps(gpu))
    if props is not None:
        p.write_text(json.dumps(props))
    with patch.object(servers, "GPU_MEM_PATH", g), patch.object(servers, "LLAMA_PROPS_PATH", p):
        return servers.gpu_mem_source(now=NOW), servers.llama_props_source(now=NOW)


def _build(tmp_path, gpu=None, props=None, catalog=CATALOG):
    g, p = _sources(tmp_path, gpu, props)
    return servers.build_servers(catalog, g, p, BY_MODEL)


def _by_name(payload):
    return {s["name"]: s for s in payload["servers"]}


# ── sources ───────────────────────────────────────────────────────────────

def test_fresh_source_has_its_age_and_no_reason(tmp_path):
    g, p = _sources(tmp_path, _gpu(), _props())
    assert (g["state"], g["age_s"], g["reason"]) == ("fresh", 10.0, None)
    assert (p["state"], p["age_s"], p["reason"]) == ("fresh", 20.0, None)


def test_stale_sources_say_so_with_the_age(tmp_path):
    g, p = _sources(tmp_path, _gpu(ts=NOW - 121), _props(ts=NOW - 301))
    assert g["state"] == "stale" and g["age_s"] == 121.0
    assert g["reason"] == ("stale: gpu_mem.json was written 121 s ago (the exporter runs every 30 s; "
                           "older than 120 s is stale)")
    assert p["state"] == "stale" and "301 s ago" in p["reason"]
    # The boundaries themselves are fresh.
    g, p = _sources(tmp_path, _gpu(ts=NOW - 120), _props(ts=NOW - 300))
    assert g["state"] == p["state"] == "fresh"


def test_missing_torn_and_future_files(tmp_path):
    g, p = _sources(tmp_path)
    assert g["state"] == p["state"] == "missing"
    assert "gpu-mem-export.timer" in g["reason"] and "llama-props-export.timer" in p["reason"]
    (tmp_path / "gpu_mem.json").write_text("{ torn wri")
    g, _ = _sources(tmp_path)
    assert g["state"] == "unreadable" and g["age_s"] is None and "JSONDecodeError" in g["reason"]
    g, _ = _sources(tmp_path, _gpu(ts=NOW + 3600))
    assert g["state"] == "stale" and "in the future" in g["reason"]


# ── all fresh ─────────────────────────────────────────────────────────────

def test_all_fresh_every_fact_comes_from_a_file(tmp_path):
    out = _build(tmp_path, _gpu(), _props())
    s = _by_name(out)
    g = s["gemma-4-e4b"]
    assert (g["state"], g["health"], g["unit"]) == ("running", "answering", "llama-gemma4-e4b")
    assert g["gpu_bytes"] == 5_475_282_944 + 4_829_184
    assert (g["gtt_bytes"], g["vram_bytes"], g["rss_bytes"]) == (5_475_282_944, 4_829_184, 11_023_216_640)
    assert (g["n_ctx"], g["total_slots"]) == (32768, 4)
    assert (g["model_file"], g["model_bytes"]) == ("gemma-4-E4B-it-qat-UD-Q4_K_XL-202607.gguf", 4_215_695_776)
    assert (g["draft_file"], g["draft_bytes"]) == ("mtp-gemma-4-E4B-it-Q8_0-202607.gguf", 98_671_936)
    assert (g["memory_age_s"], g["props_age_s"]) == (10.0, 20.0)
    assert g["agents"] == ["coordinator", "research"]
    assert g["reasons"] == {}
    d = s["devstral-2512-q4"]
    # Context and slots as the server reports them, not divided.
    assert (d["n_ctx"], d["total_slots"]) == (131072, 4)
    assert d["draft_file"] is None and d["reasons"]["draft_file"] == "the server runs no draft model"
    assert s["talkie"]["agents"] == []
    assert out["measured_age_s"] == 10.0
    assert out["sources"]["gpu_mem"]["state"] == "fresh" and "data" not in out["sources"]["gpu_mem"]
    # The catalog's old hand-written fields are gone for good.
    for row in out["servers"]:
        assert not {"vram_gb", "ctx_k", "params", "quant", "measured_gb", "healthy"} & set(row)


def test_other_gpu_processes_are_listed_with_their_memory(tmp_path):
    stray = {"unit": "llama-qwen-test", "pid": 9, "port": 11450, "gtt_bytes": 30 * 2**30,
             "vram_bytes": 0, "rss_bytes": 2**30}
    props = _props()
    props["servers"].append(_server("llama-qwen-test", 11450, "qwen-Q4_K_M.gguf", 9_000_000_000, 8192, 1))
    out = _build(tmp_path, _gpu(extra=[stray]), props)
    others = {o["unit"]: o for o in out["other_processes"]}
    assert set(others) == {"wyoming-whisper", "llama-qwen-test"}
    w = others["wyoming-whisper"]
    assert (w["kind"], w["gpu_bytes"], w["rss_bytes"]) == ("speech to text", 2_130_194_432 + 2_211_840, 781_197_312)
    assert "model_file" not in w
    q = others["llama-qwen-test"]
    assert q["kind"] == "llama unit not in the catalog"
    assert (q["gpu_bytes"], q["model_file"], q["model_bytes"]) == (30 * 2**30, "qwen-Q4_K_M.gguf", 9_000_000_000)


def test_gtt_sum_over_listed_processes(tmp_path):
    """Criterion 3 compares this sum with mem_info_gtt_used."""
    out = _build(tmp_path, _gpu(), _props())
    total = sum(s["gtt_bytes"] for s in out["servers"]) + sum(o["gtt_bytes"] for o in out["other_processes"])
    assert total == sum(p["gtt_bytes"] for p in _gpu()["processes"])


# ── staleness (criterion 6) ───────────────────────────────────────────────

def test_stale_memory_file_makes_state_unknown_and_nulls_memory(tmp_path):
    out = _build(tmp_path, _gpu(ts=NOW - 200), _props())
    for s in out["servers"]:
        assert s["state"] == "unknown" and "stale" in s["state_reason"] and "200 s ago" in s["state_reason"]
        for f in ("gpu_bytes", "gtt_bytes", "vram_bytes", "rss_bytes", "memory_age_s"):
            assert s[f] is None and "stale" in s["reasons"][f]
        # The props file is fresh: its facts stand.
        assert s["health"] == "answering" and s["n_ctx"] and s["props_age_s"] == 20.0
    assert out["measured_age_s"] is None
    assert out["other_processes"] == []
    assert out["sources"]["gpu_mem"]["age_s"] == 200.0


def test_stale_props_file_makes_health_unknown_and_nulls_its_facts(tmp_path):
    out = _build(tmp_path, _gpu(), _props(ts=NOW - 400))
    for s in out["servers"]:
        assert s["state"] == "running" and s["gpu_bytes"]
        assert s["health"] == "unknown" and "400 s ago" in s["health_reason"]
        for f in ("n_ctx", "total_slots", "model_file", "model_bytes", "draft_file", "draft_bytes", "props_age_s"):
            assert s[f] is None and "stale" in s["reasons"][f]


# ── not running, not answering (criterion 7) ──────────────────────────────

def test_catalog_port_absent_from_fresh_memory_file_is_not_running(tmp_path):
    catalog = CATALOG + [{"name": "ghost", "api_base": "http://127.0.0.1:11499/v1", "port": 11499}]
    s = _by_name(_build(tmp_path, _gpu(), _props(), catalog=catalog))["ghost"]
    assert s["state"] == "not_running"
    assert s["state_reason"] == "configured, not running: port 11499 is not in gpu_mem.json (written 10 s ago)"
    for f in ("gpu_bytes", "gtt_bytes", "vram_bytes", "rss_bytes", "n_ctx", "total_slots",
              "model_file", "model_bytes", "draft_file", "draft_bytes", "memory_age_s", "props_age_s"):
        assert s[f] is None and s["reasons"][f]
    assert s["health"] == "not_answering"


def test_running_server_absent_from_fresh_props_file_shows_memory_and_not_answering(tmp_path):
    props = _props()
    props["servers"] = [x for x in props["servers"] if x["port"] != 11441]
    s = _by_name(_build(tmp_path, _gpu(), props))["talkie"]
    assert s["state"] == "running" and s["rss_bytes"] == 24_559_325_184 and s["memory_age_s"] == 10.0
    assert s["health"] == "not_answering"
    assert s["health_reason"] == "not answering: port 11441 is not in llama_props.json (written 20 s ago)"
    for f in ("n_ctx", "total_slots", "model_file", "model_bytes"):
        assert s[f] is None and s["reasons"][f].startswith("not answering")


def test_props_error_is_not_answering_but_the_file_from_the_command_line_stands(tmp_path):
    props = _props()
    err = {"unit": "llama-talkie.service", "flags": {"model": "talkie-1930-13b-it-Q8_0.gguf", "port": "11441"},
           "port": 11441, "error": "GET /props failed: TimeoutError", "model_bytes": 14_114_014_848}
    props["servers"] = [x for x in props["servers"] if x["port"] != 11441] + [err]
    s = _by_name(_build(tmp_path, _gpu(), props))["talkie"]
    assert (s["health"], s["health_reason"]) == ("not_answering", "GET /props failed: TimeoutError")
    assert s["n_ctx"] is None and s["reasons"]["n_ctx"] == "GET /props failed: TimeoutError"
    assert (s["model_file"], s["model_bytes"]) == ("talkie-1930-13b-it-Q8_0.gguf", 14_114_014_848)


def test_props_file_from_an_older_exporter_has_no_sizes(tmp_path):
    props = _props(disk=None)
    for x in props["servers"]:
        x.pop("model_bytes", None)
        x.pop("model_draft_bytes", None)
    s = _by_name(_build(tmp_path, _gpu(), props))["gemma-4-e4b"]
    assert s["model_file"] and s["model_bytes"] is None and "older than M25" in s["reasons"]["model_bytes"]
    assert s["draft_bytes"] is None and "older than M25" in s["reasons"]["draft_bytes"]
    assert servers.disk_block(_sources(tmp_path, props=props)[1])["disk"] is None


# ── no hand-written number (criterion 8) ──────────────────────────────────

def test_every_exporter_file_missing_leaves_no_figure(tmp_path):
    with patch.object(servers, "GPU_MEM_PATH", tmp_path / "none1.json"), \
         patch.object(servers, "LLAMA_PROPS_PATH", tmp_path / "none2.json"), \
         patch.object(servers, "LITELLM_CONFIG_PATH", Path(__file__).resolve().parent.parent / "litellm/config.yaml"):
        data = _client().get("/api/servers").json()
    assert [s["name"] for s in data["servers"]] == ["devstral-2512-q4", "gemma-4-e4b", "talkie"]
    for s in data["servers"]:
        assert s["state"] == "unknown" and s["health"] == "unknown"
        for f in ("gpu_bytes", "rss_bytes", "n_ctx", "total_slots", "model_bytes", "draft_bytes",
                  "memory_age_s", "props_age_s"):
            assert s[f] is None, f
            assert "does not exist" in s["reasons"][f]
    assert data["other_processes"] == [] and data["measured_age_s"] is None


# ── no call to LiteLLM or a model server (criterion 9) ────────────────────

class _NoClient:
    def __init__(self, *a, **kw):
        raise AssertionError("/api/servers built an httpx client: it must call neither LiteLLM nor a model server")


def _client():
    import orchestrator.main as orch
    return TestClient(orch.app)


def test_api_servers_calls_nothing(tmp_path):
    g, p = tmp_path / "gpu_mem.json", tmp_path / "llama_props.json"
    g.write_text(json.dumps(_gpu(ts=time.time())))
    p.write_text(json.dumps(_props(ts=time.time())))
    with patch.object(servers, "GPU_MEM_PATH", g), patch.object(servers, "LLAMA_PROPS_PATH", p), \
         patch("servers.load_catalog", return_value=CATALOG), \
         patch.object(httpx, "AsyncClient", _NoClient):
        resp = _client().get("/api/servers")
    assert resp.status_code == 200
    assert {s["state"] for s in resp.json()["servers"]} == {"running"}
    assert not hasattr(servers, "fetch_health")


# ── /api/system ────────────────────────────────────────────────────────────

def _sysfs(tmp_path, busy="7"):
    dev = tmp_path / "drm" / "card1" / "device"
    dev.mkdir(parents=True)
    (dev / "mem_info_gtt_total").write_text("120259084288\n")
    (dev / "mem_info_gtt_used").write_text("46598000000\n")
    if busy is not None:
        (dev / "gpu_busy_percent").write_text(f"{busy}\n")
    (tmp_path / "drm" / "card0").mkdir()  # a card with no GTT counters is skipped
    return tmp_path / "drm"


def test_read_gpu_reads_gtt_and_busy_from_one_card(tmp_path):
    g = servers.read_gpu(_sysfs(tmp_path))
    assert g == {"card": "card1", "gtt_total": 120259084288, "gtt_used": 46598000000,
                 "gpu_busy_percent": 7, "reasons": {}}


def test_read_gpu_without_counters_is_null_with_reasons(tmp_path):
    g = servers.read_gpu(_sysfs(tmp_path, busy=None))
    assert g["gpu_busy_percent"] is None and "gpu_busy_percent" in g["reasons"]["gpu_busy_percent"]
    empty = servers.read_gpu(tmp_path / "nowhere")
    assert empty["gtt_total"] is None and empty["card"] is None
    assert set(empty["reasons"]) == {"gtt_total", "gtt_used", "gpu_busy_percent"}


def test_api_system_carries_host_block_and_disk(tmp_path):
    drm = _sysfs(tmp_path)
    p = tmp_path / "llama_props.json"
    p.write_text(json.dumps(_props(ts=time.time())))
    with patch.object(servers, "DRM_ROOT", drm), patch.object(servers, "LLAMA_PROPS_PATH", p):
        data = _client().get("/api/system").json()
    assert data["gpu_busy_percent"] == 7 and data["gtt_used"] == 46598000000
    assert data["mem_total"] > 0 and data["mem_available"] > 0
    assert data["disk"] == DISK and data["disk_reason"] is None
    assert data["sources"]["disk"]["state"] == "fresh" and "data" not in data["sources"]["disk"]
    assert data["sources"]["gpu"].startswith("/sys/class/drm/card1/device")
    assert data["read_at"].endswith("Z")


def test_api_system_disk_is_null_when_props_stale(tmp_path):
    p = tmp_path / "llama_props.json"
    p.write_text(json.dumps(_props(ts=time.time() - 1000)))
    with patch.object(servers, "DRM_ROOT", _sysfs(tmp_path)), patch.object(servers, "LLAMA_PROPS_PATH", p):
        data = _client().get("/api/system").json()
    assert data["disk"] is None and "stale" in data["disk_reason"]
    assert data["sources"]["disk"]["state"] == "stale"
