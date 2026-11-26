"""Work item M30 step B, the narrow test of fix round 2
(docs/sprint/M30-e2e4.md): the join in any order of arrival (randomized),
the lock against a real second process, the request bound's SIGALRM, the
arithmetic.

Same rules as tests/test_m30_voice_runs.py: everything in tmp_path, a fake
Tempo at the function boundary, no socket, every sentence invented."""
import os
import random
import signal
import sqlite3
import subprocess
import sys
import time

import pytest

from tests.test_m16_backups import no_network  # noqa: F401  (the fixture)
from tests.test_m30_voice_runs import DEV_K, EX, LS, NOW, FakeClock, FakeTempo, Scratch, sha, vrun, wav

pytestmark = pytest.mark.usefixtures("no_network")
DAY = 86400.0


@pytest.fixture
def sc(tmp_path):
    return Scratch(tmp_path)


def run(rid, start, dur=6.0, **kw):
    return vrun(rid, start, dur=dur, heard="invented", kronk__origin__device_id=DEV_K, **kw)


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


# ═══ Job 1: the join, whatever way and order the runs reach the record ═════

class World:
    """A household's runs and recordings in clusters, and a Tempo that shows
    each run from its own moment on. Paths a run can take to the record:

      on time   in Tempo seconds after it ends (voice_tracer's premise);
      cut       in Tempo on time, but the main read that would take it is
                stopped by its budget at the day mark before it;
      late      Tempo shows it 900 to 3,400 s after it ended (the main
                read's overlap still reaches it; it is inserted final);
      check     Tempo shows it 4,700 s or more after it ended (only the
                hourly check can reach it).

    Every run that arrives by one of the last three is inserted already
    final, which is what the ruling's re-open covers. A run inserted late
    and not yet final is the finding below, not in here. Some rows are
    removed along the way. Every recording is local and younger than seven
    days."""

    def __init__(self, sc, seed, monkeypatch):
        self.sc, self.rng = sc, random.Random(seed)
        rng = self.rng
        self.clk = FakeClock()
        monkeypatch.setattr(EX, "clock", self.clk)
        self.tempo = FakeTempo([])
        self.all, self.visible_at, self.n_rec = {}, {}, 0
        self.cut_marks: set[float] = set()
        self.t0 = NOW                                     # the first export (--init)
        # Old clusters: before the first export, some astride a day mark of
        # the first read (now - k days), where the budget can cut.
        for k in range(rng.randint(2, 4)):
            mark = self.t0 - rng.randint(1, 4) * DAY
            at = mark + rng.uniform(-6, 2) if rng.random() < 0.7 else self.t0 - rng.uniform(2 * 3600, 4.5 * DAY)
            self.cluster(f"o{k}", at, old=True)
            if rng.random() < 0.7:
                self.cut_marks.add(mark)
        # Live clusters: in the two hours after it.
        for k in range(rng.randint(2, 4)):
            self.cluster(f"l{k}", self.t0 + rng.uniform(60, 7200), old=False)
        self.end = max(self.visible_at.values()) + EX.FINAL_S + 3700
        self.removals = sorted((rng.uniform(self.t0, self.end - 2000), rid)
                               for rid in rng.sample(sorted(self.all), k=rng.randint(0, 2)))

    def cluster(self, name, at, old):
        rng = self.rng
        t = at
        for j in range(rng.randint(1, 4)):
            rid = f"{name}r{j}"
            dur = rng.choice([3.0, 6.0, 6.0, 12.0, 40.0])
            r = run(rid, round(t, 3), dur=dur)
            self.all[rid] = r
            path = rng.choice(["on time", "on time", "late", "check"])
            end = r["start"] + dur
            if path == "on time":
                vis = end + rng.uniform(1, 20)
            elif path == "late":
                vis = end + rng.uniform(EX.FINAL_S, 3400)
            else:
                vis = end + rng.uniform(4700, 9000)
            if old and path != "on time":                 # an old run Tempo shows only after the first export
                vis = self.t0 + rng.uniform(30, 9000)
            self.visible_at[rid] = vis
            # recordings: most runs have one, written inside the run or just after its STT
            if rng.random() < 0.8:
                self.n_rec += 1
                wav(self.sc.rec, DEV_K, "kronk", f"w{self.n_rec}", round(r["start"] + rng.uniform(-1.5, dur + 4.5), 3),
                    seconds=0.05)
            t = end + rng.choice([-2.0, 1.0, 3.0, 5.0, 6.5, 9.0, 30.0]) + rng.uniform(0, 1)

    def get(self, url, timeout=None):
        out = self.tempo(url, timeout)
        if "/api/search" in url and self.burn:
            end = int(url.split("end=")[1].split("&")[0])
            if any(abs(end - m) <= 1 for m in self.burn):
                self.clk.t += EX.READ_BUDGET_S + 1        # this day used the main read's budget up
                self.burn = set()
        return out

    def export(self, now, *extra):
        self.tempo.runs = [r for k, r in self.all.items() if self.visible_at[k] <= now]
        self.burn = set(self.cut_marks) if self.rng.random() < 0.6 else set()
        rc, log = self.sc.run(self.get, now, *extra)
        assert rc == 0, log
        return log

    def play(self):
        rng = self.rng
        times = {self.t0, self.end, self.end + 60}
        for rid, r in self.all.items():
            for base in (self.visible_at[rid], r["start"] + r["dur"] + EX.FINAL_S):
                if base > self.t0:
                    times |= {base + rng.uniform(0.5, 59), base + 60 + rng.uniform(0, 59)}
        t = self.t0
        while t < self.end:                               # and a run now and then, so the hourly check comes round
            t += rng.uniform(60, 1500)
            times.add(t)
        removals = list(self.removals)
        for now in sorted(times):
            while removals and removals[0][0] <= now:
                at, rid = removals.pop(0)
                if rid in self.sc.rows():
                    rc, log = self.sc.run(self.get, at, "--remove", rid)
                    assert rc == 0, log
            self.export(now, *(("--check-now",) if now >= self.end else ()))

    def differing(self):
        rows = self.sc.rows()
        assert set(rows) == set(self.all), sorted(set(self.all) - set(rows))
        want, got = oracle(self.sc), stored(self.sc)
        return {k: (got[k], want[k]) for k, r in rows.items() if r["removed"] is None and got[k] != want[k]}


SEEDS = int(os.environ.get("KRONK_M30_E2E4_SEEDS", "25"))


@pytest.mark.parametrize("seed", range(SEEDS))
def test_job1_random_arrivals_leave_every_stored_join_as_the_join_says(sc, monkeypatch, seed):
    w = World(sc, seed, monkeypatch)
    w.play()
    assert w.differing() == {}
    rows = sc.rows()
    assert all(r["audio_tried_at"] for r in rows.values() if r["removed"] is None)
    # B1 after all of it: one more run changes nothing and rewrites no copy
    before = ({k: r["seq"] for k, r in rows.items()}, sha(sc.pub), sc.pub.stat().st_mtime_ns)
    w.export(w.end + 120)
    assert sc.status_json()["this_run"]["published"] == 0
    assert ({k: r["seq"] for k, r in sc.rows().items()}, sha(sc.pub), sc.pub.stat().st_mtime_ns) == before


# ── The finding: a run inserted late and NOT yet final ─────────────────────

@pytest.mark.xfail(strict=True, reason="M30-e2e4 finding 1: a run inserted after its neighbour was joined, and not "
                   "yet final itself, re-opens nothing; the neighbour keeps a certain the join calls ambiguous")
def test_job1_a_catch_up_read_cut_at_a_day_mark_before_a_long_run_that_is_not_final_yet(sc, monkeypatch):
    """Inside the premises: Tempo has both runs on time. The exporter was
    stopped for about a day; its catch-up read goes a day per search and
    its budget ends at the day mark, between A and B. A is final and is
    joined alone. B (two minutes long) comes with the next run, 889 s
    after it ended: not final, so the re-open does not look at it."""
    clk = FakeClock()
    monkeypatch.setattr(EX, "clock", clk)
    tempo = FakeTempo([run("Z", NOW - 5000)])
    sc.run(tempo, NOW)
    mark = sc.state()["watermark_s"] - EX.OVERLAP_S + DAY     # the day mark of the next read, if it is that long
    a, b = run("A", mark - 8), run("B", mark + 1, dur=120.0)
    wav(sc.rec, DEV_K, "kronk", "1", mark + 2)                # inside A's span (its end + 4) and B's
    tempo.runs += [a, b]
    cut = {"on": True}

    def get(url, timeout=None):
        out = tempo(url, timeout)
        if cut["on"] and "/api/search" in url and any(t["traceID"] == a["tid"].lstrip("0") for t in out["traces"]):
            clk.t += EX.READ_BUDGET_S + 1
        return out
    now = mark + 950                                          # A ended 952 s ago, B 829 s ago
    sc.run(get, now)
    assert set(sc.rows()) == {"Z", "A"} and stored(sc)["A"][0] == "certain"
    assert sc.status_json()["this_run"]["read_stopped_at_s"] == mark
    cut["on"] = False
    sc.run(get, now + 60)
    assert set(sc.rows()) == {"Z", "A", "B"}
    sc.run(get, now + 1200)
    assert oracle(sc)["A"] == oracle(sc)["B"] == ("ambiguous", None)
    assert stored(sc) == oracle(sc)


@pytest.mark.xfail(strict=True, reason="M30-e2e4 finding 1, outside voice_tracer's premise: the same through a run "
                   "Tempo shows late but before its own end + FINAL_S")
def test_job1_a_long_run_tempo_shows_late_but_before_it_is_final(sc):
    t = NOW
    a, b = run("A", t), run("B", t + 7, dur=300.0)
    wav(sc.rec, DEV_K, "kronk", "1", t + 8)
    tempo = FakeTempo([a])
    sc.run(tempo, t + 916)                                    # A final, joined alone
    tempo.runs.append(b)                                      # 943 s after B started; it ended 669 s ago
    sc.run(tempo, t + 976)
    sc.run(tempo, t + 307 + 960)
    assert stored(sc) == oracle(sc)


# ── The cost of the re-open ────────────────────────────────────────────────

def test_job1_a_late_insert_on_a_record_of_thousands_stays_well_under_a_second(sc):
    """4,000 runs in bursts of four, 300 recordings; then a run Tempo shows
    an hour late in the middle of a burst with recordings: its set is
    re-opened and joined again."""
    runs = []
    for i in range(1000):
        t = NOW - 80 * DAY + i * (80 * DAY / 1000)
        runs += [run(f"Q{i:04d}{j}", t + 8 * j) for j in range(4)]
    for k, r in enumerate(runs[-300:]):
        wav(sc.rec, DEV_K, "kronk", f"{k}", r["start"] + 7, seconds=0.05)
    tempo = FakeTempo(runs)
    assert sc.run(tempo, NOW)[0] == 0 and len(sc.rows()) == 4000
    for k in range(1, 20):                                    # the whole traces, 300 a run, until none is left
        sc.run(tempo, NOW + k)
        if not sc.status_json()["this_run"]["trace_reads"]:
            break
    seqs = {k: r["seq"] for k, r in sc.rows().items()}
    assert all(r["raw_trace"] for r in sc.rows().values())
    worst = 0.0
    for n, i in enumerate((999, 990, 950)):                   # three late runs, each inside a burst, 2 to 4 days old
        late = run(f"L{n}", runs[4 * i + 1]["start"] + 3.5)
        tempo.runs.append(late)
        t1 = time.perf_counter()
        rc, log = sc.run(tempo, NOW + 120 + 60 * n, "--check-now")
        took = time.perf_counter() - t1
        worst = max(worst, took)
        this = sc.status_json()["this_run"]
        assert rc == 0 and this["inserted"] == 1 and this["audio_reopened"] >= 4, (log, this)
    print(f"worst run with a late insert and a re-open: {worst:.3f} s")
    assert worst < 0.5, worst
    after = sc.rows()
    assert all(after[k]["seq"] == s for k, s in seqs.items())
    assert {k: (j or "none", rel) for k, (j, rel) in stored(sc).items()} == oracle(sc)


# ═══ Job 2: the lock, held by a real second process ════════════════════════

HOLDER = """
import fcntl, os, sys, time
fd = os.open(sys.argv[1], os.O_RDONLY | os.O_DIRECTORY)
fcntl.flock(fd, fcntl.LOCK_EX)
print("held", flush=True)
time.sleep(float(sys.argv[2]))
"""
# The exporter in a process of its own, with a shorter wait and a Tempo that
# is down (no socket: the getter raises).
DRIVER = """
import sys
sys.path.insert(0, sys.argv[1])
import voice_runs_export as E
E.LOCK_WAIT_S = float(sys.argv[2])
def down(url, timeout=None):
    raise ConnectionRefusedError(111, "Connection refused")
sys.exit(E.main(sys.argv[3:], get_json=down))
"""


def holder(sc, seconds):
    p = subprocess.Popen([sys.executable, "-c", HOLDER, str(sc.db.parent), str(seconds)], stdout=subprocess.PIPE,
                         text=True)
    assert p.stdout.readline().strip() == "held"
    return p


def exporter(sc, wait, *extra):
    t = time.monotonic()
    p = subprocess.run([sys.executable, "-B", "-c", DRIVER, str(EX.SCRIPTS), str(wait), *sc.args(*extra)],
                       capture_output=True, text=True, timeout=60)
    return p.returncode, p.stdout + p.stderr, time.monotonic() - t


@pytest.fixture
def rec3(sc):
    """A record of three runs, made in this process."""
    tempo = FakeTempo([run(k, NOW - 5000 + 100 * i) for i, k in enumerate("ABC")])
    assert sc.run(tempo, NOW)[0] == 0
    return sc


@pytest.mark.parametrize("what", ["--remove", "--init"])
def test_job2_another_process_lets_go_in_time_and_the_run_by_hand_does_its_work(rec3, what):
    sc = rec3
    h = holder(sc, 1.0)
    try:
        rc, log, took = exporter(sc, 10, *(("--remove", "B") if what == "--remove" else ("--init",)))
    finally:
        h.wait()
    assert rc == 0 and "Traceback" not in log, log
    assert 0.5 < took < 6, took                               # it waited for the holder, not for the whole 10 s
    if what == "--remove":
        assert sc.rows()["B"]["removed"] and "B" not in sc.published()
    else:
        assert "ordinary run" in log and sc.status_json()["last_error"].startswith("Tempo:")


@pytest.mark.parametrize("what", ["--remove", "--init", "--init, no record"])
def test_job2_another_process_never_lets_go_and_the_run_by_hand_exits_1(sc, what):
    if what != "--init, no record":
        assert sc.run(FakeTempo([run("B", NOW - 5000)]), NOW)[0] == 0
        before = (sha(sc.db), sha(sc.pub), sha(sc.status))
    else:
        sc.db.parent.mkdir(parents=True, mode=0o700)
    h = holder(sc, 30)
    try:
        rc, log, took = exporter(sc, 1.2, *(("--remove", "B") if what == "--remove" else ("--init",)))
    finally:
        h.kill()
        h.wait()
    assert rc == 1 and "FAILED" in log and "lock" in log and "Traceback" not in log, log
    assert 1.1 < took < 5, took
    if what != "--init, no record":
        assert (sha(sc.db), sha(sc.pub), sha(sc.status)) == before
        assert sc.rows()["B"]["removed"] is None
    else:
        assert not sc.db.exists() and not sc.status.exists()


def test_job2_the_ordinary_run_gives_way_to_another_process_at_once(rec3):
    sc = rec3
    before = (sha(sc.db), sha(sc.status))
    h = holder(sc, 30)
    try:
        rc, log, took = exporter(sc, 20)                      # were it to wait, it would wait 20 s
    finally:
        h.kill()
        h.wait()
    assert rc == 0 and "does nothing" in log, log
    assert took < 3, took
    assert (sha(sc.db), sha(sc.status)) == before


def test_job2_a_holder_that_dies_frees_the_lock(rec3):
    """The unit kills a run at 50 s: the kernel drops a dead process's
    flock, so the wait ends when the holder does."""
    sc = rec3
    h = holder(sc, 30)
    import threading
    threading.Timer(0.8, h.kill).start()
    rc, log, took = exporter(sc, 10, "--remove", "A")
    h.wait()
    assert rc == 0 and sc.rows()["A"]["removed"] and took < 6, (log, took)


@pytest.mark.parametrize("argv", [("--remove", "A", "--check-now"), ("--why", "invented"), ("--init", "--why", "x"),
                                  ("--remove", "A", "--init")])
def test_job2_the_refusals_come_before_the_lock_is_even_tried(rec3, argv):
    sc = rec3
    before = (sha(sc.db), sha(sc.status))
    h = holder(sc, 30)
    try:
        rc, log, took = exporter(sc, 20, *argv)
    finally:
        h.kill()
        h.wait()
    assert rc == 2 and "REFUSED" in log and took < 3, (log, took)
    assert (sha(sc.db), sha(sc.status)) == before


# ═══ Job 3: the request bound's SIGALRM ════════════════════════════════════

class Resp:
    """What urlopen returns, as far as http_get_json uses it."""

    def __init__(self, body=b'{"traces": []}', wait=0.0):
        self.body, self.wait = body, wait

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        end = time.monotonic() + self.wait
        while time.monotonic() < end:
            time.sleep(0.001)                                 # a dribble: many short waits, never one long one
        return self.body


@pytest.fixture
def alarm_watch():
    """A handler of our own as the 'previous' SIGALRM handler: an alarm
    that fires outside a request lands here and is counted (with the
    default handler it would kill the test run)."""
    stray = []
    old = signal.signal(signal.SIGALRM, lambda s, f: stray.append(time.monotonic()))
    try:
        yield stray, signal.getsignal(signal.SIGALRM)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old)


def test_job3_a_request_that_ends_at_the_very_moment_of_the_bound_leaves_nothing_behind(monkeypatch, alarm_watch):
    """600 requests whose answer comes within a millisecond or two of the
    bound, either side. Each ends with the answer or with TimeoutError, the
    previous handler back and no timer left; no alarm ever reaches the
    previous handler."""
    stray, ours = alarm_watch
    rng = random.Random(4)
    bound = 0.004
    got = {"answer": 0, "cut": 0}
    for _ in range(600):
        monkeypatch.setattr(EX.urllib.request, "urlopen",
                            lambda url, timeout=None, w=bound + rng.uniform(-0.002, 0.002): Resp(wait=w))
        try:
            assert EX.http_get_json("http://127.0.0.1:3200/api/search", bound) == {"traces": []}
            got["answer"] += 1
        except TimeoutError as e:
            assert "longer than" in str(e)
            got["cut"] += 1
        assert signal.getsignal(signal.SIGALRM) is ours
        assert signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0)
    time.sleep(0.02)
    assert stray == []
    assert got["answer"] > 50 and got["cut"] > 50, got        # both sides of the bound were really reached


def test_job3_no_timer_is_set_when_sqlite_or_a_file_is_written_even_after_requests_were_cut(sc, monkeypatch,
                                                                                              alarm_watch):
    """A whole export through the real http_get_json (urlopen faked, no
    socket). Whole-trace reads dribble past the bound and are cut; searches
    answer. Every SQLite statement and every file write of the run starts
    with no ITIMER_REAL set and the previous handler in place."""
    import json as _json
    stray, ours = alarm_watch
    fake = FakeTempo([run(k, NOW - 5000 + 100 * i) for i, k in enumerate("ABCD")])
    cut = {"traces": True}

    def urlopen(url, timeout=None):
        slow = cut["traces"] and "/api/v2/traces/" in url
        return Resp(_json.dumps(fake(url, timeout)).encode(), wait=0.2 if slow else 0.0)
    monkeypatch.setattr(EX.urllib.request, "urlopen", urlopen)
    checked = {"sql": 0, "write": 0}

    def quiet(kind):
        assert signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0), kind
        assert signal.getsignal(signal.SIGALRM) is ours, kind
        checked[kind] += 1

    class Conn(sqlite3.Connection):
        def execute(self, *a, **k):
            quiet("sql")
            return super().execute(*a, **k)

        def executescript(self, *a, **k):
            quiet("sql")
            return super().executescript(*a, **k)
    real_connect, real_write = sqlite3.connect, os.write

    def connect(*a, **k):
        k.setdefault("factory", Conn)
        return real_connect(*a, **k)

    def write(fd, data):
        quiet("write")
        return real_write(fd, data)
    monkeypatch.setattr(EX.sqlite3, "connect", connect)
    monkeypatch.setattr(EX.os, "write", write)
    rc, log = sc.run(EX.http_get_json, NOW, tempo_timeout=0.05)
    assert rc == 0, log
    this = sc.status_json()["this_run"]
    assert this["inserted"] == 4 and this["trace_read_failures"] == 4
    assert "the request took longer than 0.05 s" in sc.status_json()["last_error"]
    cut["traces"] = False
    rc, log = sc.run(EX.http_get_json, NOW + 60, tempo_timeout=0.05)
    assert rc == 0 and sc.status_json()["this_run"]["trace_reads"] == 4, log
    assert checked["sql"] > 40 and checked["write"] >= 6, checked
    time.sleep(0.1)
    assert stray == []


def test_job3_no_thread_and_no_other_alarm_anywhere_in_the_exporter():
    """The bound works on the main thread only and refuses elsewhere, so
    nothing the exporter imports may start a thread or set an alarm."""
    import re
    for name in ("voice_runs_export.py", "voice_transcripts.py", "voice_label_sheet.py", "../voice_tracer/marks.py"):
        src = (EX.SCRIPTS / name).read_text()
        code = "\n".join(line.split("#")[0] for line in src.splitlines())
        assert not re.search(r"\bThread\(|ThreadPool|concurrent\.futures|multiprocessing|asyncio|\.start_new_thread",
                             code), name
        if name != "voice_runs_export.py":
            assert not re.search(r"\bsignal\.|setitimer|\balarm\(", code), name
    src = (EX.SCRIPTS / "voice_runs_export.py").read_text()
    assert src.count("signal.setitimer(") == 2 and src.count("signal.signal(") == 2   # the one pair, in http_get_json
    assert src.count("http_get_json(") == 1                   # its definition; called only through Tempo.get
