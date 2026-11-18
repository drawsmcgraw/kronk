"""Sprint M21 step 2, criteria 12 to 14: the voice-corpus check before a
music-policy or lead-in save, "apply to dev first" and "bring over from
dev", and the prompt note.

The parser-and-decision layer runs in a child process of tool_service, as
in the service; the child blocks every socket. Everything else is
in-process (httpx ASGI and mock transports). No sockets in this process."""
import asyncio
import inspect
import json
import subprocess
import sys
import time

import httpx
import pytest

import config_loader
import corpus_routing
import settings_api
import settings_auth
import settings_corpus
import settings_git
import settings_save
from tool_service import config_loader as ts_config
from tool_service import corpus_check, settings_info
from tests.m21s2_helpers import HOST, ORIGIN, REPO, git, make_live, orch_transport, ts_transport

pytestmark = pytest.mark.usefixtures("no_network")

HASH = settings_auth.hash_passphrase("throwaway-corpus-test", log2_n=14)


@pytest.fixture
def live(tmp_path, monkeypatch):
    d = make_live(tmp_path)
    monkeypatch.setenv("KRONK_CONFIG_DIR", str(d))
    monkeypatch.setenv("SETTINGS_PASSPHRASE_HASH", HASH)
    monkeypatch.setenv("SETTINGS_INSTANCE", "production")
    monkeypatch.setenv("SETTINGS_AUDIT_LOG", str(tmp_path / "audit.jsonl"))
    monkeypatch.setattr(settings_api, "ts_client", lambda timeout=5: httpx.AsyncClient(
        transport=ts_transport(), base_url="http://tool_service", timeout=timeout))
    monkeypatch.setattr(settings_corpus, "CORPUS_FILE", REPO / "tests" / "voice_corpus.jsonl")
    monkeypatch.setattr(settings_info, "CORPUS_FILE", REPO / "tests" / "voice_corpus.jsonl")
    monkeypatch.setattr(settings_info, "SNAPSHOT_FILE", REPO / "tests" / "fixtures" / "music_snapshot.json")
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


def call(method, path, session=None, *, body=None, params=None, headers=None, real_ip="192.0.2.10"):
    async def go():
        h = {"origin": ORIGIN}
        if real_ip:
            h["x-real-ip"] = real_ip
        cookies = {}
        if session is not None:
            cookies["kronk_settings_production"] = session.sid
            h["x-settings-token"] = session.token
        h.update(headers or {})
        async with httpx.AsyncClient(transport=orch_transport(), base_url=f"http://{HOST}", cookies=cookies) as c:
            return await c.request(method, path, json=body, params=params, headers=h)
    return asyncio.run(go())


def opened(name):
    return call("GET", "/api/settings/file", params={"name": name}).json()


def head(live):
    return git(live, "rev-parse", "HEAD").strip()


# ── criterion 12 ─────────────────────────────────────────────────────────────

LEAD_IN_ROWS = None


def _lead_in_rows():
    rows = settings_corpus.load_rows() if settings_corpus.CORPUS_FILE.exists() else []
    return [r["id"] for r in rows if r["expect"]["rule"] == "lead_in" and "routing" not in r.get("xfail", {})]


def test_a_lead_in_edit_that_breaks_rows_is_blocked_then_saved_with_overrides(live, session):
    f = opened("lead_in.yaml")
    text = f["text"].replace("- Kronk", "- Jarvis", 1)
    assert text != f["text"]
    before = head(live)
    r = call("POST", "/api/settings/save", session, body={"name": "lead_in.yaml", "text": text, "base": f["blob"]})
    assert r.status_code == 409, r.text
    b = r.json()
    blocking = b["blocking"]
    assert blocking == sorted(set(blocking))
    routing_rows = {c["id"] for c in b["corpus"]["newly_failing"] if c["layer"] == "routing"}
    # Every lead-in row that routes by the lead-in today newly fails its routing...
    assert routing_rows == set(_lead_in_rows()) and routing_rows
    # ...and the music rows among them also read differently (the lead-in is no longer cut off).
    parser_rows = {c["id"] for c in b["corpus"]["newly_failing"] if c["layer"] == "parser and decision"}
    assert parser_rows <= routing_rows
    assert set(blocking) == routing_rows | parser_rows
    assert b["corpus"]["corpus_digest"] == settings_corpus.digest()
    assert head(live) == before and "- Kronk" in (live / "lead_in.yaml").read_text()
    assert config_loader.latest().lead_in.names == ["Kronk"]
    # Overriding only some rows still blocks.
    r = call("POST", "/api/settings/save", session, body={"name": "lead_in.yaml", "text": text, "base": f["blob"],
                                                        "overrides": blocking[:1]})
    assert r.status_code == 409 and r.json()["blocking"] == blocking[1:]
    # Every row confirmed: saved, and the commit records the overrides.
    r = call("POST", "/api/settings/save", session, body={"name": "lead_in.yaml", "text": text, "base": f["blob"],
                                                        "overrides": blocking})
    assert r.status_code == 200, r.text
    msg = git(live, "log", "-1", "--format=%B")
    assert "Override: voice corpus rows " + ", ".join(sorted(blocking)) in msg
    assert config_loader.latest().lead_in.names == ["Jarvis"]
    audit = [json.loads(x) for x in (live.parent / "audit.jsonl").read_text().splitlines()]
    assert audit[-1]["event"] == "save" and audit[-1]["overrides"] == sorted(blocking)


def test_a_music_policy_edit_that_breaks_rows_is_blocked(live, session):
    f = opened("music.yaml")
    old = "  radio: [pandora]"
    assert old in f["text"], "music.yaml layout changed; update this test"
    text = f["text"].replace(old, "  radio: [ytmusic]")
    r = call("POST", "/api/settings/check", session, body={"name": "music.yaml", "text": text})
    assert r.status_code == 200, r.text
    c = r.json()["corpus"]
    assert c["parser_rows"] > 50 and c["newly_failing"]
    assert all(x["layer"] == "parser and decision" for x in c["newly_failing"])
    assert any("provider" in x["why"] for x in c["newly_failing"])
    before = head(live)
    r = call("POST", "/api/settings/save", session, body={"name": "music.yaml", "text": text, "base": f["blob"]})
    assert r.status_code == 409 and r.json()["blocking"]
    assert head(live) == before and ts_config.latest().music.decision.provider_order.radio == ["pandora"]


def test_a_harmless_music_edit_changes_no_row_and_saves(live, session):
    f = opened("music.yaml")
    text = f["text"].replace("timeout_s: 8", "timeout_s: 9", 1)
    r = call("POST", "/api/settings/save", session, body={"name": "music.yaml", "text": text, "base": f["blob"]})
    assert r.status_code == 200, r.text
    assert r.json()["corpus"]["changed"] == [] and r.json()["corpus"]["parser_rows"] > 50


def test_other_files_run_no_corpus_check(live):
    assert not settings_corpus.applies("tuning.yaml") and not settings_corpus.applies("prompts/home.md")
    assert settings_corpus.applies("music.yaml") and settings_corpus.applies("lead_in.yaml")


def test_the_check_and_the_suite_share_the_code():
    from tests import music_snapshot
    import tests.test_voice_corpus as tvc
    for name in ("offline_env", "decide_row", "Replay", "origin_of", "key", "READ_COMMANDS", "DEVICE_IDS"):
        assert getattr(music_snapshot, name) is getattr(corpus_check, name), name
    assert "_cc.parser_diff" in inspect.getsource(tvc.parser_diff)
    assert "corpus_routing.routing_diff" in inspect.getsource(tvc.test_corpus_routing)
    assert "corpus_routing.direct_diff" in inspect.getsource(tvc.test_corpus_direct_call)


def test_the_images_carry_the_corpus_from_the_same_commit():
    ts = (REPO / "tool_service" / "Dockerfile").read_text()
    orch = (REPO / "orchestrator" / "Dockerfile").read_text()
    assert "COPY --from=corpus voice_corpus.jsonl fixtures/music_snapshot.json /app/corpus/" in ts
    assert "corpus_check.py" in ts
    assert "COPY --from=corpus voice_corpus.jsonl /app/corpus/" in orch
    import yaml
    compose = yaml.safe_load((REPO / "docker-compose.yml").read_text())["services"]
    for svc in ("orchestrator", "orchestrator-dev", "tool_service", "tool_service-dev"):
        assert compose[svc]["build"]["additional_contexts"] == {"corpus": "./tests"}, svc
    ignore = (REPO / "tests" / ".dockerignore").read_text().split()
    assert "*" in ignore and "!voice_corpus.jsonl" in ignore


def test_the_child_process_blocks_every_socket():
    r = subprocess.run([sys.executable, "-I", "-c",
                        "import sys; sys.path.insert(0, sys.argv[1]); import corpus_check, socket\n"
                        "corpus_check._block_network()\n"
                        "for f in (lambda: socket.create_connection(('192.0.2.1', 9)),\n"
                        "          lambda: socket.getaddrinfo('example.com', 80),\n"
                        "          lambda: socket.socket(socket.AF_INET, socket.SOCK_STREAM)):\n"
                        "    try: f(); print('REACHED')\n"
                        "    except OSError as e: print('blocked', e)\n",
                        str(REPO / "tool_service")], capture_output=True, text=True, timeout=60)
    assert r.stdout.count("blocked the corpus check reaches no network") == 3, r.stdout + r.stderr


def test_the_routing_check_never_leaks_the_candidate(live):
    """The candidate is pinned for the check only: after it, requests see
    the values in effect."""
    f = opened("lead_in.yaml")
    asyncio.run(settings_corpus.check("lead_in.yaml", f["text"].replace("- Kronk", "- Jarvis", 1)))
    assert config_loader.current().lead_in.names == ["Kronk"]
    assert corpus_routing.outcome("ask Kronk to play jazz")["rule"] == "lead_in"


# ── criterion 13 ─────────────────────────────────────────────────────────────

class FakeDev:
    """Dev's orchestrator, as production's page sees it over the network."""

    def __init__(self, files=None, save_status=200):
        self.requests = []
        self.files = files or {}
        self.save_status = save_status

    def __call__(self, request: httpx.Request):
        self.requests.append(request)
        if request.url.path == "/api/settings/digests":
            return httpx.Response(200, json={"instance": "dev", "files": self.files})
        if request.url.path == "/api/settings/file":
            return httpx.Response(200, json={"name": request.url.params["name"], "text": "<!-- dev -->\ndev text\n",
                                             "blob": "d" * 40, "head": "e" * 40})
        if request.url.path == "/api/settings/peer/save":
            return httpx.Response(self.save_status, json={"ok": self.save_status == 200, "keys": ["prompt text"],
                                                          "commit": "f" * 40, "message": "saved on dev"})
        return httpx.Response(404, json={})


def _with_dev(monkeypatch, fake):
    monkeypatch.setenv("SETTINGS_DEV_URL", "http://orchestrator-dev:8100")
    monkeypatch.setattr(settings_api, "dev_client", lambda timeout=5: httpx.AsyncClient(
        transport=httpx.MockTransport(fake), base_url="http://orchestrator-dev:8100"))


def test_apply_to_dev_signs_and_sends_only_to_dev(live, session, monkeypatch):
    fake = FakeDev()
    _with_dev(monkeypatch, fake)
    f = opened("prompts/home.md")
    disk, before = (live / "prompts" / "home.md").read_bytes(), head(live)
    r = call("POST", "/api/settings/apply_dev", session, body={"name": "prompts/home.md", "text": f["text"] + "\nx\n"})
    assert r.status_code == 200, r.text
    assert "Production is unchanged" in r.json()["message"]
    (req,) = [q for q in fake.requests if q.url.path == "/api/settings/peer/save"]
    h = req.headers
    assert settings_auth.peer_verify(req.content, h["x-kronk-peer-ts"], h["x-kronk-peer-nonce"],
                                     h["x-kronk-peer-sig"]) is None
    sent = json.loads(req.content)
    assert sent["name"] == "prompts/home.md" and sent["who"] == "production page, client 192.0.2.10"
    assert (live / "prompts" / "home.md").read_bytes() == disk and head(live) == before
    assert config_loader.latest().prompts["home"] in f["text"]
    audit = [json.loads(x) for x in (live.parent / "audit.jsonl").read_text().splitlines()]
    assert audit[-1]["event"] == "apply_dev" and audit[-1]["commit"] == "f" * 40


def test_apply_to_dev_needs_the_session(live, monkeypatch):
    fake = FakeDev()
    _with_dev(monkeypatch, fake)
    r = call("POST", "/api/settings/apply_dev", None, body={"name": "prompts/home.md", "text": "x"})
    assert r.status_code == 403 and fake.requests == []


def test_the_page_marks_files_where_dev_differs(live, monkeypatch):
    d = call("GET", "/api/settings").json()
    blobs = {f["name"]: f["blob"] for f in d["files"]}
    fake = FakeDev(files={**blobs, "prompts/home.md": "0" * 40})
    _with_dev(monkeypatch, fake)
    files = {f["name"]: f for f in call("GET", "/api/settings").json()["files"]}
    assert files["prompts/home.md"]["dev"]["differs"] is True
    assert files["tuning.yaml"]["dev"]["differs"] is False


def test_bring_over_from_dev_loads_dev_text_and_saves_through_production(live, session, monkeypatch):
    _with_dev(monkeypatch, FakeDev())
    r = call("GET", "/api/settings/dev_file", session, params={"name": "prompts/home.md"})
    assert r.status_code == 200 and r.json()["text"] == "<!-- dev -->\ndev text\n"
    assert call("GET", "/api/settings/dev_file", None, params={"name": "prompts/home.md"}).status_code == 403
    f = opened("prompts/home.md")
    r = call("POST", "/api/settings/save", session, body={"name": "prompts/home.md", "text": r.json()["text"],
                                                        "base": f["blob"]})
    assert r.status_code == 200 and config_loader.latest().prompts["home"] == "dev text"


def _peer(payload: dict, *, ts=None, nonce=None, sig=None, real_ip=None):
    raw = json.dumps(payload).encode()
    ts = int(time.time()) if ts is None else ts
    nonce = nonce or f"n{time.time_ns()}"
    sig = sig or settings_auth.peer_sign(raw, ts, nonce)

    async def go():
        h = {"content-type": "application/json", "x-kronk-peer-ts": str(ts), "x-kronk-peer-nonce": nonce,
             "x-kronk-peer-sig": sig}
        if real_ip:
            h["x-real-ip"] = real_ip
        async with httpx.AsyncClient(transport=orch_transport(), base_url="http://orchestrator-dev:8100") as c:
            return await c.post("/api/settings/peer/save", content=raw, headers=h)
    return asyncio.run(go()), nonce


def test_dev_takes_a_signed_candidate_through_its_own_save_path(live, monkeypatch):
    monkeypatch.setenv("SETTINGS_INSTANCE", "dev")
    f = opened("prompts/talkie.md")
    payload = {"name": "prompts/talkie.md", "text": f["text"] + "\nfrom production\n", "overrides": [],
               "who": "production page, client 192.0.2.10", "client": "192.0.2.10"}
    r, nonce = _peer(payload)
    assert r.status_code == 200, r.text
    assert "from production" in config_loader.latest().prompts["talkie"]
    assert "By: production page, client 192.0.2.10, applied to dev first" in git(live, "log", "-1", "--format=%B")
    r, _ = _peer(payload, nonce=nonce)                     # replay
    assert r.status_code == 403 and "nonce" in r.json()["detail"]
    r, _ = _peer(payload, ts=int(time.time()) - 600)        # stale
    assert r.status_code == 403
    r, _ = _peer(payload, sig="0" * 64)                     # forged
    assert r.status_code == 403
    r, _ = _peer(payload, real_ip="192.0.2.99")             # through nginx
    assert r.status_code == 403
    bad = {**payload, "text": "<!-- h -->\n{{nope}}\n"}
    r, _ = _peer(bad)                                       # dev validates
    assert r.status_code == 422


# ── criterion 14 ─────────────────────────────────────────────────────────────

def test_the_prompt_note_is_on_the_page():
    page = (REPO / "orchestrator" / "static" / "settings.html").read_text()
    assert "A saved prompt reaches the household at once" in page
    assert "re-reads the whole prompt" in page and "that one request is slower" in page
    assert "if (f.kind === 'prompt') kids.push(" in page
