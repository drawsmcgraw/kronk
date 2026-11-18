"""Sprint M21 step 2, criteria 1 to 4: the read side of /settings.

A temporary live directory (its own git repository), both loaders pointed
at it, the orchestrator and tool_service apps in-process. No sockets."""
import asyncio
import json
import re
from pathlib import Path

import httpx
import pytest

import config_loader
import settings_api
import settings_auth
import settings_git
from tool_service import config_loader as ts_config
from tests.m21s2_helpers import REPO, git, make_live, orch_client, ts_transport, tree

pytestmark = pytest.mark.usefixtures("no_network")

SECRETS = {
    "HA_TOKEN": "secret-ha-token-7f3a91",
    "MA_TOKEN": "secret-ma-token-1c2d3e",
    "SOLAR_SERIAL": "secret-solar-serial-554433",
    "MM_SSH_TARGET": "secret-pi@mirror-target-998877",
    "LANGFUSE_SECRET_KEY": "secret-langfuse-665544",
}


@pytest.fixture
def live(tmp_path, monkeypatch):
    d = make_live(tmp_path)
    monkeypatch.setenv("KRONK_CONFIG_DIR", str(d))
    monkeypatch.setattr(settings_api, "STATIC", REPO / "orchestrator" / "static")
    monkeypatch.setattr(settings_api, "ts_client", lambda timeout=5: httpx.AsyncClient(
        transport=ts_transport(), base_url="http://tool_service", timeout=timeout))
    settings_git.forget_health()
    settings_auth.reset()
    config_loader.start()
    ts_config.start()
    yield d
    monkeypatch.setenv("KRONK_CONFIG_DIR", str(REPO / "config"))
    config_loader.start()
    ts_config.start()
    settings_git.forget_health()


def get(path, **kw):
    async def go():
        async with orch_client() as c:
            return await c.get(path, **kw)
    return asyncio.run(go())


def test_every_file_and_every_key_with_value_source_comment_and_last_change(live):
    r = get("/api/settings")
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/json")
    d = r.json()
    names = [f["name"] for f in d["files"]]
    assert set(names) == {str(p.relative_to(live)) for p in live.rglob("*") if p.is_file() and ".git" not in p.parts}
    seed = git(live, "rev-parse", "HEAD").strip()
    for f in d["files"]:
        assert f["description"], f["name"]
        assert f["last_change"]["commit"] == seed
        if f["kind"] == "prompt":
            name = f["name"][len("prompts/"):-3]
            assert f["prompt_in_effect"] == config_loader.current().prompts[name]    # whole
            continue
        assert f["keys"], f["name"]
        for k in f["keys"]:
            assert set(k) >= {"key", "value", "source", "default", "bounds", "type", "comment", "last_change"}
            assert k["source"] in ("file", "code default", "environment")
    tuning = next(f for f in d["files"] if f["name"] == "tuning.yaml")
    research = next(k for k in tuning["keys"] if k["key"] == "agent_loop.max_rounds.research")
    assert research["value"] == 8 and research["source"] == "file"
    assert "budget-cliff" in research["comment"]
    assert research["bounds"] == {"ge": 1, "le": 20}
    assert research["last_change"]["commit"] == seed
    music = next(f for f in d["files"] if f["name"] == "music.yaml")
    verify = next(k for k in music["keys"] if k["key"] == "verify.timeout_s")
    assert verify["value"] == 8 and verify["source"] == "file" and verify["comment"]


def test_sources_and_per_key_last_change_follow_the_git_history(live, monkeypatch):
    text = (live / "tuning.yaml").read_text()
    (live / "tuning.yaml").write_text(text.replace("research: 8", "research: 6"))
    git(live, "commit", "-qam", "research budget", "-m", "By: production page, client 192.0.2.7")
    second = git(live, "rev-parse", "HEAD").strip()
    seed = git(live, "rev-parse", "HEAD~1").strip()
    text = (live / "tuning.yaml").read_text()
    (live / "tuning.yaml").write_text(re.sub(r"\n  repeat_tool_nudge_at: 3\n", "\n", text))
    git(live, "commit", "-qam", "drop the nudge key")
    third = git(live, "rev-parse", "HEAD").strip()
    monkeypatch.setenv("WEATHER_CTX_MAX_AGE_S", "3600")
    assert config_loader.reload()["ok"]
    settings_git.forget_health()
    d = get("/api/settings").json()
    keys = {k["key"]: k for k in next(f for f in d["files"] if f["name"] == "tuning.yaml")["keys"]}
    assert keys["agent_loop.max_rounds.research"]["value"] == 6
    assert keys["agent_loop.max_rounds.research"]["last_change"]["commit"] == second
    assert keys["agent_loop.max_rounds.research"]["last_change"]["who"] == "production page, client 192.0.2.7"
    assert keys["agent_loop.repeat_tool_nudge_at"]["source"] == "code default"
    assert keys["agent_loop.repeat_tool_nudge_at"]["last_change"]["commit"] == third
    assert keys["weather_context.max_age_s"]["source"] == "environment"
    assert keys["weather_context.max_age_s"]["env"] == "WEATHER_CTX_MAX_AGE_S"
    assert keys["weather_context.max_age_s"]["value"] == 3600
    assert keys["tool_timeouts.default_s"]["last_change"]["commit"] == seed


def test_readonly_sections(live):
    ro = get("/api/settings").json()["readonly"]
    import routing
    assert [r["name"] for r in ro["routing"]] == [r.name for r in routing.RULES]
    assert all(r["doc"] for r in ro["routing"])
    talkie = next(r for r in ro["routing"] if r["name"] == "talkie_explicit")
    assert talkie["patterns"][0]["source"] == routing._TALKIE_PHRASES.pattern
    env_names = {e["name"] for e in ro["env"]["orchestrator"]} | {e["name"] for e in ro["env"]["tool_service"]}
    for name in ("VOICE_STREAM", "MODEL_LOCK", "MUSIC_SHADOW", "MUSIC_DRY_RUN", "ERROR_STYLE",
                 "TELEMETRY_RECORD_TEXT", "COORDINATOR_MODEL", "DEVOPS_AGENT_MODEL", "ROUTER_MODEL"):
        assert name in env_names, name
    assert all(e["change"]["production"] and e["change"]["dev"] for e in ro["env"]["orchestrator"])
    tools = {t["name"]: t for t in ro["tools"]}
    assert "web_search" in tools and tools["web_search"]["description"]
    assert "devops" not in tools["web_search"]["offered_to"]
    assert "ask_home" in tools and "escalate" in tools
    texts = [c["name"] for c in ro["code_text"]["orchestrator"]] + [c["name"] for c in ro["code_text"]["tool_service"]]
    assert "Budget exhausted" in texts and "Music model question (system message)" in texts
    assert ro["models"]["props_error"]          # no exporter file in the test
    assert {g["name"] for g in ro["guards"]} >= {"Magic mirror update", "Devops tools", "fetch_url address block",
                                                "Read-only exec classifier", "Music dry run"}


def test_llama_props_from_the_exporter_file(live, tmp_path, monkeypatch):
    import time
    import servers
    p = tmp_path / "llama_props.json"
    p.write_text(json.dumps({"ts": time.time(), "servers": [
        {"unit": "llama-gemma4-e4b.service", "port": 11438, "n_ctx": 32768, "total_slots": 4,
         "sampling": {"temperature": 1.0, "top_k": 64}, "flags": {"ctx_size": "32768", "reasoning_budget": "256"}}]}))
    monkeypatch.setattr(servers, "LLAMA_PROPS_PATH", p)
    monkeypatch.setattr(servers, "LITELLM_CONFIG_PATH", REPO / "litellm" / "config.yaml")
    m = get("/api/settings").json()["readonly"]["models"]
    row = next(s for s in m["servers"] if s["name"] == "gemma-4-e4b")
    assert row["n_ctx"] == 32768 and row["ctx_k_litellm"] == 128 and row["flags"]["reasoning_budget"] == "256"
    assert m["props_error"] is None
    p.write_text(json.dumps({"ts": time.time() - 3600, "servers": []}))
    assert "stale" in get("/api/settings").json()["readonly"]["models"]["props_error"]


def test_instance_release_editing_state_and_config_errors(live, monkeypatch):
    monkeypatch.setenv("SETTINGS_INSTANCE", "dev")
    monkeypatch.setenv("KRONK_RELEASE", "sprint-test")
    monkeypatch.delenv("SETTINGS_PASSPHRASE_HASH", raising=False)
    (live / "voice.yaml").write_text("deadline_s: 500\n")
    assert config_loader.reload()["ok"] is False
    d = get("/api/settings").json()
    assert d["instance"] == "dev" and d["release"] == "sprint-test"
    assert d["editing"]["enabled"] is False
    assert "SETTINGS_PASSPHRASE_HASH is not set" in d["editing"]["reason"]
    assert "scripts/settings_passphrase.sh" in d["editing"]["how"]
    (err,) = [e for e in d["config_errors"] if e["service"] == "orchestrator"]
    assert err["file"] == "config/voice.yaml" and err["line"] == 1 and "deadline_s" in err["message"]
    voice = next(f for f in d["files"] if f["name"] == "voice.yaml")
    assert voice["differs_from_effect"] is True and voice["uncommitted"] is True


def test_no_secret_in_any_read_answer(live, monkeypatch):
    for k, v in SECRETS.items():
        monkeypatch.setenv(k, v)
    h = settings_auth.hash_passphrase("throwaway-view-test", log2_n=14)
    monkeypatch.setenv("SETTINGS_PASSPHRASE_HASH", h)
    import tool_service.main as ts_main
    monkeypatch.setattr(ts_main, "HA_TOKEN", SECRETS["HA_TOKEN"])
    monkeypatch.setattr(ts_main, "MA_TOKEN", SECRETS["MA_TOKEN"])
    seed = git(live, "rev-parse", "HEAD").strip()
    bodies = [get("/api/settings").text, get("/settings").text, get("/api/settings/digests").text,
              get("/api/settings/history", params={"name": "tuning.yaml"}).text,
              get("/api/settings/diff", params={"name": "tuning.yaml", "commit": seed}).text]
    for name in ("tuning.yaml", "music.yaml", "prompts/home.md"):
        bodies.append(get("/api/settings/file", params={"name": name}).text)
    blob = "\n".join(bodies)
    for k, v in SECRETS.items():
        assert v not in blob, k
    for part in h.split(":")[4:]:
        assert part not in blob, "a part of the passphrase hash"
    assert "SETTINGS_PASSPHRASE_HASH" not in {e["name"] for e in json.loads(bodies[0])["readonly"]["env"]["orchestrator"]}


def test_read_endpoints_change_nothing_and_need_no_cookie(live):
    before, head = tree(live), git(live, "rev-parse", "HEAD")
    status_before = config_loader.latest()
    seed = head.strip()
    for path, params in (("/api/settings", None), ("/settings", None), ("/api/settings/digests", None),
                         ("/api/settings/file", {"name": "lead_in.yaml"}),
                         ("/api/settings/history", {"name": "prompts/home.md"}),
                         ("/api/settings/diff", {"name": "prompts/home.md", "commit": seed})):
        r = get(path, params=params)
        assert r.status_code == 200, (path, r.text)
        assert "set-cookie" not in r.headers
    assert tree(live) == before and git(live, "rev-parse", "HEAD") == head
    assert config_loader.latest() is status_before


def test_unknown_names_are_404_on_read_routes(live):
    for name in ("../tuning.yaml", "/etc/passwd", "prompts/x.md", ".git/config"):
        assert get("/api/settings/file", params={"name": name}).status_code == 404
        assert get("/api/settings/history", params={"name": name}).status_code == 404


def test_html_in_a_prompt_is_returned_as_data_and_the_page_never_parses_html(live):
    p = live / "prompts" / "talkie.md"
    p.write_text(p.read_text() + "\n<script>alert('x')</script><img src=x onerror=alert(1)>\n")
    assert config_loader.reload()["ok"]
    r = get("/api/settings")
    assert r.headers["content-type"].startswith("application/json")
    talkie = next(f for f in r.json()["files"] if f["name"] == "prompts/talkie.md")
    assert "<script>alert('x')</script>" in talkie["prompt_in_effect"]
    page = (REPO / "orchestrator" / "static" / "settings.html").read_text()
    script = page.split("<script>", 1)[1]
    for sink in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function"):
        assert sink not in script, sink
    assert "document.createTextNode" in script and ".textContent" in script


def test_page_is_phone_width_ready():
    page = (REPO / "orchestrator" / "static" / "settings.html").read_text()
    assert '<meta name="viewport" content="width=device-width, initial-scale=1.0">' in page
    assert "@media (max-width: 640px)" in page
    assert "/static/kronk.css" in page
    assert "fetch('api/settings" not in page and "api('api/settings" in page   # relative: works under /dev/


def test_a_broken_git_repository_never_blocks_viewing(live):
    import shutil
    shutil.rmtree(live / ".git")
    settings_git.forget_health()
    d = get("/api/settings").json()
    assert d["git"]["ok"] is False and "not a git repository" in d["git"]["message"]
    assert d["editing"]["enabled"] is False
    assert all(f["last_change"] is None for f in d["files"])
    assert next(f for f in d["files"] if f["name"] == "tuning.yaml")["keys"]


def test_nginx_serves_the_page_like_the_ui_and_refuses_reload_and_peer_routes():
    conf = (REPO / "nginx" / "nginx.conf").read_text()
    for path in ("/config/", "/dev/config/", "/api/settings/peer/", "/dev/api/settings/peer/"):
        assert f"location ^~ {path}" in conf and "return 404" in conf.split(f"location ^~ {path}", 1)[1].split("\n")[0]
    assert "location /settings" not in conf and "location /api/settings " not in conf   # served by location /
    assert "listen 80;" in conf and conf.count("listen ") == 1                          # nothing new listens
