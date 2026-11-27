"""GET /api/status asks no model for a completion (work item M38).

LiteLLM's /health sent a 5-token completion to every catalog model and
emptied gemma's prompt cache on every poll of the chat page's dot. The
route now asks LiteLLM /health/liveliness, tool_service /health, and reads
each model server's health from llama_props.json (the host exporter's
file). Criterion 1: no request to LiteLLM's /health or to a model server;
a server down in the file, or a stale file, gives 503 with its name.

The HTTP client is an httpx.MockTransport that records every request; no
socket is opened.
"""
import json
import time
from unittest.mock import patch

import httpx
import pytest
import yaml
from fastapi.testclient import TestClient

import servers

pytestmark = pytest.mark.usefixtures("no_network")

GEMMA, TALKIE = 11438, 11441


def _entry(unit, port, **kw):
    return {"unit": f"{unit}.service", "port": port, "n_ctx": 2048, "total_slots": 1,
            "flags": {"model": f"{unit}.gguf"}, **kw}


@pytest.fixture
def env(tmp_path, monkeypatch):
    cfg = tmp_path / "litellm.yaml"
    cfg.write_text(yaml.safe_dump({"model_list": [
        {"model_name": "gemma-4-e4b", "litellm_params": {"api_base": f"http://127.0.0.1:{GEMMA}/v1"}},
        {"model_name": "talkie", "litellm_params": {"api_base": f"http://127.0.0.1:{TALKIE}/v1"}},
    ]}))
    props = tmp_path / "llama_props.json"
    monkeypatch.setattr(servers, "LITELLM_CONFIG_PATH", cfg)
    monkeypatch.setattr(servers, "LLAMA_PROPS_PATH", props)

    def write(entries, age_s=10.0):
        props.write_text(json.dumps({"ts": time.time() - age_s, "servers": entries}))
    write([_entry("llama-gemma4-e4b", GEMMA), _entry("llama-talkie", TALKIE)])
    return {"cfg": cfg, "props": props, "write": write}


def _call(liveliness=200, tool=200, tool_exc=None):
    """GET /api/status with every outbound request recorded."""
    import orchestrator.main as orch
    seen: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        url = str(req.url)
        seen.append(f"{req.method} {url}")
        if url.endswith("/health/liveliness"):
            return httpx.Response(liveliness, json="I'm alive!")
        if url.endswith("/health") and ":8002" not in url and "host.docker.internal" not in url:
            if tool_exc:
                raise tool_exc
            return httpx.Response(tool, json={"status": "ok", "music_dry_run": True})
        return httpx.Response(599, text="unexpected request in test")

    real = httpx.AsyncClient

    def client(*a, **kw):
        kw.pop("transport", None)
        return real(*a, transport=httpx.MockTransport(handler), **kw)

    with patch.object(orch, "LLM_SERVICE_URL", "http://litellm.test:8002"), \
            patch.object(orch.tools, "TOOL_SERVICE_URL", "http://tools.test:8003"), \
            patch.object(orch.httpx, "AsyncClient", client):
        resp = TestClient(orch.app).get("/api/status")
    return resp, seen


def _no_model_or_health_completion(seen):
    for s in seen:
        assert not s.endswith("litellm.test:8002/health"), f"LiteLLM /health runs a completion: {s}"
        assert f":{GEMMA}" not in s and f":{TALKIE}" not in s, f"request to a model server: {s}"
        assert "/v1/" not in s, s


def test_all_up_is_200_and_asks_no_model(env):
    resp, seen = _call()
    assert resp.status_code == 200
    assert seen == ["GET http://litellm.test:8002/health/liveliness", "GET http://tools.test:8003/health"]
    _no_model_or_health_completion(seen)
    body = resp.json()
    assert body["status"] == "ok"
    # every key a caller reads today (voice_replay.py, m9_lock_bench.py)
    assert body["tool_service"]["music_dry_run"] is True
    llm = body["llm_service"]
    assert isinstance(llm, dict)
    assert llm["litellm"] == {"up": True, "reason": None}
    assert [(s["name"], s["port"], s["health"], s["up"]) for s in llm["servers"]] == [
        ("gemma-4-e4b", GEMMA, "answering", True), ("talkie", TALKIE, "answering", True)]
    assert (llm["healthy_count"], llm["unhealthy_count"], llm["down"]) == (2, 0, [])
    src = llm["source"]
    assert src["file"] == "llama_props.json" and src["state"] == "fresh"
    assert 9 <= src["age_s"] <= 12 and src["max_age_s"] == servers.LLAMA_PROPS_MAX_AGE_S
    assert "data" not in src


def test_server_missing_from_the_file_is_503_by_name(env):
    env["write"]([_entry("llama-gemma4-e4b", GEMMA)])
    resp, seen = _call()
    assert resp.status_code == 503
    _no_model_or_health_completion(seen)
    body = resp.json()
    assert body["status"] == "down"
    assert "talkie" in body["detail"] and "gemma-4-e4b" not in body["detail"]
    assert f"port {TALKIE} is not in llama_props.json" in body["detail"]
    talkie = body["llm_service"]["servers"][1]
    assert (talkie["name"], talkie["health"], talkie["up"]) == ("talkie", "not_answering", False)
    assert body["tool_service"]["music_dry_run"] is True


def test_server_with_an_error_in_the_file_is_503_by_name(env):
    env["write"]([_entry("llama-gemma4-e4b", GEMMA, error="URLError: connection refused"),
                  _entry("llama-talkie", TALKIE)])
    resp, _ = _call()
    assert resp.status_code == 503
    assert "model server gemma-4-e4b: URLError: connection refused" in resp.json()["detail"]
    assert "talkie" not in resp.json()["detail"]


def test_stale_file_is_503_naming_every_server(env):
    env["write"]([_entry("llama-gemma4-e4b", GEMMA), _entry("llama-talkie", TALKIE)],
                 age_s=servers.LLAMA_PROPS_MAX_AGE_S + 60)
    resp, seen = _call()
    assert resp.status_code == 503
    _no_model_or_health_completion(seen)
    body = resp.json()
    assert "model server gemma-4-e4b: stale" in body["detail"]
    assert "model server talkie: stale" in body["detail"]
    assert {s["health"] for s in body["llm_service"]["servers"]} == {"unknown"}
    assert body["llm_service"]["source"]["state"] == "stale"


def test_missing_file_is_503(env):
    env["props"].unlink()
    resp, _ = _call()
    assert resp.status_code == 503
    assert "llama_props.json does not exist" in resp.json()["detail"]
    assert resp.json()["llm_service"]["source"]["state"] == "missing"


def test_empty_catalog_is_503(env):
    env["cfg"].write_text("model_list: []\n")
    resp, _ = _call()
    assert resp.status_code == 503
    assert "no model server in the catalog" in resp.json()["detail"]


def test_litellm_not_answering_is_503(env):
    resp, seen = _call(liveliness=500)
    assert resp.status_code == 503
    _no_model_or_health_completion(seen)
    body = resp.json()
    assert "LiteLLM http://litellm.test:8002/health/liveliness" in body["detail"]
    assert body["llm_service"]["litellm"]["up"] is False
    # tool_service is still asked and reported
    assert body["tool_service"]["music_dry_run"] is True


def test_tool_service_down_is_503(env):
    resp, _ = _call(tool_exc=httpx.ConnectError("refused"))
    assert resp.status_code == 503
    body = resp.json()
    assert "tool_service http://tools.test:8003/health: ConnectError: refused" in body["detail"]
    assert body["tool_service"] is None
    assert body["llm_service"]["litellm"]["up"] is True


def test_tool_service_error_status_is_503(env):
    resp, _ = _call(tool=500)
    assert resp.status_code == 503
    assert "tool_service" in resp.json()["detail"]


def test_api_servers_health_is_the_same_definition(env):
    """One definition of a server's health: /api/servers and /api/status
    agree, server by server."""
    env["write"]([_entry("llama-gemma4-e4b", GEMMA), _entry("llama-talkie", TALKIE, error="timed out")])
    props = servers.llama_props_source()
    cat = servers.load_catalog()
    gpu = {"state": "missing", "reason": "no file", "age_s": None, "data": None}
    rows = servers.build_servers(cat, gpu, props, {})["servers"]
    status = servers.model_server_health(cat, props)["servers"]
    assert [(r["name"], r["health"], r["health_reason"]) for r in rows] == \
        [(s["name"], s["health"], s["reason"]) for s in status]
