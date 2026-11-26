"""Work item M30 step B, fix round 2 (docs/sprint/M30-fix2.md): the four jobs
of the coordinator's ruling on the narrow test (docs/sprint/M30-e2e3.md).

1. A row inserted already final re-opens the audio join of its whole set.
2. --remove and --init wait for the record's lock and exit 1 without it;
   --check-now and --why where they do nothing are refused.
3. A request to Tempo is bounded in wall time, whole.
4. BUSY_TIMEOUT_S fits inside the unit with the 43 s, and "database is
   locked" reaches the status.

Same rules as tests/test_m30_voice_runs.py: everything in tmp_path, a fake
Tempo at the function boundary, no socket, every sentence invented."""
import io
import os
import signal
import sqlite3
import threading
import time

import pytest

from tests.test_m16_backups import no_network  # noqa: F401  (the fixture)
from tests.test_m30_voice_runs import DEV_K, EX, LS, NOW, FakeClock, FakeTempo, Scratch, household, sha, vrun, wav

pytestmark = pytest.mark.usefixtures("no_network")


@pytest.fixture
def sc(tmp_path):
    return Scratch(tmp_path)


def oracle(sc):
    """voice_label_sheet.join's own answer over every row and recording of
    the finished record."""
    rows = sc.rows()
    recs = LS.scan(sc.rec)
    runs = [EX.run_for_join(r) for r in rows.values()]
    LS.join(recs, runs)
    certain = {r.run["ha.pipeline_run_id"]: r.path.relative_to(sc.rec).as_posix() for r in recs if r.run}
    unsure = {i for r in recs if r.note.startswith("ambiguous") for i in LS._candidates(r, runs)}
    return {k: ("certain", certain[k]) if k in certain else ("ambiguous", None) if i in unsure else ("none", None)
            for i, k in enumerate(rows)}


def stored(sc):
    return {k: (r["audio_join"], r["audio_rel"]) for k, r in sc.rows().items()}


def run(rid, start, dur=6.0, **kw):
    return vrun(rid, start, dur=dur, heard="invented", kronk__origin__device_id=DEV_K, **kw)


# ═══ Job 1: a row inserted already final re-opens its set ══════════════════

# Shapes for the third path (a run Tempo shows later than FINAL_S, read by
# the ordinary main read). Times are offsets from a base; `late` is the run
# that shows up after the others were joined.
SHAPES = {
    "two runs, one recording": ({"A": 0, "B": 3}, {"1": 5}, "B"),
    # A and B share recording 1, B alone fits 2; C late, fits 2 only. C's
    # span does not reach recording 1 nor A's span: A changes two hops away.
    "a chain, the change two hops away": ({"A": 0, "B": 9, "C": 20}, {"1": 8, "2": 19}, "C"),
    "a chain, a third recording only the late run fits": ({"A": 0, "B": 9, "C": 18}, {"1": 8, "2": 17, "3": 28}, "C"),
    "the late run fits no recording": ({"A": 0, "B": 30}, {"1": 4}, "B"),
}


@pytest.mark.parametrize("shape", list(SHAPES))
def test_job1_a_run_tempo_shows_after_final_s_leaves_every_join_as_the_join_says(sc, shape):
    starts, recs, late = SHAPES[shape]
    base = NOW - 3000
    for name, off in recs.items():
        wav(sc.rec, DEV_K, "kronk", name, base + off)
    runs = {k: run(k, base + off) for k, off in starts.items()}
    tempo = FakeTempo([r for k, r in runs.items() if k != late])
    sc.run(tempo, NOW - 2000)                             # every run final: joined now
    assert all(r["audio_tried_at"] for r in sc.rows().values())
    before = sc.rows()
    tempo.runs.append(runs[late])                         # Tempo shows it 1,000 s after it ended
    rc, log = sc.run(tempo, NOW - 1940)
    assert rc == 0, log
    assert set(sc.rows()) == set(starts)
    assert stored(sc) == oracle(sc)
    after = sc.rows()
    for k in before:                                      # no seq moves, no change is made to a row
        assert (after[k]["seq"], after[k]["last_changed_at"]) == (before[k]["seq"], before[k]["last_changed_at"])
    assert all(r["audio_tried_at"] for r in after.values())


def test_job1_a_run_only_the_hourly_check_can_reach_reopens_its_neighbours_join(sc):
    """B is older than the main read's reach (the watermark less an hour)
    when it shows up in Tempo, so the hourly check is what takes it. (In
    test_m30_voice_runs_e2e3.py's hourly-check test the main read's
    overlap still reaches B and takes it first.)"""
    a, b = run("A", NOW - 7200), run("B", NOW - 7197)
    wav(sc.rec, DEV_K, "kronk", "1", NOW - 7195)
    tempo = FakeTempo([a])
    sc.run(tempo, NOW - 3000)
    sc.run(tempo, NOW)                                    # the watermark: NOW - 900
    assert stored(sc)["A"][0] == "certain"
    assert sc.state()["watermark_s"] - EX.OVERLAP_S > NOW - 7197      # the main read cannot reach B
    tempo.runs.append(b)
    rc, log = sc.run(tempo, NOW + 60, "--check-now")
    assert rc == 0, log
    this = sc.status_json()["this_run"]
    assert this["inserted"] == 1 and this["check"]["taken"] == 1
    assert stored(sc) == oracle(sc) == {"A": ("ambiguous", None), "B": ("ambiguous", None)}


def test_job1_the_two_hops_shape_changes_the_far_row(sc):
    """The shape above is chosen so a re-open of the late run's direct
    neighbours only would leave A wrong: A's answer moves, and A is not
    a direct neighbour of C."""
    starts, recs, late = SHAPES["a chain, the change two hops away"]
    base = NOW - 3000
    for name, off in recs.items():
        wav(sc.rec, DEV_K, "kronk", name, base + off)
    tempo = FakeTempo([run(k, base + off) for k, off in starts.items() if k != late])
    sc.run(tempo, NOW - 2000)
    assert stored(sc)["A"][0] == "certain"
    tempo.runs.append(run(late, base + starts[late]))
    sc.run(tempo, NOW - 1940)
    assert stored(sc)["A"] == ("ambiguous", None)
    c_lo = base + starts["C"] - LS.JOIN_BEFORE_S
    a_hi = base + starts["A"] + 6.0 + LS.JOIN_AFTER_S
    assert a_hi < c_lo and not (c_lo <= base + recs["1"])  # A's span and recording 1 are outside C's


def test_job1_a_removed_row_in_the_set_is_not_reopened(sc):
    """B was removed after its join. A late run beside it re-opens A, not B:
    a removed row is never written again."""
    base = NOW - 3000
    wav(sc.rec, DEV_K, "kronk", "1", base + 8)
    wav(sc.rec, DEV_K, "kronk", "2", base + 17)
    tempo = FakeTempo([run("A", base), run("B", base + 9)])
    sc.run(tempo, NOW - 2000)
    assert sc.run(tempo, NOW - 1990, "--remove", "B")[0] == 0
    b = sc.rows()["B"]
    tempo.runs.append(run("C", base + 18))
    sc.run(tempo, NOW - 1940)
    after = sc.rows()
    assert {c: after["B"][c] for c in b} == {**b, "last_seen_at": after["B"]["last_seen_at"]}
    want = oracle(sc)
    assert {k: stored(sc)[k] for k in "AC"} == {k: want[k] for k in "AC"}
    assert sc.status_json()["this_run"]["audio_reopened"] == 1   # A only


def test_job1_a_reopen_raises_no_seq_and_rewrites_no_copy(sc, monkeypatch):
    """Two runs older than the 90 days the copy holds, on one recording, a
    budget cut between them in the first read. The second run inserts B and
    re-opens A: A's answer moves, and nothing published does."""
    clk = FakeClock()
    monkeypatch.setattr(EX, "clock", clk)
    mark = NOW - EX.FIRST_READ_S + 86400                  # the first read's first day mark: 91 days ago
    a, b = run("A", mark - 4), run("B", mark + 3)
    wav(sc.rec, DEV_K, "kronk", "1", mark + 4)
    tempo = FakeTempo([a, b])
    cut = {"on": True}

    def get(url, timeout=None):
        out = tempo(url, timeout)
        if cut["on"] and "/api/search" in url and any(t["traceID"] == a["tid"].lstrip("0") for t in out["traces"]):
            clk.t += EX.READ_BUDGET_S + 1
        return out
    assert sc.run(get, NOW)[0] == 0
    cut["on"] = False
    assert set(sc.rows()) == {"A"} and stored(sc)["A"][0] == "certain"
    seq_a, pub, pub_mtime = sc.rows()["A"]["seq"], sha(sc.pub), os.stat(sc.pub).st_mtime_ns
    rc, log = sc.run(get, NOW + 60)
    assert rc == 0, log
    assert set(sc.rows()) == {"A", "B"} and stored(sc) == oracle(sc) == {"A": ("ambiguous", None),
                                                                         "B": ("ambiguous", None)}
    assert sc.rows()["A"]["seq"] == seq_a
    assert sc.status_json()["this_run"]["published"] == 0
    assert sha(sc.pub) == pub and os.stat(sc.pub).st_mtime_ns == pub_mtime and sc.published() == {}


# ═══ Job 2: --remove and --init wait for the lock; misuse refused ══════════

def _hold(sc):
    sc.db.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    return EX.lock_dir(sc.db)


def _done(sc, what):
    if what == "--remove":
        return sc.rows()["R1"]["removed"] is not None
    if what == "--init (a record exists)":
        return sc.status_json()["ts"] == NOW + 60
    return sc.db.exists()


def _start(sc, what, tempo):
    if what != "--init (no record yet)":
        sc.run(tempo, NOW)
    return ("--remove", "R1") if what == "--remove" else ()


WHAT = ["--remove", "--init (a record exists)", "--init (no record yet)"]


@pytest.mark.parametrize("what", WHAT)
def test_job2_a_run_by_hand_waits_for_the_lock_and_then_does_its_work(sc, monkeypatch, what):
    monkeypatch.setattr(EX, "LOCK_WAIT_S", 5.0)
    tempo = FakeTempo(household(2))
    extra = _start(sc, what, tempo)
    fd = _hold(sc)
    threading.Timer(0.4, os.close, (fd,)).start()        # the export holding it finishes
    t = time.monotonic()
    rc, log = sc.run(tempo, NOW + 60, *extra, init=what.startswith("--init"))
    took = time.monotonic() - t
    assert rc == 0, log
    assert took >= 0.35 and _done(sc, what)


@pytest.mark.parametrize("what", WHAT)
def test_job2_a_run_by_hand_that_never_gets_the_lock_exits_1_and_does_nothing(sc, monkeypatch, what):
    monkeypatch.setattr(EX, "LOCK_WAIT_S", 0.3)
    tempo = FakeTempo(household(2))
    extra = _start(sc, what, tempo)
    before = sha(sc.db) if sc.db.exists() else None
    fd = _hold(sc)
    try:
        t = time.monotonic()
        rc, log = sc.run(tempo, NOW + 60, *extra, init=what.startswith("--init"))
        took = time.monotonic() - t
    finally:
        os.close(fd)
    assert rc == 1 and "FAILED" in log and "lock" in log and "Traceback" not in log
    assert took >= 0.3
    assert (sha(sc.db) if sc.db.exists() else None) == before
    if what == "--remove":
        assert "nothing removed (R1)" in log and sc.rows()["R1"]["removed"] is None


def test_job2_the_timers_ordinary_run_still_gives_way_at_once_with_exit_0(sc, monkeypatch):
    monkeypatch.setattr(EX, "LOCK_WAIT_S", 5.0)
    sc.run(FakeTempo(household(1)), NOW)
    fd = EX.lock_dir(sc.db)
    try:
        tempo = FakeTempo(household(2))
        t = time.monotonic()
        rc, log = sc.run(tempo, NOW + 60)
        took = time.monotonic() - t
    finally:
        os.close(fd)
    assert rc == 0 and "another export" in log and tempo.calls == [] and took < 1.0


def test_job2_the_wait_outlasts_the_longest_a_run_can_hold_the_lock():
    unit = (EX.ROOT / "systemd" / "kronk-voice-runs-export.service").read_text()
    limit = int(unit.split("TimeoutStartSec=")[1].split()[0])
    assert EX.LOCK_WAIT_S > limit


@pytest.mark.parametrize("argv", [("--remove", "R1", "--check-now"), ("--why", "a guest"),
                                  ("--init", "--why", "a guest"), ("--check-now", "--why", "a guest")])
def test_job2_check_now_or_why_where_they_do_nothing_is_refused(sc, argv):
    tempo = FakeTempo(household(2))
    sc.run(tempo, NOW)
    before, status = sha(sc.db), sha(sc.status)
    calls = len(tempo.calls)
    rc, log = sc.run(tempo, NOW + 60, *argv, init=False)
    assert rc == 2 and "REFUSED" in log
    assert sha(sc.db) == before and sha(sc.status) == status and len(tempo.calls) == calls


def test_job2_remove_without_why_still_marks_the_run(sc):
    tempo = FakeTempo(household(2))
    sc.run(tempo, NOW)
    assert sc.run(tempo, NOW + 60, "--remove", "R1")[0] == 0
    assert sc.rows()["R1"]["removed"].endswith("removed by hand")


# ═══ Job 3: a request is bounded in wall time, whole ═══════════════════════

class Dribble(io.RawIOBase):
    """A response that hands out one byte every `gap` seconds."""

    def __init__(self, body, gap):
        self.body, self.gap = body, gap

    def read(self, n=-1):
        out = b""
        for ch in self.body:
            time.sleep(self.gap)
            out += bytes([ch])
        return out

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


BODY = b'{"traces": []}' + b" " * 100                  # 114 bytes: 2.3 s at 20 ms a byte


@pytest.mark.parametrize("where", ["headers", "body"])
def test_job3_a_tempo_that_dribbles_is_cut_at_the_whole_requests_timeout(monkeypatch, where):
    seen = {}

    def fake_urlopen(url, timeout=None):
        seen["timeout"] = timeout
        if where == "headers":
            for _ in range(len(BODY)):                    # the status line and headers, a byte at a time
                time.sleep(0.02)
            return Dribble(b'{"traces": []}', 0.0)
        return Dribble(BODY, 0.02)
    monkeypatch.setattr(EX.urllib.request, "urlopen", fake_urlopen)
    handler = signal.getsignal(signal.SIGALRM)
    t = time.monotonic()
    with pytest.raises(TimeoutError, match="longer than 0.3 s"):
        EX.http_get_json("http://127.0.0.1:3200/api/search?q=x", 0.3)
    took = time.monotonic() - t
    assert 0.3 <= took < 0.8 and seen["timeout"] == 0.3
    assert signal.getsignal(signal.SIGALRM) is handler and signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0)


def test_job3_through_tempo_the_cut_is_a_failed_read_that_names_the_bound(monkeypatch):
    monkeypatch.setattr(EX.urllib.request, "urlopen", lambda url, timeout=None: Dribble(BODY, 0.02))
    tempo = EX.Tempo("http://127.0.0.1:3200", timeout=0.3)
    with pytest.raises(EX.TempoError, match="TimeoutError: the request took longer than 0.3 s"):
        tempo.get("http://127.0.0.1:3200/api/search?q=x")


def test_job3_a_prompt_answer_leaves_no_timer_and_the_old_handler(monkeypatch):
    monkeypatch.setattr(EX.urllib.request, "urlopen", lambda url, timeout=None: Dribble(b'{"traces": []}', 0.0))
    handler = signal.getsignal(signal.SIGALRM)
    assert EX.http_get_json("http://127.0.0.1:3200/api/search?q=x", 5.0) == {"traces": []}
    assert signal.getsignal(signal.SIGALRM) is handler and signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0)
    time.sleep(0.01)


def test_job3_off_the_main_thread_the_request_is_refused_not_unbounded(monkeypatch):
    monkeypatch.setattr(EX.urllib.request, "urlopen", lambda url, timeout=None: Dribble(b'{"traces": []}', 0.0))
    got = {}

    def go():
        try:
            EX.http_get_json("http://127.0.0.1:3200/api/search?q=x", 5.0)
        except RuntimeError as e:
            got["e"] = str(e)
    th = threading.Thread(target=go)
    th.start()
    th.join()
    assert "main thread only" in got["e"]


def test_job3_the_exporter_uses_the_bounded_request_by_default():
    import inspect
    assert inspect.signature(EX.main).parameters["get_json"].default is EX.http_get_json
    assert inspect.signature(EX.Tempo).parameters["get_json"].default is EX.http_get_json


# ═══ Job 4: the wait for the record fits, and is in the status ═════════════

def test_job4_tempo_at_its_worst_and_a_person_holding_the_record_ends_inside_the_unit(sc, monkeypatch):
    """Every request takes its whole TEMPO_TIMEOUT_S on the budget clock, an
    hourly check is due, and a person's transaction holds the record with
    the real BUSY_TIMEOUT_S: the run reaches 'database is locked' in the
    status file, and the budget's bound (43 s) plus the wait it really
    took is under the unit's 50 s."""
    sc.run(FakeTempo(household(30, start=NOW - 30 * 86400, gap=80000.0)), NOW)
    clk = FakeClock()
    monkeypatch.setattr(EX, "clock", clk)
    tempo = FakeTempo(household(30, start=NOW - 30 * 86400, gap=80000.0))

    def get(url, timeout=None):
        clk.t += EX.TEMPO_TIMEOUT_S
        return tempo(url, timeout)
    c = sqlite3.connect(sc.db, isolation_level=None)
    c.execute("BEGIN IMMEDIATE")
    try:
        t0, t = clk.t, time.monotonic()
        rc, log = sc.run(get, NOW + 60, "--check-now")
        took = time.monotonic() - t
    finally:
        c.execute("ROLLBACK")
        c.close()
    st = sc.status_json()
    assert rc == 1 and "Traceback" not in log
    assert st["ts"] == NOW + 60 and st["ok"] is False and "database is locked" in st["last_error"]
    assert clk.t - t0 <= EX.CHECK_BUDGET_S + EX.TEMPO_TIMEOUT_S == 43
    assert EX.BUSY_TIMEOUT_S - 0.2 <= took and 43 + took < 50
