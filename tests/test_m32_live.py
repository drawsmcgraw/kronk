"""/work live and safe (work item M32 step 1, docs/sprint/M32-criteria.md,
"Rewritten after the review", step 1).

What the suite can check without a JavaScript engine: the route (?have=,
the in-memory copy read once per change, the page version in the envelope
and stamped into /work, no-store on /work and both API routes), the
telemetry exclusions, the exporter's house_zone, the path unit, and the
page's literals and the parts of its script the criteria name. The
scheduler itself (the fake clock, fetches that fail, hang and answer) was
run under gjs against a stub DOM (docs/sprint/M32-build.md); a real
browser is the test agent's. No socket is opened.
"""
import importlib.util
import json
import os
import re
import time
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import work_api

pytestmark = pytest.mark.usefixtures("no_network")

REPO = Path(__file__).resolve().parent.parent
STATIC = REPO / "orchestrator" / "static"
PAGE = (STATIC / "work.html").read_text()
SCRIPT = PAGE.split("<script>", 1)[1]
CODE = "\n".join(line for line in SCRIPT.splitlines() if not line.lstrip().startswith("//"))
STYLE = PAGE.split("<style>", 1)[1].split("</style>", 1)[0]
SYSTEMD = REPO / "systemd"


def _literal(name):
    from tests.test_m27_work_page import _literal as lit
    return lit(PAGE, name)


def _outline(ts, **extra):
    return {"schema": 3, "ts": ts, "items": [], "house_zone": {"name": "America/New_York", "utc_offset_s": -18000},
            **extra}


class _File(type(Path())):
    """The fixture's work.json, carrying the route's kept copy as .cache."""


@pytest.fixture
def work_file(tmp_path):
    p = _File(tmp_path / "work.json")
    cache: dict = {}
    with patch.object(work_api, "WORK_PATH", p), patch.object(work_api, "_cache", cache):
        p.cache = cache
        yield p


def _client():
    import orchestrator.main as orch
    return TestClient(orch.app)


# ═══ the route: ?have=, the in-memory copy, the envelope ═══════════════════

def test_have_the_files_ts_answers_unchanged_with_no_outline(work_file):
    ts = time.time() - 5
    work_file.write_text(json.dumps(_outline(ts)))
    c = _client()
    full = c.get("/api/work").json()
    assert full["unchanged"] is False and full["work"]["ts"] == ts
    same = c.get(f"/api/work?have={full['work']['ts']!r}").json()
    assert same["unchanged"] is True and same["work"] is None
    assert same["source"]["state"] == "fresh" and same["source"]["age_s"] is not None
    assert set(same) == set(full)


@pytest.mark.parametrize("have", ["1", "", "soon", "nan", "inf", "1e999", "0x10"])
def test_have_another_or_no_number_answers_the_whole_file(work_file, have):
    work_file.write_text(json.dumps(_outline(1_800_000_000.5)))
    d = _client().get("/api/work", params={"have": have}).json()
    assert d["unchanged"] is False and d["work"]["ts"] == 1_800_000_000.5


def test_have_is_matched_as_the_number_the_page_sends_back():
    # JavaScript's String(ts) is the shortest round-trip form, as Python's repr.
    for ts in (1764151234.123456, 1764151234.0, 1764151234, 1.5e9 + 1 / 3):
        assert work_api._same_ts(repr(ts), ts)
        assert work_api._same_ts(str(float(ts)), ts)
    assert not work_api._same_ts("1764151234.123457", 1764151234.123456)
    assert not work_api._same_ts(None, 1.0) and not work_api._same_ts("1", True) and not work_api._same_ts("1", None)


def test_have_with_no_file_or_an_unreadable_one_is_never_unchanged(work_file):
    c = _client()
    d = c.get("/api/work?have=5").json()
    assert d["source"]["state"] == "missing" and d["unchanged"] is False and d["work"] is None
    work_file.write_text("{not json")
    d = c.get("/api/work?have=5").json()
    assert d["source"]["state"] == "unreadable" and d["unchanged"] is False


def test_have_on_a_stale_file_still_says_stale(work_file):
    now = 1_800_000_000.0
    work_file.write_text(json.dumps(_outline(now - 400)))
    d = work_api.work_source(now, have=repr(now - 400))
    assert d["unchanged"] is True and d["source"]["state"] == "stale" and d["source"]["age_s"] == 400


def test_one_read_of_the_file_per_change_however_many_polls(work_file):
    """Criterion 1e's file reads: twenty pages polling see one read per
    change of the file; the age still counts on every poll."""
    now = 1_800_000_000.0
    work_file.write_text(json.dumps(_outline(now - 5)))
    real_read = Path.read_text
    with patch.object(Path, "read_text", side_effect=AssertionError("read_text on a poll")):
        ages = [work_api.work_source(now + i)["source"]["age_s"] for i in range(20)]
    assert work_file.cache["reads"] == 1 and ages == [5.0 + i for i in range(20)]
    # The exporter's way of writing: temp file and rename.
    tmp = work_file.with_name(".work.json.abcdefgh")
    tmp.write_text(json.dumps(_outline(now + 50)))
    os.replace(tmp, work_file)
    for i in range(20):
        d = work_api.work_source(now + 60 + i)
    assert work_file.cache["reads"] == 2 and d["work"]["ts"] == now + 50
    assert real_read(work_file)                                      # the file is what was read


def test_the_kept_copy_follows_a_file_rewritten_in_place(work_file):
    now = 1_800_000_000.0
    work_file.write_text(json.dumps(_outline(now - 5, n=1)))
    assert work_api.work_source(now)["work"]["n"] == 1
    work_file.write_text(json.dumps(_outline(now - 4, n=22)))       # another size and mtime
    assert work_api.work_source(now)["work"]["n"] == 22
    work_file.write_text("{broken")
    assert work_api.work_source(now)["source"]["state"] == "unreadable"
    assert work_api.work_source(now)["source"]["reason"].startswith("work.json cannot be read: JSONDecodeError")
    assert work_file.cache["reads"] == 3


def _outline_encodings(spy_calls):
    return sum(1 for v in spy_calls if isinstance(v, dict) and "items" in v)


def test_one_encoding_of_the_outline_per_change_however_many_full_answers(work_file):
    """Fix round 1, S2: the full answer's work part is kept encoded beside
    the parse. Twenty full answers of one file: one encoding; the file
    changes, twenty more: one more."""
    now = time.time()
    work_file.write_text(json.dumps(_outline(now - 5, items=[{"id": f"M{i}", "t": "é ✓"} for i in range(50)])))
    calls = []
    real = work_api._dumps
    c = _client()
    with patch.object(work_api, "_dumps", side_effect=lambda v: (calls.append(v), real(v))[1]):
        bodies = [c.get("/api/work").content for _ in range(20)]
        assert _outline_encodings(calls) == 1 and work_file.cache["encodings"] == 1
        assert len({json.loads(b)["work"]["ts"] for b in bodies}) == 1
        tmp = work_file.with_name(".work.json.abcdefgh")
        tmp.write_text(json.dumps(_outline(now - 1, items=[])))
        os.replace(tmp, work_file)
        bodies = [c.get("/api/work").content for _ in range(20)]
        assert _outline_encodings(calls) == 2 and work_file.cache["encodings"] == 2
        assert json.loads(bodies[-1])["work"]["ts"] == now - 1
        unchanged = c.get(f"/api/work?have={now - 1!r}").json()       # no outline to encode
        assert unchanged["unchanged"] is True and unchanged["work"] is None
        assert _outline_encodings(calls) == 2


@pytest.mark.parametrize("have", [None, "match", "5"])
def test_the_answers_bytes_parse_to_what_they_did(work_file, have):
    """The spliced answer is what FastAPI's JSONResponse rendered from
    work_source's dict: the same bytes, so the same parse and key order."""
    from fastapi.encoders import jsonable_encoder
    from fastapi.responses import JSONResponse
    now = 1_800_000_000.0
    work_file.write_text(json.dumps(_outline(now - 5, items=[{"id": "M32", "t": "Heard — “as of” ✓"}])))
    h = repr(now - 5) if have == "match" else have
    want = JSONResponse(jsonable_encoder(work_api.work_source(now, have=h))).body
    assert work_api.work_body(now, have=h) == want
    assert work_api.work_body(now, have=h) == want                  # from the kept encoding
    r = _client().get("/api/work", params={} if h is None else {"have": h})
    assert r.headers["content-type"] == "application/json" and r.headers["cache-control"] == "no-store"
    assert list(r.json()) == ["source", "schema_known", "work", "unchanged", "page_version", "server_s"]


def test_the_envelope_carries_the_page_version_and_the_hosts_clock(work_file):
    work_file.write_text(json.dumps(_outline(1_800_000_000.0)))
    d = work_api.work_source(1_800_000_010.0)
    assert d["page_version"] == work_api.PAGE_VERSION and re.fullmatch(r"[0-9a-f]{12}", d["page_version"])
    assert d["server_s"] == 1_800_000_010.0


def test_the_page_version_is_work_html_and_kronk_css(tmp_path):
    for n in work_api.PAGE_FILES:
        (tmp_path / n).write_text(n)
    v = work_api.page_version(tmp_path)
    assert work_api.page_version(tmp_path) == v
    (tmp_path / "kronk.css").write_text("changed")
    assert work_api.page_version(tmp_path) != v
    (tmp_path / "kronk.css").unlink()
    assert re.fullmatch(r"[0-9a-f]{12}", work_api.page_version(tmp_path))     # a version still
    assert work_api.page_version(STATIC) == work_api.PAGE_VERSION


def _served(path):
    """GET a page with /app/static read from the tree."""
    real_open = open

    def fake_open(p, *a, **kw):
        if str(p).startswith("/app/static/"):
            return real_open(STATIC / str(p)[len("/app/static/"):], *a, **kw)
        return real_open(p, *a, **kw)
    with patch("builtins.open", side_effect=fake_open):
        return _client().get(path)


def test_work_is_served_with_its_version_in_its_own_bytes_and_no_store():
    r = _served("/work")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    assert PAGE.count(work_api.PAGE_STAMP) == 1
    m = re.findall(r'<meta name="kronk-page-version" content="([^"]*)">', r.text)
    assert m == [work_api.PAGE_VERSION]
    # The page's own reload check reads the same tag the same way.
    assert '/<meta name="kronk-page-version" content="([^"]*)">/' in SCRIPT
    assert r.text.replace(m[0], "", 1) == PAGE                       # nothing else changed


def test_both_api_routes_say_no_store(work_file, tmp_path):
    c = _client()
    assert c.get("/api/work").headers["cache-control"] == "no-store"
    assert c.get("/api/work?have=1").headers["cache-control"] == "no-store"
    import voice_runs_api
    with patch.object(voice_runs_api, "VOICE_RUNS_STATUS", tmp_path / "none.json"):
        ok = c.get("/api/voice_runs?limit=0")
        bad = c.get("/api/voice_runs?limit=-1")
    assert ok.status_code == 200 and ok.headers["cache-control"] == "no-store"
    assert bad.status_code == 400 and bad.headers["cache-control"] == "no-store"


def test_the_old_page_reads_the_new_route():
    """Criterion 1g: an answer without ?have= is the old envelope with three
    keys added; item-M34's page reads source and work only."""
    import subprocess
    old = subprocess.run(["git", "-C", str(REPO), "show", "item-M34:orchestrator/static/work.html"],
                         capture_output=True, text=True)
    if old.returncode != 0:
        pytest.skip("item-M34 is not here")
    script = old.stdout.split("<script>", 1)[1].split("// ── Heard (work item M30 step C)", 1)[0]
    read = set(re.findall(r"\bresp\.(\w+)", script))
    assert read <= {"source", "work"}, read
    assert "fetch('api/work')" in script


# ═══ telemetry: no trace per poll ═════════════════════════════════════════

def test_work_and_its_two_routes_leave_the_telemetry():
    from opentelemetry.util.http import parse_excluded_urls

    import telemetry
    ex = parse_excluded_urls(telemetry._DEFAULT_EXCLUDED)        # what instrument_app() is given
    for path in ("/work", "/api/work", "/api/voice_runs"):
        assert ex.url_disabled(f"http://kronk{path}"), path
    for path in ("/message", "/api/chat", "/voice/api/chat", "/v1/chat/completions", "/workx", "/api/workers",
                 "/api/work/x", "/api/settings"):
        assert not ex.url_disabled(f"http://kronk{path}"), path


# ═══ the exporter: house_zone, schema 3 ═══════════════════════════════════

def _exporter():
    path = os.environ.get("M27_WORK_EXPORT", REPO / "scripts" / "work_export.py")
    spec = importlib.util.spec_from_file_location("m32_we", path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_house_zone_by_tz_by_the_localtime_link_and_never_a_wrong_name(tmp_path, monkeypatch):
    we = _exporter()
    assert we.SCHEMA >= 3                                          # 3 added house_zone
    now = 1_800_000_000.0
    monkeypatch.setenv("TZ", "America/New_York")
    time.tzset()
    try:
        assert we.house_zone(now) == {"name": "America/New_York", "utc_offset_s": -18000}   # 2027-01-15, EST
        monkeypatch.setenv("TZ", ":America/New_York")
        time.tzset()
        assert we.house_zone(now)["name"] == "America/New_York"
        # A link elsewhere than the TZ's zone is not taken: TZ wins, and a
        # name whose offset now is not the host's is null.
        monkeypatch.setenv("TZ", "UTC0")                   # a POSIX rule, not a name the page can use
        time.tzset()
        assert we.house_zone(now) == {"name": None, "utc_offset_s": 0}
        monkeypatch.delenv("TZ")
        time.tzset()                                       # the host's own zone, by /etc/localtime
        host = os.path.realpath("/etc/localtime")
        want = host.split("/zoneinfo/", 1)[1] if "/zoneinfo/" in host else None
        link = tmp_path / "localtime"
        link.symlink_to(host)
        assert we.house_zone(now, str(link))["name"] == want
        other = "Asia/Tokyo" if want != "Asia/Tokyo" else "Europe/Berlin"
        elsewhere = tmp_path / "elsewhere"
        elsewhere.symlink_to(f"/usr/share/zoneinfo/{other}")
        assert we.house_zone(now, str(elsewhere))["name"] is None     # its offset is not the host's
        (tmp_path / "plain").write_text("x")
        assert we.house_zone(now, str(tmp_path / "plain"))["name"] is None
    finally:
        monkeypatch.undo()
        time.tzset()


def test_the_house_zone_is_in_the_file(tmp_path):
    we = _exporter()
    with patch.object(we, "herdr_agents", side_effect=RuntimeError("no herdr here")):
        data = we.collect(REPO, tmp_path / "no.db")
    assert data["schema"] >= 3 and data["database"]["state"] == "missing"
    z = data["house_zone"]
    assert set(z) == {"name", "utc_offset_s"} and isinstance(z["utc_offset_s"], int)


# ═══ the path unit ════════════════════════════════════════════════════════

def test_the_path_unit_starts_the_export_on_the_live_dump():
    u = (SYSTEMD / "kronk-work-export.path").read_text()
    assert re.search(r"^PathChanged=/home/drew/\.local/state/kronk-work/work\.db\.dump\.sql$", u, re.M)
    assert re.search(r"^Unit=kronk-work-export\.service$", u, re.M)
    assert "WantedBy=default.target" in u
    for op in ("install:", "check:", "verify:", "remove:"):
        assert op in u, op
    # The timer stays at 60 s in step 1.
    assert "OnUnitActiveSec=60" in (SYSTEMD / "kronk-work-export.timer").read_text()


# ═══ the page ═════════════════════════════════════════════════════════════

def test_the_live_literals():
    live = _literal("LIVE")
    assert live["every_ms"] == 3000 and live["timeout_ms"] == 10000 and live["tick_ms"] == 1000
    assert live["reload_gap_s"] == 600 and live["daily_ms"] == 86_400_000 and live["heard_folded_ms"] == 600_000
    assert live["version_meta"] == "kronk-page-version" and live["url"] == "api/work"
    t = _literal("LIVE_TEXT")
    for k in ("{since}", "{why}", "{for}"):
        assert k in t["no_answer"], k
    assert "{age}" in t["age"]


def test_one_scheduler_a_settimeout_chain_and_no_interval():
    assert "setInterval" not in CODE
    tick = _body("liveTick")
    assert "setTimeout(liveTick, Math.max(LIVE.tick_min_ms, wait));" in tick.split("finally {", 1)[1]
    assert "Math.min(LIVE.tick_ms, live.due - performance.now())" in tick
    assert "if (!live.inflight && now >= live.due) pending(loadAll());" in tick
    # The next poll is due every_ms after the last one ended.
    assert "live.due = performance.now() + LIVE.every_ms;" in _body("loadAll")
    assert len(re.findall(r"^\s+liveTick\(\);", CODE, re.M)) == 1 and CODE.count("liveStart();") == 1


def test_every_fetch_goes_through_the_time_limit():
    assert CODE.count("fetch(") == 1 and "fetch(url, ctl ?" in _body("getText")
    assert "LIVE.timeout_ms" in _body("getText") and "Promise.race([ask, late])" in _body("getText")


def test_a_failed_poll_keeps_the_last_state():
    load = _body("loadAll")
    assert "renderEmpty" not in load and "liveFailed(" in load
    failed = _body("liveFailed")
    assert "render" not in failed and "clear(" not in failed


def test_no_clock_of_this_browser():
    """Ages by performance.now() and the route's age_s; the reload limit by
    the route's server_s. new Date(x) of a stated time reads no clock."""
    assert "Date.now" not in CODE and not re.search(r"new Date\(\s*\)", CODE)
    assert "new Date().toLocale" not in CODE


def test_shown_times_are_in_the_house_zone():
    hs = _body("houseString")
    assert "timeZone: z.name" in hs and "z.utc_offset_s" in hs and "zoneKnown(z)" in hs
    assert "zoneKnown(z)" in _body("zoneNote")
    assert "houseString(d, { month: 'short'" in SCRIPT and "houseString(new Date(sec * 1000)" in SCRIPT
    assert "toLocaleTimeString" not in CODE                         # "Heard"'s "as of" too (fix round 1, note 7)
    assert re.search(r"const clockOf = sec => \(sec == null \? '\?' : fmtClockS\(sec\)\);", SCRIPT)
    assert "this browser’s local time." not in PAGE.split("const LIVE_TEXT", 1)[0]


def test_the_cover_is_the_stylesheets_and_nothing_moves_by_itself():
    assert re.search(r"\.cover \{[^}]*animation: cover-show 1s linear 90s forwards;", STYLE)
    # No endless animation in step 1: step 3's two (the pulse and the stripe,
    # tests/test_m32_step3.py) are in the track's block only.
    assert "infinite" not in STYLE.split("/* The track and its motion", 1)[0]
    assert re.search(r"\.header-dot\.beat\.on \{ animation: beat 0\.9s ease-out 1; \}", STYLE)
    assert 'id="cover"' in PAGE.split("<script>", 1)[0]
    assert "This page has stopped updating." in PAGE.split("<script>", 1)[0]
    ans = _body("liveAnswered")
    assert "restart(document.getElementById('cover'))" in ans and "classList.add('on')" in ans


def test_the_reload_cannot_strand_the_page():
    r = _body("maybeReload")
    assert "r.status === 200" in r and "m[1] !== want" in r
    assert r.index("getText(location.href") < r.index("location.reload()")
    assert "LIVE.reload_gap_s" in r and "live.serverAt" in r and "LIVE.retry_page_ms" in r
    assert CODE.count("location.reload()") == 1 and "location.href =" not in CODE and "location.assign" not in CODE
    # The limit's stamp is stored before the reload, and no storage means no reload.
    assert r.index("reloadStamp(live.serverAt)") < r.index("location.reload()")
    assert "if (last === undefined) {" in r


def test_heard_joins_the_scheduler_and_is_trimmed():
    assert "now - heard.lastEnd >= HEARD.every_ms" in _body("liveTick")
    assert "heardTrim();" in _body("heardPatch")
    trim = _body("heardTrim")
    assert "heard.rows.size <= heard.limit" in trim and "dropRow(k.run_id)" in trim
    assert "heard.nextBefore = `${last.start_s},${last.run_id}`" in trim
    assert "heard.limit = Math.max(HEARD.rows, runs.length);" in _body("heardLoad")
    assert "LIVE.heard_folded_ms) heardForget();" in _body("liveTick")


def test_note1_the_date_note_names_the_utc_days():
    note = PAGE.split('<div class="section-label">Done</div>', 1)[1].split("</div>", 2)[0]
    assert "UTC day" in note
    for i in ("M2", "M5", "M12", "M13", "M21-step2"):
        assert i in note, i


def test_text_is_still_text():
    assert not re.search(r"\.(innerHTML|outerHTML|insertAdjacentHTML)\b|document\.write", SCRIPT)


def _body(name):
    i = SCRIPT.index(f"function {name}(")
    j = SCRIPT.index("{", SCRIPT.index(")", i))
    depth = 0
    for k in range(j, len(SCRIPT)):
        depth += {"{": 1, "}": -1}.get(SCRIPT[k], 0)
        if depth == 0:
            return SCRIPT[j:k + 1]
    raise AssertionError(name)
