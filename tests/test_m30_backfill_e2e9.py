"""Work item M30 step D, the narrow test of fix round 1 (docs/sprint/M30-e2e9.md).

Random houses against the backfill's own plan() and apply(), the oracle
being which line the house gave which run. Where every run that logged a
line has a voice.stt span, no run is ever filled with another's line. What
is left is a line whose owner has no window (a removed row, a row with no
span, a run the record does not hold): three strict xfail tests keep it.

In memory and tmp_path, invented sentences only, no journalctl, no socket."""
import collections
import json
import random
import sqlite3

import pytest

from tests import test_m30_backfill as D
from tests import test_m30_backfill_e2e8 as E
from tests import test_m30_voice_runs as M30

pytestmark = pytest.mark.usefixtures("no_network")
sc = M30.sc

BF, EX = D.BF, D.EX
HH, TB, GAP, NOW = D.HH, D.TB, D.GAP, D.NOW
T0 = 1_790_000_000.0
LENS = [12, 12, 12, 15, 15, 20]         # few lengths, so equal lengths are common
NO_WINDOW = "A line whose owner has no window fills the neighbour (M30-e2e9.md, B1)"


def trace(end, span):
    spans = [{"name": "voice.wake", "startTimeUnixNano": str(int((end - 3) * 1e9)),
              "endTimeUnixNano": str(int((end - 2) * 1e9))}]
    if span:
        spans.append({"name": "voice.stt", "startTimeUnixNano": str(int((end - 1.5) * 1e9)),
                      "endTimeUnixNano": str(int(end * 1e9))})
    return json.dumps({"resourceSpans": [{"scopeSpans": [{"spans": spans}]}]})


def line(k, n):
    s = f" inv{k:04d}"
    return s + "x" * (n - len(s))


def random_house(rng, every_owner_has_a_span):
    """Two to seven runs at gaps of 0 to 5 s, one or both units. Each run
    with or without its own line (logged within 50 ms of its span's end),
    with or without a stored count, with or without a span, empty or with
    words or removed. Returns (rows, journal, {run_id: its own words})."""
    t, rows, lines, own = T0, [], collections.defaultdict(list), {}
    mixed = rng.random() < 0.2
    for i in range(rng.randint(2, 7)):
        t += rng.choice([0.0, rng.uniform(0, 1), rng.uniform(0, 5), rng.uniform(0, 5)])
        rid, n = f"R-{i}", rng.choice(LENS)
        is_dev = 1 if mixed and rng.random() < 0.3 else 0
        kind = rng.choices(["empty", "words", "removed"], [0.6, 0.25, 0.15])[0]
        span = rng.random() < 0.8
        has_line = rng.random() < 0.7
        if every_owner_has_a_span and (kind == "removed" or not span):
            has_line = False
        if has_line:
            lines[BF.UNIT_OF[is_dev]].append(BF.Transcript(t + rng.uniform(0, 0.05), line(i, n)))
            own[rid] = line(i, n).strip()
        rows.append(dict(
            run_id=rid, trace_id="t" + rid, start_s=t - 3, end_s=t + 4, local_time="x", local_day="x", service="s",
            is_dev=is_dev, stages="wake_word,stt", heard=line(i, n).strip() if kind == "words" else None,
            heard_chars=rng.choices([n, None, 0], [0.75, 0.2, 0.05])[0],
            raw_trace=None if kind == "removed" else trace(t, span),
            removed="2026-11-20T00:00:00 test" if kind == "removed" else None,
            first_seen_at="x", last_seen_at="x", last_changed_at="x", seq=i + 1))
    j = BF.Journal("fixture")
    for u in (BF.HOUSEHOLD_UNIT, BF.DEV_UNIT):
        j.first[u], j.last[u] = T0 - 3600, t + 3600
        j.lines[u] = sorted(lines[u], key=lambda x: x.t)
    return rows, j, own


def backfilled(rows, j):
    """{run_id: the words apply() stored} for the rows whose heard changed."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(EX.DDL)
        for r in rows:
            conn.execute(f"INSERT INTO runs ({', '.join(r)}) VALUES ({', '.join('?' * len(r))})", list(r.values()))
        BF.apply(conn, BF.plan(conn, j, {"off": True}, None, T0 + 86400), T0 + 86400)
        was = {r["run_id"]: r["heard"] for r in rows}
        return {r["run_id"]: r["heard"] for r in conn.execute("SELECT run_id, heard FROM runs")
                if r["heard"] != was[r["run_id"]]}
    finally:
        conn.close()


def test_no_run_is_filled_with_another_runs_line_where_every_owner_has_a_span():
    """3,000 houses. Every fill is the run's own line, and there are fills."""
    right, wrong = 0, []
    for seed in range(3000):
        rows, j, own = random_house(random.Random(seed), every_owner_has_a_span=True)
        for rid, words in backfilled(rows, j).items():
            if words == own.get(rid):
                right += 1
            else:
                wrong.append((seed, rid))
    assert not wrong, wrong[:5]
    assert right > 500, right


def test_the_random_houses_find_the_fill_whose_owner_has_no_window():
    """The same houses with owners of every kind: the wrong fills there
    are all a line of a removed row or a row with no span. If this stops
    finding any, the three xfail tests below should be passing."""
    owners = collections.Counter()
    for seed in range(3000):
        rows, j, own = random_house(random.Random(seed), every_owner_has_a_span=False)
        by_words = {w: rid for rid, w in own.items()}
        meta = {r["run_id"]: r for r in rows}
        for rid, words in backfilled(rows, j).items():
            if words != own.get(rid):
                o = meta[by_words[words]]
                owners["removed" if o["removed"] else "a span" if BF.stt_window(o) else "no span"] += 1
    assert owners["a span"] == 0, owners


def pair(j, **b_kw):
    """A on the kitchen puck: empty, a count, a span, and its own line lost.
    B on the living-room puck half a second later: its line, the length A
    stores, at B's span's end, which is inside A's window."""
    text = j.say(HH, TB + GAP + 0.5 + D.STT_END + 0.05, D.said(2))
    return text, [D.early("R-A", 1, len(text)), E.other_puck("R-B", TB + GAP + 0.5, len(text), **b_kw)]


@pytest.mark.xfail(strict=True, reason=NO_WINDOW)
def test_a_removed_runs_line_is_not_given_to_its_neighbour(sc, tmp_path):
    j = D.Journal(tmp_path / "w.json")
    text, runs = pair(j)
    tempo = M30.FakeTempo(runs)
    sc.run(tempo, NOW)
    rc, log = sc.run(tempo, NOW + 10, "--remove", "R-B")
    assert rc == 0, log
    rc, log = D.bf(sc, j.write(), "--apply", "--no-audio")
    assert rc == 0, log
    assert sc.rows()["R-A"]["heard"] is None, E.fills(log)


@pytest.mark.xfail(strict=True, reason=NO_WINDOW)
def test_the_line_of_a_run_with_no_span_is_not_given_to_its_neighbour(sc, tmp_path):
    j = D.Journal(tmp_path / "w.json")
    text, runs = pair(j, children=False)
    sc.run(M30.FakeTempo(runs), NOW)
    rc, log = D.bf(sc, j.write(), "--apply", "--no-audio")
    assert rc == 0, log
    assert sc.rows()["R-A"]["heard"] is None, E.fills(log)


@pytest.mark.xfail(strict=True, reason=NO_WINDOW + "; no rule on the record can see this one")
def test_the_line_of_a_run_the_record_does_not_hold_is_not_given_to_its_neighbour(sc, tmp_path):
    j = D.Journal(tmp_path / "w.json")
    text, runs = pair(j)
    sc.run(M30.FakeTempo(runs[:1]), NOW)            # B was never in Tempo
    rc, log = D.bf(sc, j.write(), "--apply", "--no-audio")
    assert rc == 0, log
    assert sc.rows()["R-A"]["heard"] is None, E.fills(log)
