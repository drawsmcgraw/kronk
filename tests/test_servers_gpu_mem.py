"""Tests for measured GPU memory on /api/servers (servers.load_gpu_mem).

The exporter (scripts/gpu_mem_export.py, host-side systemd timer) writes
data/gpu_mem.json; the orchestrator joins it into the catalog by port.
Staleness must degrade gracefully to the static estimates — a dead timer
should never render months-old numbers as 'measured'
(docs/features/resources-live-memory.md)."""
import json
import time
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

import servers


def _write(tmp_path, ts, processes):
    p = tmp_path / "gpu_mem.json"
    p.write_text(json.dumps({"ts": ts, "processes": processes}))
    return p


PROC = {"unit": "llama-devstral-q4", "pid": 1, "port": 11440,
        "gtt_bytes": 36_063_371_264, "vram_bytes": 40_325_120,
        "rss_bytes": 902_684_672}


def test_fresh_file_is_keyed_by_port(tmp_path):
    p = _write(tmp_path, time.time() - 10, [PROC, {"unit": "wyoming-whisper",
                                                   "port": None,
                                                   "gtt_bytes": 1}])
    with patch.object(servers, "GPU_MEM_PATH", p):
        out = servers.load_gpu_mem()
    assert out[11440]["gtt_bytes"] == 36_063_371_264
    assert 9 <= out["_age_s"] <= 60
    # portless processes (whisper) are measured but not joinable — excluded.
    assert set(out) == {11440, "_age_s"}


def test_stale_file_returns_empty(tmp_path):
    p = _write(tmp_path, time.time() - servers.GPU_MEM_MAX_AGE_S - 5, [PROC])
    with patch.object(servers, "GPU_MEM_PATH", p):
        assert servers.load_gpu_mem() == {}


def test_missing_and_malformed_files_return_empty(tmp_path):
    with patch.object(servers, "GPU_MEM_PATH", tmp_path / "nope.json"):
        assert servers.load_gpu_mem() == {}
    bad = tmp_path / "bad.json"
    bad.write_text("{ torn wri")
    with patch.object(servers, "GPU_MEM_PATH", bad):
        assert servers.load_gpu_mem() == {}


def test_far_future_timestamp_is_rejected(tmp_path):
    """A wrong host clock must not make garbage look permanently fresh."""
    p = _write(tmp_path, time.time() + 3600, [PROC])
    with patch.object(servers, "GPU_MEM_PATH", p):
        assert servers.load_gpu_mem() == {}


def test_health_key_handles_null_api_base():
    """Regression 2026-07-06: LiteLLM v1.88 reports api_base=null and
    model='openai/<name>' in /health — the api_base join made every
    server's health dot None (dead, not gray-by-choice)."""
    assert servers._health_key({"api_base": None, "model": "openai/gemma-4-e4b"}) \
        == "gemma-4-e4b"
    # older payload shapes keep working
    assert servers._health_key({"api_base": "http://127.0.0.1:11440/v1"}) \
        == "http://127.0.0.1:11440/v1"
    assert servers._health_key({}) is None


def test_api_servers_joins_measurement_by_port():
    import orchestrator.main as orch

    catalog = [
        {"name": "devstral-2512-q4", "api_base": "http://127.0.0.1:11440/v1",
         "port": 11440, "params": "24B", "quant": "Q4_K_M",
         "vram_gb": 14, "ctx_k": 128},
        {"name": "talkie", "api_base": "http://127.0.0.1:11441/v1",
         "port": 11441, "params": "13B", "quant": "Q8_0",
         "vram_gb": 13, "ctx_k": 2},
    ]
    gpu = {"_age_s": 12.0, 11440: PROC}

    with patch("orchestrator.main.servers.load_catalog", return_value=catalog), \
         patch("orchestrator.main.servers.load_gpu_mem", return_value=gpu), \
         patch("orchestrator.main.servers.fetch_health", new=AsyncMock(return_value={})):
        resp = TestClient(orch.app).get("/api/servers")

    data = resp.json()
    assert data["measured_age_s"] == 12.0
    by_name = {s["name"]: s for s in data["servers"]}
    # 36_063_371_264 + 40_325_120 bytes -> 36.1 GB (vs the 14 GB estimate)
    assert by_name["devstral-2512-q4"]["measured_gb"] == 36.1
    assert by_name["devstral-2512-q4"]["vram_gb"] == 14  # estimate retained
    assert by_name["talkie"]["measured_gb"] is None      # not running/no data
