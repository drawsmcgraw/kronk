"""Work item M30 step D, fix round 1 (docs/sprint/M30-fix5.md): the rule of
the coordinator's ruling on step D's test. Each journal line is one run's;
no fill without a stored count or a voice.stt span; the window is the end of
the span, STT_END_S either side; the NAS scan runs before the lock and the
transaction; a dry run that meets an interrupted write says so.

Everything in tmp_path, invented sentences only, no journalctl, no socket."""
import fcntl
import os
import signal
import subprocess
import sys

import pytest

from tests import test_m30_backfill as D
from tests import test_m30_backfill_e2e8 as E
from tests import test_m30_voice_runs as M30
from tests.test_m30_backfill import old_house  # noqa: F401  (the fixture)

pytestmark = pytest.mark.usefixtures("no_network")
sc = M30.sc

BF, EX = D.BF, D.EX
HH, TB, GAP, NOW = D.HH, D.TB, D.GAP, D.NOW
OTHER = "the transcript is in another run's window too"


# ═══ Each journal line is one run's ═════════════════════════════════════════

def two_pucks(j, a_kind):
    """A on the kitchen puck, B on the living-room puck half a second later,
    one Whisper unit; the journal holds one line, at B's STT span's end, the
    length both rows store. Both windows hold it. A is of the kind given."""
    text = j.say(HH, TB + GAP + 0.5 + D.STT_END + 0.05, D.said(2))
    a = {"empty": D.early("R-A", 1, len(text)),
         "words": D.early("R-A", 1, None, heard=text.strip(), voice__transcript_chars=len(text)),
         "no count": D.early("R-A", 1, None),
         "nothing heard": D.early("R-A", 1, 0, voice__error_code="stt-no-text-recognized")}[a_kind]
    return text, [a, E.other_puck("R-B", TB + GAP + 0.5, len(text))]


@pytest.mark.parametrize("a_kind", ["empty", "words", "no count", "nothing heard"])
def test_a_line_inside_two_runs_windows_fills_neither(sc, tmp_path, a_kind):
    """Without the rule B is filled with a line that may be A's: whatever A
    is (empty, with Tempo's words, with no count, heard nothing), it is a
    run of the same unit whose window holds the line. (A removed row holds
    none: --remove empties its raw_trace, so it has no span; below.)"""
    j = D.Journal(tmp_path / "w.json")
    text, runs = two_pucks(j, a_kind)
    sc.run(M30.FakeTempo(runs), NOW)
    before = sc.rows()
    rc, log = D.bf(sc, j.write(), "--apply", "--no-audio")
    assert rc == 0, log
    assert sc.rows() == before, log
    got = D.lines_for(log, "R-B")
    assert len(got) == 1 and got[0].startswith(f"voice_runs_backfill: left  R-B  {OTHER}: also inside the window "
                                               "of R-A (wyoming-whisper.service)"), got
    if a_kind == "empty":
        assert D.lines_for(log, "R-A")[0].startswith(f"voice_runs_backfill: left  R-A  {OTHER}: also inside "
                                                     "the window of R-B")
    if a_kind == "words":
        assert "the journal agrees" in D.lines_for(log, "R-A")[0]      # reported, never written
    assert D.MARK not in log


def test_a_removed_row_has_no_window_and_holds_no_line(sc, tmp_path):
    """--remove empties raw_trace, so a removed run has no voice.stt span:
    it is never filled and holds no line. Its neighbour takes the line."""
    j = D.Journal(tmp_path / "w.json")
    text, runs = two_pucks(j, "empty")
    tempo = M30.FakeTempo(runs)
    sc.run(tempo, NOW)
    rc, log = sc.run(tempo, NOW + 10, "--remove", "R-A")
    assert rc == 0, log
    assert sc.rows()["R-A"]["raw_trace"] is None
    rc, log = D.bf(sc, j.write(), "--apply", "--no-audio")
    assert rc == 0, log
    assert sc.rows()["R-B"]["heard"] == text.strip()
    assert D.lines_for(log, "R-A")[0] == "voice_runs_backfill: left  R-A  removed"


def test_runs_three_seconds_apart_each_take_their_own_line(sc, tmp_path):
    """The rule does not reach past the window: two pucks three seconds
    apart, a line each, both filled."""
    j = D.Journal(tmp_path / "w.json")
    a = j.say(HH, D.at(1), D.said(1))
    b = j.say(HH, D.at(1) + 3, D.said(11))
    sc.run(M30.FakeTempo([D.early("R-A", 1, len(a)), E.other_puck("R-B", TB + GAP + 3, len(b))]), NOW)
    rc, log = D.bf(sc, j.write(), "--apply", "--no-audio")
    assert rc == 0, log
    rows = sc.rows()
    assert (rows["R-A"]["heard"], rows["R-B"]["heard"]) == (a.strip(), b.strip())


def test_the_other_unit_does_not_hold_a_line(sc, tmp_path):
    """A dev run half a second from a household run does not hold the
    household unit's line: each journal line is one run's of its own unit."""
    j = D.Journal(tmp_path / "w.json")
    h = j.say(HH, D.at(1), D.said(1))
    runs = [D.early("R-h", 1, len(h)),
            M30.vrun("R-d", TB + GAP + 0.5, service=M30.DEV, pipeline="kronk-dev", stages="wake_word,stt,intent,tts",
                     voice__transcript_chars=len(h))]
    sc.run(M30.FakeTempo(runs), NOW)
    rc, log = D.bf(sc, j.write(), "--apply", "--no-audio")
    assert rc == 0, log
    rows = sc.rows()
    assert rows["R-h"]["heard"] == h.strip() and rows["R-d"]["heard"] is None


# ═══ No fill without a count or a span ══════════════════════════════════════

def test_a_row_with_no_stored_count_is_never_filled_even_alone(sc, tmp_path):
    """Its own line, alone in the journal for an hour either side: left."""
    j = D.Journal(tmp_path / "w.json")
    j.say(HH, D.at(1), D.said(1))
    sc.run(M30.FakeTempo([D.early("R-1", 1, None)]), NOW)
    before = sc.rows()
    rc, log = D.bf(sc, j.write(), "--apply", "--no-audio")
    assert rc == 0 and sc.rows() == before
    assert D.lines_for(log, "R-1")[0] == ("voice_runs_backfill: left  R-1  no stored count: "
                                          "voice.transcript_chars was not recorded")


def test_a_row_with_no_stt_span_is_never_filled(sc, tmp_path):
    """Its count stored and its line at the run's start: no whole-run window."""
    j = D.Journal(tmp_path / "w.json")
    text = j.say(HH, D.at(1), D.said(1))
    sc.run(M30.FakeTempo([D.early("R-1", 1, len(text), children=False)]), NOW)
    before = sc.rows()
    rc, log = D.bf(sc, j.write(), "--apply", "--no-audio")
    assert rc == 0 and sc.rows() == before
    assert D.lines_for(log, "R-1")[0] == ("voice_runs_backfill: left  R-1  no speech-to-text span: "
                                          "no voice.stt span in the trace")


# ═══ The window: one named constant ═════════════════════════════════════════

def test_the_window_is_the_one_constant(sc, tmp_path, monkeypatch):
    """A line 1.5 s after the span's end is outside at STT_END_S = 1 and
    inside when the constant alone is raised to 2."""
    j = D.Journal(tmp_path / "w.json")
    text = j.say(HH, TB + GAP + D.STT_END + 1.5, D.said(1))
    sc.run(M30.FakeTempo([D.early("R-1", 1, len(text))]), NOW)
    journal = j.write()
    rc, log = D.bf(sc, journal, "--no-audio")
    assert rc == 0 and "no transcript in the window" in D.lines_for(log, "R-1")[0]
    monkeypatch.setattr(BF, "STT_END_S", 2.0)
    rc, log = D.bf(sc, journal, "--no-audio")
    assert rc == 0 and D.lines_for(log, "R-1")[0].startswith("voice_runs_backfill: fill  R-1  certain")
    assert "+1.50 s from the stt span's end" in D.lines_for(log, "R-1")[0]


def test_the_window_is_measured_from_the_spans_end_not_its_start():
    span = {"name": "voice.stt", "startTimeUnixNano": "100000000000", "endTimeUnixNano": "102265000000"}
    raw = '{"resourceSpans":[{"scopeSpans":[{"spans":[' + __import__("json").dumps(span) + ']}]}]}'
    assert BF.stt_window({"start_s": 99.0, "end_s": 110.0, "raw_trace": raw}) == (101.265, 103.265)


# ═══ The NAS scan: before the lock and the transaction ══════════════════════

def lock_held(db):
    fd = os.open(db.parent, os.O_RDONLY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    except BlockingIOError:
        return True
    finally:
        os.close(fd)


@pytest.mark.parametrize("apply", [True, False], ids=["apply", "dry-run"])
def test_the_nas_scan_runs_before_the_lock_and_before_the_record_is_opened(sc, old_house, monkeypatch, apply):  # noqa: F811
    events = []
    real_scan, real_open, real_ro, real_joins = BF.ls.scan, BF.ve.open_record, BF.open_read_only, BF.ve.audio_joins

    def scan(root):
        events.append(("scan", lock_held(sc.db)))
        return real_scan(root)

    def joins(conn, recordings, now, recs=None):
        events.append(("join", recs is not None))
        return real_joins(conn, recordings, now, recs=recs)
    monkeypatch.setattr(BF.ls, "scan", scan)
    monkeypatch.setattr(BF.ve, "open_record", lambda db, create=False: (events.append(("open", lock_held(sc.db))),
                                                                         real_open(db, create))[1])
    monkeypatch.setattr(BF, "open_read_only", lambda db: (events.append(("open", lock_held(sc.db))), real_ro(db))[1])
    monkeypatch.setattr(BF.ve, "audio_joins", joins)
    rc, log = D.bf(sc, old_house["journal"], *(["--apply"] if apply else []), "--nas-recordings", str(old_house["nas"]))
    assert rc == 0, log
    assert events == [("scan", False), ("scan", False), ("open", apply), ("join", True)], events
    assert ("joined 3" if apply else "would join 3") in log and "(1 certain, 2 ambiguous); left 2" in log


# ═══ A dry run after an interrupted write ═══════════════════════════════════

def test_a_dry_run_after_a_killed_apply_says_a_write_was_interrupted(sc, tmp_path, monkeypatch):
    n = 400
    monkeypatch.setattr(EX, "TRACE_READS_MAX", n)
    sc.run(M30.FakeTempo(E.many(n)), NOW)
    before = sc.rows()
    journal = E.journal_for(tmp_path, n)
    script = tmp_path / "kill.py"
    script.write_text(E.KILL.format(scripts=str(M30.SCRIPTS), after=250, db=str(sc.db), journal=str(journal),
                                    now=NOW + 60))
    p = subprocess.run([sys.executable, str(script)], capture_output=True, text=True, timeout=120)
    assert p.returncode == -signal.SIGKILL, (p.returncode, p.stderr[-400:])
    assert sc.db.with_name(sc.db.name + "-journal").exists(), "no hot journal: the case is not made"
    rc, log = D.bf(sc, journal, "--no-audio")
    assert rc == 1
    assert log.splitlines()[-1] == ("voice_runs_backfill: FAILED: a write was interrupted; the next export or "
                                    "--apply repairs it. Nothing read, nothing written")
    assert "readonly" not in log
    # And --apply does repair it: the hot journal rolled back, then every row filled.
    rc, log = D.bf(sc, journal, "--apply", "--no-audio")
    assert rc == 0 and f"filled {n};" in log
    after = sc.rows()
    assert all(after[k]["heard_source"] == "whisper_journal" for k in before)
