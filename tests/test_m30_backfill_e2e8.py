"""Work item M30 step D, the test agent's probes of the backfill
(docs/sprint/M30-e2e8.md). Everything in tmp_path, invented sentences only,
no journalctl, no socket."""
import json
import os
import signal
import sqlite3
import subprocess
import sys
import textwrap

import pytest

from tests import test_m30_backfill as D
from tests import test_m30_voice_runs as M30

pytestmark = pytest.mark.usefixtures("no_network")
sc = M30.sc

BF, EX, LS = D.BF, D.EX, D.LS
HH, DEVU, TB, GAP, NOW = D.HH, D.DEVU, D.TB, D.GAP, D.NOW
LIVING = "assist_satellite.home_assistant_voice_0bbbbb_assist_satellite"
DEV_L = "f" * 32


def other_puck(rid, start, chars, **kw):
    kw = {"ha__satellite_id": LIVING, "kronk__origin__device_id": DEV_L, "voice__handled_by": "kronk",
          **({} if chars is None else {"voice__transcript_chars": chars}), **kw}
    return M30.vrun(rid, start, **kw)


def fills(log):
    return sorted(ln.split()[2] for ln in log.splitlines() if ln.startswith("voice_runs_backfill: fill "))


# ═══ One transcript, two runs ═══════════════════════════════════════════════

def test_a_run_with_no_count_does_not_take_the_transcript_of_a_run_seconds_later(sc, tmp_path):
    """Two pucks, three seconds apart, one Whisper unit. A has no stored
    count and no transcript of its own in the journal; B's is the only line
    within a minute. B's words belong to B alone."""
    j = D.Journal(tmp_path / "w.json")
    b_text = j.say(HH, TB + GAP + 3 + D.STT_END + 0.05, D.said(2))
    runs = [D.early("R-A", 1, None), other_puck("R-B", TB + GAP + 3, len(b_text))]
    sc.run(M30.FakeTempo(runs), NOW)
    rc, log = D.bf(sc, j.write(), "--apply", "--no-audio")
    assert rc == 0, log
    rows = sc.rows()
    assert rows["R-B"]["heard"] == b_text.strip()
    assert rows["R-A"]["heard"] is None, "R-A holds R-B's words"


def test_two_empty_rows_that_match_one_line_are_neither_certain(sc, tmp_path):
    """Both rows have the same count; the journal holds one line of that
    length in both windows (the other was lost). Neither is certain."""
    j = D.Journal(tmp_path / "w.json")
    # Half a second apart, so the line is inside both windows (fix round 1 of step D: the window
    # is a second either side of the STT span's end; at three seconds apart it is B's alone).
    text = j.say(HH, TB + GAP + 0.5 + D.STT_END + 0.05, D.said(2))
    runs = [D.early("R-A", 1, len(text)), other_puck("R-B", TB + GAP + 0.5, len(text))]
    sc.run(M30.FakeTempo(runs), NOW)
    rc, log = D.bf(sc, j.write(), "--apply", "--no-audio")
    assert rc == 0, log
    assert fills(log) == [], log


def test_a_line_that_is_the_transcript_of_a_row_with_words_is_not_given_to_its_neighbour(sc, tmp_path):
    """B has Tempo's words and the journal agrees with it. A, three seconds
    earlier, empty, the same count, has nothing of its own in the journal."""
    j = D.Journal(tmp_path / "w.json")
    text = j.say(HH, TB + GAP + 3 + D.STT_END + 0.05, D.said(2))
    runs = [D.early("R-A", 1, len(text)),
            other_puck("R-B", TB + GAP + 3, None, heard=text.strip(), voice__transcript_chars=len(text))]
    sc.run(M30.FakeTempo(runs), NOW)
    rc, log = D.bf(sc, j.write(), "--apply", "--no-audio")
    assert rc == 0, log
    assert "the journal agrees" in D.lines_for(log, "R-B")[0]
    assert sc.rows()["R-A"]["heard"] is None, "R-A holds R-B's words"


def test_a_gap_in_the_middle_of_the_journal_does_not_make_a_neighbours_line_the_only_one(sc, tmp_path):
    """The journal has entries before and after, so it 'covers' the run,
    but nothing between TB+GAP-5 and TB+GAP+4 (rotated away, or the unit was
    restarting). A's own line was in the gap; the next line, 6 s after A's
    STT span began, is another run's that Tempo never held."""
    j = D.Journal(tmp_path / "w.json")
    stray = j.say(HH, TB + GAP + D.STT_END + 4.9, D.said(9))
    sc.run(M30.FakeTempo([D.early("R-A", 1, None)]), NOW)
    rc, log = D.bf(sc, j.write(), "--apply", "--no-audio")
    assert rc == 0, log
    assert sc.rows()["R-A"]["heard"] != stray.strip()


# ═══ The length rule ════════════════════════════════════════════════════════

@pytest.mark.parametrize("text", [" café déjà vu", " play 🎵 now", " café au lait", " 日本語のテスト"],
                         ids=["latin1", "emoji", "combining", "cjk"])
@pytest.mark.parametrize("as_bytes", [False, True], ids=["string", "byte-array"])
def test_the_length_is_code_points_as_voice_tracer_counts_it(sc, tmp_path, text, as_bytes):
    """voice.transcript_chars is Python's len of Whisper's text. The journal
    line's length must be counted the same way, whether journalctl gave the
    MESSAGE as a string or as an array of bytes."""
    j = D.Journal(tmp_path / "w.json")
    full = text + f" {D.MARK}"
    j.say(HH, D.at(1), full)
    if as_bytes:
        j.entries[-1]["MESSAGE"] = list((D.LOGGER + full).encode())
    sc.run(M30.FakeTempo([D.early("R-u", 1, len(full)), D.early("R-bytes", 2, len(full.encode()))]), NOW)
    j.say(HH, D.at(2), full)
    rc, log = D.bf(sc, j.write(), "--apply", "--no-audio")
    assert rc == 0, log
    rows = sc.rows()
    assert rows["R-u"]["heard"] == full.strip()
    assert rows["R-bytes"]["heard"] is None and "length differs" in D.lines_for(log, "R-bytes")[0]
    assert D.MARK not in log and "café" not in log


def test_lines_that_look_like_transcripts_but_are_not(sc, tmp_path):
    j = D.Journal(tmp_path / "w.json")
    t = D.at(1)
    for k, msg in enumerate([
            "INFO:wyoming_faster_whisper.dispatch_handler",                 # no colon after the logger
            "DEBUG:wyoming_faster_whisper.dispatch_handler: debug words",
            "WARNING:wyoming_faster_whisper.dispatch_handler: warned words",
            "INFO:wyoming_faster_whisper.sherpa_handler:Downloading x",
            "INFO:wyoming_faster_whisper.__main__: main words",
            " INFO:wyoming_faster_whisper.dispatch_handler: indented words",
            "x INFO:wyoming_faster_whisper.dispatch_handler: prefixed words",
            "INFO:faster_whisper:Processing audio with duration 00:02.000"]):
        j.entries.append(D.entry(HH, t + 0.01 * k, msg))
    # The same line from another unit, and from systemd about the unit.
    j.entries.append(D.entry("wyoming-piper.service", t, D.LOGGER + D.said(5)))
    j.entries.append(D.entry(HH, t, D.LOGGER + D.said(6), own=False))
    # A stored count (fix round 1 of step D: a row with none is left before the window is read).
    sc.run(M30.FakeTempo([D.early("R-1", 1, len(D.said(5)))]), NOW)
    rc, log = D.bf(sc, j.write(), "--apply", "--no-audio")
    assert rc == 0, log
    assert sc.rows()["R-1"]["heard"] is None
    assert "no transcript in the window" in D.lines_for(log, "R-1")[0]


def test_a_transcript_split_over_two_journal_lines_is_not_filled_when_the_count_is_stored(sc, tmp_path):
    """A text with a newline reaches the journal as two entries; only the
    first carries the logger's name. With a stored count the half is refused."""
    j = D.Journal(tmp_path / "w.json")
    whole = D.said(1) + "\n second line " + D.MARK
    j.say(HH, D.at(1), D.said(1))
    j.entries.append(D.entry(HH, D.at(1), " second line " + D.MARK))
    sc.run(M30.FakeTempo([D.early("R-1", 1, len(whole))]), NOW)
    rc, log = D.bf(sc, j.write(), "--apply", "--no-audio")
    assert rc == 0 and sc.rows()["R-1"]["heard"] is None and "length differs" in log


# ═══ Windows ════════════════════════════════════════════════════════════════

def test_dev_and_household_seconds_apart_each_take_their_own_unit(sc, tmp_path):
    j = D.Journal(tmp_path / "w.json")
    h = j.say(HH, D.at(1), D.said(1))
    d = j.say(DEVU, D.at(1) + 1, D.said(11) + " dev")
    runs = [D.early("R-h", 1, len(h)),
            M30.vrun("R-d", TB + GAP + 1, service=M30.DEV, pipeline="kronk-dev", stages="wake_word,stt,intent,tts",
                     voice__transcript_chars=len(d))]
    sc.run(M30.FakeTempo(runs), NOW)
    rc, log = D.bf(sc, j.write(), "--apply", "--no-audio")
    rows = sc.rows()
    assert (rows["R-h"]["heard"], rows["R-d"]["heard"]) == (h.strip(), d.strip())


@pytest.mark.parametrize("raw", ["[]", "null", "7", '"x"', "{", '{"resourceSpans": 5}',
                                 '{"resourceSpans":[{"scopeSpans":[{"spans":[{"name":"voice.stt"}]}]}]}',
                                 '{"resourceSpans":[{"scopeSpans":[{"spans":[{"name":"voice.stt",'
                                 '"startTimeUnixNano":"x","endTimeUnixNano":null}]}]}]}'])
def test_a_raw_trace_that_is_not_a_trace_gives_no_window(raw):
    """Fix round 1 of step D: no whole-run window; such a row is never filled."""
    assert BF.stt_window({"start_s": 100.0, "end_s": 110.0, "raw_trace": raw}) is None


# ═══ The second writer ══════════════════════════════════════════════════════

def many(n):
    return [D.early(f"R-{i:03d}", i, len(D.said(i))) for i in range(1, n + 1)]


def journal_for(tmp_path, n):
    j = D.Journal(tmp_path / "w.json", start=TB - 3600)
    for i in range(1, n + 1):
        j.say(HH, D.at(i), D.said(i))
    return j.write()


def test_seq_is_raised_from_the_maximum_with_no_gap_and_no_reuse_and_the_write_is_one_immediate_transaction(
        sc, tmp_path):
    n = 12
    sc.run(M30.FakeTempo(many(n) + [D.early("R-left", 40, 33)]), NOW)
    before = sc.rows()
    top = max(r["seq"] for r in before.values())
    stmts = []
    real_open = EX.open_record

    def traced(db, create=False):
        conn = real_open(db, create)
        conn.set_trace_callback(stmts.append)
        return conn
    BF.ve.open_record, keep = traced, BF.ve.open_record
    try:
        rc, log = D.bf(sc, journal_for(tmp_path, n), "--apply", "--no-audio")
    finally:
        BF.ve.open_record = keep
    assert rc == 0, log
    after = sc.rows()
    new = sorted(after[f"R-{i:03d}"]["seq"] for i in range(1, n + 1))
    assert new == list(range(top + 1, top + n + 1))
    assert after["R-left"] == before["R-left"]
    seqs = [r["seq"] for r in after.values()]
    assert len(set(seqs)) == len(seqs)
    tx = [s.split()[0].upper() + (" " + s.split()[1].upper() if s.upper().startswith("BEGIN") else "")
          for s in stmts if s.split()[0].upper() in ("BEGIN", "COMMIT", "ROLLBACK")]
    assert tx == ["BEGIN IMMEDIATE", "COMMIT"], tx
    writes = [s for s in stmts if s.lstrip().upper().startswith(("UPDATE", "INSERT", "DELETE", "REPLACE"))]
    assert len(writes) == n and all(s.startswith("UPDATE runs SET heard = ") for s in writes)
    i_begin = next(k for k, s in enumerate(stmts) if s.upper().startswith("BEGIN"))
    assert not any("FROM runs" in s for s in stmts[:i_begin]), "the plan is read inside the transaction"


def test_only_heard_source_seq_and_last_changed_at_change_on_a_filled_row_and_no_other_table(sc, tmp_path):
    n = 5
    sc.run(M30.FakeTempo(many(n)), NOW)
    con = sc.con()
    other = {t: con.execute(f"SELECT * FROM {t} ORDER BY 1").fetchall() for t in EX.TABLES if t != "runs"}
    con.close()
    before = sc.rows()
    rc, log = D.bf(sc, journal_for(tmp_path, n), "--apply", "--no-audio")
    assert rc == 0, log
    after = sc.rows()
    for rid, r in after.items():
        diff = {c for c in r.keys() if r[c] != before[rid][c]}
        assert diff == {"heard", "heard_source", "seq", "last_changed_at"}, (rid, diff)
    con = sc.con()
    assert {t: con.execute(f"SELECT * FROM {t} ORDER BY 1").fetchall() for t in EX.TABLES if t != "runs"} == other
    assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    con.close()


KILL = textwrap.dedent("""
    import os, signal, sqlite3, sys
    sys.path.insert(0, {scripts!r})
    import voice_runs_backfill as bf
    real = bf.ve.open_record
    class Conn(sqlite3.Connection):
        n = 0
        def execute(self, sql, *a):
            if sql.startswith("UPDATE runs SET heard"):
                Conn.n += 1
                if Conn.n == {after}:
                    os.kill(os.getpid(), signal.SIGKILL)
            return super().execute(sql, *a)
    def opened(db, create=False):
        c = real(db, create)
        c.close()
        c = sqlite3.connect(str(db), timeout=5, isolation_level=None, factory=Conn)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA secure_delete = ON")
        c.execute("PRAGMA cache_size = 1")          # spill early, so the kill finds pages on disk
        return c
    bf.ve.open_record = opened
    sys.exit(bf.main(["--db", {db!r}, "--journal", {journal!r}, "--no-audio", "--apply"], now={now}))
""")


def test_a_kill_between_rows_leaves_the_record_whole_and_the_next_apply_finishes(sc, tmp_path, monkeypatch):
    n = 400
    # Every row's trace read in the one export (fix round 1 of step D: a row with no voice.stt
    # span is never filled, and the exporter reads 300 traces a run).
    monkeypatch.setattr(EX, "TRACE_READS_MAX", n)
    sc.run(M30.FakeTempo(many(n)), NOW)
    before = sc.rows()
    journal = journal_for(tmp_path, n)
    script = tmp_path / "kill.py"
    script.write_text(KILL.format(scripts=str(M30.SCRIPTS), after=250, db=str(sc.db), journal=str(journal),
                                  now=NOW + 60))
    p = subprocess.run([sys.executable, str(script)], capture_output=True, text=True, timeout=120)
    assert p.returncode == -signal.SIGKILL, (p.returncode, p.stderr[-400:])
    hot = sc.db.with_name(sc.db.name + "-journal").exists()
    # A dry run straight after the kill: what does a read-only opener make of a hot journal?
    rc_dry, log_dry = D.bf(sc, journal, "--no-audio")
    # The exporter (or any writer) opens next: the hot journal is rolled back.
    con = sqlite3.connect(str(sc.db))
    assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    con.close()
    assert sc.rows() == before, "a killed apply left part of its writes"
    assert not sc.db.with_name(sc.db.name + "-journal").exists()
    rc, log = D.bf(sc, journal, "--apply", "--no-audio")
    assert rc == 0 and f"filled {n};" in log
    assert sorted(r["seq"] for r in sc.rows().values()) == list(range(n + 1, 2 * n + 1))
    print(f"hot journal after the kill: {hot}; dry run on it: rc {rc_dry}: {log_dry.splitlines()[-1][:160]}")


def test_apply_takes_the_lock_before_it_opens_the_record_and_holds_it_to_the_end(sc, tmp_path, monkeypatch):
    import fcntl
    sc.run(M30.FakeTempo(many(2)), NOW)
    seen = []

    def held():
        fd = os.open(sc.db.parent, os.O_RDONLY)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(fd, fcntl.LOCK_UN)
            return False
        except BlockingIOError:
            return True
        finally:
            os.close(fd)
    real_open, real_apply = EX.open_record, BF.apply
    monkeypatch.setattr(BF.ve, "open_record", lambda db, create=False: (seen.append(("open", held())), real_open(db, create))[1])
    monkeypatch.setattr(BF, "apply", lambda *a: (seen.append(("apply", held())), real_apply(*a))[1])
    rc, log = D.bf(sc, journal_for(tmp_path, 2), "--apply", "--no-audio")
    assert rc == 0 and seen == [("open", True), ("apply", True)]
    assert not held()


# ═══ D4 on the error paths ══════════════════════════════════════════════════

def test_no_words_reach_the_output_when_the_script_dies_in_the_middle(sc, tmp_path):
    """A traceback from inside the pass (a broken row, a full disk) must not
    carry a transcript or a row."""
    n = 3
    sc.run(M30.FakeTempo(many(n)), NOW)
    journal = journal_for(tmp_path, n)
    code = textwrap.dedent(f"""
        import sqlite3, sys
        sys.path.insert(0, {str(M30.SCRIPTS)!r})
        import voice_runs_backfill as bf
        how = sys.argv[1]
        if how == "decide":
            real = bf.decide
            def boom(row, j):
                out = real(row, j)
                raise RuntimeError("boom")
            bf.decide = boom
        elif how == "sqlite":
            def boom(conn, p, now):
                raise sqlite3.OperationalError("database or disk is full")
            bf.apply = boom
        elif how == "window":
            bf.stt_window = lambda row: (None, None, "x")
        sys.exit(bf.main(["--db", {str(sc.db)!r}, "--journal", {str(journal)!r}, "--no-audio", *sys.argv[2:]]))
    """)
    (tmp_path / "die.py").write_text(code)
    for how in ("decide", "sqlite", "window"):
        for extra in ([], ["--apply"]):
            p = subprocess.run([sys.executable, str(tmp_path / "die.py"), how, *extra], capture_output=True,
                               text=True, timeout=60)
            both = p.stdout + p.stderr
            assert D.MARK not in both and "Invented sentence" not in both, (how, extra, both[-600:])
            assert p.returncode != 0 or how == "sqlite" and not extra
    # And a journal file whose lines are not JSON: counted, not echoed.
    bad = tmp_path / "bad.json"
    bad.write_text(journal.read_text() + "{broken " + D.said(77) + "\n" + json.dumps([D.said(78)]) + "\n")
    rc, log = D.bf(sc, bad, "--no-audio")
    assert rc == 0 and D.MARK not in log and "2 line(s) not read" in log


# ═══ Plumbing 5 ═════════════════════════════════════════════════════════════

def not_tried(sc):
    return {rid for rid, r in sc.rows().items() if r["audio_join"] is None and r["audio_tried_at"] is not None}


@pytest.mark.parametrize("seed", range(6))
def test_the_nas_join_is_voice_label_sheets_join_over_the_archive_for_every_target(sc, tmp_path, seed):
    """Random crowded runs on two pucks and one device-less pipeline, random
    recordings in the archive's layout. For each row stored not tried the
    backfill stores what voice_label_sheet.join says over every run and
    every archived recording, and nothing else in the record moves."""
    import random
    rnd = random.Random(seed)
    nas = tmp_path / "nas" / "recordings"
    nas.mkdir(parents=True)
    runs, t = [], TB
    for i in range(40):
        t += rnd.choice([1.0, 3.0, 6.0, 9.0, 40.0, 600.0])
        mk = rnd.choice([D.early, other_puck])
        runs.append(D.early(f"R-{i:02d}", 0, 30, dur=rnd.choice([3.0, 5.0, 12.0])) if mk is D.early
                    else other_puck(f"R-{i:02d}", t, 30, dur=rnd.choice([3.0, 5.0, 12.0])))
        runs[-1]["start"] = t
        if rnd.random() < 0.75:
            dev = rnd.choice([M30.DEV_K, DEV_L, runs[-1]["attrs"]["kronk.origin.device_id"]])
            D.nas_wav(nas, dev, "kronk", f"12036888{i:08d}", t + rnd.choice([-1.5, 1.0, 2.5, 4.0, 8.0, 11.0]))
    sc.run(M30.FakeTempo(runs), NOW)
    before = sc.rows()
    targets = not_tried(sc)
    assert targets == set(before)
    rc, log = D.bf(sc, D.Journal(tmp_path / "w.json").write(), "--apply", "--nas-recordings", str(nas))
    assert rc == 0, log
    after = sc.rows()
    recs = LS.scan(nas)
    js = [EX.run_for_join(r) for r in before.values()]
    LS.join(recs, js)
    want = {}
    for k, run in enumerate(js):
        got = [r for r in recs if r.run is run]
        if len(got) == 1:
            p = got[0].path
            parts = p.relative_to(nas).parts
            want[run["ha.pipeline_run_id"]] = ("certain", "/".join(parts[:2] + parts[3:]), p.stat().st_size,
                                               p.stat().st_mtime_ns)
        elif any(r.note.startswith("ambiguous") and k in LS._candidates(r, js) for r in recs):
            want[run["ha.pipeline_run_id"]] = ("ambiguous", None, None, None)
    got = {rid: (r["audio_join"], r["audio_rel"], r["audio_bytes"], r["audio_mtime_ns"])
           for rid, r in after.items() if r["audio_join"] is not None}
    assert got == want
    assert len({v[1] for v in got.values() if v[1]}) == sum(1 for v in got.values() if v[1]), "one recording, two rows"
    audio = {"audio_join", "audio_rel", "audio_bytes", "audio_mtime_ns", "audio_tried_at"}
    for rid, r in after.items():
        diff = {c for c in r.keys() if r[c] != before[rid][c]}
        assert diff <= audio and (bool(diff) == (rid in want)), (rid, diff)


def test_a_set_the_archives_cut_split_is_left_whole(sc, tmp_path):
    """Two runs four seconds apart on one puck; one recording archived, the
    other still local. Joined against the archive alone the first would read
    certain; the pair is left."""
    nas = tmp_path / "nas" / "recordings"
    nas.mkdir(parents=True)
    runs = [D.early("R-1", 1, 30), D.early("R-2", 1, 30)]
    runs[1]["start"] += 4.0
    sc.run(M30.FakeTempo(runs), NOW)
    assert not_tried(sc) == {"R-1", "R-2"}
    D.nas_wav(nas, M30.DEV_K, "kronk", "1203688800000001", TB + GAP + 2.5)
    M30.wav(sc.rec, M30.DEV_K, "kronk", "1203688800000002", TB + GAP + 6.5)
    before = sc.rows()
    rc, log = D.bf(sc, D.Journal(tmp_path / "w.json").write(), "--apply", "--nas-recordings", str(nas))
    assert rc == 0, log
    assert sc.rows() == before and log.count("still under") == 2


def test_a_recording_another_row_already_holds_is_not_given_to_a_target_as_certain(sc, tmp_path):
    """U was joined 'certain' to its recording while it was local; the
    recording is now in the archive. T, in the same window, is stored not
    tried. T must not come out certain with U's recording."""
    nas = tmp_path / "nas" / "recordings"
    nas.mkdir(parents=True)
    runs = [D.early("R-T", 1, 30), D.early("R-U", 1, 30)]
    runs[1]["start"] += 4.0
    sc.run(M30.FakeTempo(runs), NOW)
    p = D.nas_wav(nas, M30.DEV_K, "kronk", "1203688800000001", TB + GAP + 6.5)
    rel = f"{M30.DEV_K}/kronk/1203688800000001/{p.name}"
    sc.person("UPDATE runs SET audio_join = 'certain', audio_rel = ?, audio_bytes = ?, audio_mtime_ns = ? "
              "WHERE run_id = 'R-U'", rel, p.stat().st_size, p.stat().st_mtime_ns)
    rc, log = D.bf(sc, D.Journal(tmp_path / "w.json").write(), "--apply", "--nas-recordings", str(nas))
    assert rc == 0, log
    rows = sc.rows()
    assert rows["R-U"]["audio_rel"] == rel
    assert (rows["R-T"]["audio_join"], rows["R-T"]["audio_rel"]) != ("certain", rel), log
    print("R-T:", rows["R-T"]["audio_join"])


def test_nothing_is_written_under_a_read_only_archive_and_no_file_there_is_opened_for_writing(sc, tmp_path):
    nas = tmp_path / "nas" / "recordings"
    nas.mkdir(parents=True)
    sc.run(M30.FakeTempo([D.early("R-1", 1, 30)]), NOW)
    D.nas_wav(nas, M30.DEV_K, "kronk", "1203688800000001", TB + GAP + 2.5)
    for d, _, files in os.walk(nas, topdown=False):
        for f in files:
            os.chmod(os.path.join(d, f), 0o400)
        os.chmod(d, 0o500)
    journal = D.Journal(tmp_path / "w.json").write()
    # In a child, so the audit hook (which cannot be removed) dies with it.
    code = textwrap.dedent(f"""
        import json, os, sys
        sys.path.insert(0, {str(M30.SCRIPTS)!r})
        import voice_runs_backfill as bf
        nas, seen = {str(nas)!r}, []
        WRITES = ("os.remove", "os.rename", "os.mkdir", "os.rmdir", "os.utime", "os.chmod", "os.chown", "os.link",
                  "os.symlink", "os.truncate", "shutil.move", "shutil.copyfile", "shutil.rmtree")
        def under(a):
            return isinstance(a, (str, bytes, os.PathLike)) and nas in os.fsdecode(a)
        def hook(event, args):
            if event == "open" and under(args[0]):
                seen.append(["open", args[1], args[2]])
            elif event in WRITES and any(under(a) for a in args):
                seen.append([event, None, None])
        sys.addaudithook(hook)
        rc = bf.main(["--db", {str(sc.db)!r}, "--journal", {str(journal)!r}, "--recordings", {str(sc.rec)!r},
                      "--nas-recordings", nas, "--apply"], now={NOW + 60}, out=sys.stderr)
        print(json.dumps([rc, seen]))
    """)
    (tmp_path / "audit.py").write_text(code)
    try:
        p = subprocess.run([sys.executable, str(tmp_path / "audit.py")], capture_output=True, text=True, timeout=60)
    finally:
        for d, _, _ in os.walk(nas):
            os.chmod(d, 0o700)
    assert p.returncode == 0, p.stderr[-800:]
    rc, seen = json.loads(p.stdout)
    assert rc == 0 and sc.rows()["R-1"]["audio_join"] == "certain", p.stderr
    assert seen, "the hook saw the scan"
    for event, mode, flags in seen:
        assert event == "open", event
        assert mode in ("r", "rb", None) and not (flags or 0) & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC), \
            (mode, flags)
