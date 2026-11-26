"""Work item M30 step B, the test agent's findings (docs/sprint/M30-e2e2.md).

Each test shows a fault the step's own tests do not reach and is marked
xfail (strict) with the finding's number. When a fix lands the test passes,
the strict mark fails the run, and the mark is removed with the fix.

Same rules as tests/test_m30_voice_runs.py: everything in tmp_path, a fake
Tempo at the function boundary, no socket, every sentence invented."""
import os
import re
import sqlite3
import urllib.error
from pathlib import Path

import pytest

from tests.test_m16_backups import no_network  # noqa: F401  (the fixture)
from tests.test_m30_voice_runs import (DEV_K, EX, LS, MARK, NOW, FakeTempo, Scratch, household, tid_of, vrun, wav)

pytestmark = pytest.mark.usefixtures("no_network")

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def sc(tmp_path):
    return Scratch(tmp_path)


@pytest.mark.xfail(strict=True, reason="M30-e2e2 finding 1 (blocker): the audio join is made when a run is first "
                   "seen, against the runs that have reached Tempo so far; a run that arrives one export later "
                   "and fits the same recording turns a guess into a stored 'certain'")
def test_f1_the_audio_join_is_the_same_whether_two_runs_arrive_together_or_an_export_apart(sc):
    """One recording, two runs it fits (same puck, same pipeline, three
    seconds apart). voice_label_sheet.join calls that ambiguous for both.
    Runs reach Tempo one at a time, so the usual case is an export apart."""
    a1 = vrun("A1", NOW - 600, dur=6, heard="invented one", kronk__origin__device_id=DEV_K)
    a2 = vrun("A2", NOW - 597, dur=6, heard="invented two", kronk__origin__device_id=DEV_K)
    wav(sc.rec, DEV_K, "kronk", "777", NOW - 595)
    tempo = FakeTempo([a1])
    sc.run(tempo, NOW)
    tempo.runs.append(a2)                                  # voice_tracer exports the second a little later
    sc.run(tempo, NOW + 60)
    rows = sc.rows()
    recs = LS.scan(sc.rec)
    LS.join(recs, [EX.run_for_join(r) for r in rows.values()])
    assert recs[0].run is None and recs[0].note.startswith("ambiguous")      # the answer of the join itself
    assert {k: (r["audio_join"], r["audio_rel"]) for k, r in rows.items()} == {
        "A1": ("ambiguous", None), "A2": ("ambiguous", None)}


TRACE_NONSENSE = [
    {"trace": {"resourceSpans": [{"scopeSpans": 5}]}},
    {"trace": {"resourceSpans": [{"scopeSpans": [{"spans": 5}]}]}},
    {"trace": {"resourceSpans": [{"scopeSpans": [{"spans": [{"name": "voice.run", "attributes": 5}]}]}]}},
]


@pytest.mark.xfail(strict=True, raises=TypeError, reason="M30-e2e2 finding 2: B3's 'answering nonsense' is "
                   "checked for the search answer only; these shapes of a trace-by-id answer raise TypeError out "
                   "of main(): no status file, a traceback in the journal, and the same on every later run")
@pytest.mark.parametrize("shape", TRACE_NONSENSE)
def test_f2_a_nonsense_trace_answer_is_a_failed_trace_read_not_a_crash(sc, shape):
    tempo = FakeTempo(household(2))
    real = tempo.__call__

    def get(url, timeout=None):
        return shape if "/api/v2/traces/" in url else real(url, timeout)
    rc, _ = sc.run(get, NOW)
    assert rc == 0 and len(sc.rows()) == 2
    assert sc.status_json()["ts"] == NOW and "trace read" in sc.status_json()["last_error"]


@pytest.mark.xfail(strict=True, reason="M30-e2e2 finding 3: trace reads go newest first and stop at the third "
                   "failure, so three runs whose trace never reads keep every older waiting run from being read "
                   "at all; the status says ok")
def test_f3_three_traces_that_never_read_do_not_starve_the_older_ones(sc):
    runs = household(20, start=NOW - 6000, gap=60)
    tempo = FakeTempo(runs)
    real = tempo.__call__
    state = {"all_fail": True}
    never = {tid_of(f"R{i}") for i in (18, 19, 20)}

    def get(url, timeout=None):
        if "/api/v2/traces/" in url and (state["all_fail"] or url.rsplit("/", 1)[1] in never):
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
        return real(url, timeout)
    sc.run(get, NOW)                                       # trace reads fail for a while: 20 rows wait
    state["all_fail"] = False
    for k in range(1, 40):
        sc.run(get, NOW + 60 * k)
    assert sum(1 for r in sc.rows().values() if r["raw_trace"]) == 17


@pytest.mark.xfail(strict=True, reason="M30-e2e2 finding 4: one run with the clock a day ahead puts the watermark "
                   "in the future; reads start an hour before it, so the next 23 hours of speech are never taken. "
                   "The hourly check counts them missing and takes none; the status says ok")
def test_f4_a_clock_a_day_ahead_for_one_run_loses_no_run(sc):
    tempo = FakeTempo(household(1, start=NOW - 1800))
    sc.run(tempo, NOW)
    sc.run(tempo, NOW + 86400)                             # one run with a wrong clock
    tempo.runs += [vrun(f"C{i}", NOW + 3600 * (i + 1), heard="invented") for i in range(20)]
    for m in range(1, 24 * 60 + 200, 7):                   # the clock is right again, a day and a bit of exports
        sc.run(tempo, NOW + 60 * m)
    assert len([k for k in sc.rows() if k.startswith("C")]) == 20


@pytest.mark.xfail(strict=True, reason="M30-e2e2 finding 5: a record that has gone is made again, empty, by the "
                   "next run, which reports ok; rows older than Tempo's 90 days are then only in the backups, "
                   "nothing says so, and backups.py's 'record is missing' warning can never fire")
def test_f5_a_lost_record_is_said_not_silently_started_again(sc):
    old = household(5, start=NOW - 100 * 86400, gap=60)
    sc.run(FakeTempo(old), NOW - 99 * 86400)
    assert len(sc.rows()) == 5
    os.unlink(sc.db)                                       # lost; the status file and the copies are still there
    rc, log = sc.run(FakeTempo([]), NOW)
    assert rc != 0 or sc.status_json()["ok"] is False, log


@pytest.mark.xfail(strict=True, reason="M30-e2e2 finding 6: the feature doc's removal statement empties the row; "
                   "the words stay in run_changes.old_row (and in the file's free pages) when the row had been "
                   "changed before")
def test_f6_the_feature_docs_removal_takes_the_words_out_of_the_record(sc):
    runs = household(2, start=NOW - 1800, gap=60)
    tempo = FakeTempo(runs)
    sc.run(tempo, NOW)
    runs[1]["attrs"]["voice.handled_by"] = "ha_local"      # a changed value: the old row goes to run_changes
    sc.run(tempo, NOW + 60)
    doc = (ROOT / "docs" / "features" / "voice-dataset.md").read_text()
    m = re.search(r"```\n(UPDATE runs SET removed = .*?)\n```", doc, re.S)
    assert m, "the feature doc's removal statement is missing"
    script = m.group(1).replace("<run_id>", "R2").replace("<date> <who>: <why>", "2026-11-25 test: a guest")
    c = sqlite3.connect(sc.db)
    c.executescript(script)
    c.commit()
    c.close()
    sc.run(tempo, NOW + 120)
    assert f"invented request 2 {MARK}".encode() not in sc.db.read_bytes()
