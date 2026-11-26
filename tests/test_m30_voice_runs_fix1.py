"""Work item M30 step B, fix round 1 (docs/sprint/M30-fix1.md): one test or
more per job of the coordinator's ruling of 2026-11-25 that the e2e2 file
(tests/test_m30_voice_runs_e2e2.py) does not already show.

Same rules as tests/test_m30_voice_runs.py: everything in tmp_path, a fake
Tempo at the function boundary, a fake budget clock, no socket, every
sentence invented."""
import json
import os
import random
import re
import sqlite3
import threading
import urllib.error
from pathlib import Path

import pytest

from tests import test_m16_backups as M16
from tests.test_m16_backups import no_network  # noqa: F401  (the fixture)
from tests.test_m30_voice_runs import (DEV_K, EX, LS, MARK, NOW, B, FakeClock, FakeOps, FakeTempo, Scratch,
                                       household, house, real_record, tid_of, vrun, wav)  # noqa: F401

pytestmark = pytest.mark.usefixtures("no_network")

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def sc(tmp_path):
    return Scratch(tmp_path)


def join_answer(sc):
    """voice_label_sheet.join's own answer over every row and recording."""
    rows = sc.rows()
    recs = LS.scan(sc.rec)
    runs = [EX.run_for_join(r) for r in rows.values()]
    LS.join(recs, runs)
    certain = {r.run["ha.pipeline_run_id"]: r.path.relative_to(sc.rec).as_posix() for r in recs if r.run}
    out = {}
    for i, k in enumerate(rows):
        if k in certain:
            out[k] = ("certain", certain[k])
        elif any(r.note.startswith("ambiguous") and i in LS._candidates(r, runs) for r in recs):
            out[k] = ("ambiguous", None)
        else:
            out[k] = ("none", None)
    return out


# ═══ F1: the join when the row is final ════════════════════════════════════

@pytest.mark.parametrize("seed", range(8))
def test_f1_the_stored_join_is_the_joins_answer_in_any_order_of_arrival(sc, seed):
    """Six runs on one puck and pipeline, four recordings, some fitting two
    runs, one run with no recording: reached Tempo in a random order, one
    or two an export. Once all are final, every stored answer is
    voice_label_sheet.join's over all of them."""
    base = NOW - 1500
    runs = [vrun(f"J{i}", base + off, dur=6, heard="invented", kronk__origin__device_id=DEV_K)
            for i, off in enumerate((0, 3, 8, 40, 43, 100))]
    for k, off in enumerate((5, 9, 46, 104)):
        wav(sc.rec, DEV_K, "kronk", f"88{k}", base + off)
    order = runs[:]
    random.Random(seed).shuffle(order)
    tempo = FakeTempo([])
    t = base + 110                         # every run reaches Tempo before it is final, as voice_tracer's do
    while order:
        tempo.runs += [order.pop() for _ in range(min(len(order), random.Random(seed + t).choice((1, 2))))]
        sc.run(tempo, t)
        t += 60
    assert t < base + 6 + EX.FINAL_S
    sc.run(tempo, NOW + 1200)
    got = {k: (r["audio_join"], r["audio_rel"]) for k, r in sc.rows().items()}
    assert got == join_answer(sc)
    assert {"certain", "ambiguous"} <= {v[0] for v in got.values()}       # the fixture has both


def test_f1_a_final_row_waits_while_a_run_that_could_share_its_recording_is_not(sc):
    a = vrun("W1", NOW - 911, dur=6, heard="invented", kronk__origin__device_id=DEV_K)   # ended NOW-905: final
    b = vrun("W2", NOW - 902, dur=6, heard="invented", kronk__origin__device_id=DEV_K)   # ends NOW-896: not yet
    wav(sc.rec, DEV_K, "kronk", "1", NOW - 903)            # fits W1 and W2
    wav(sc.rec, DEV_K, "kronk", "2", NOW - 897)            # fits W2 only
    tempo = FakeTempo([a, b])
    sc.run(tempo, NOW)
    r = sc.rows()["W1"]
    assert r["audio_join"] is None and r["audio_tried_at"] is None
    assert sc.status_json()["this_run"]["audio_waiting"] == 1
    sc.run(tempo, NOW + 60)
    got = {k: (r["audio_join"], r["audio_rel"]) for k, r in sc.rows().items()}
    assert got == join_answer(sc) and {v[0] for v in got.values()} == {"certain"}


def test_f1_a_removed_row_is_never_joined_but_still_competes(sc):
    a = vrun("X1", NOW - 3000, dur=6, heard="invented", kronk__origin__device_id=DEV_K)
    b = vrun("X2", NOW - 2997, dur=6, heard="invented", kronk__origin__device_id=DEV_K)
    wav(sc.rec, DEV_K, "kronk", "1", NOW - 2995)
    tempo = FakeTempo([a])
    sc.run(tempo, NOW - 2900)                              # X1 not final yet
    sc.run(tempo, NOW - 2890, "--remove", "X1")
    tempo.runs.append(b)
    sc.run(tempo, NOW)
    rows = sc.rows()
    assert rows["X1"]["audio_join"] is None and rows["X1"]["audio_tried_at"] is None
    assert rows["X2"]["audio_join"] == "ambiguous"         # X1 still fits the recording


# ═══ F3: a failed trace read goes to the back ══════════════════════════════

def test_f3_a_trace_that_failed_is_tried_after_those_never_tried(sc):
    runs = household(4, start=NOW - 6000, gap=60)
    tempo = FakeTempo(runs)
    real = tempo.__call__
    state = {"fail": {tid_of("R4")}}
    asked = []

    def get(url, timeout=None):
        if "/api/v2/traces/" in url:
            asked.append(url.rsplit("/", 1)[1])
        if "/api/v2/traces/" in url and url.rsplit("/", 1)[1] in state["fail"]:
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
        return real(url, timeout)
    sc.run(get, NOW)
    assert sc.rows()["R4"]["trace_failed_at"] == EX.now_iso(NOW)
    tempo.runs += [vrun("R9", NOW - 1000, heard="invented")]
    asked.clear()
    sc.run(get, NOW + 60)
    assert asked == [tid_of("R9"), tid_of("R4")]           # the new one first, the failed one after


# ═══ F4: the watermark ahead, the check's stamp ahead ══════════════════════

def test_f4_a_watermark_ahead_is_reported_and_mended_in_one_run(sc):
    tempo = FakeTempo(household(1, start=NOW - 1800))
    sc.run(tempo, NOW)
    sc.run(tempo, NOW + 86400)                             # a clock a day ahead
    rc, _ = sc.run(tempo, NOW + 60)
    st = sc.status_json()
    assert rc == 0 and st["ok"] is False and "watermark was" in st["last_error"]
    assert st["this_run"]["read_from_s"] == NOW + 60 - EX.OVERLAP_S
    assert sc.state()["watermark_s"] == pytest.approx(NOW + 60 - EX.FINAL_S)
    sc.run(tempo, NOW + 120)
    assert sc.status_json()["ok"] is True


def test_f4_a_check_stamped_in_the_future_does_not_stop_the_check(sc):
    tempo = FakeTempo(household(1))
    sc.run(tempo, NOW)
    sc.person("UPDATE export_state SET last_check_at = ?", EX.now_iso(NOW + 86400))
    tempo.runs.append(vrun("R-lost", NOW - 7200, heard="invented"))   # outside the next read's window
    sc.person("UPDATE export_state SET watermark_s = ?", NOW + 120 - EX.FINAL_S)
    sc.run(tempo, NOW + 600)
    assert "R-lost" in sc.rows() and sc.state()["last_check_at"] == EX.now_iso(NOW + 600)


# ═══ F5: no record is made but by --init ═══════════════════════════════════

def test_f5_a_first_run_without_init_makes_nothing_and_says_to_run_init(sc):
    tempo = FakeTempo(household(2))
    rc, log = sc.run(tempo, NOW, init=False)
    assert rc == 1 and "--init" in log and not sc.db.exists() and tempo.calls == []
    st = sc.status_json()
    assert st["ok"] is False and st["record_lost"] is False and "--init" in st["last_error"]
    rc, log = sc.run(tempo, NOW + 60, init=False)          # that status file is no evidence of a record
    assert rc == 1 and "no record" in log
    rc, log = sc.run(tempo, NOW + 120, init=True)
    assert rc == 0 and len(sc.rows()) == 2 and sc.status_json()["ok"] is True


def test_f5_a_lost_record_keeps_saying_so_until_restored_or_made_on_purpose(sc):
    tempo = FakeTempo(household(2))
    sc.run(tempo, NOW)
    last_ok = sc.status_json()["last_ok_at"]
    os.unlink(sc.db)
    for k in (1, 2, 3):
        rc, log = sc.run(tempo, NOW + 60 * k)
        st = sc.status_json()
        assert rc == 1 and not sc.db.exists() and "is gone" in log and EX.RESTORE_STEP in st["last_error"]
        assert st["record_lost"] is True and st["last_ok_at"] == last_ok
    sc.status.unlink()
    sc.status_dev.unlink()
    rc, log = sc.run(tempo, NOW + 300, init=False)         # a published copy is evidence enough
    assert rc == 1 and "published copy" in log and not sc.db.exists()
    rc, log = sc.run(tempo, NOW + 360, init=True)          # a new one, on purpose
    assert rc == 0 and len(sc.rows()) == 2


def test_f5_init_on_an_existing_record_is_an_ordinary_run(sc):
    tempo = FakeTempo(household(2))
    sc.run(tempo, NOW)
    before = {k: r["seq"] for k, r in sc.rows().items()}
    rc, log = sc.run(tempo, NOW + 60, init=True)
    assert rc == 0 and "nothing to make" in log and {k: r["seq"] for k, r in sc.rows().items()} == before


# ═══ F6: removal is a command; publishing follows the record ═══════════════

def test_f6_remove_reads_no_tempo_publishes_at_once_and_is_never_undone(sc):
    runs = household(3, start=NOW - 1800, gap=60)
    tempo = FakeTempo(runs)
    sc.run(tempo, NOW)
    sc.person("INSERT INTO labels VALUES ('R2', 'invented correct', 'household', NULL, NULL, 'x', 'drew')")
    tempo.mode = "down"
    tempo.calls.clear()
    rc, log = sc.run(tempo, NOW + 30, "--remove", "R2", "--why", "2026-11-25 drew: a guest")
    assert rc == 0 and tempo.calls == [] and "has a label" in log
    r2 = sc.rows()["R2"]
    assert r2["removed"] == f"{EX.now_iso(NOW + 30)} 2026-11-25 drew: a guest"
    assert (r2["heard"], r2["reply"], r2["raw_trace"]) == (None, None, None)
    for dev in (False, True):
        assert "R2" not in sc.published(dev) and {"R1", "R3"} <= set(sc.published(dev))
    rc, _ = sc.run(tempo, NOW + 60)                        # Tempo down: still not published, not refilled
    tempo.mode = None
    sc.run(tempo, NOW + 120, "--check-now")
    assert sc.rows()["R2"]["heard"] is None and "R2" not in sc.published()
    rc, log = sc.run(tempo, NOW + 180, "--remove", "R-none")
    assert rc == 1 and "no run R-none" in log


def test_f6_the_copies_follow_the_record_while_tempo_is_down(sc):
    tempo = FakeTempo(household(2))
    sc.run(tempo, NOW)
    sc.pub.unlink()                                        # a container damaged it
    tempo.mode = "down"
    rc, log = sc.run(tempo, NOW + 60)
    assert rc == 0 and set(sc.published()) == {"R1", "R2"} and sc.status_json()["ok"] is False


# ═══ F7: the restore step stops the timer around the install ═══════════════

def test_f7_the_restore_step_stops_the_exporter_before_the_install_and_starts_it_after():
    text = (ROOT / "docs" / "runbooks" / "restore.md").read_text()
    step = text[text.index("### 14."):text.index("### 15.")]
    stop = step.index("systemctl --user stop kronk-voice-runs-export.timer kronk-voice-runs-export.service")
    install = step.index("install -m 600 $VOICE_COPY")
    start = step.index("systemctl --user start kronk-voice-runs-export.timer")
    assert stop < install < start


# ═══ F8: the whole-window reads have a budget and carry on ═════════════════

def test_f8_a_slow_tempo_never_runs_past_the_unit_and_the_reads_carry_on(sc, monkeypatch):
    """Every request takes 0.6 s: the first read (92 searches) and the
    hourly check (91) cannot finish in one run. Each run stays inside the
    unit's 50 s, writes its status, and the next carries on."""
    clk = FakeClock()
    monkeypatch.setattr(EX, "clock", clk)
    runs = [vrun(f"S{i:02d}", NOW - 89 * 86400 + i * 3 * 86400, heard="invented") for i in range(30)]
    tempo = FakeTempo(runs)

    def slow(url, timeout=None):
        clk.t += 0.6
        return tempo(url, timeout)
    for k in range(8):
        t0 = clk.t
        rc, log = sc.run(slow, NOW + 60 * k)
        assert rc == 0, log
        assert clk.t - t0 <= EX.CHECK_BUDGET_S + EX.TEMPO_TIMEOUT_S < 50
        assert sc.status_json()["ts"] == NOW + 60 * k
        if k == 0:
            assert "the read stopped" in sc.status_json()["last_error"] and sc.state()["last_check_at"] is None
            assert sc.state()["check_progress"] is not None
    assert len(sc.rows()) == 30 and sc.state()["watermark_s"] == pytest.approx(NOW + 420 - EX.FINAL_S)
    assert sc.state()["check_progress"] is None and sc.status_json()["last_check"]["listed"] == 30
    assert sum(1 for r in sc.rows().values() if r["raw_trace"]) == 30


# ═══ F9: a SQLite error reaches the status ═════════════════════════════════

def test_f9_a_locked_record_is_in_the_status_with_its_reason_and_exit_1(sc, monkeypatch):
    tempo = FakeTempo(household(1, start=NOW - 1800))
    sc.run(tempo, NOW)
    monkeypatch.setattr(EX, "BUSY_TIMEOUT_S", 0.1)
    c = sqlite3.connect(sc.db, isolation_level=None)
    c.execute("BEGIN IMMEDIATE")                           # a person's session holds the record
    try:
        rc, log = sc.run(tempo, NOW + 60)
    finally:
        c.execute("ROLLBACK")
        c.close()
    st = sc.status_json()
    assert rc == 1 and "Traceback" not in log and "database is locked" in log
    assert st["ts"] == NOW + 60 and st["ok"] is False and "database is locked" in st["last_error"]


# ═══ F10: a failed enrichment is said ══════════════════════════════════════

def test_f10_a_failed_enrichment_is_in_last_error_and_the_status(sc):
    k = "f" * 32
    run = vrun("E1", NOW - 600, heard="invented", voice__handled_by="kronk", kronk__trace_id=k)
    rc, _ = sc.run(FakeTempo([run]), NOW)                  # the Kronk trace is not in Tempo: 404
    st = sc.status_json()
    assert rc == 0 and st["this_run"]["enrich_failures"] == 1
    assert "Kronk trace read(s) for the rule failed (TempoError: HTTPError: HTTP Error 404" in st["last_error"]


# ═══ From the notes ════════════════════════════════════════════════════════

def test_every_write_to_the_record_is_inside_begin_immediate(sc, monkeypatch):
    real = sqlite3.connect
    stmts: list[str] = []

    def connect(*a, **kw):
        c = real(*a, **kw)
        if str(a[0]) == str(sc.db):
            c.set_trace_callback(stmts.append)
        return c
    monkeypatch.setattr(EX.sqlite3, "connect", connect)
    runs = household(3, start=NOW - 1800, gap=60)
    tempo = FakeTempo(runs)
    sc.run(tempo, NOW)
    runs[0]["attrs"]["voice.handled_by"] = "ha_local"
    sc.run(tempo, NOW + 60, "--check-now")
    sc.run(tempo, NOW + 90, "--remove", "R3")
    begins = [s for s in stmts if re.match(r"\s*BEGIN", s, re.I)]
    assert begins and all(re.fullmatch(r"\s*BEGIN IMMEDIATE;?\s*", s, re.I) for s in begins), begins
    inside, writes = False, 0
    for s in stmts:
        head = s.strip().split(None, 1)[0].upper().rstrip(";")
        if head == "BEGIN":
            inside = True
        elif head in ("COMMIT", "ROLLBACK"):
            inside = False
        elif head in ("INSERT", "UPDATE", "DELETE", "CREATE"):
            writes += 1
            assert inside, s
    assert writes > 10


def test_the_weekly_copy_is_read_back_and_a_copy_that_differs_fails(house, tmp_path, monkeypatch):
    real_record(house, tmp_path)
    real = B._write_all

    def flipped(fd, b):
        real(fd, b[:-1] + bytes([b[-1] ^ 1]))              # the disk gives back something else
    monkeypatch.setattr(B, "_write_all", flipped)
    vr = B.voice_runs_copy(house, FakeOps())
    assert vr["result"] == "failed" and "read back differs" in vr["detail"]
    assert [p.name for p in (house.dest / "voice-runs").iterdir()] == []


def test_the_weekly_copy_is_by_the_backup_api_while_a_writer_holds_the_record(house, tmp_path):
    """A writer whose transaction has spilled uncommitted pages into the
    file: a plain read of the file would copy them; the backup API waits
    for the lock and copies the committed record."""
    want_runs, want_labels = real_record(house, tmp_path)
    w = sqlite3.connect(house.voice_db, isolation_level=None, check_same_thread=False)
    w.execute("PRAGMA cache_size = 1")
    w.execute("BEGIN IMMEDIATE")
    for i in range(300):
        w.execute("INSERT INTO labels (run_id, correct_text, labelled_at, labelled_by) VALUES (?, ?, 'x', 'w')",
                  (f"U{i}", "UNCOMMITTED-" + "u" * 2000))
    timer = threading.Timer(1.0, lambda: w.execute("ROLLBACK"))
    timer.start()
    try:
        vr = B.voice_runs_copy(house, FakeOps())
    finally:
        timer.join()
        w.close()
    assert vr["result"] == "ok", vr
    blob = Path(vr["path"]).read_bytes()
    assert b"UNCOMMITTED-" not in blob and (vr["runs"], vr["labels"]) == (len(want_runs), len(want_labels))


def test_a_copy_whose_rows_do_not_match_its_meta_is_rebuilt_on_the_next_run(sc):
    tempo = FakeTempo(household(3))
    sc.run(tempo, NOW)
    c = sqlite3.connect(sc.pub)
    c.execute("DELETE FROM runs")
    c.commit()
    c.close()
    assert sc.published() == {}
    rc, _ = sc.run(tempo, NOW + 60)                        # nothing changed in the record
    assert rc == 0 and set(sc.published()) == {"R1", "R2", "R3"}
    assert sc.status_json()["this_run"]["published"] == 1


def test_a_schema_1_record_is_refused_with_its_reason(sc):
    sc.db.parent.mkdir(parents=True, mode=0o700)
    c = sqlite3.connect(sc.db)
    c.executescript("CREATE TABLE runs (run_id TEXT); PRAGMA user_version = 1;")
    c.close()
    os.chmod(sc.db, 0o600)
    rc, log = sc.run(FakeTempo([]), NOW)
    assert rc == 1 and "schema 1" in log and "--init" in log
    assert MARK not in log


def test_f6_the_record_has_secure_delete_on_even_where_sqlite_defaults_it_off(sc, monkeypatch):
    """This host's SQLite is built with SECURE_DELETE; another may not be.
    Every connection the exporter opens on the record turns it on."""
    real = sqlite3.connect

    def default_off(*a, **kw):
        c = real(*a, **kw)
        c.execute("PRAGMA secure_delete = OFF")
        return c
    monkeypatch.setattr(EX.sqlite3, "connect", default_off)
    sc.run(FakeTempo(household(1)), NOW)
    conn = EX.open_record(sc.db)
    try:
        assert conn.execute("PRAGMA secure_delete").fetchone()[0] == 1
    finally:
        conn.close()


def test_note7_an_old_row_the_join_gives_no_recording_is_null_and_a_young_one_none(sc):
    """A recording in the run's time span but from another puck: the join
    pairs it with nothing. Older than seven days that is NULL (its own
    recording may be on the NAS), younger 'none'."""
    other = "a" * 32
    runs = [vrun("O-old", NOW - 8 * 86400, heard="invented", kronk__origin__device_id=DEV_K),
            vrun("O-young", NOW - 2 * 86400, heard="invented", kronk__origin__device_id=DEV_K)]
    for r in runs:
        wav(sc.rec, other, "kronk", f"{int(r['start'])}", r["start"] + 3)
    sc.run(FakeTempo(runs), NOW)
    rows = sc.rows()
    assert rows["O-old"]["audio_join"] is None and rows["O-old"]["audio_tried_at"] is not None
    assert rows["O-young"]["audio_join"] == "none"
