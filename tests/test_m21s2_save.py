"""Sprint M21 step 2, criteria 7, 8, 10, 16 and 24: the save path.

A temporary live directory that is its own git repository, both loaders
pointed at it, the orchestrator and tool_service apps in-process. Each
failure case checks that the file on disk, the values in effect and git
agree afterwards. No sockets."""
import asyncio
import json
import os
import stat
from pathlib import Path

import httpx
import pytest

import config_loader
import settings_api
import settings_auth
import settings_git
import settings_save
import settings_store
from tool_service import config_loader as ts_config
from tests.m21s2_helpers import HOST, ORIGIN, REPO, git, make_live, orch_transport, ts_transport

pytestmark = pytest.mark.usefixtures("no_network")


@pytest.fixture
def live(tmp_path, monkeypatch):
    d = make_live(tmp_path)
    monkeypatch.setenv("KRONK_CONFIG_DIR", str(d))
    monkeypatch.setenv("SETTINGS_PASSPHRASE_HASH", settings_auth.hash_passphrase("throwaway-save-test", log2_n=14))
    monkeypatch.setenv("SETTINGS_INSTANCE", "production")
    monkeypatch.setenv("SETTINGS_AUDIT_LOG", str(tmp_path / "audit.jsonl"))
    monkeypatch.setattr(settings_api, "ts_client", lambda timeout=5: httpx.AsyncClient(
        transport=ts_transport(), base_url="http://tool_service", timeout=timeout))
    settings_git.forget_health()
    settings_auth.reset()
    # asyncio locks bind to the first event loop that waits on them; each
    # test runs its own loop.
    monkeypatch.setattr(settings_save, "_lock", asyncio.Lock())
    config_loader.start()
    ts_config.start()
    yield d
    monkeypatch.setenv("KRONK_CONFIG_DIR", str(REPO / "config"))
    config_loader.start()
    ts_config.start()
    settings_git.forget_health()
    settings_auth.reset()


@pytest.fixture
def session(live):
    return settings_auth._new_session("192.0.2.10")


def call(method, path, session=None, *, body=None, params=None, origin=ORIGIN, token=True):
    async def go():
        h = {"x-real-ip": "192.0.2.10"}
        if origin:
            h["origin"] = origin
        cookies = {}
        if session is not None:
            cookies["kronk_settings_production"] = session.sid
            if token:
                h["x-settings-token"] = session.token
        async with httpx.AsyncClient(transport=orch_transport(), base_url=f"http://{HOST}", cookies=cookies) as c:
            return await c.request(method, path, json=body, params=params, headers=h)
    return asyncio.run(go())


def opened(name):
    return call("GET", "/api/settings/file", params={"name": name}).json()


def save(session, name, text, base, overrides=None):
    return call("POST", "/api/settings/save", session, body={"name": name, "text": text, "base": base,
                                                            "overrides": overrides or []})


def audit(live):
    p = live.parent / "audit.jsonl"
    return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []


def head(live):
    return git(live, "rev-parse", "HEAD").strip()


def test_a_save_validates_writes_reloads_verifies_and_commits(live, session):
    f = opened("tuning.yaml")
    text = f["text"].replace("research: 8", "research: 7")
    before = head(live)
    r = save(session, "tuning.yaml", text, f["blob"])
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["keys"] == ["agent_loop.max_rounds.research"] and b["commit"] != before
    assert (live / "tuning.yaml").read_text() == text
    assert config_loader.latest().tuning.agent_loop.max_rounds.research == 7
    assert config_loader.latest().digests["tuning.yaml"] == settings_save.digest(text)
    msg = git(live, "log", "-1", "--format=%B")
    assert msg.startswith("settings: tuning.yaml: agent_loop.max_rounds.research\n")
    assert "File: tuning.yaml" in msg and "By: production page, client 192.0.2.10" in msg
    assert git(live, "status", "--porcelain") == ""
    assert git(live, "show", "--stat", "--format=", "HEAD").strip().startswith("tuning.yaml")
    (a,) = audit(live)
    assert a["event"] == "save" and a["file"] == "tuning.yaml" and a["commit"] == b["commit"]
    assert a["keys"] == ["agent_loop.max_rounds.research"] and a["client"] == "192.0.2.10"


def test_a_music_save_takes_effect_in_tool_service(live, session):
    f = opened("music.yaml")
    text = f["text"].replace("timeout_s: 8", "timeout_s: 9", 1)
    r = save(session, "music.yaml", text, f["blob"])
    assert r.status_code == 200, r.text
    assert ts_config.latest().music.verify.timeout_s == 9
    assert r.json()["keys"] == ["verify.timeout_s"]


def test_an_invalid_save_never_takes_effect_and_names_file_and_line(live, session):
    f = opened("tuning.yaml")
    before, disk = head(live), (live / "tuning.yaml").read_bytes()
    r = save(session, "tuning.yaml", f["text"].replace("research: 8", "research: 80"), f["blob"])
    assert r.status_code == 422
    (e,) = r.json()["errors"]
    assert e["file"] == "config/tuning.yaml" and e["line"] == 21 and "less than or equal to 20" in e["message"]
    assert (live / "tuning.yaml").read_bytes() == disk and head(live) == before
    assert config_loader.latest().tuning.agent_loop.max_rounds.research == 8
    assert audit(live)[-1]["event"] == "save_refused" and audit(live)[-1]["reason"] == "invalid"


@pytest.mark.parametrize("text, where", [
    ("<!-- header -->\n{{nope}}\n", 2), ("no header\n", 1), ("<!-- header -->\n\n", 1),
    ("<!-- h -->\nhi \x1b there\n", 2)])
def test_invalid_prompts_are_refused(live, session, text, where):
    f = opened("prompts/home.md")
    r = save(session, "prompts/home.md", text, f["blob"])
    assert r.status_code == 422, r.text
    assert r.json()["errors"][0]["line"] == where


def test_a_stale_base_is_refused_with_the_difference(live, session):
    f = opened("voice.yaml")
    first = f["text"].replace("deadline_s: 25", "deadline_s: 30")
    assert save(session, "voice.yaml", first, f["blob"]).status_code == 200
    mine = f["text"].replace("hold_sentences: 1", "hold_sentences: 2")
    r = save(session, "voice.yaml", mine, f["blob"])          # based on the version before the first save
    assert r.status_code == 409
    c = r.json()["conflict"]
    assert "-deadline_s: 25" in c["their_change"] and "+deadline_s: 30" in c["their_change"]
    assert "+  hold_sentences: 2" in c["yours_vs_current"] and "-deadline_s: 30" in c["yours_vs_current"]
    assert c["who"] == "production page, client 192.0.2.10"
    assert "deadline_s: 30" in (live / "voice.yaml").read_text()            # not overwritten
    assert "hold_sentences: 1" in (live / "voice.yaml").read_text()         # not merged
    assert audit(live)[-1]["reason"] == "conflict"


def test_two_saves_at_once_one_wins_one_is_refused(live, session):
    f = opened("lead_in.yaml")
    a = f["text"].replace("- ask", "- ask\n  - tell", 1)
    b = f["text"].replace("- Kronk", "- Kronk\n  - Kronky", 1)

    async def both():
        async def one(text):
            h = {"origin": ORIGIN, "x-real-ip": "192.0.2.10", "x-settings-token": session.token}
            async with httpx.AsyncClient(transport=orch_transport(), base_url=f"http://{HOST}",
                                         cookies={"kronk_settings_production": session.sid}) as c:
                return await c.post("/api/settings/save", json={"name": "lead_in.yaml", "text": text,
                                                                "base": f["blob"]}, headers=h)
        return await asyncio.gather(one(a), one(b))
    ra, rb = asyncio.run(both())
    assert sorted([ra.status_code, rb.status_code]) == [200, 409], (ra.text, rb.text)


def test_owner_reload_failure_restores_the_file_and_commits_nothing(live, session):
    (live / "voice.yaml").write_text("deadline_s: 500\n")     # an invalid file on disk (by hand)
    f = opened("tuning.yaml")
    disk, before = (live / "tuning.yaml").read_bytes(), head(live)
    r = save(session, "tuning.yaml", f["text"].replace("research: 8", "research: 7"), f["blob"])
    assert r.status_code == 422, r.text
    b = r.json()
    assert b["step"] == "the orchestrator reload" and b["restored"] is True
    assert "config/voice.yaml:1" in b["message"]
    assert (live / "tuning.yaml").read_bytes() == disk and head(live) == before
    assert config_loader.latest().tuning.agent_loop.max_rounds.research == 8
    assert git(live, "status", "--porcelain", "--", "tuning.yaml") == ""
    assert audit(live)[-1]["event"] == "save_failed" and audit(live)[-1]["outcome"] == "restored"


def test_a_wrong_value_in_effect_restores(live, session, monkeypatch):
    real = settings_save.reload_service

    async def lying(owner):
        r = await real(owner)
        return {**r, "digests": {**r["digests"], "tuning.yaml": "000000000000"}}
    monkeypatch.setattr(settings_save, "reload_service", lying)
    f = opened("tuning.yaml")
    r = save(session, "tuning.yaml", f["text"].replace("research: 8", "research: 7"), f["blob"])
    assert r.status_code == 500 and r.json()["step"] == "the check of the value in effect"
    monkeypatch.setattr(settings_save, "reload_service", real)
    assert config_loader.latest().tuning.agent_loop.max_rounds.research == 8
    assert "research: 8" in (live / "tuning.yaml").read_text()


def test_a_failed_commit_restores_file_and_values(live, session, monkeypatch):
    def boom(*a, **kw):
        raise settings_git.GitError("git commit failed: disk full")
    monkeypatch.setattr(settings_git, "commit_file", boom)
    f = opened("music.yaml")
    before = head(live)
    r = save(session, "music.yaml", f["text"].replace("timeout_s: 8", "timeout_s: 9", 1), f["blob"])
    assert r.status_code == 500 and r.json()["step"] == "the git commit"
    assert "previous music.yaml is back on disk and in effect" in r.json()["message"]
    assert ts_config.latest().music.verify.timeout_s == 8
    assert head(live) == before and git(live, "status", "--porcelain") == ""


def test_a_failed_restore_says_exactly_what_state_is_left(live, session, monkeypatch):
    def boom(*a, **kw):
        raise settings_git.GitError("git commit failed")
    monkeypatch.setattr(settings_git, "commit_file", boom)
    real_write = settings_store.write_file
    calls = {"n": 0}

    def write_once(*a, **kw):
        calls["n"] += 1
        if calls["n"] > 1:
            raise settings_store.StoreError("read-only file system")
        return real_write(*a, **kw)
    monkeypatch.setattr(settings_store, "write_file", write_once)
    f = opened("voice.yaml")
    r = save(session, "voice.yaml", f["text"].replace("deadline_s: 25", "deadline_s: 30"), f["blob"])
    assert r.status_code == 500
    assert r.json()["message"].startswith("NOT SAVED and NOT CONSISTENT")
    assert audit(live)[-1]["outcome"] == "inconsistent"


def test_the_other_service_failing_is_a_warning_and_the_save_stands(live, session):
    (live / "music.yaml").write_text("verify:\n  timeout_s: 999\n")    # tool_service's file, broken by hand
    f = opened("tuning.yaml")
    r = save(session, "tuning.yaml", f["text"].replace("research: 8", "research: 7"), f["blob"])
    assert r.status_code == 200, r.text
    (w,) = r.json()["warnings"]
    assert w.startswith("tool_service (which does not read tuning.yaml) did not reload cleanly")
    assert config_loader.latest().tuning.agent_loop.max_rounds.research == 7
    assert git(live, "status", "--porcelain").strip() == "M music.yaml"      # only the hand edit is left


def test_tool_service_down_refuses_a_music_save_and_writes_nothing(live, session, monkeypatch):
    def down(request):
        raise httpx.ConnectError("connection refused")
    monkeypatch.setattr(settings_api, "ts_client", lambda timeout=5: httpx.AsyncClient(
        transport=httpx.MockTransport(down), base_url="http://tool_service"))
    f = opened("music.yaml")
    disk = (live / "music.yaml").read_bytes()
    r = save(session, "music.yaml", f["text"].replace("timeout_s: 8", "timeout_s: 9", 1), f["blob"])
    assert r.status_code == 422 and "tool_service could not validate it" in r.json()["errors"][0]["message"]
    assert (live / "music.yaml").read_bytes() == disk


def test_no_change_makes_no_commit(live, session):
    f = opened("voice.yaml")
    before = head(live)
    r = save(session, "voice.yaml", f["text"], f["blob"])
    assert r.status_code == 200 and r.json()["changed"] is False and head(live) == before


@pytest.mark.parametrize("name", ["../tuning.yaml", "/etc/passwd", "prompts/new.md", "new.yaml",
                                  ".git/config", "prompts/../voice.yaml", None, 7])
def test_the_editor_cannot_write_outside_the_set(live, session, name):
    r = save(session, name, "x: 1\n", "0" * 40)
    assert r.status_code == 404
    assert not (live.parent / "tuning.yaml").exists() and not (live / "new.yaml").exists()
    assert git(live, "status", "--porcelain") == ""


def test_size_limit_on_save(live, session):
    f = opened("prompts/home.md")
    r = save(session, "prompts/home.md", "<!-- h -->\n" + "x" * (64 * 1024), f["blob"])
    assert r.status_code == 422 and "too large" in r.json()["errors"][0]["message"]


def test_schema_bounds_hold_and_no_env_or_routing_can_be_set(live, session):
    f = opened("tuning.yaml")
    for bad in (f["text"] + "\nrouting:\n  rules: []\n", f["text"] + "\nWEATHER_CTX_MAX_AGE_S: 60\n",
                f["text"].replace("max_overtakes: 3", "max_overtakes: 101")):
        r = save(session, "tuning.yaml", bad, f["blob"])
        assert r.status_code == 422, bad[-60:]
    assert head(live) == git(live, "rev-parse", "HEAD").strip()


def test_state_changing_requests_need_session_token_and_origin(live, session):
    f = opened("voice.yaml")
    body = {"name": "voice.yaml", "text": f["text"].replace("deadline_s: 25", "deadline_s: 30"), "base": f["blob"]}
    assert call("POST", "/api/settings/save", None, body=body).status_code == 403
    assert call("POST", "/api/settings/save", session, body=body, token=False).status_code == 403
    assert call("POST", "/api/settings/save", session, body=body, origin="http://evil.example").status_code == 403
    assert call("POST", "/api/settings/save", session, body=body, origin=None).status_code == 403
    assert "deadline_s: 25" in (live / "voice.yaml").read_text()
    assert call("POST", "/api/settings/save", session, body=body).status_code == 200


def test_editing_off_without_git_but_viewing_still_works(live, session):
    import shutil
    shutil.rmtree(live / ".git")
    settings_git.forget_health()
    f = opened("voice.yaml")
    r = save(session, "voice.yaml", f["text"].replace("deadline_s: 25", "deadline_s: 30"), f["blob"])
    assert r.status_code == 403 and "not a git repository" in r.json()["detail"]
    assert call("GET", "/api/settings").status_code == 200
    assert config_loader.reload()["ok"]


def test_a_corrupt_repository_disables_editing(live, session):
    objects = [p for p in (live / ".git" / "objects").rglob("*") if p.is_file() and len(p.parent.name) == 2]
    victim = objects[0]
    victim.chmod(0o644)
    victim.write_bytes(b"garbage")
    settings_git.forget_health()
    st = call("GET", "/api/settings").json()
    assert st["editing"]["enabled"] is False and "not usable" in st["editing"]["reason"]


def test_files_stay_owned_by_the_user_after_many_saves(live, session):
    blob = opened("voice.yaml")["blob"]
    for n in (26, 27, 28, 29, 30):
        text = opened("voice.yaml")["text"]
        r = save(session, "voice.yaml", text.replace(f"deadline_s: {n - 1}", f"deadline_s: {n}"),
                 opened("voice.yaml")["blob"])
        assert r.status_code == 200, r.text
    assert blob != opened("voice.yaml")["blob"]
    uid = os.getuid()
    for p in [live / "voice.yaml", *(live / ".git").rglob("*")]:
        assert p.lstat().st_uid == uid, p
    assert stat.S_IMODE((live / "voice.yaml").stat().st_mode) == 0o644


def test_a_prompt_save_records_no_prompt_text_in_the_audit(live, session):
    f = opened("prompts/talkie.md")
    marker = "quorvexilant-prompt-marker"
    r = save(session, "prompts/talkie.md", f["text"] + f"\n{marker}\n", f["blob"])
    assert r.status_code == 200, r.text
    assert marker not in (live.parent / "audit.jsonl").read_text()
    assert r.json()["keys"][0].startswith("prompt text (")
    assert marker in config_loader.latest().prompts["talkie"]


def test_check_writes_nothing(live, session):
    f = opened("tuning.yaml")
    before, disk = head(live), (live / "tuning.yaml").read_bytes()
    r = call("POST", "/api/settings/check", session, body={"name": "tuning.yaml",
                                                         "text": f["text"].replace("research: 8", "research: 5")})
    assert r.status_code == 200 and r.json()["changed_keys"] == ["agent_loop.max_rounds.research"]
    r = call("POST", "/api/settings/check", session, body={"name": "tuning.yaml",
                                                         "text": f["text"].replace("research: 8", "research: 0")})
    assert r.status_code == 422 and r.json()["errors"][0]["line"] == 21
    assert head(live) == before and (live / "tuning.yaml").read_bytes() == disk
