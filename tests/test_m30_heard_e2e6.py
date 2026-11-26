"""M30 step C, findings of the test in docs/sprint/M30-e2e6.md, and fix
round 1 (docs/sprint/M30-fix4.md; the coordinator's ruling on step C's test
in docs/sprint/M30-criteria.md).

Nothing here opens a socket or touches a live file: the guard's functions
are called with made-up snapshots and journal lines, its record reader and
its write fence are pointed at files in tmp_path, and the route reads a copy
built in tmp_path. Every sentence is invented.
"""
import hashlib
import io
import os
import re
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

import voice_runs_api as V
from tests import conftest as C
from tests import test_m27_work_page as M27
from tests import test_m30_heard as H
from tests import test_m30_voice_runs as M30

pytestmark = pytest.mark.usefixtures("no_network")

files = H.files
sc = M30.sc
house = M30.house

RECORD = C.LIVE_VOICE_RECORD


def _snap(runs: dict, removed=0, labels=0, run_changes=0, t=(1000.0, 1000.01)):
    return {"runs": dict(runs), "removed": removed, "labels": labels, "run_changes": run_changes, "t": t}


def _run(start, end, new=0, changed=0, copies=0):
    return {"start": start, "end": end, "new": new, "changed": changed, "copies": copies}


START = {"R1": 1, "R2": 2, "R3": 3}
BEFORE = _snap(START, t=(1000.0, 1000.01))
# Two runs of the live timer inside the session, a minute apart.
QUIET = [_run(1059.8, 1060.0), _run(1119.8, 1120.0)]
END_T = (1200.0, 1200.01)


# ═══ B1: the guard, by content ═════════════════════════════════════════════

def test_e2e6_the_guard_reports_the_record_deleted_during_the_run():
    """B1 of M30-e2e6 (was: stat values before, None after, with the timer's
    runs patched in). The record existed at the start and is gone at the
    end: never excused, whatever the journal says."""
    assert C._record_problems(BEFORE, None, QUIET) == ["the record was deleted"]
    assert C._record_problems(BEFORE, None, None) == ["the record was deleted"]
    assert C._voice_guard_problems({}, {}, BEFORE, None, QUIET) == [f"{RECORD}: the record was deleted"]


def test_e2e6_the_guard_reports_a_write_the_timer_then_wrote_over():
    """B1 of M30-e2e6 (was: an mtime the timer's next run left and a size a
    page larger). A test inserts a row between two runs; the timer's next run
    writes the record too, and its journal line says new 0. The row the
    journal does not account for is reported, whenever the timer ran."""
    after = _snap({**START, "T-test": 4}, t=END_T)
    p = C._record_problems(BEFORE, after, QUIET)
    assert len(p) == 1 and "1 row(s) added" in p[0] and "accounts for new 0, changed 0" in p[0]
    # A journal that says "changed 1" accounts for one seq, not for a new row.
    assert C._record_problems(BEFORE, after, [_run(1059.8, 1060.0, changed=1)])
    # The same row inserted by the timer (its line says new 1) is the timer's.
    assert C._record_problems(BEFORE, after, [_run(1059.8, 1060.0, new=1), _run(1119.8, 1120.0)]) == []


def test_b1_the_timer_alone_passes_new_and_changed_rows_as_its_journal_says():
    runs = [_run(1059.8, 1060.0, new=2, changed=1), _run(1119.8, 1120.0, changed=1)]
    # R2 changed twice (seq 2 -> 5 -> 7, the second change counted once in raised), N1 and N2 new.
    after = _snap({"R1": 1, "R2": 7, "R3": 3, "N1": 4, "N2": 6}, t=END_T)
    assert C._record_problems(BEFORE, after, runs) == []
    # Nothing changed: nothing to account for, even with no journal.
    assert C._record_problems(BEFORE, _snap(START, t=END_T), None) == []


def test_b1_a_row_deleted_is_reported_even_when_the_count_is_made_up_by_a_new_one():
    runs = [_run(1059.8, 1060.0, new=1)]
    after = _snap({"R1": 1, "R3": 3, "N1": 4}, t=END_T)                       # R2 gone, N1 the timer's
    p = C._record_problems(BEFORE, after, runs)
    assert p == ["1 run(s) of the 3 at the start are gone"]


def test_b1_a_seq_raised_or_lowered_by_hand_is_reported():
    raised = _snap({"R1": 1, "R2": 4, "R3": 3}, t=END_T)
    assert C._record_problems(BEFORE, raised, QUIET)                           # no "changed" in between
    assert C._record_problems(BEFORE, raised, [_run(1059.8, 1060.0, changed=1)]) == []
    lowered = _snap({"R1": 1, "R2": 2, "R3": 0}, t=END_T)
    assert C._record_problems(BEFORE, lowered, [_run(1059.8, 1060.0, changed=1)]) == \
        ["1 run(s) have a lower seq than at the start"]
    # A new row with a seq the timer would never give (at or below the start's max).
    odd = _snap({"R1": 1, "R2": 5, "R3": 3, "N1": 2.5}, t=END_T)
    assert C._record_problems(BEFORE, odd, [_run(1059.8, 1060.0, new=1, changed=1)])


def test_b1_a_removal_is_never_the_timers_and_labels_and_run_changes_never_shrink():
    after = _snap(START, removed=1, t=END_T)
    assert C._record_problems(BEFORE, after, QUIET) == \
        ["the removed rows went from 0 to 1 (a removal is never the timer's)"]
    b = _snap(START, labels=2, run_changes=5)
    assert C._record_problems(b, _snap(START, labels=1, run_changes=5, t=END_T), QUIET) == \
        ["labels went from 2 to 1"]
    assert C._record_problems(b, _snap(START, labels=2, run_changes=4, t=END_T), QUIET) == \
        ["run_changes went from 5 to 4"]
    assert C._record_problems(b, _snap(START, labels=3, run_changes=9, t=END_T), QUIET) == []   # growth is fine


def test_b1_the_journal_unread_fails_any_difference_and_a_record_that_appears_fails():
    after = _snap({**START, "N1": 4}, t=END_T)
    p = C._record_problems(BEFORE, after, None)
    assert p and "journal cannot be read" in p[0]
    assert C._record_problems(None, after, QUIET) == \
        ["the record exists now and did not at the start (the timer never makes it)"]
    assert C._record_problems(None, None, None) == []
    e = C._record_problems(BEFORE, {"error": "DatabaseError: file is not a database", "t": END_T}, QUIET)
    assert e and "could not be read at the end" in e[0]


def test_b1_a_run_overlapping_a_read_counts_either_way():
    """The start read sits inside a run: its commit may be before or after
    the read, so the guard accepts the change with it or without it."""
    straddle = _run(999.95, 1000.1, new=1)
    later = _run(1059.8, 1060.0, new=1)
    with_it = _snap({**START, "N1": 4, "N2": 5}, t=END_T)          # the straddling run's row came after the read
    without = _snap({**START, "N2": 4}, t=END_T)                   # its row was in the start read already
    assert C._record_problems(BEFORE, with_it, [straddle, later]) == []
    assert C._record_problems(BEFORE, without, [straddle, later]) == []
    # A run wholly before the start read is not counted.
    assert C._record_problems(BEFORE, with_it, [_run(900.0, 900.2, new=1), later])


def test_b1_the_journal_lines_are_read_with_their_counts():
    j = [{"__REALTIME_TIMESTAMP": "100000000", "MESSAGE": "Starting kronk-voice-runs-export.service - x..."},
         {"__REALTIME_TIMESTAMP": "100500000", "MESSAGE": "voice_runs_export: listed 3, new 2, changed 1 "
                                                          "(replaced 0), traces read 2 (failed 0), copies written 2 of 2"},
         {"__REALTIME_TIMESTAMP": "101000000", "MESSAGE": "Finished kronk-voice-runs-export.service - x."},
         {"__REALTIME_TIMESTAMP": "160000000", "MESSAGE": "Starting kronk-voice-runs-export.service - x..."},
         {"__REALTIME_TIMESTAMP": "160100000", "MESSAGE": "Finished kronk-voice-runs-export.service - x."},
         # journald read the exporter's stdout after systemd's "Finished": still this run's
         {"__REALTIME_TIMESTAMP": "160300000", "MESSAGE": "voice_runs_export: listed 1, new 1, changed 0 "
                                                          "(replaced 0), traces read 1 (failed 0), copies written 0 of 2"},
         {"__REALTIME_TIMESTAMP": "220000000", "MESSAGE": "Starting kronk-voice-runs-export.service - x..."},
         {"__REALTIME_TIMESTAMP": "220100000", "MESSAGE": "voice_runs_export: not read: Tempo: down; the record "
                                                          "is unchanged, copies written 0 of 2"},
         {"__REALTIME_TIMESTAMP": "220200000", "MESSAGE": "kronk-voice-runs-export.service: Failed with result "
                                                          "'exit-code'."},
         {"__REALTIME_TIMESTAMP": "280000000", "MESSAGE": "Starting kronk-voice-runs-export.service - x..."}]

    class R:
        returncode = 0
        stdout = "\n".join(__import__("json").dumps(x) for x in j)
    with patch.object(C.subprocess, "run", return_value=R()):
        runs = C._exporter_runs(150)
    assert [(r["start"], r["end"], r["new"], r["changed"], r["copies"]) for r in runs] == [
        (100.0, 101.0, 2, 1, 2), (160.0, 160.3, 1, 0, 0), (220.0, 220.2, 0, 0, 0), (280.0, None, 0, 0, 0)]
    R.returncode = 1
    with patch.object(C.subprocess, "run", return_value=R()):
        assert C._exporter_runs(150) is None
    with patch.object(C.subprocess, "run", side_effect=OSError("no journalctl")):
        assert C._exporter_runs(150) is None


def test_b1_the_copies_change_only_in_a_run_that_wrote_one_and_status_files_only_in_a_run():
    cp, st, side = C.LIVE_VOICE_COPIES[0], C.LIVE_VOICE_STATUS[0], C.LIVE_VOICE_SIDE[0]
    ns = lambda t: int(t * 10**9)                                            # noqa: E731
    before = {cp: (ns(1000.05), 4096, 7), st: (ns(1000.05), 300, 8), side: (ns(1000.05), 512, 9)}
    runs = [_run(1059.8, 1060.0, copies=0), _run(1119.8, 1120.0, copies=2)]
    ok = {cp: (ns(1119.9), 8192, 11), st: (ns(1059.9), 301, 12), side: None}
    assert C._file_problems(before, ok, runs + [_run(999.9, 1000.1)]) == []          # side file's old mtime in a run
    # A copy written in a run whose line says no copy was written: not the timer's.
    assert C._file_problems(before, {**ok, cp: (ns(1059.9), 8192, 11)}, runs) == [cp, side + " (deleted)"]
    # Between runs, or gone: reported.
    assert C._file_problems(before, {**ok, st: (ns(1090.0), 301, 12)}, runs + [_run(999.9, 1000.1)]) == [st]
    assert C._file_problems(before, {**ok, st: None}, runs + [_run(999.9, 1000.1)]) == [st + " (deleted)"]
    assert C._file_problems(before, {**ok, cp: None}, runs + [_run(999.9, 1000.1)]) == [cp + " (deleted)"]
    assert C._file_problems(before, ok, None) == [f"{p} (the timer's journal cannot be read)" for p in ok]


def test_b1_the_record_is_read_mode_ro_ids_seq_and_counts_only(sc):
    sc.run(M30.FakeTempo(M30.household(3)), M30.NOW)
    with sqlite3.connect(sc.db) as c:
        c.execute("INSERT INTO labels (run_id, correct_text, labelled_at, labelled_by) "
                  "VALUES ('R1', 'invented', 'x', 'test')")
    c.close()
    os.chmod(sc.db, 0o400)                                   # mode=ro: a read-only file reads
    before = hashlib.sha256(sc.db.read_bytes()).hexdigest()
    mtime = sc.db.stat().st_mtime_ns
    s = C._record_snapshot(str(sc.db))
    assert s["runs"] == {"R1": 1, "R2": 2, "R3": 3}
    assert (s["removed"], s["labels"], s["run_changes"]) == (0, 1, 0)
    assert set(s) == {"runs", "removed", "labels", "run_changes", "t"}
    assert hashlib.sha256(sc.db.read_bytes()).hexdigest() == before and sc.db.stat().st_mtime_ns == mtime
    assert sorted(p.name for p in sc.db.parent.iterdir()) == ["voice_runs.db"]          # no -journal left
    assert C._record_snapshot(str(sc.db.parent / "nothing.db")) is None
    bad = sc.db.parent / "bad.db"
    bad.write_bytes(b"not a database" * 300)
    assert "error" in C._record_snapshot(str(bad))


# ═══ B1: the write fence ═══════════════════════════════════════════════════

@pytest.fixture
def fenced(tmp_path, monkeypatch):
    """Paths in tmp_path taken for live ones: a fence that fails to refuse
    writes a scratch file, never a live one. The test calls forget() at the
    end of its body: the hook that fails a test for a refused write runs
    before fixture teardown."""
    live_dir = tmp_path / "kronk-voice"
    live_dir.mkdir(mode=0o700)
    copy = tmp_path / "data" / "voice_runs.db"
    copy.parent.mkdir()
    copy.write_bytes(b"")
    monkeypatch.setattr(C, "_LIVE_VOICE_EXACT", {str(copy.resolve())})
    monkeypatch.setattr(C, "_LIVE_VOICE_UNDER", [str(live_dir.resolve())])
    n = len(C._LIVE_VOICE_TRIED)

    def forget():                                            # this test's own attempts fail no test
        del C._LIVE_VOICE_TRIED[n:]
    yield live_dir, copy, forget
    forget()


def test_b1_the_fence_is_installed_and_covers_the_live_paths():
    assert sqlite3.connect is C._guard_connect and sqlite3.dbapi2.connect is C._guard_connect
    assert open is io.open and getattr(open, "__wrapped__", None) is not None
    for f in ("open", "replace", "rename", "remove", "unlink", "truncate"):
        assert getattr(os, f).__wrapped__ is not None, f
    for p in C.LIVE_VOICE_FILES:
        assert C._is_live_voice(p), p
    assert C._is_live_voice(os.path.join(C.LIVE_VOICE_DIR, "voice_runs.db.lock"))
    assert C._is_live_voice("file:" + C.LIVE_VOICE_RECORD + "?mode=ro")
    assert C._is_live_voice(Path(C.LIVE_VOICE_COPIES[1]))
    assert not C._is_live_voice(os.environ["KRONK_VOICE_DB"]) and not C._is_live_voice(":memory:")
    assert not C._is_live_voice(3)


def test_b1_the_fence_refuses_each_write_of_a_live_path(fenced):
    live_dir, copy, forget = fenced
    rec = live_dir / "voice_runs.db"
    refused = [
        lambda: sqlite3.connect(rec),
        lambda: sqlite3.connect(f"file:{copy}?mode=ro", uri=True),
        lambda: open(copy, "wb"),
        lambda: open(copy, mode="r+b"),
        lambda: copy.write_text("x"),
        lambda: rec.touch(),
        lambda: os.open(rec, os.O_WRONLY | os.O_CREAT, 0o600),
        lambda: os.replace(live_dir.parent / "elsewhere", copy),
        lambda: os.rename(copy, live_dir.parent / "elsewhere"),
        lambda: os.unlink(copy),
        lambda: copy.unlink(),
        lambda: os.truncate(copy, 0),
    ]
    for i, f in enumerate(refused):
        with pytest.raises(C.LiveVoiceFileBlocked):
            f()
        assert not rec.exists(), i
        assert copy.exists() and copy.read_bytes() == b"", i            # a read is not refused
    # Elsewhere, and reads of a live path, go through.
    other = live_dir.parent / "other.db"
    sqlite3.connect(other).close()
    with open(copy, "rb") as f:
        assert f.read() == b""
    os.close(os.open(copy, os.O_RDONLY))
    assert len(C._LIVE_VOICE_TRIED) >= len(refused)
    forget()


def test_b1_a_refused_write_fails_its_test_even_when_the_code_caught_it(fenced):
    _, copy, forget = fenced
    n = len(C._LIVE_VOICE_TRIED)
    try:
        copy.write_bytes(b"x")
    except Exception:                                        # code under test that swallows everything
        pass
    with pytest.raises(C.LiveVoiceFileBlocked, match="voice_runs.db"):
        C._fail_if_tried(n)
    C._fail_if_tried(len(C._LIVE_VOICE_TRIED))               # nothing new: passes
    forget()


def test_b1_the_exporter_in_process_against_a_fenced_record_writes_nothing(fenced, tmp_path):
    live_dir, copy, forget = fenced
    out = io.StringIO()
    args = ["--db", str(live_dir / "voice_runs.db"), "--publish", str(copy), "--status",
            str(tmp_path / "data" / "voice_runs.json"), "--init"]
    with pytest.raises(C.LiveVoiceFileBlocked):
        M30.EX.main(args, now=M30.NOW, get_json=M30.FakeTempo(M30.household(1)), out=out)
    assert sorted(p.name for p in live_dir.iterdir()) == [] and copy.read_bytes() == b""
    forget()


# ═══ S1: numbers out of range are a 400 ════════════════════════════════════

@pytest.mark.parametrize("params", [
    "?changed_after=99999999999999999999999&known_rows=1&known_built_s=1",
    "?changed_after=0&known_rows=1&known_built_s=1e18",
    "?changed_after=0&known_rows=1&known_built_s=-1e18",
])
def test_e2e6_a_number_out_of_range_is_a_400_not_a_500(files, params):
    """S1 of M30-e2e6: a parameter SQLite or datetime cannot hold is a bad
    parameter, answered 400 with the reason like every other."""
    db, _ = files
    H.write_copy(db, [H.run("R1", H.NOW - 100, 1)])
    from fastapi.testclient import TestClient
    import orchestrator.main as orch
    r = TestClient(orch.app, raise_server_exceptions=False).get(f"/api/voice_runs{params}")
    assert r.status_code == 400, r.status_code


@pytest.mark.parametrize("params,name", [
    ("?changed_after=9223372036854775808", "changed_after"),
    ("?changed_after=0&known_rows=9223372036854775808&known_built_s=1", "known_rows"),
    ("?changed_after=0&known_rows=1&known_built_s=253402300800", "known_built_s"),
    ("?changed_after=0&known_rows=1&known_built_s=-0.5", "known_built_s"),
])
def test_s1_the_400_names_the_parameter_and_the_edges_still_answer(files, params, name):
    db, _ = files
    H.write_copy(db, [H.run("R1", H.NOW - 100, 1)])
    from fastapi.testclient import TestClient
    import orchestrator.main as orch
    c = TestClient(orch.app, raise_server_exceptions=False)
    r = c.get(f"/api/voice_runs{params}")
    assert r.status_code == 400 and r.json()["error"].startswith(name), r.text
    for ok in ("?changed_after=9223372036854775807", "?changed_after=0&known_rows=1&known_built_s=253402300799",
               "?changed_after=0&known_rows=1&known_built_s=0"):
        assert c.get(f"/api/voice_runs{ok}").status_code == 200, ok


# ═══ S3: a required database that holds no table is INCOMPLETE ═════════════

def test_s3_a_zero_byte_record_backs_up_as_incomplete(house):
    M30.M16.no_voice_record(house)
    house.voice_db.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    house.voice_db.write_bytes(b"")
    os.chmod(house.voice_db, 0o600)
    ops = M30.FakeOps()
    assert M30.B.cmd_config(house, ops, dry_run=False) == 4
    title, body = ops.notices[-1]
    assert title == "Kronk backup: config backup INCOMPLETE"
    assert "voice_runs.db: sqlite: holds no table (0 bytes)" in body


def test_s3_an_optional_database_with_no_table_is_still_backed_up(house, tmp_path, monkeypatch):
    """Only a required member must hold a table: work.db is not required,
    and an empty one goes in as before."""
    work = tmp_path / "work-empty.db"
    work.write_bytes(b"")
    monkeypatch.setenv("KRONK_WORK_DB", str(work))
    c = M30.B.collect(house, M30.FakeOps())
    assert any(m.arc == "work-state/work.db" for m in c.members)
    assert not any(src == str(work) for src, _ in c.unreadable)


# ═══ S2, notes 1 and 3: the page's script (no JavaScript engine here) ══════

SCRIPT = H.SCRIPT


def _fn(name: str) -> str:
    return H._function_body(name)


def test_s2_a_resync_loads_as_many_rows_as_were_loaded_in_pages_of_500_at_most():
    h = M27._literal(H.PAGE, "HEARD")
    assert h["page_max"] == V.LIMIT_MAX == 500 and h["rows"] == 200
    patch_ = _fn("heardPatch")
    assert "if (resp.resync) { await heardLoad(heard.rows.size); return; }" in patch_
    load = _fn("heardLoad")
    assert "want = Math.max(HEARD.rows, want || 0);" in load
    assert "limit: Math.min(HEARD.page_max, want - runs.length), before" in load
    assert re.search(r"\} while \(before && runs\.length < want\);", load)
    assert "heard.nextBefore = before;" in load                     # "show older" goes on from the last page
    # Open rows and the place are kept by run id across the reload.
    assert "const open = new Set(" in load and "const a = anchor();" in load and "keepAnchor(a);" in load
    assert load.index("const a = anchor();") < load.index("clear(heard.list);") < load.index("keepAnchor(a);")
    # The first opening and a filter change still load the first 200.
    assert "heardLoad()" in _fn("heardTick")


def test_s2_the_place_is_kept_by_run_id_falling_back_to_the_next_row_still_there():
    a, k = _fn("anchor"), _fn("keepAnchor")
    assert "out.push({ run: det.dataset.run, top });" in a and "HEARD.anchor_rows" in a
    assert "for (const { run, top } of a || [])" in k and "heard.rows.get(run)" in k
    assert "det.isConnected" in k and "return;" in k


def test_note1_no_anchor_while_the_sections_head_is_in_view_and_rows_in_page_order():
    a = _fn("anchor")
    assert "heard.box.querySelector(':scope > summary')" in a
    assert "if (head && head.getBoundingClientRect().top >= 0) return null;" in a
    assert "heard.list.querySelectorAll('details.heard-row')" in a     # page order, not the Map's
    assert "heard.rows.values()" not in a
    assert '<details class="heard" id="heard">\n    <summary>' in H.PAGE


def test_note3_the_satellite_map_is_looked_up_as_own_keys_only():
    assert "Object.hasOwn(SATELLITE_NAME, id)" in SCRIPT
    uses = re.findall(r"SATELLITE_NAME\[[^\]]*\]", SCRIPT)
    assert uses == ["SATELLITE_NAME[id]"], uses
    assert "satelliteName(r.satellite_id)" in _fn("fillRow")
