"""Work item M30 step B, the last check of fix round 3
(docs/sprint/M30-e2e5.md): every inserted row re-opens its set, final or
not. The randomized join with long runs that arrive late but not yet final,
what the re-open costs a steady household, and B1 around a re-open.

Same rules as tests/test_m30_voice_runs.py: everything in tmp_path, a fake
Tempo at the function boundary, no socket, every sentence invented."""
import os
import time

import pytest

from tests.test_m16_backups import no_network  # noqa: F401  (the fixture)
from tests.test_m30_voice_runs import DEV_K, EX, NOW, FakeTempo, Scratch, sha, wav
from tests.test_m30_voice_runs_e2e4 import DAY, World, oracle, run, stored

pytestmark = pytest.mark.usefixtures("no_network")


@pytest.fixture
def sc(tmp_path):
    return Scratch(tmp_path)


def differing(sc):
    """Stored joins against voice_label_sheet.join over the finished record
    (a row with no recording and older than seven days is stored NULL)."""
    want, rows = oracle(sc), sc.rows()
    return {k: (g, want[k]) for k, g in stored(sc).items()
            if rows[k]["removed"] is None and (g[0] or "none", g[1]) != want[k]}


# ═══ The randomized join, with long runs inserted late and not yet final ═══

class LongWorld(World):
    """World, with two more ways for a live run to reach the record:

      young   Tempo shows it 30 to 880 s after it ended: late, and inserted
              before it is final, beside neighbours that may be joined;
      long    60 to 600 s long (a third of the live runs), so a run that
              started beside a joined neighbour is still young.

    Old clusters are as in World (inserted final)."""

    def cluster(self, name, at, old):
        if old:
            return super().cluster(name, at, old)
        rng = self.rng
        t = at
        for j in range(rng.randint(1, 4)):
            rid = f"{name}r{j}"
            dur = round(rng.uniform(60, 600), 3) if rng.random() < 0.35 else rng.choice([3.0, 6.0, 12.0, 40.0])
            r = run(rid, round(t, 3), dur=dur)
            self.all[rid] = r
            end = r["start"] + dur
            path = rng.choice(["on time", "young", "young", "late", "check"])
            self.visible_at[rid] = end + {"on time": rng.uniform(1, 20), "young": rng.uniform(30, 880),
                                          "late": rng.uniform(EX.FINAL_S, 3400),
                                          "check": rng.uniform(4700, 9000)}[path]
            if rng.random() < 0.8:
                self.n_rec += 1
                # inside the run, at its start (where a neighbour's span reaches) or just after it
                at_s = rng.choice([rng.uniform(-1.5, 6), rng.uniform(-1.5, dur + 4.5), dur + rng.uniform(-3, 4.5)])
                wav(self.sc.rec, DEV_K, "kronk", f"w{self.n_rec}", round(r["start"] + at_s, 3), seconds=0.05)
            # the next run: after this one, or started while this long one was still going
            nxt = end if dur < 60 or rng.random() < 0.5 else r["start"] + rng.uniform(1, 12)
            t = nxt + rng.choice([-2.0, 1.0, 3.0, 5.0, 6.5, 9.0, 30.0]) + rng.uniform(0, 1)


SEEDS = int(os.environ.get("KRONK_M30_E2E5_SEEDS", "10"))
BASE = 50_000                                             # not the seeds of test_m30_voice_runs_e2e4.py


@pytest.mark.parametrize("seed", range(BASE, BASE + SEEDS))
def test_random_arrivals_with_long_young_runs_leave_every_stored_join_as_the_join_says(sc, monkeypatch, seed):
    w = LongWorld(sc, seed, monkeypatch)
    w.play()
    assert w.differing() == {}
    rows = sc.rows()
    assert all(r["audio_tried_at"] for r in rows.values() if r["removed"] is None)
    before = ({k: r["seq"] for k, r in rows.items()}, sha(sc.pub), sc.pub.stat().st_mtime_ns)
    w.export(w.end + 120)
    assert sc.status_json()["this_run"]["published"] == 0
    assert ({k: r["seq"] for k, r in sc.rows().items()}, sha(sc.pub), sc.pub.stat().st_mtime_ns) == before


# ═══ A set that never gets joined? ═════════════════════════════════════════

def tried(sc):
    return {k: r["audio_tried_at"] for k, r in sc.rows().items()}


def test_a_steady_stream_of_ordinary_runs_reopens_and_delays_no_other_set(sc):
    """Three sets joined long ago (an ambiguous pair, a certain run, a run
    with no recording), then a new run a minute for three hours, each beside
    nothing, each with a recording. No export re-opens anything, no row
    waits, the old sets are never touched, and every stream run is joined
    by the first export after it is final."""
    old = [run("A", NOW - 5000), run("B", NOW - 4993), run("C", NOW - 4000), run("D", NOW - 3000)]
    wav(sc.rec, DEV_K, "kronk", "ab", NOW - 4992)
    wav(sc.rec, DEV_K, "kronk", "c", NOW - 3998)
    tempo = FakeTempo(old)
    assert sc.run(tempo, NOW)[0] == 0
    first = tried(sc)
    assert all(first.values())
    joins = stored(sc)
    assert [joins[k][0] for k in "ABCD"] == ["ambiguous", "ambiguous", "certain", "none"]
    stream = {}
    for m in range(1, 181):
        now = NOW + 60 * m
        rid = f"S{m:03d}"
        stream[rid] = now - 20 + 6                        # started 20 s before this export, 6 s long
        tempo.runs.append(run(rid, now - 20))
        wav(sc.rec, DEV_K, "kronk", rid, now - 18, seconds=0.05)
        rc, log = sc.run(tempo, now)
        this = sc.status_json()["this_run"]
        assert rc == 0 and this["inserted"] == 1, log
        assert "audio_reopened" not in this and "audio_waiting" not in this, this
        got = tried(sc)
        assert {k: got[k] for k in "ABCD"} == first
        for k, end in stream.items():                     # joined at the first export after it is final, not later
            assert bool(got[k]) == (end < now - EX.FINAL_S), (k, now - end)
    for k in "ABCD":
        assert stored(sc)[k] == joins[k]
    assert sum(1 for k in stream if stored(sc)[k][0] == "certain") == 180 - 15
    assert set(differing(sc)) == {k for k, e in stream.items() if e >= now - EX.FINAL_S}   # the 15 not final yet


def test_runs_that_do_chain_hold_their_set_open_only_until_the_last_of_them_is_final(sc):
    """The bound on the wait: runs back to back (each inside the last one's
    span) are one set, and each new one re-opens all of it. Here they reach
    the record final, six an export: the set is joined, re-opened and
    joined again each time, and is done by the first export after the last
    run is final. A set out of reach beside it is not touched."""
    tempo = FakeTempo([run("C", NOW - 4000)])
    wav(sc.rec, DEV_K, "kronk", "c", NOW - 3998)
    assert sc.run(tempo, NOW)[0] == 0
    c_tried = tried(sc)["C"]
    t0 = NOW + 100
    n = 40
    for i in range(n):                                    # a run every 10 s for 400 s, a recording between each pair
        tempo.runs.append(run(f"K{i:02d}", t0 + 10 * i))
        wav(sc.rec, DEV_K, "kronk", f"k{i}", t0 + 10 * i + 9, seconds=0.05)
    last_end = t0 + 10 * (n - 1) + 6
    reopened = []
    now = t0 + 960
    while now < last_end + EX.FINAL_S:
        tempo_now = FakeTempo([r for r in tempo.runs if r["start"] + r["dur"] + 900 <= now or r["attrs"][
            "ha.pipeline_run_id"] == "C"])                # each run reaches Tempo 900 s late: inserted final
        assert sc.run(tempo_now, now)[0] == 0
        reopened.append(sc.status_json()["this_run"].get("audio_reopened", 0))
        now += 60
        assert all(tried(sc).values()) and tried(sc)["C"] == c_tried and differing(sc) == {}
    assert reopened == [6 * k for k in range(len(reopened))] and len(reopened) >= 6, reopened
    assert sc.run(tempo, now)[0] == 0                     # the first export after the last of them is final
    got = tried(sc)
    assert all(got[f"K{i:02d}"] for i in range(n)) and got["C"] == c_tried
    assert differing(sc) == {}
    print(f"rows re-opened per export while the chain arrived: {reopened}")


def test_runs_back_to_back_that_arrive_on_time_wait_whole_and_are_joined_when_the_last_is_final(sc):
    """The same chain arriving as it happens, an export a minute: every new
    run re-opens the set, nothing of it is joined while one of them is
    young, and all of it is joined by the first export after the last run
    is final (900 s after the household stops, not later)."""
    tempo = FakeTempo([run("C", NOW - 4000)])
    wav(sc.rec, DEV_K, "kronk", "c", NOW - 3998)
    assert sc.run(tempo, NOW)[0] == 0
    c_tried = tried(sc)["C"]
    t0, n = NOW + 100, 40
    chain = [run(f"K{i:02d}", t0 + 10 * i) for i in range(n)]
    for i in range(n):
        wav(sc.rec, DEV_K, "kronk", f"k{i}", t0 + 10 * i + 9, seconds=0.05)
    last_end = chain[-1]["start"] + 6
    now = t0 + 30
    while True:
        tempo.runs = [tempo.runs[0]] + [r for r in chain if r["start"] + 6 + 5 <= now]
        assert sc.run(tempo, now)[0] == 0
        got = tried(sc)
        assert got["C"] == c_tried
        ks = [got[k] for k in got if k.startswith("K")]
        if now - 60 > last_end + EX.FINAL_S:
            break
        if last_end >= now - EX.FINAL_S:                  # one of them is young: none is joined
            assert not any(ks), now - t0
        else:                                             # the first export after the last is final: all are
            assert len(ks) == n and all(ks), now - t0
        now += 60
    assert differing(sc) == {}


# ═══ The cost on a record of thousands ═════════════════════════════════════

def test_one_young_insert_on_a_record_of_4000_rows_stays_under_a_second(sc):
    """4,000 runs in bursts of four, 300 recordings, all joined. Then a run
    ten minutes long that started inside the last burst reaches the record
    before it is final: the burst is re-opened and waits, the export takes
    well under a second, and so do the ones after it, until the set is
    joined again as the join says."""
    runs = []
    for i in range(1000):
        t = NOW - 1000 - 80 * DAY + (i + 1) * (80 * DAY / 1000)
        runs += [run(f"Q{i:04d}{j}", t + 8 * j) for j in range(4)]
    for k, r in enumerate(runs[-300:]):
        wav(sc.rec, DEV_K, "kronk", f"{k}", r["start"] + 7, seconds=0.05)
    tempo = FakeTempo(runs)
    assert sc.run(tempo, NOW)[0] == 0 and len(sc.rows()) == 4000
    for k in range(1, 20):                                # the whole traces, 300 a run, until none is left
        sc.run(tempo, NOW + k)
        if not sc.status_json()["this_run"]["trace_reads"]:
            break
    assert all(tried(sc).values())
    seqs = {k: r["seq"] for k, r in sc.rows().items()}
    young = run("Y", runs[-3]["start"] + 3.5, dur=600.0)  # ends 388 s before NOW
    tempo.runs.append(young)
    took = []
    t1 = time.perf_counter()
    rc, log = sc.run(tempo, NOW + 120)                    # Y ended 508 s ago: not final
    took.append(time.perf_counter() - t1)
    this = sc.status_json()["this_run"]
    assert rc == 0 and this["inserted"] == 1 and this["audio_reopened"] == 4, (log, this)
    assert sum(1 for v in tried(sc).values() if not v) == 5
    pub = (sha(sc.pub), sc.pub.stat().st_mtime_ns)
    for now in (NOW + 180, NOW + 240, NOW + 120 + 400):   # the set waits twice, then is final and joined
        t1 = time.perf_counter()
        assert sc.run(tempo, now)[0] == 0
        took.append(time.perf_counter() - t1)
        if now < NOW + 500:                               # waiting: no copy written
            assert (sha(sc.pub), sc.pub.stat().st_mtime_ns) == pub
    print(f"exports after one young insert on 4,000 rows: {[round(x, 3) for x in took]} s")
    assert max(took) < 1.0, took
    assert all(tried(sc).values())
    after = sc.rows()
    assert all(after[k]["seq"] == s for k, s in seqs.items())
    assert differing(sc) == {}


# ═══ B1 around a re-open ═══════════════════════════════════════════════════

def test_b1_a_run_with_nothing_new_changes_nothing_while_a_set_waits_and_when_it_is_joined(sc):
    """A is joined alone (certain); B, five minutes long and on the same
    recording, is inserted before it is final and re-opens A. While the set
    waits, and after it is joined, a run leaves each seq, each
    last_changed_at, and the copies' bytes and mtimes as they were. The run
    that joins it is also the run that reads B's whole trace (rule 2: once,
    when the run is final), which is something new and raises B's seq; A's
    join goes from certain to ambiguous in that run and A's seq does not
    move."""
    t = NOW
    a, b = run("A", t), run("B", t + 7, dur=300.0)
    wav(sc.rec, DEV_K, "kronk", "1", t + 8)
    tempo = FakeTempo([a])
    assert sc.run(tempo, t + 916)[0] == 0
    assert stored(sc)["A"][0] == "certain"
    tempo.runs.append(b)
    assert sc.run(tempo, t + 976)[0] == 0                 # B ended 669 s ago
    assert sc.status_json()["this_run"]["audio_reopened"] == 1

    def snap():
        rows = sc.rows()
        return ({k: (r["seq"], r["last_changed_at"]) for k, r in rows.items()},
                [(sha(p), p.stat().st_mtime_ns, p.stat().st_ino) for p in (sc.pub, sc.pub_dev)])
    before = snap()
    assert tried(sc) == {"A": None, "B": None}            # the set waits whole; A's stored answer is still there
    assert stored(sc)["A"][0] == "certain"
    for now in (t + 1040, t + 1100, t + 307 + 880):       # waiting
        assert sc.run(tempo, now)[0] == 0
        assert sc.status_json()["this_run"]["published"] == 0 and snap() == before
        assert tried(sc) == {"A": None, "B": None}
    assert sc.run(tempo, t + 307 + 960)[0] == 0           # final: joined again, both ambiguous; B's whole trace
    this = sc.status_json()["this_run"]
    assert (this["trace_reads"], this["changed"], this["audio_ambiguous"]) == (1, 1, 2), this
    assert snap()[0]["A"] == before[0]["A"] and snap()[0]["B"][0] == before[0]["B"][0] + 1
    before = snap()
    assert stored(sc) == {"A": ("ambiguous", None), "B": ("ambiguous", None)} and differing(sc) == {}
    done = tried(sc)
    assert all(done.values())
    for now in (t + 307 + 1020, t + 307 + 1080):          # done
        assert sc.run(tempo, now)[0] == 0
        this = sc.status_json()["this_run"]
        assert this["published"] == 0 and "audio_reopened" not in this and snap() == before and tried(sc) == done
