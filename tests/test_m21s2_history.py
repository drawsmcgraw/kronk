"""Sprint M21 step 2, criteria 15 and 16: history and revert through the
save path. Temporary live directory and git repository; no sockets."""
import asyncio
import json

import httpx
import pytest

import config_loader
import settings_api
import settings_auth
import settings_git
import settings_save
from tool_service import config_loader as ts_config
from tests.m21s2_helpers import HOST, ORIGIN, REPO, git, make_live, orch_transport, ts_transport

pytestmark = pytest.mark.usefixtures("no_network")


@pytest.fixture
def live(tmp_path, monkeypatch):
    d = make_live(tmp_path)
    monkeypatch.setenv("KRONK_CONFIG_DIR", str(d))
    monkeypatch.setenv("SETTINGS_PASSPHRASE_HASH", settings_auth.hash_passphrase("throwaway-history-test", log2_n=14))
    monkeypatch.setenv("SETTINGS_INSTANCE", "dev")
    monkeypatch.setenv("SETTINGS_AUDIT_LOG", str(tmp_path / "audit.jsonl"))
    monkeypatch.setattr(settings_api, "ts_client", lambda timeout=5: httpx.AsyncClient(
        transport=ts_transport(), base_url="http://tool_service", timeout=timeout))
    monkeypatch.setattr(settings_save, "_lock", asyncio.Lock())
    settings_git.forget_health()
    settings_auth.reset()
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


def call(method, path, session=None, *, body=None, params=None):
    async def go():
        h = {"origin": ORIGIN, "x-real-ip": "192.0.2.10"}
        cookies = {}
        if session is not None:
            cookies["kronk_settings_dev"] = session.sid
            h["x-settings-token"] = session.token
        async with httpx.AsyncClient(transport=orch_transport(), base_url=f"http://{HOST}", cookies=cookies) as c:
            return await c.request(method, path, json=body, params=params, headers=h)
    return asyncio.run(go())


def opened(name):
    return call("GET", "/api/settings/file", params={"name": name}).json()


def save(session, name, old, new):
    f = opened(name)
    assert old in f["text"], old
    r = call("POST", "/api/settings/save", session, body={"name": name, "text": f["text"].replace(old, new, 1),
                                                        "base": f["blob"]})
    assert r.status_code == 200, r.text
    return r.json()["commit"]


def revert(session, name, commit):
    return call("POST", "/api/settings/revert", session, body={"name": name, "commit": commit,
                                                             "base": opened(name)["blob"]})


def test_history_lists_commits_with_date_message_and_who(live, session):
    c1 = save(session, "voice.yaml", "deadline_s: 25", "deadline_s: 30")
    h = call("GET", "/api/settings/history", params={"name": "voice.yaml"}).json()["commits"]
    assert [c["commit"] for c in h] == [c1, git(live, "rev-parse", "HEAD~1").strip()]
    assert h[0]["who"] == "dev page, client 192.0.2.10" and h[0]["subject"].startswith("settings: voice.yaml:")
    assert h[0]["date"] and h[0]["revertable"] is True and h[1]["revertable"] is False
    d = call("GET", "/api/settings/diff", params={"name": "voice.yaml", "commit": c1}).json()["diff"]
    assert "-deadline_s: 25" in d and "+deadline_s: 30" in d


def test_revert_is_a_new_commit_through_the_save_path(live, session):
    c1 = save(session, "voice.yaml", "deadline_s: 25", "deadline_s: 30")
    assert config_loader.latest().voice.deadline_s == 30
    r = revert(session, "voice.yaml", c1)
    assert r.status_code == 200, r.text
    assert config_loader.latest().voice.deadline_s == 25
    msg = git(live, "log", "-1", "--format=%B")
    assert f"Revert of: {c1}" in msg and "Keys: deadline_s" in msg
    assert git(live, "rev-parse", "HEAD").strip() == r.json()["commit"] != c1
    assert git(live, "status", "--porcelain") == ""
    events = [json.loads(x)["event"] for x in (live.parent / "audit.jsonl").read_text().splitlines()]
    assert events == ["save", "revert"]


def test_reverting_an_older_commit_keeps_later_edits_to_other_lines(live, session):
    c1 = save(session, "tuning.yaml", "research: 8", "research: 7")
    save(session, "tuning.yaml", "repeat_tool_nudge_at: 3", "repeat_tool_nudge_at: 4")
    r = revert(session, "tuning.yaml", c1)
    assert r.status_code == 200, r.text
    t = config_loader.latest().tuning.agent_loop
    assert t.max_rounds.research == 8 and t.repeat_tool_nudge_at == 4


def test_a_revert_of_lines_edited_again_later_is_refused(live, session):
    c1 = save(session, "voice.yaml", "deadline_s: 25", "deadline_s: 30")
    save(session, "voice.yaml", "deadline_s: 30", "deadline_s: 35")
    head = git(live, "rev-parse", "HEAD")
    r = revert(session, "voice.yaml", c1)
    assert r.status_code == 409 and "edited again later" in r.json()["message"]
    assert git(live, "rev-parse", "HEAD") == head and config_loader.latest().voice.deadline_s == 35


def test_the_seed_cannot_be_reverted(live, session):
    seed = git(live, "rev-parse", "HEAD").strip()
    r = revert(session, "voice.yaml", seed)
    assert r.status_code == 409 and "seed" in r.json()["message"]


def test_a_revert_that_no_longer_validates_is_refused_with_the_reason(live, session):
    """A hand-committed version the running code refuses, then fixed on the
    page: reverting the fix would bring the refused version back."""
    p = live / "tuning.yaml"
    p.write_text(p.read_text().replace("research: 8", "research: 8\n    reserch: 9"))
    git(live, "commit", "-qam", "by hand, with a typo")
    fix = save(session, "tuning.yaml", "research: 8\n    reserch: 9", "research: 8")
    head = git(live, "rev-parse", "HEAD")
    r = revert(session, "tuning.yaml", fix)
    assert r.status_code == 422
    m = r.json()["message"]
    assert m.startswith(f"not reverted: going back to before {fix[:7]} no longer validates against the running code")
    assert "config/tuning.yaml:22: agent_loop.max_rounds.reserch: unknown key" in m
    assert git(live, "rev-parse", "HEAD") == head


def test_revert_needs_the_session_and_a_real_commit(live, session):
    c1 = save(session, "voice.yaml", "deadline_s: 25", "deadline_s: 30")
    assert call("POST", "/api/settings/revert", None, body={"name": "voice.yaml", "commit": c1}).status_code == 403
    assert revert(session, "voice.yaml", "not-a-commit").status_code == 404
    assert revert(session, "voice.yaml", "0" * 40).status_code == 404
    assert revert(session, "tuning.yaml", c1).status_code == 404          # c1 did not touch tuning.yaml
    assert call("POST", "/api/settings/revert", session,
                body={"name": "../voice.yaml", "commit": c1, "base": "0" * 40}).status_code == 404


def test_a_music_revert_runs_the_corpus_check(live, session):
    c1 = save(session, "music.yaml", "timeout_s: 8", "timeout_s: 9")
    r = revert(session, "music.yaml", c1)
    assert r.status_code == 200, r.text
    assert r.json()["corpus"]["parser_rows"] > 50
    assert ts_config.latest().music.verify.timeout_s == 8
