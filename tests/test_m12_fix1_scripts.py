"""Sprint M12 fix 1, F8 and F9 (docs/sprint/M12-e2e.md must-know 6, 10, 11
and the bench note).

F8: the labelling sheet never puts another run's transcript beside an audio
file silently. The cases are the end-to-end tester's (scope 5, "How the join
is made"), with times in seconds from T0.

F9: --out inside the recordings directory is refused; the transcript listing
splits a search that hits Tempo's limit and says so, with a non-zero exit,
when even the smallest window is full; m9_lock_bench tolerates a null
text_preview; the replay's text-log check reads "true" the way the services
do.
"""
import csv
import importlib.util
import os
import sys
import wave
from pathlib import Path

import pytest

pytestmark = pytest.mark.usefixtures("no_network")

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def _load(name):
    spec = importlib.util.spec_from_file_location(f"{name}_m12fix1", SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


VT = _load("voice_transcripts")
LS = _load("voice_label_sheet")

DEV_K = "e0949788663255952ce7778975527882"
DEV_O = "ac6dd1f1c8cf1d229cda0f42224a2013"
T0 = 1_795_000_000.0
STT = "01_stt-stt.faster_whisper.wav"


def _wav(path: Path, seconds: float, mtime: float):
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setframerate(16000)
        w.setsampwidth(2)
        w.setnchannels(1)
        w.writeframes(b"\x00\x00" * int(16000 * seconds))
    os.utime(path, (mtime, mtime))


def _run(rid, start, end, device=DEV_K, heard=None, pipeline="kronk"):
    a = {"ha.pipeline_name": pipeline, "ha.pipeline_run_id": rid, "voice.stages": "stt,intent,tts",
         "voice.handled_by": "kronk", "_start_s": T0 + start, "_end_s": T0 + end, "_trace_id": rid.lower() * 8,
         "voice.transcript": heard or f"what {rid} said"}
    if device:
        a["kronk.origin.device_id"] = device
    return a


def _sheet(tmp_path, recordings: list[tuple[str, str, float, float]], runs):
    """recordings: (device or "", run dir name, seconds of audio, WAV mtime from T0)."""
    root = tmp_path / "recordings"
    for dev, name, secs, mt in recordings:
        base = root / dev if dev else root
        _wav(base / "kronk" / name / STT, secs, T0 + mt)
    out = tmp_path / "sheet"
    m = LS.build(root, out, runs, n=0, copy=False)
    rows = {Path(r["source_audio"]).parent.name: r for r in csv.DictReader(open(out / "labels.csv"))}
    return rows, m


def test_same_puck_two_runs_both_traced(tmp_path):
    rows, m = _sheet(tmp_path, [(DEV_K, "100", 3.0, 8.5), (DEV_K, "200", 1.5, 22.9)],
                     [_run("A", 0, 20), _run("B", 21, 23)])
    assert (rows["100"]["run_id"], rows["200"]["run_id"]) == ("A", "B")
    assert rows["100"]["heard"] == "what A said" and rows["200"]["heard"] == "what B said"
    assert m["recordings_ambiguous"] == 0


def test_long_kronk_answer_then_not_that(tmp_path):
    rows, _ = _sheet(tmp_path, [(DEV_K, "100", 4.0, 9.0), (DEV_K, "200", 1.5, 33.0)],
                     [_run("A", 0, 30), _run("B", 31, 33.2)])
    assert (rows["100"]["run_id"], rows["200"]["run_id"]) == ("A", "B")


def test_two_pucks_simultaneous(tmp_path):
    rows, _ = _sheet(tmp_path, [(DEV_K, "100", 2.0, 2.5), (DEV_O, "200", 2.0, 2.6)],
                     [_run("K", 0, 3, DEV_K), _run("O", 0, 3, DEV_O)])
    assert (rows["100"]["run_id"], rows["200"]["run_id"]) == ("K", "O")


@pytest.mark.parametrize("names", [("100", "200"), ("99999999999", "100000000000")],
                         ids=["sorted-in-time", "sorted-against-time"])
def test_own_trace_missing_is_ambiguous_not_wrong(tmp_path, names):
    """The tester's silent wrong join: A has no trace, A's WAV closed at
    3.0 s; B runs 4.5-6.6 s, B's WAV at 6.5 s. Both fit B. Neither row may
    carry B's transcript as if certain; the outcome no longer depends on the
    order the directories sort in (paths sort as strings: after a host
    reboot the later run can have the smaller name)."""
    a_dir, b_dir = names
    rows, m = _sheet(tmp_path, [(DEV_K, a_dir, 2.0, 3.0), (DEV_K, b_dir, 1.5, 6.5)], [_run("B", 4.5, 6.6)])
    for d in (a_dir, b_dir):
        assert rows[d]["heard"] == "" and rows[d]["run_id"] == "", rows[d]
        assert rows[d]["notes"].startswith("ambiguous join: candidate runs B")
        assert "1 other recording(s) fit the same run(s)" in rows[d]["notes"]
        assert rows[d]["device_id"] == DEV_K                   # the audio's own facts stay
    assert m["recordings_ambiguous"] == 2 and m["recordings_without_trace"] == 0


def test_own_trace_missing_but_out_of_range_is_no_trace(tmp_path):
    """B starts 2.5 s after A's WAV closed: only B's audio fits B."""
    rows, _ = _sheet(tmp_path, [(DEV_K, "100", 2.0, 3.0), (DEV_K, "200", 1.5, 7.5)], [_run("B", 5.5, 7.6)])
    assert rows["100"]["notes"] == "no trace" and rows["100"]["heard"] == ""
    assert rows["200"]["run_id"] == "B" and rows["200"]["notes"] == ""


def test_device_less_run_contested_by_another_puck(tmp_path):
    """A run whose span has no device id (a false trigger, an STT error) is
    the only candidate for its own recording and for another puck's whose
    trace is missing: both ambiguous."""
    rows, _ = _sheet(tmp_path, [(DEV_K, "100", 2.0, 2.9), (DEV_O, "200", 1.5, 2.0)],
                     [_run("R", 0, 3, device=None)])
    assert rows["100"]["notes"].startswith("ambiguous") and rows["200"]["notes"].startswith("ambiguous")
    assert rows["100"]["heard"] == rows["200"]["heard"] == ""


def test_device_less_run_with_the_other_puck_traced(tmp_path):
    """Same, but the other puck's run is traced (with its device id): the
    only pairing that uses every recording is certain, and the time-only
    join says so."""
    rows, _ = _sheet(tmp_path, [(DEV_K, "100", 2.0, 2.9), (DEV_O, "200", 1.5, 2.0)],
                     [_run("R", 0, 3, device=None), _run("O", 0, 2.5, DEV_O)])
    assert rows["100"]["run_id"] == "R" and rows["100"]["heard"] == "what R said"
    assert rows["100"]["notes"] == "joined by time and pipeline only (no device id on one side)"
    assert rows["200"]["run_id"] == "O" and rows["200"]["notes"] == ""


def test_rewritten_mtimes_at_a_household_run(tmp_path):
    """mtimes rewritten (a copy without -p, a restore) to a moment one run
    happened: every recording fits that run, none is joined to it."""
    rows, m = _sheet(tmp_path, [(DEV_K, str(n), 1.0, 10.0) for n in (100, 200, 300)], [_run("X", 9, 12)])
    assert all(r["notes"].startswith("ambiguous") and r["heard"] == "" for r in rows.values())
    assert m["recordings_ambiguous"] == 3


def test_clock_stepped_back_is_no_trace(tmp_path):
    rows, _ = _sheet(tmp_path, [(DEV_K, "100", 1.0, -10.0)], [_run("A", 0, 3)])
    assert rows["100"]["notes"] == "no trace"


def test_text_runs_are_not_candidates(tmp_path):
    rows, _ = _sheet(tmp_path, [(DEV_K, "100", 1.0, 2.0)],
                     [{**_run("T", 0, 3), "voice.stages": "intent,tts"}, _run("A", 0, 3)])
    assert rows["100"]["run_id"] == "A" and rows["100"]["notes"] == ""


# ── F9 ──────────────────────────────────────────────────────────────────────

def test_sheet_refuses_out_inside_the_recordings(tmp_path):
    root = tmp_path / "recordings"
    _wav(root / DEV_K / "kronk" / "100" / STT, 1.0, T0)
    for out in (root, root / "labels", root / DEV_K / "x"):
        with pytest.raises(LS.vt.Refused, match="inside the recordings directory"):
            LS.build(root, out, [], n=0)
        assert not (out / "labels.csv").exists()
    link = tmp_path / "via-link"
    link.symlink_to(root, target_is_directory=True)
    with pytest.raises(LS.vt.Refused, match="inside the recordings directory"):
        LS.build(root, link / "labels", [], n=0)
    assert sorted(p.name for p in root.rglob("*")) == sorted([DEV_K, "kronk", "100", STT])


def _full_then_sparse(limit):
    calls = []

    def get_json(url, timeout=30):
        from urllib.parse import parse_qs, urlparse
        q = parse_qs(urlparse(url).query)
        s, e = int(q["start"][0]), int(q["end"][0])
        calls.append((s, e))
        if e - s > 7200:                          # wide windows come back full
            return {"traces": [{"traceID": f"{i:x}", "spanSets": []} for i in range(limit)]}
        return {"traces": []}
    return get_json, calls


def test_listing_splits_a_full_search(monkeypatch, capsys):
    monkeypatch.setattr(VT, "SEARCH_LIMIT", 5)
    get_json, calls = _full_then_sparse(5)
    truncated = []
    VT.fetch_runs(T0, T0 + 86400, get_json=get_json, enrich=False, truncated=truncated)
    assert truncated == []                        # split until the windows were not full
    leaves = sorted((s, e) for s, e in calls if e - s <= 7200)
    assert len(leaves) > 1 and leaves[0][0] == int(T0) and leaves[-1][1] >= int(T0 + 86400)
    assert all(b[0] <= a[1] for a, b in zip(leaves, leaves[1:]))   # no gap between them
    assert "WARNING" not in capsys.readouterr().err


def test_listing_warns_and_exits_nonzero_when_the_smallest_window_is_full(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(VT, "SEARCH_LIMIT", 3)

    def always_full(url, timeout=30):
        return {"traces": [{"traceID": f"{i:x}", "spanSets": []} for i in range(3)]}
    rc = VT.main(["--since", "2h", "--out", str(tmp_path / "runs.csv")], now=T0, get_json=always_full)
    err = capsys.readouterr().err
    assert rc == 3
    assert "WARNING: Tempo returned its limit of 3 traces" in err
    assert "hit Tempo's search limit" in err
    assert (tmp_path / "runs.csv").exists()       # what was read is still written


def test_label_sheet_exits_nonzero_when_tempo_was_truncated(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(VT, "SEARCH_LIMIT", 3)
    monkeypatch.setattr(LS.vt, "SEARCH_LIMIT", 3)
    root = tmp_path / "recordings"
    _wav(root / DEV_K / "kronk" / "100" / STT, 1.0, T0)

    def always_full(url, timeout=30):
        return {"traces": [{"traceID": f"{i:x}", "spanSets": []} for i in range(3)]}
    rc = LS.main(["--recordings", str(root), "--out", str(tmp_path / "sheet")], now=T0 + 60, get_json=always_full)
    assert rc == 3
    assert "search limit was hit" in capsys.readouterr().err


def test_m9_lock_bench_tolerates_a_null_preview(monkeypatch):
    bench = _load("m9_lock_bench")
    import time
    now = time.time()
    evs = [{"event": "request", "rid": "r1", "ts": now - 5, "text_preview": None},
           {"event": "request", "rid": "r2", "ts": now - 5, "text_preview": "play some jazz"}]
    monkeypatch.setattr(bench, "container_events", lambda c, since: evs)
    assert bench.prod_in_flight() == ["r1: ''", "r2: 'play some jazz'"]


@pytest.mark.parametrize("value, ok", [("true", True), ("TRUE", True), ("True", True),
                                       ("false", False), ("1", False), ("", False), (None, False)])
def test_replay_text_check_reads_true_as_the_services_do(monkeypatch, value, ok):
    vr = _load("voice_replay")
    env = "PATH=/usr/bin\n" + (f"TELEMETRY_RECORD_TEXT={value}\n" if value is not None else "")
    monkeypatch.setattr(vr, "docker", lambda *a: env)
    target = {"orchestrator": "o", "tool_service": "t"}
    if ok:
        vr.require_text_logs(target)
    else:
        with pytest.raises(vr.Refused, match="TELEMETRY_RECORD_TEXT off"):
            vr.require_text_logs(target)
