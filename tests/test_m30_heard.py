"""GET /api/voice_runs and the "Heard" section of /work (work item M30 step
C, criteria C1 to C4; docs/sprint/M30-criteria.md).

The route reads the published copy scripts/voice_runs_export.py writes
(data/voice_runs.db, opened mode=ro&immutable=1) and its status file
(data/voice_runs.json). The copies here are built by the exporter's own
build_copy() in tmp_path, or by a scratch export against a fake Tempo; every
sentence in them is invented.

The page's own decisions are JSON literals in work.html (HEARD,
HEARD_BANNER, HEARD_ERROR, SATELLITE_NAME), read here as JSON and checked
against what the route gives. The suite has no JavaScript runtime: the
DOM building, the patching and the scroll are checked in a browser on dev
by the test agent (C3). No socket is opened.
"""
import ast
import datetime as dt
import hashlib
import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
from fastapi.testclient import TestClient

import voice_runs_api as V
import work_api
from tests import test_m27_work_page as M27
from tests import test_m30_voice_runs as M30

pytestmark = pytest.mark.usefixtures("no_network")

REPO = Path(__file__).resolve().parent.parent
PAGE = (REPO / "orchestrator" / "static" / "work.html").read_text()
SCRIPT = PAGE.split("<script>", 1)[1]
EX = M30.EX
BASE = "56e9368"                 # the last commit before step C

NOW = 1_795_700_000.0
KITCHEN = "assist_satellite.home_assistant_voice_0ac919_assist_satellite"
OFFICE = "assist_satellite.satellite1_d8d1bc_assist_satellite"


def iso(t: float) -> str:
    return dt.datetime.fromtimestamp(t, dt.timezone.utc).isoformat(timespec="seconds")


def run(rid, start, seq, heard="invented words", reply="invented reply", **kw):
    """One row of the published copy, as a dict of its columns."""
    r = {c: None for c in EX.PUBLISHED_COLUMNS}
    local = dt.datetime.fromtimestamp(start).astimezone()
    r.update(run_id=rid, trace_id=f"t{rid}", start_s=start, end_s=start + 5, local_time=local.isoformat(timespec="seconds"),
             local_day=local.date().isoformat(), service="kronk-voice-tracer", pipeline="kronk", is_dev=0,
             satellite_id=KITCHEN, stages="stt,intent,tts", heard=heard, heard_chars=len(heard) if heard else None,
             heard_source="tempo" if heard else None, reply=reply, tier="kronk", rule="music",
             first_seen_at=iso(start + 200), last_changed_at=iso(start + 200), seq=seq)
    r.update(kw)
    return r


def write_copy(path: Path, rows: list[dict], built: float = NOW, rows_meta=None) -> Path:
    """A published copy built by the exporter's build_copy, renamed into place."""
    tuples = [tuple(r[c] for c in EX.PUBLISHED_COLUMNS) for r in sorted(rows, key=lambda r: r["run_id"])]
    h = hashlib.sha256(f"schema {EX.PUBLISH_SCHEMA}\n".encode())
    for r in sorted(rows, key=lambda r: r["run_id"]):
        h.update(f"{r['run_id']}\x00{r['seq']}\n".encode())
    blob = EX.build_copy(tuples, h.hexdigest(), built - EX.PUBLISH_WINDOW_S, built, EX.publish_ddl())
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(blob)
    os.replace(tmp, path)
    if rows_meta is not None:
        c = sqlite3.connect(path)
        c.execute("UPDATE meta SET rows = ?", (rows_meta,))
        c.commit()
        c.close()
    return path


def status(ts=NOW, **kw) -> dict:
    d = {"ts": ts, "schema": 1, "ok": True, "last_ok_at": iso(ts), "last_error": None, "last_error_at": None,
         "this_run": {"truncated_windows": 0, "trace_read_failures": 0, "enrich_failures": 0},
         "last_check": {"differing": 0, "missing": 0},
         "summary": {"as_of": iso(ts), "as_of_s": ts,
                     "household": {"runs_24h": 2, "wrong_24h": 1, "error_24h": 0, "flagged_24h": 1,
                                   "newest_s": ts - 300, "newest_time": "2026-11-26T08:55:45-05:00"},
                     "dev": {"runs_24h": 0, "wrong_24h": 0, "error_24h": 0, "flagged_24h": 0,
                             "newest_s": None, "newest_time": None}}}
    d.update(kw)
    return d


@pytest.fixture
def files(tmp_path):
    db, st = tmp_path / "data" / "voice_runs.db", tmp_path / "data" / "voice_runs.json"
    db.parent.mkdir(parents=True)
    st.write_text(json.dumps(status(time.time())))
    with patch.object(V, "VOICE_RUNS_DB", db), patch.object(V, "VOICE_RUNS_STATUS", st):
        yield db, st


class _NoClient:
    def __init__(self, *a, **kw):
        raise AssertionError("/api/voice_runs built an HTTP client")


def get(params="", code=200):
    import orchestrator.main as orch
    with patch.object(httpx, "AsyncClient", _NoClient), patch.object(httpx, "Client", _NoClient), \
         patch("urllib.request.urlopen", side_effect=AssertionError("/api/voice_runs opened a URL")), \
         patch("subprocess.run", side_effect=AssertionError("/api/voice_runs ran a command")):
        r = TestClient(orch.app).get(f"/api/voice_runs{params}")
    assert r.status_code == code, r.text
    return r.json()


def ids(d):
    return [r["run_id"] for r in d["runs"]]


# ═══ C1: paging, q, limit, the copy missing, of an unknown schema, replaced ══

def test_c1_paging_returns_every_row_once_with_two_rows_sharing_a_timestamp(files):
    db, _ = files
    t = NOW - 3600
    rows = [run(f"R{i}", t - i * 60, i + 1) for i in range(7)]
    rows += [run("TIE-a", t - 150, 20), run("TIE-b", t - 150, 21)]    # the same start_s
    write_copy(db, rows)
    seen, before, pages = [], None, 0
    while True:
        d = get("?limit=2" + (f"&before={before}" if before else ""))
        seen += ids(d)
        pages += 1
        before = d["next_before"]
        if before is None:
            break
        assert pages < 20
    assert sorted(seen) == sorted(r["run_id"] for r in rows) and len(seen) == len(set(seen)) == 9
    # newest first, the tie broken by run id, descending
    assert seen.index("TIE-b") + 1 == seen.index("TIE-a")
    assert seen == [r["run_id"] for r in sorted(rows, key=lambda r: (r["start_s"], r["run_id"]), reverse=True)]


def test_c1_a_tie_on_the_page_boundary_is_not_dropped(files):
    db, _ = files
    write_copy(db, [run("A", NOW - 10, 1), run("B", NOW - 10, 2), run("C", NOW - 10, 3), run("D", NOW - 20, 4)])
    d1 = get("?limit=2")
    assert ids(d1) == ["C", "B"] and d1["next_before"] == f"{NOW - 10!r},B"
    d2 = get(f"?limit=2&before={d1['next_before']}")
    assert ids(d2) == ["A", "D"] and d2["next_before"] is None


@pytest.mark.parametrize("q,want", [
    ("%", ["PCT"]), ("_", ["UND"]), ("'", ["QUOTE"]), ('"', ["DQ"]), ("<b>", ["MARKUP"]),
    ("100%", ["PCT"]), ("a_b", ["UND"]), ("\\", ["BS"]), ("REPLY-ONLY", ["REP"]), ("nomatch-zz", []),
    ("INVENTED", ["BS", "DQ", "MARKUP", "PCT", "QUOTE", "REP", "UND"]),          # LIKE is case-blind for ASCII
])
def test_c1_q_is_bound_and_its_wildcards_are_literal(files, q, want):
    db, _ = files
    t = NOW - 600
    write_copy(db, [run("PCT", t - 1, 1, heard="invented 100% sure"),
                    run("UND", t - 2, 2, heard="invented a_b here"),
                    run("QUOTE", t - 3, 3, heard="invented it's"),
                    run("DQ", t - 4, 4, heard='invented say "x"'),
                    run("MARKUP", t - 5, 5, heard="invented <b>bold</b><script>alert(1)</script>"),
                    run("BS", t - 6, 6, heard="invented back\\slash"),
                    run("REP", t - 7, 7, heard="invented plain", reply="the REPLY-ONLY words"),
                    run("ABXB", t - 8, 8, heard="axb 100x", reply="nothing")])
    d = get("?" + httpx.QueryParams({"q": q}).__str__())
    assert sorted(ids(d)) == sorted(want)
    if q == "<b>":
        assert d["runs"][0]["heard"] == "invented <b>bold</b><script>alert(1)</script>"   # data, as stored


def test_c1_a_q_of_sql_is_only_text(files):
    db, _ = files
    write_copy(db, [run("A", NOW - 10, 1)])
    d = get("?" + str(httpx.QueryParams({"q": "x' OR 1=1 --"})))
    assert d["runs"] == []
    assert len(get()["runs"]) == 1


def test_c1_q_longer_than_the_limit_is_refused(files):
    assert "longer than" in get("?q=" + "a" * (V.Q_MAX + 1), code=400)["error"]


@pytest.mark.parametrize("limit,code,n", [("600", 200, 500), ("500", 200, 500), ("1", 200, 1), ("-1", 400, None),
                                          ("abc", 400, None), ("2.5", 400, None)])
def test_c1_limit_out_of_range(files, limit, code, n):
    db, _ = files
    write_copy(db, [run(f"R{i:03d}", NOW - 10 * i, i + 1) for i in range(600)])
    d = get(f"?limit={limit}", code=code)
    if code == 200:
        assert len(d["runs"]) == n and d["next_before"] is not None
        assert d["query"]["limit"] == n
    else:
        assert "limit" in d["error"]


def test_c1_the_default_is_200_rows(files):
    db, _ = files
    write_copy(db, [run(f"R{i:03d}", NOW - 10 * i, i + 1) for i in range(250)])
    assert len(get()["runs"]) == V.LIMIT_DEFAULT == 200


def test_c1_limit_0_answers_the_summary_without_opening_the_copy(files):
    db, _ = files
    write_copy(db, [run("A", NOW - 10, 1)])
    with patch.object(V, "open_copy", side_effect=AssertionError("the copy was opened")):
        d = get("?limit=0")
    assert d["runs"] == [] and d["copy"] is None
    assert d["summary"]["household"]["runs_24h"] == 2 and d["source"]["state"] == "fresh"


@pytest.mark.parametrize("before", ["x", "123", ",R1", "123,", "nan,R1"])
def test_c1_a_bad_cursor_is_refused(files, before):
    assert "next_before" in get(f"?before={before}", code=400)["error"]


def test_c1_changed_after_and_before_do_not_go_together(files):
    assert "do not go together" in get("?changed_after=1&before=1.0,R", code=400)["error"]


@pytest.mark.parametrize("flag", ["2", "maybe"])
def test_c1_a_flag_that_is_not_0_or_1_is_refused(files, flag):
    assert "flagged" in get(f"?flagged={flag}", code=400)["error"]


def test_c1_flagged_and_include_dev(files):
    db, _ = files
    t = NOW - 600
    write_copy(db, [run("OK", t - 1, 1), run("WRONG", t - 2, 2, corrected_by="stop"),
                    run("ERR", t - 3, 3, error_code="stt-no-text-recognized"),
                    run("DEV", t - 4, 4, is_dev=1, service="kronk-voice-tracer-dev", pipeline="kronk-dev")])
    assert ids(get()) == ["OK", "WRONG", "ERR"]
    assert ids(get("?flagged=1")) == ["WRONG", "ERR"]
    assert ids(get("?include_dev=1")) == ["OK", "WRONG", "ERR", "DEV"]
    assert [r["flagged"] for r in get()["runs"]] == [False, True, True]


def test_c1_the_copy_missing(files):
    d = get()
    assert d["copy"]["state"] == "missing" and d["runs"] == [] and d["next_before"] is None
    assert "kronk-voice-runs-export.timer" in d["copy"]["reason"]


def test_c1_the_copy_not_a_database(files):
    db, _ = files
    db.write_bytes(b"not a database" * 400)
    d = get()
    assert d["copy"]["state"] == "unreadable" and d["runs"] == []


def test_c1_the_copy_of_an_unknown_schema(files):
    db, _ = files
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE runs (run_id TEXT)")
    c.commit()
    c.close()
    d = get()
    assert d["copy"]["state"] == "unknown_schema" and d["runs"] == []
    assert "schema 0" in d["copy"]["reason"] and "reads schema 1" in d["copy"]["reason"]


def test_c1_a_newer_schema_with_the_columns_is_read_with_a_note_and_one_without_is_not(files):
    db, _ = files
    write_copy(db, [run("A", NOW - 10, 1)])
    c = sqlite3.connect(db)
    c.execute("PRAGMA user_version = 2")
    c.commit()
    c.close()
    d = get()
    assert ids(d) == ["A"] and d["copy"]["state"] == "ok" and "newer than this orchestrator's 1" in d["copy"]["reason"]
    c = sqlite3.connect(db)
    c.execute("ALTER TABLE runs DROP COLUMN reply")
    c.commit()
    c.close()
    d = get()
    assert d["copy"]["state"] == "unknown_schema" and d["runs"] == []


def test_c1_a_copy_whose_rows_are_not_its_meta_is_served_and_said(files):
    db, _ = files
    write_copy(db, [run("A", NOW - 10, 1), run("B", NOW - 20, 2)], rows_meta=3)
    d = get()
    assert d["copy"]["state"] == "damaged" and "holds 2 rows and its meta says 3" in d["copy"]["reason"]
    assert ids(d) == ["A", "B"]


def test_c1_the_copy_replaced_mid_request_is_read_whole_from_the_old_one(files):
    db, _ = files
    write_copy(db, [run("OLD1", NOW - 10, 1), run("OLD2", NOW - 20, 2)], built=NOW)
    real_meta = V.copy_meta

    def meta_then_replace(conn):
        m = real_meta(conn)
        write_copy(db, [run("NEW1", NOW - 5, 3)], built=NOW + 60)       # the exporter renames a new one in
        return m
    with patch.object(V, "copy_meta", meta_then_replace):
        d = get()
    assert ids(d) == ["OLD1", "OLD2"] and d["copy"]["rows"] == 2 and d["max_seq"] == 2
    d = get()
    assert ids(d) == ["NEW1"] and d["copy"]["rows"] == 1


def test_c1_reading_the_copy_makes_nothing_beside_it_and_changes_no_byte(files):
    db, _ = files
    write_copy(db, [run("A", NOW - 10, 1)])
    before = db.read_bytes()
    for p in ("", "?q=x", "?changed_after=0&known_rows=1&known_built_s=1", "?flagged=1&include_dev=1"):
        get(p)
    assert db.read_bytes() == before
    assert sorted(p.name for p in db.parent.iterdir()) == ["voice_runs.db", "voice_runs.json"]


def test_c1_the_three_states_of_heard_and_the_reply_split(files):
    db, _ = files
    write_copy(db, [run("TEXT", NOW - 1, 1, reply="first line\nsecond line\nthird"),
                    run("NOTREC", NOW - 2, 2, heard=None, heard_chars=14, reply=None),
                    run("NOTREC2", NOW - 3, 3, heard=None, heard_chars=None),
                    run("NOTHING", NOW - 4, 4, heard="", heard_chars=0, error_code="stt-no-text-recognized")])
    r = {x["run_id"]: x for x in get()["runs"]}
    assert r["TEXT"]["heard_state"] == "text"
    assert r["NOTREC"]["heard_state"] == r["NOTREC2"]["heard_state"] == "not_recorded"
    assert r["NOTHING"]["heard_state"] == "nothing_heard"
    assert (r["TEXT"]["reply_head"], r["TEXT"]["reply_rest"]) == ("first line", "second line\nthird")
    assert (r["NOTREC"]["reply_head"], r["NOTREC"]["reply_rest"]) == (None, None)
    assert r["NOTREC2"]["reply_rest"] is None and r["NOTREC2"]["reply_head"] == "invented reply"


def test_c1_a_blueprint_hand_off_has_no_rule(files):
    db, _ = files
    write_copy(db, [run("BP", NOW - 1, 1, tier="blueprint", rule=None)])
    r = get()["runs"][0]
    assert r["tier"] == "blueprint" and r["rule"] is None


def test_c1_no_column_the_copy_does_not_hold_is_asked_for():
    assert set(V.COLUMNS) <= set(EX.PUBLISHED_COLUMNS)
    assert not set(V.COLUMNS) & EX.UNPUBLISHED


# ── changed_after: new and changed rows; a removal is noticed ─────────────

def test_c1_changed_after_gives_new_and_changed_rows_with_match(files):
    db, _ = files
    base = [run("A", NOW - 300, 1), run("B", NOW - 200, 2), run("C", NOW - 100, 3)]
    write_copy(db, base, built=NOW)
    first = get()
    assert first["max_seq"] == 3 and first["copy"]["rows"] == 3
    # The next export: B changed (marked wrong, seq raised), D inserted.
    later = NOW + 60
    write_copy(db, [base[0], run("B", NOW - 200, 4, corrected_by="stop"), base[2],
                    run("D", NOW + 10, 5, first_seen_at=iso(later))], built=later)
    q = f"?changed_after=3&known_rows=3&known_built_s={first['copy']['built_s']!r}"
    d = get(q)
    assert ids(d) == ["D", "B"] and d["resync"] is False and d["max_seq"] == 5
    assert all(r["match"] for r in d["runs"])
    d = get(q + "&flagged=1")
    assert {r["run_id"]: r["match"] for r in d["runs"]} == {"D": False, "B": True}
    assert get(f"?changed_after=5&known_rows=4&known_built_s={later!r}")["runs"] == []


def test_c1_a_removal_raises_no_seq_and_is_noticed_by_the_row_count(files):
    db, _ = files
    rows = [run("A", NOW - 300, 1), run("B", NOW - 200, 2), run("C", NOW - 100, 3)]
    write_copy(db, rows, built=NOW)
    built = get()["copy"]["built_s"]
    write_copy(db, [rows[0], rows[2]], built=NOW + 30)          # --remove B publishes at once
    d = get(f"?changed_after=3&known_rows=3&known_built_s={built!r}")
    assert d["runs"] == [] and d["resync"] is True


def test_c1_a_removal_and_an_insert_in_the_same_export_is_still_noticed(files):
    db, _ = files
    rows = [run("A", NOW - 300, 1), run("B", NOW - 200, 2)]
    write_copy(db, rows, built=NOW)
    later = NOW + 60
    write_copy(db, [rows[0], run("N", NOW + 5, 3, first_seen_at=iso(later))], built=later)   # B gone, N new
    d = get(f"?changed_after=2&known_rows=2&known_built_s={NOW!r}")
    assert ids(d) == ["N"] and d["resync"] is True


def test_c1_an_insert_alone_is_no_resync_and_the_same_copy_is_none(files):
    db, _ = files
    rows = [run("A", NOW - 300, 1)]
    write_copy(db, rows, built=NOW)
    assert get(f"?changed_after=1&known_rows=1&known_built_s={NOW!r}")["resync"] is False
    later = NOW + 60
    write_copy(db, rows + [run("N", NOW + 5, 2, first_seen_at=iso(later))], built=later)
    assert get(f"?changed_after=1&known_rows=1&known_built_s={NOW!r}")["resync"] is False


def test_c1_changed_after_without_what_the_page_knew_resyncs(files):
    db, _ = files
    write_copy(db, [run("A", NOW - 300, 1)])
    assert get("?changed_after=0")["resync"] is True


def test_c1_more_changed_than_one_answer_holds_resyncs(files):
    db, _ = files
    write_copy(db, [run(f"R{i:03d}", NOW - i, i + 1, first_seen_at=iso(NOW)) for i in range(V.LIMIT_MAX + 3)],
               built=NOW)
    d = get(f"?changed_after=0&known_rows=0&known_built_s={NOW - 600!r}")
    assert d["resync"] is True and len(d["runs"]) == V.LIMIT_MAX


# ── the envelope and the summary, from the status file ─────────────────────

def test_c1_status_fresh_stale_missing_unreadable_unknown(files):
    db, st = files
    now = time.time()
    st.write_text(json.dumps(status(now - 5)))
    assert get("?limit=0")["source"]["state"] == "fresh"
    st.write_text(json.dumps(status(now - 181)))
    d = get("?limit=0")
    assert d["source"]["state"] == "stale" and "older than 180 s is stale" in d["source"]["reason"]
    assert d["summary"]["household"]["runs_24h"] == 2               # the last known counts, still given
    st.write_text(json.dumps(status(now - 170)))
    assert get("?limit=0")["source"]["state"] == "fresh"
    st.unlink()
    d = get("?limit=0")
    assert d["source"]["state"] == "missing" and d["summary"] is None
    assert "kronk-voice-runs-export.timer" in d["source"]["reason"]
    st.write_text("{not json")
    assert get("?limit=0")["source"]["state"] == "unreadable"
    st.write_text(json.dumps(status(now, schema="1")))
    d = get("?limit=0")
    assert d["source"]["state"] == "unknown_schema" and d["summary"] is None


def test_c1_ok_true_is_not_the_only_signal(files):
    _, st = files
    st.write_text(json.dumps(status(time.time(), this_run={"truncated_windows": 2, "trace_read_failures": 3,
                                                           "enrich_failures": 1},
                                    last_check={"differing": 4, "missing": 0}, check_progress={"day": 3},
                                    last_error="a whole-trace read failed: timed out",
                                    last_error_at="2026-11-26T06:00:00+00:00")))
    src = get("?limit=0")["source"]
    assert src["ok"] is True and src["last_error"].startswith("a whole-trace read failed")
    assert src["last_error_at"] == "2026-11-26T06:00:00+00:00"
    w = " | ".join(src["warnings"])
    for needle in ("cut short by Tempo's search limit in 2", "3 whole-trace read(s) failed",
                   "1 Kronk-trace enrichment(s) failed", "found 4 row(s) Tempo now gives differently",
                   "hourly check is under way"):
        assert needle in w, needle


def test_c1_a_failed_run_and_a_lost_record(files):
    _, st = files
    st.write_text(json.dumps(status(time.time(), ok=False, last_error="Tempo did not answer",
                                    last_error_at="2026-11-26T06:00:00+00:00", record_lost=True, summary=None)))
    d = get("?limit=0")
    assert d["source"]["ok"] is False and d["summary"] is None
    assert any("the record is gone" in w for w in d["source"]["warnings"])


def test_c1_the_route_against_a_real_scratch_export(files, tmp_path):
    """The exporter's own copy and status file, from a scratch export of
    invented runs against the fake Tempo, read by the route."""
    db, st = files
    sc = M30.Scratch(tmp_path / "exp")
    runs = M30.household(3) + [M30.vrun("R-nothing", M30.T0 + 3000, stages="stt", voice__transcript_chars=0,
                                        voice__error_code="stt-no-text-recognized")]
    rc, log = sc.run(M30.FakeTempo(runs), M30.NOW)
    assert rc == 0, log
    with patch.object(V, "VOICE_RUNS_DB", sc.pub), patch.object(V, "VOICE_RUNS_STATUS", sc.status):
        d = V.voice_runs({"limit": "200"}, now=M30.NOW + 30)
    assert d["source"]["state"] == "fresh" and d["source"]["ok"] is True
    assert d["copy"]["state"] == "ok" and d["copy"]["rows"] == 4 and d["max_seq"] == 4
    assert ids(d) == ["R-nothing", "R3", "R2", "R1"]
    assert d["runs"][0]["heard_state"] == "nothing_heard" and d["runs"][1]["heard_state"] == "text"
    assert d["summary"]["household"]["runs_24h"] == 4
    assert d["runs"][1]["satellite_id"] == KITCHEN and d["runs"][1]["rule"] == "music"


# ═══ C2: /api/work is what it was ═══════════════════════════════════════════

def test_c2_api_work_byte_for_byte_what_it_was(tmp_path):
    """The same work.json gives the same bytes from this tree's /api/work as
    from the route as it stood at BASE, the commit before step C."""
    old_src = subprocess.run(["git", "-C", str(REPO), "show", f"{BASE}:orchestrator/work_api.py"],
                             capture_output=True, text=True, check=True).stdout
    spec = importlib.util.spec_from_loader("work_api_at_base", loader=None)
    old = importlib.util.module_from_spec(spec)
    exec(compile(old_src, "work_api_at_base", "exec"), old.__dict__)
    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(old.router)
    p = tmp_path / "work.json"
    now = 1_800_000_000.0
    for body in (M27._outline(now - 5), M27._outline(now - 400), M27._outline(now, schema=2, x=1)):
        p.write_text(json.dumps(body))
        with patch.object(work_api, "WORK_PATH", p), patch.object(old, "WORK_PATH", p), \
             patch("servers.time.time", return_value=now):
            import orchestrator.main as orch
            new_bytes = TestClient(orch.app).get("/api/work").content
            old_bytes = TestClient(app).get("/api/work").content
        assert new_bytes == old_bytes


def test_c2_work_api_work_export_unchanged_since_base():
    r = subprocess.run(["git", "-C", str(REPO), "diff", "--quiet", BASE, "--", "orchestrator/work_api.py",
                        "scripts/work_export.py"])
    assert r.returncode == 0


# ═══ C3 (what a browser is not needed for): the page's script ═══════════════

def _heard_script() -> str:
    return SCRIPT.split("// ── Heard (work item M30 step C)", 1)[1]


def _function_body(name: str) -> str:
    i = SCRIPT.index(f"function {name}(")
    j = SCRIPT.index("{", i)
    depth = 0
    for k in range(j, len(SCRIPT)):
        depth += {"{": 1, "}": -1}.get(SCRIPT[k], 0)
        if depth == 0:
            return SCRIPT[j:k + 1]
    raise AssertionError(name)


def test_c3_heard_is_the_first_section_and_folded_on_every_load():
    main = PAGE.split("<main>", 1)[1]
    first = main.lstrip().split("\n", 1)[0]
    assert first == '<details class="heard" id="heard">'                 # no open attribute
    assert "heard.box.open = false" in _heard_script()


def test_c3_open_or_closed_is_never_stored_the_filter_and_the_switch_are():
    h = M27._literal(PAGE, "HEARD")
    assert h["store_q"] == "kronk.work.heard.q" and h["store_flagged"] == "kronk.work.heard.flagged"
    # localStorage is touched in one place, store(), wrapped in try/catch.
    code = "\n".join(line for line in SCRIPT.splitlines() if not line.lstrip().startswith("//"))
    assert code.count("localStorage") == 2
    assert "try { if (val === undefined) return localStorage.getItem(key); localStorage.setItem(key, val); } catch" \
        in SCRIPT
    calls = [line for line in SCRIPT.splitlines() if "store(" in line and "function store" not in line]
    assert calls and all("HEARD.store_q" in c or "HEARD.store_flagged" in c for c in calls), calls
    assert "sessionStorage" not in SCRIPT and "indexedDB" not in SCRIPT


def test_c3_built_outside_loadAll_with_its_own_timer():
    body = _function_body("loadAll")
    assert "heard" not in body.lower() and "voice_runs" not in body
    assert "setInterval(heardTick, HEARD.every_ms)" in SCRIPT
    assert M27._literal(PAGE, "HEARD")["every_ms"] == 30000


def test_c3_rows_are_keyed_by_run_id_and_patched_from_changed_after():
    s = _heard_script()
    for needle in ("det.dataset.run = r.run_id", "heard.rows.get(r.run_id)", "changed_after: heard.maxSeq",
                   "known_rows: heard.knownRows", "known_built_s: heard.knownBuilt", "if (resp.resync)",
                   "if (!r.match) { dropRow(r.run_id)", "det.open = wasOpen", "keepAnchor(a)"):
        assert needle in s, needle


def test_c3_text_only_relative_urls_and_no_link_is_built():
    s = _heard_script()
    assert not __import__("re").search(r"\.(innerHTML|outerHTML|insertAdjacentHTML)\b|document\.write", SCRIPT)
    assert M27._literal(PAGE, "HEARD")["url"] == "api/voice_runs"
    assert "fetch(`${HEARD.url}?${params}`, { cache: 'no-store' })" in s
    assert ".href" not in s and "grafana" not in s.lower()                 # the trace id is text to copy
    assert "el('code', null, v)" in s


def test_c3_the_banners_cover_every_state_the_route_gives():
    b = M27._literal(PAGE, "HEARD_BANNER")
    assert set(b) == {"fresh", "stale", "missing", "unreadable", "unknown_schema", "damaged"}
    assert b["fresh"] is None and b["stale"]["strip"] == "stale: "
    assert all(b[k]["bad"] for k in ("missing", "unreadable", "unknown_schema"))
    e = M27._literal(PAGE, "HEARD_ERROR")
    assert "{at}" in e["failed"] and "{at}" in e["earlier"]                 # last_error always with its time


def test_c3_the_words_for_heard_and_the_summary_line():
    h = M27._literal(PAGE, "HEARD")
    assert h["heard_text"] == {"not_recorded": "not recorded", "nothing_heard": "nothing heard"}
    assert "as of {as_of}" in h["summary"]
    for k in ("{runs}", "{newest}", "{wrong}", "{error}"):
        assert k in h["summary"]
    assert h["no_rule"] == "—" and h["rows"] == 200


def test_c3_satellite_names_are_a_three_line_map_of_the_real_ids(files):
    names = M27._literal(PAGE, "SATELLITE_NAME")
    assert len(names) == 3 and KITCHEN in names and OFFICE in names
    assert all(k.startswith("assist_satellite.") and k.endswith("_assist_satellite") for k in names)


def test_c3_the_summary_keys_the_page_reads_are_the_exporters():
    s = _heard_script()
    for k in ("runs_24h", "wrong_24h", "error_24h", "newest_time", "as_of_s"):
        assert k in s, k
    src = (REPO / "scripts" / "voice_runs_export.py").read_text()
    for k in ('"runs_24h"', '"wrong_24h"', '"error_24h"', '"newest_time"', '"as_of_s"'):
        assert k in src, k


# ═══ C4: no model completion, no Tempo request ══════════════════════════════

def test_c4_the_module_imports_no_http_client():
    tree = ast.parse((REPO / "orchestrator" / "voice_runs_api.py").read_text())
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            names.add((node.module or "").split(".")[0])
    assert names <= {"datetime", "os", "sqlite3", "pathlib", "fastapi", "servers"}, names
    assert not {"httpx", "requests", "aiohttp", "urllib", "http", "socket", "llm", "tools", "litellm"} & names


def test_c4_twenty_loads_call_nothing(files):
    db, _ = files
    write_copy(db, [run("A", NOW - 10, 1)])
    import llm
    with patch.object(llm, "LLM_SERVICE_URL", "http://blocked.invalid"):
        for i in range(20):
            get("?limit=0")
            d = get("?limit=200")
            get(f"?changed_after={d['max_seq']}&known_rows=1&known_built_s={NOW!r}")


def test_c4_the_routes_are_registered():
    import orchestrator.main as orch
    paths = {getattr(r, "path", None) for r in orch.app.routes}
    assert {"/work", "/api/work", "/api/voice_runs"} <= paths


# ═══ the conftest guard: the live timer's writes are told from a test's ════

def _journal(lines):
    class R:
        returncode = 0
        stdout = "\n".join(json.dumps(x) for x in lines)
    return R()


def test_the_guard_reads_the_timers_runs_from_its_journal():
    """Fix round 1 of step C changed the shape (the runs carry their counts;
    a status file gone is reported; the record is judged by content, in
    tests/test_m30_heard_e2e6.py); the cases are the same."""
    from tests import conftest as C
    j = [{"__REALTIME_TIMESTAMP": "100000000", "MESSAGE": "Starting kronk-voice-runs-export.service - x"},
         {"__REALTIME_TIMESTAMP": "100500000", "MESSAGE": "voice_runs_export: listed 0"},
         {"__REALTIME_TIMESTAMP": "101000000", "MESSAGE": "Finished kronk-voice-runs-export.service - x"},
         {"__REALTIME_TIMESTAMP": "160000000", "MESSAGE": "Starting kronk-voice-runs-export.service - x"},
         {"__REALTIME_TIMESTAMP": "161000000", "MESSAGE": "kronk-voice-runs-export.service: Failed with result 'exit-code'."},
         {"__REALTIME_TIMESTAMP": "220000000", "MESSAGE": "Starting kronk-voice-runs-export.service - x"}]
    with patch.object(C.subprocess, "run", return_value=_journal(j)):
        runs = C._exporter_runs(150)
    assert [(r["start"], r["end"]) for r in runs] == [(100.0, 101.0), (160.0, 161.0), (220.0, None)]
    p, side = C.LIVE_VOICE_STATUS[0], C.LIVE_VOICE_SIDE[0]
    before = {p: (90 * 10**9, 10, 1), side: (100 * 10**9 + 5 * 10**8, 1, 2), "/same": (1, 1, 1)}
    after = {p: (int(100.9 * 10**9), 11, 3), side: None, "/same": (1, 1, 1)}
    assert C._file_problems(before, after, runs[:2]) == []
    after[p] = (int(130 * 10**9), 12, 4)                                   # between runs: a test did it
    assert C._file_problems(before, after, runs[:2]) == [p]
    # no journal: every change reported
    assert set(C._file_problems(before, after, None)) == {f"{x} (the timer's journal cannot be read)" for x in (p, side)}
