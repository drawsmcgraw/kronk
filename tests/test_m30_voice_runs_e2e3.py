"""Work item M30 step B, the narrow test of fix round 1
(docs/sprint/M30-e2e3.md): arrival orders and shapes of the audio join the
fix's own tests do not try, the misuse of --remove and --init, and what the
fix could have broken (B1, B9, the published copy's columns).

Same rules as tests/test_m30_voice_runs.py: everything in tmp_path, a fake
Tempo at the function boundary, a fake budget clock, no socket, every
sentence invented. A test marked xfail(strict=True) is a finding of the
report, by its number there."""
import io
import itertools
import os
import sqlite3
import tarfile
import time

import pytest

from tests.test_m16_backups import no_network  # noqa: F401  (the fixture)
from tests.test_m30_voice_runs import (DEV_K, EX, LS, NOW, B, FakeClock, FakeOps, FakeTempo, Scratch, house,  # noqa: F401
                                       household, restore_into, sha, vrun, wav)

pytestmark = pytest.mark.usefixtures("no_network")


@pytest.fixture
def sc(tmp_path):
    return Scratch(tmp_path)


def oracle(sc):
    """voice_label_sheet.join's own answer over every row and recording."""
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


def arrive(sc, runs, order, t, step=60, per=1):
    """The runs reach Tempo in `order`, `per` an export, an export a minute."""
    tempo = FakeTempo([])
    order = [runs[i] for i in order]
    while order:
        tempo.runs += [order.pop(0) for _ in range(min(per, len(order)))]
        rc, log = sc.run(tempo, t)
        assert rc == 0, log
        t += step
    return tempo, t


# ═══ F1: orders and shapes ═════════════════════════════════════════════════

@pytest.mark.parametrize("order", list(itertools.permutations(range(3))))
def test_f1_three_runs_on_one_recording_in_every_order(sc, order):
    base = NOW - 600
    runs = [run(f"T{i}", base + 3 * i) for i in range(3)]
    wav(sc.rec, DEV_K, "kronk", "1", base + 8)            # fits all three
    tempo, t = arrive(sc, runs, order, base + 20)
    assert all(r["audio_tried_at"] is None for r in sc.rows().values())
    sc.run(tempo, NOW + 1200)
    assert stored(sc) == oracle(sc) == {f"T{i}": ("ambiguous", None) for i in range(3)}


@pytest.mark.parametrize("order", list(itertools.permutations(range(3))))
@pytest.mark.parametrize("third_rec", [False, True])
def test_f1_a_chain_of_three_runs_and_two_or_three_recordings_in_every_order(sc, order, third_rec):
    """A and B share recording 1, B and C share recording 2. With a third
    recording only C fits, the join is certain for all three; without it
    two pairings tie."""
    base = NOW - 600
    runs = [run("A", base), run("B", base + 9), run("C", base + 18)]
    wav(sc.rec, DEV_K, "kronk", "1", base + 8)            # A (ends +6, +5) and B (starts +9, -2)
    wav(sc.rec, DEV_K, "kronk", "2", base + 17)           # B (ends +15, +5) and C (starts +18, -2)
    if third_rec:
        wav(sc.rec, DEV_K, "kronk", "3", base + 28)       # C only
    tempo, t = arrive(sc, runs, order, base + 30)
    sc.run(tempo, NOW + 1200)
    want = oracle(sc)
    assert stored(sc) == want
    assert {v[0] for v in want.values()} == ({"certain"} if third_rec else {"ambiguous"})


def test_f1_a_neighbour_that_arrives_while_the_target_waits_and_stretches_the_chain(sc):
    """A is final and waits for B (young). While it waits, C arrives beside
    B. A's answer is made with all three, when C is final."""
    a, b, c = run("A", NOW - 912), run("B", NOW - 903), run("C", NOW - 894)
    wav(sc.rec, DEV_K, "kronk", "1", NOW - 904)           # A and B
    wav(sc.rec, DEV_K, "kronk", "2", NOW - 895)           # B and C
    tempo = FakeTempo([a, b])
    sc.run(tempo, NOW)                                    # A final (ended NOW-906), B not
    assert stored(sc)["A"] == (None, None) and sc.status_json()["this_run"]["audio_waiting"] == 1
    tempo.runs.append(c)
    sc.run(tempo, NOW + 4)                                # B final now (ended NOW-897), C not (NOW-888)
    assert all(r["audio_tried_at"] is None for r in sc.rows().values())
    assert sc.status_json()["this_run"]["audio_waiting"] == 2
    sc.run(tempo, NOW + 60)
    assert stored(sc) == oracle(sc)
    assert all(r["audio_tried_at"] for r in sc.rows().values())


@pytest.mark.parametrize("when", ["before the join", "after the join"])
@pytest.mark.parametrize("order", list(itertools.permutations(range(3))))
def test_f1_a_removed_row_in_the_group(sc, when, order):
    """B of the chain is removed, before or after the join was made. The
    others' answers are the join's over all three rows (a removed row still
    fits its recording); B's own is never written after the removal."""
    base = NOW - 600
    runs = [run("A", base), run("B", base + 9), run("C", base + 18)]
    wav(sc.rec, DEV_K, "kronk", "1", base + 8)
    wav(sc.rec, DEV_K, "kronk", "2", base + 17)
    wav(sc.rec, DEV_K, "kronk", "3", base + 28)
    tempo, t = arrive(sc, runs, order, base + 30)
    if when == "before the join":
        assert sc.run(tempo, t, "--remove", "B")[0] == 0
    sc.run(tempo, NOW + 1200)
    want = oracle(sc)
    if when == "after the join":
        assert stored(sc) == want
        assert sc.run(tempo, NOW + 1260, "--remove", "B")[0] == 0
        sc.run(tempo, NOW + 1320)
        assert stored(sc) == want                         # the removal moves no join
    else:
        got = stored(sc)
        assert got["B"] == (None, None) and sc.rows()["B"]["audio_tried_at"] is None
        assert {k: got[k] for k in "AC"} == {k: want[k] for k in "AC"}


def test_f1_a_long_neighbour_holds_the_join_no_longer_than_its_own_end_plus_final(sc):
    """The longest run voice_tracer can export (840 s from its start) holds
    every row whose recording it could fit until it is final itself: a
    bound, not for ever."""
    a = run("A", NOW - 2000)
    long = run("L", NOW - 1997, dur=830.0)
    wav(sc.rec, DEV_K, "kronk", "1", NOW - 1995)
    tempo = FakeTempo([a, long])
    sc.run(tempo, NOW - 1000)                             # A final long ago; L ended NOW-1167: 167 s old
    assert sc.rows()["A"]["audio_tried_at"] is None
    sc.run(tempo, NOW - 1167 + EX.FINAL_S + 1)
    assert stored(sc) == oracle(sc) == {"A": ("ambiguous", None), "L": ("ambiguous", None)}


def test_f1_note_a_recording_that_appears_after_the_row_was_tried_is_never_joined(sc):
    """Stated, not a finding by itself: Home Assistant closes the WAV inside
    the run, so a recording is there long before the row is final. A file
    that turns up later (copied back from the NAS, say) is not looked for."""
    a = run("A", NOW - 3000)
    tempo = FakeTempo([a])
    sc.run(tempo, NOW)
    assert stored(sc)["A"] == ("none", None)
    wav(sc.rec, DEV_K, "kronk", "1", NOW - 2996)
    sc.run(tempo, NOW + 60)
    assert stored(sc)["A"] == ("none", None) and oracle(sc)["A"][0] == "certain"


def test_f1_note_a_neighbour_with_a_nonsense_end_holds_its_neighbours_until_that_end(sc):
    """A run Tempo gives a ten-day duration (voice_tracer cannot export one:
    840 s from the start) holds the join of every later row that has a
    recording until it would be final. No wrong answer is stored."""
    bad = run("BAD", NOW - 5000, dur=10 * 86400.0)
    a = run("A", NOW - 3000)
    wav(sc.rec, DEV_K, "kronk", "1", NOW - 2996)
    tempo = FakeTempo([bad, a])
    sc.run(tempo, NOW)
    sc.run(tempo, NOW + 86400)
    assert sc.rows()["A"]["audio_tried_at"] is None
    assert sc.status_json()["this_run"]["audio_waiting"] == 1
    sc.run(tempo, NOW - 5000 + 10 * 86400 + EX.FINAL_S + 1)
    assert stored(sc)["A"][0] == "ambiguous"              # BAD's span holds the recording too


def split_read(sc, monkeypatch, a, b, now):
    """A first read whose budget ends between the day that holds run a and
    the day that holds run b. Returns the fake Tempo."""
    clk = FakeClock()
    monkeypatch.setattr(EX, "clock", clk)
    tempo = FakeTempo([a, b])
    cut = {"on": True}

    def get(url, timeout=None):
        out = tempo(url, timeout)
        if cut["on"] and "/api/search" in url and any(t["traceID"] == a["tid"].lstrip("0") for t in out["traces"]):
            clk.t += EX.READ_BUDGET_S + 1                 # the day with a in it used the budget up
        return out
    rc, log = sc.run(get, now)
    assert rc == 0, log
    cut["on"] = False
    return get


@pytest.mark.xfail(strict=True, reason="M30-e2e3 finding 1: a read the budget cuts between two runs that fit one "
                                       "recording stores 'certain' for the first, for good")
def test_f1_a_first_read_cut_by_its_budget_between_two_runs_on_one_recording(sc, monkeypatch):
    """The first read goes a day per search from now - 92 days. Run A starts
    four seconds before one of those day marks and run B three seconds after
    it; one recording fits both. The budget (F8) ends after A's day. A is
    old, so it is final at once and joined alone: certain. B comes with the
    next run. voice_tracer's premise is kept: both reached Tempo days ago."""
    now = NOW
    mark = now - EX.FIRST_READ_S + 88 * 86400             # a day mark of the first read, four days ago
    a, b = run("A", mark - 4), run("B", mark + 3)
    wav(sc.rec, DEV_K, "kronk", "1", mark + 4)
    get = split_read(sc, monkeypatch, a, b, now)
    assert set(sc.rows()) == {"A"} and "the read stopped" in sc.status_json()["last_error"]
    for k in (1, 2):
        sc.run(get, now + 60 * k)
    assert set(sc.rows()) == {"A", "B"}
    assert oracle(sc) == {"A": ("ambiguous", None), "B": ("ambiguous", None)}
    assert stored(sc) == oracle(sc)


@pytest.mark.xfail(strict=True, reason="M30-e2e3 finding 1: a run the hourly check takes arrives after its "
                                       "neighbour was joined")
def test_f1_a_run_the_hourly_check_takes_beside_a_row_already_joined(sc):
    """B was not in Tempo's answer when the main read passed it (F4's
    mended stretch, a search that lagged). The hourly check takes it, an
    hour later, beside A whose join is already stored as certain."""
    a, b = run("A", NOW - 7200), run("B", NOW - 7197)
    wav(sc.rec, DEV_K, "kronk", "1", NOW - 7195)
    tempo = FakeTempo([a])
    sc.run(tempo, NOW - 3000)
    assert stored(sc)["A"][0] == "certain"
    tempo.runs.append(b)
    sc.run(tempo, NOW + 3600, "--check-now")
    assert set(sc.rows()) == {"A", "B"}
    assert stored(sc) == oracle(sc)


def test_f1_the_premise_voice_tracers_export_limit_is_under_final_s():
    """The join is made FINAL_S after a row ended, and rests on every run
    that could fit its recording being in Tempo by then. voice_tracer
    exports no run whose first span is older than EXPORT_AGE_MAX_S (from
    the start, so the run's own length is inside it), then a batch delay
    (5 s) and an exporter deadline (10 s), as tests/test_m12_fix1_tracer.py
    counts them. A run that fits A's recording starts no later than A's
    end + JOIN_BEFORE_S + JOIN_AFTER_S. The ruling on fix round 1, point 3:
    the two constants pinned against each other."""
    from voice_tracer import main as tracer
    latest = tracer.EXPORT_AGE_MAX_S + 5 + 10 + LS.JOIN_BEFORE_S + LS.JOIN_AFTER_S
    assert latest == 862 < EX.FINAL_S == 900


def test_f1_note_a_run_that_reaches_tempo_after_final_s_is_ambiguous_beside_a_stored_certain(sc):
    """Outside the premise (voice_tracer does not export such a run; this is
    Tempo's search answering late): the late run is stored ambiguous, the
    neighbour joined before it keeps its certain. The join over both says
    ambiguous for both."""
    a, b = run("A", NOW - 3000), run("B", NOW - 2997)
    wav(sc.rec, DEV_K, "kronk", "1", NOW - 2995)
    tempo = FakeTempo([a])
    sc.run(tempo, NOW - 2000)
    tempo.runs.append(b)
    sc.run(tempo, NOW - 1940)
    got = stored(sc)
    assert got["A"][0] == "certain" and got["B"] == ("ambiguous", None)
    assert oracle(sc) == {"A": ("ambiguous", None), "B": ("ambiguous", None)}


# ═══ F3: the order among the rows that failed ══════════════════════════════

def test_f3_rows_whose_trace_read_failed_are_tried_again_longest_waiting_first(sc, monkeypatch):
    """Three traces that never read, and a budget that lets one read start
    a run. Each is tried in turn; none is tried twice before the others
    were tried once (ordered by start time alone, the newest would be
    tried every run and the other two never again)."""
    clk = FakeClock()
    monkeypatch.setattr(EX, "clock", clk)
    tempo = FakeTempo(household(3, start=NOW - 3000, gap=100.0))
    tried = []

    def get(url, timeout=None):
        if "/api/v2/traces/" in url:
            tried.append(url.rsplit("/", 1)[1])
            clk.t += EX.TRACE_BUDGET_S + 1
            raise TimeoutError("timed out")
        return tempo(url, timeout)
    for k in range(9):
        sc.run(get, NOW + 60 * k)
    ids = {r["trace_id"]: k for k, r in sc.rows().items()}
    assert [ids[t] for t in tried] == ["R3", "R2", "R1"] * 3


# ═══ What the fix could have broken ════════════════════════════════════════

def test_b1_the_bookkeeping_columns_move_no_seq_and_rewrite_no_copy(sc):
    """A run joined late (it waited), a trace read that failed, and a check
    under way all write the record. None raises seq, changes
    last_changed_at, or rewrites a copy."""
    runs = household(4, start=NOW - 3000, gap=200.0)
    runs += [run("W1", NOW - 911), run("W2", NOW - 902)]
    wav(sc.rec, DEV_K, "kronk", "1", NOW - 903)
    tempo = FakeTempo(runs)
    real = tempo.__call__
    fail = {"on": True}

    def get(url, timeout=None):
        if fail["on"] and "/api/v2/traces/" in url:
            raise TimeoutError("timed out")
        return real(url, timeout)
    sc.run(get, NOW)
    seqs = {k: (r["seq"], r["last_changed_at"]) for k, r in sc.rows().items()}
    pub, mt = sha(sc.pub), os.stat(sc.pub).st_mtime_ns
    assert sum(1 for r in sc.rows().values() if r["trace_failed_at"]) == 5     # W2 is not final yet
    rc, _ = sc.run(get, NOW + 60)                         # the joins of W1, W2; trace_failed_at stamped again
    rows = sc.rows()
    assert rows["W1"]["audio_tried_at"] and rows["W2"]["audio_tried_at"] and rows["W2"]["trace_failed_at"]
    assert {k: (r["seq"], r["last_changed_at"]) for k, r in rows.items()} == seqs
    assert sha(sc.pub) == pub and os.stat(sc.pub).st_mtime_ns == mt
    assert sc.status_json()["this_run"]["published"] == 0


def test_the_published_copy_has_none_of_the_new_columns_and_schema_1(sc):
    sc.run(FakeTempo(household(2)), NOW)
    c = sqlite3.connect(f"file:{sc.pub}?mode=ro&immutable=1", uri=True)
    try:
        cols = {r[1] for r in c.execute("PRAGMA table_info(runs)")}
        tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        assert c.execute("PRAGMA user_version").fetchone()[0] == 1
    finally:
        c.close()
    assert not cols & {"audio_tried_at", "trace_failed_at", "audio_join", "audio_rel", "raw_trace", "removed"}
    assert tables == {"runs", "meta"}                     # no export_state, so no check_progress


def big_record(sc, n=4000, recs=300):
    """n runs over 80 days, six seconds each, in bursts of four a few
    seconds apart (so the join's closure has neighbours), `recs` recordings
    in the newest six days."""
    runs = []
    for i in range(n // 4):
        t = NOW - 80 * 86400 + i * (80 * 86400 / (n // 4))
        runs += [run(f"Q{i:04d}{j}", t + 8 * j) for j in range(4)]
    for k, r in enumerate(runs[-recs:]):
        wav(sc.rec, DEV_K, "kronk", f"{k}", r["start"] + 7, seconds=0.1)
    return runs


def test_b9_an_incremental_run_stays_under_a_second_on_a_record_of_thousands_with_joins_waiting(sc):
    runs = big_record(sc)
    tempo = FakeTempo(runs)
    rc, _ = sc.run(tempo, NOW)
    assert rc == 0 and len(sc.rows()) == 4000
    worst = 0.0
    for k in range(20):
        # each minute a burst of three arrives beside a recording: a join waits, another is made
        t = NOW + 60 * k
        tempo.runs += [run(f"N{k:02d}{j}", t - 50 + 8 * j) for j in range(3)]
        wav(sc.rec, DEV_K, "kronk", f"n{k}", t - 43, seconds=0.1)
        t1 = time.perf_counter()
        rc, _ = sc.run(tempo, t + 30)
        worst = max(worst, time.perf_counter() - t1)
        assert rc == 0
    print(f"worst incremental run: {worst:.3f} s")
    sc.run(tempo, NOW + 60 * 20 + EX.FINAL_S + 60)
    assert worst < 1.0, worst
    # a row older than seven days with no recording is NULL ("not tried against the NAS"), the join's "none"
    assert {k: (j or "none", rel) for k, (j, rel) in stored(sc).items()} == oracle(sc)


def dump(db):
    c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        return (c.execute("PRAGMA user_version").fetchone()[0], c.execute("PRAGMA integrity_check").fetchall(),
                list(c.iterdump()))
    finally:
        c.close()


@pytest.mark.parametrize("source", ["weekly copy", "config tar"])
def test_b8_a_schema_2_record_with_every_new_column_in_use_is_restored_whole_and_exported_on(house, tmp_path, source):
    """A record with a join made, a join waiting, a trace read that failed,
    a check under way, a removed run and a label: backed up, restored by
    the runbook's block, the same row for row, and the exporter carries on
    with it (schema 2 accepted, nothing rewritten, the next run taken)."""
    sc = Scratch(tmp_path / "exp")
    sc.db = house.voice_db
    clk = FakeClock()
    runs = household(6, start=NOW - 40 * 86400, gap=5 * 86400.0) + [run("W1", NOW - 911), run("W2", NOW - 902)]
    wav(sc.rec, DEV_K, "kronk", "1", NOW - 903)
    tempo = FakeTempo(runs)

    searches = []

    def get(url, timeout=None):
        if "/api/search" in url:
            searches.append(url)
            if len(searches) > 92:                        # the main read done: the check cannot finish in a run
                clk.t += 0.5
        if "/api/v2/traces/" in url and url.endswith(runs[0]["tid"]):
            raise TimeoutError("timed out")
        return tempo(url, timeout)
    saved = EX.clock
    EX.clock = clk
    try:
        assert sc.run(get, NOW, init=True)[0] == 0
    finally:
        EX.clock = saved
    assert sc.run(tempo, NOW + 1, "--remove", "R2", init=False)[0] == 0
    sc.person("INSERT INTO labels (run_id, correct_text, labelled_at, labelled_by) VALUES ('R3', 'invented', 'x', 'y')")
    st, rows = sc.state(), sc.rows()
    assert st["check_progress"] and rows["R1"]["trace_failed_at"] and rows["R3"]["audio_tried_at"]
    assert rows["W1"]["audio_tried_at"] is None and rows["R2"]["removed"]
    want = dump(house.voice_db)
    assert want[0] == 2
    ops = FakeOps()
    if source == "weekly copy":
        house.recordings.mkdir()
        assert B.cmd_archive(house, ops, dry_run=False) == 0, ops.notices[-1][1]
        copy = next((house.dest / "voice-runs").iterdir())
    else:
        assert B.cmd_config(house, ops, dry_run=False) == 0
        tar = next((house.dest / "config").iterdir())
        with tarfile.open(tar, "r:gz") as t:
            member = next(m for m in t.getmembers() if m.name.endswith("voice-state/voice_runs.db"))
            copy = tmp_path / "unpacked.db"
            copy.write_bytes(t.extractfile(member).read())
    home = tmp_path / "restored-home"
    restore_into(home, copy)
    restored = home / ".local" / "state" / "kronk-voice" / "voice_runs.db"
    assert dump(restored) == want and want[1] == [("ok",)]
    sc.db = restored
    seqs = {k: r["seq"] for k, r in sc.rows().items() if r["raw_trace"] or r["removed"]}   # nothing more to read
    tempo.runs.append(run("NEW", NOW - 30))
    out = io.StringIO()
    assert EX.main(sc.args(), now=NOW + 60, get_json=tempo, out=out) == 0, out.getvalue()
    after = sc.rows()
    assert {k: after[k]["seq"] for k in seqs} == seqs and len(seqs) == 6
    assert after["R1"]["raw_trace"] and after["R1"]["trace_failed_at"]      # the failed read, read now
    assert after["W1"]["audio_join"] == after["W2"]["audio_join"] == "ambiguous"     # the waiting join, made
    assert after["R2"]["heard"] is None and "R2" not in sc.published()
    assert sc.state()["check_progress"] is None and sc.status_json()["last_check"]["listed"] >= 6


# ═══ --remove and --init: misuse ═══════════════════════════════════════════

def test_remove_with_no_record_makes_nothing_and_exits_1(sc):
    rc, log = sc.run(FakeTempo([]), NOW, "--remove", "R1", init=False)
    assert rc == 1 and not sc.db.exists() and not sc.db.parent.exists()
    assert "--init" in log and sc.status_json()["ok"] is False


def test_remove_twice_keeps_the_first_mark_and_exits_0(sc):
    tempo = FakeTempo(household(2))
    sc.run(tempo, NOW)
    assert sc.run(tempo, NOW + 60, "--remove", "R1", "--why", "first")[0] == 0
    mark, pub = sc.rows()["R1"]["removed"], sha(sc.pub)
    rc, log = sc.run(tempo, NOW + 120, "--remove", "R1", "--why", "second")
    assert rc == 0 and sc.rows()["R1"]["removed"] == mark and mark.endswith("first")
    assert sha(sc.pub) == pub and set(sc.published()) == {"R2"}


@pytest.mark.parametrize("given", [("--db",), ("--db", "--publish"), ("--status",)])
def test_remove_with_scratch_paths_partly_given_is_refused(sc, given):
    full = {"--db": str(sc.db), "--publish": str(sc.pub), "--status": str(sc.status)}
    argv = [x for g in given for x in (g, full[g])] + ["--remove", "R1"]
    out = io.StringIO()
    assert EX.main(argv, now=NOW, get_json=FakeTempo([]), out=out) == 2
    assert "REFUSED" in out.getvalue() and not sc.db.exists()


@pytest.mark.xfail(strict=True, reason="M30-e2e3 finding 2: --remove while an export holds the lock removes "
                                       "nothing and exits 0")
def test_remove_while_an_export_holds_the_lock_does_not_exit_0_with_nothing_removed(sc):
    tempo = FakeTempo(household(2))
    sc.run(tempo, NOW)
    fd = EX.lock_dir(sc.db)
    try:
        rc, log = sc.run(tempo, NOW + 60, "--remove", "R1")
    finally:
        os.close(fd)
    removed = sc.rows()["R1"]["removed"] is not None
    assert removed or rc != 0, log


def test_init_when_a_record_exists_keeps_every_row_and_label(sc):
    tempo = FakeTempo(household(3))
    sc.run(tempo, NOW)
    sc.person("INSERT INTO labels (run_id, correct_text, labelled_at, labelled_by) VALUES ('R1', 'invented', 'x', 'y')")
    before = sc.rows()
    rc, log = sc.run(tempo, NOW + 60, init=True)
    assert rc == 0 and "ordinary run" in log
    after = sc.rows()
    assert {k: {c: v for c, v in r.items() if c != "last_seen_at"} for k, r in after.items()} == \
           {k: {c: v for c, v in r.items() if c != "last_seen_at"} for k, r in before.items()}
    assert sc.one("SELECT COUNT(*) FROM labels")[0] == 1


@pytest.mark.parametrize("left", ["status of a record", "status of no record", "a copy only"])
def test_a_status_file_or_a_copy_left_from_before_with_no_record(sc, left):
    tempo = FakeTempo(household(2))
    if left == "status of no record":
        sc.run(tempo, NOW, init=False)                    # writes "run --init", no record
    else:
        sc.run(tempo, NOW)
        os.unlink(sc.db)
        if left == "a copy only":
            os.unlink(sc.status), os.unlink(sc.status_dev), os.unlink(sc.pub_dev)
    rc, log = sc.run(tempo, NOW + 60, init=False)
    st = sc.status_json()
    assert rc == 1 and not sc.db.exists() and st["ok"] is False and st["ts"] == NOW + 60
    assert st["record_lost"] is (left != "status of no record")
    if left != "status of no record":
        assert "step 14" in st["last_error"]


# ═══ F8: the arithmetic, and one case ══════════════════════════════════════

def test_f8_the_budgets_and_the_unit_by_the_constants():
    unit = (EX.ROOT / "systemd" / "kronk-voice-runs-export.service").read_text()
    limit = int(unit.split("TimeoutStartSec=")[1].split()[0])
    assert EX.READ_BUDGET_S < EX.TRACE_BUDGET_S < EX.CHECK_BUDGET_S
    assert EX.CHECK_BUDGET_S + EX.TEMPO_TIMEOUT_S == 43 < limit == 50


def test_f8_every_request_at_its_whole_timeout_ends_inside_43_s(sc, monkeypatch):
    """The worst Tempo the constants allow: every request takes the whole
    5 s and then answers. The last request starts at or before 38 s."""
    clk = FakeClock()
    monkeypatch.setattr(EX, "clock", clk)
    tempo = FakeTempo(household(30, start=NOW - 30 * 86400, gap=80000.0))
    starts = []

    def get(url, timeout=None):
        starts.append(clk.t)
        clk.t += EX.TEMPO_TIMEOUT_S
        return tempo(url, timeout)
    for k in range(6):
        t0, n = clk.t, len(starts)
        rc, _ = sc.run(get, NOW + 60 * k)
        assert rc == 0
        assert max(starts[n:]) - t0 <= EX.CHECK_BUDGET_S and clk.t - t0 <= 43
