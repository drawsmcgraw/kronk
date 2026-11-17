"""Sprint M12: the transcript listing (scripts/voice_transcripts.py) and the
labelling sheet (scripts/voice_label_sheet.py), against a stubbed Tempo and
a recordings directory in Home Assistant's layout built in a temp dir."""
import csv
import hashlib
import importlib.util
import io
import json
import os
import sys
import wave
from pathlib import Path

import pytest

pytestmark = pytest.mark.usefixtures("no_network")

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def _load(name):
    spec = importlib.util.spec_from_file_location(f"{name}_m12", SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod            # dataclasses look their module up
    spec.loader.exec_module(mod)
    return mod


VT = _load("voice_transcripts")
LS = _load("voice_label_sheet")

KITCHEN = "assist_satellite.home_assistant_voice_0ac919_assist_satellite"
OFFICE = "assist_satellite.satellite1_d8d1bc_assist_satellite"
DEV_K = "e0949788663255952ce7778975527882"
DEV_O = "ac6dd1f1c8cf1d229cda0f42224a2013"
T0 = 1_795_000_000.0      # 2026-11-18 (UTC)


def _attr(k, v):
    if isinstance(v, bool):
        return {"key": k, "value": {"boolValue": v}}
    if isinstance(v, int):
        return {"key": k, "value": {"intValue": str(v)}}
    if isinstance(v, float):
        return {"key": k, "value": {"doubleValue": v}}
    if isinstance(v, list):
        return {"key": k, "value": {"arrayValue": {"values": [{"stringValue": x} for x in v]}}}
    return {"key": k, "value": {"stringValue": v}}


def _trace(tid, start, dur, service="kronk-voice-tracer", **attrs):
    a = [_attr(k.replace("__", "."), v) for k, v in attrs.items()]
    a.append(_attr("service.name", service))
    return {"traceID": tid.lstrip("0"), "rootServiceName": service, "startTimeUnixNano": str(int(start * 1e9)),
            "spanSets": [{"spans": [{"spanID": "1", "startTimeUnixNano": str(int(start * 1e9)),
                                     "durationNanos": str(int(dur * 1e9)), "attributes": a}]}]}


def _runs():
    """Four voice runs: a Kronk play corrected by a stop, the stop, a dev
    run, and a run traced before M12 (rule only in the Kronk trace)."""
    return [
        _trace("0a" * 16, T0, 8.0, ha__pipeline_name="kronk", ha__pipeline_run_id="R1",
               ha__satellite_id=KITCHEN, kronk__origin__device_id=DEV_K, voice__stages="stt,intent,tts",
               voice__transcript="Play French Dinner Radio.", voice__transcript_chars=25,
               voice__handled_by="kronk", kronk__rule="music", kronk__tools=["play_music"],
               voice__speech="Now playing French Dinner Radio.", voice__corrected_by="stop",
               voice__corrected_after_s=12.5, voice__corrected_by_run_id="R2",
               voice__mark_basis="rule_and_text", kronk__trace_id="ab" * 16),
        _trace("0b" * 16, T0 + 20.5, 2.5, ha__pipeline_name="kronk", ha__pipeline_run_id="R2",
               ha__satellite_id=KITCHEN, kronk__origin__device_id=DEV_K, voice__stages="stt,intent,tts",
               voice__transcript="Stop.", voice__handled_by="ha_local", voice__correction_kind="stop",
               voice__correction_of_run_id="R1"),
        _trace("0c" * 16, T0 + 30, 3.0, service="kronk-voice-tracer-dev", ha__pipeline_name="kronk-dev",
               ha__pipeline_run_id="R3", ha__satellite_id=OFFICE, voice__stages="stt,intent,tts",
               voice__transcript="play some jazz in the office", voice__handled_by="kronk"),
        _trace("0d" * 16, T0 + 3600, 6.0, ha__pipeline_name="kronk", ha__pipeline_run_id="R4",
               ha__satellite_id=OFFICE, kronk__origin__device_id=DEV_O, voice__stages="stt,intent,tts",
               voice__transcript_chars=11, voice__handled_by="kronk", kronk__trace_id="cd" * 16),
    ]


def _kronk_trace(rule, action=None, tools=()):
    spans = [{"name": "routing.decide", "startTimeUnixNano": "1", "attributes":
              [_attr("kronk.rule", rule)] + ([_attr("kronk.direct_action", action)] if action else [])}]
    spans += [{"name": f"tool.{t}", "startTimeUnixNano": "5", "attributes": [_attr("kronk.tool", t)]} for t in tools]
    return {"trace": {"resourceSpans": [{"scopeSpans": [{"spans": spans}]}]}}


class Tempo:
    def __init__(self, traces=None):
        self.traces = traces if traces is not None else _runs()
        self.urls = []

    def __call__(self, url, timeout=30):
        self.urls.append(url)
        assert url.startswith("http://127.0.0.1:3200/")
        if "/api/search?" in url:
            from urllib.parse import parse_qs, urlparse
            q = parse_qs(urlparse(url).query)
            s, e = int(q["start"][0]), int(q["end"][0])
            return {"traces": [t for t in self.traces if s <= int(t["startTimeUnixNano"]) / 1e9 <= e]}
        if url.endswith("/api/v2/traces/" + "cd" * 16):
            return _kronk_trace("playback", "pause", ("control_music",))
        raise AssertionError(url)


# ── voice_transcripts ───────────────────────────────────────────────────────

def test_listing_rows_dev_excluded_and_enriched():
    tempo = Tempo()
    runs = VT.fetch_runs(T0 - 60, T0 + 7200, get_json=tempo)
    rows = [VT.to_row(a) for a in runs]
    assert [r["run_id"] for r in rows] == ["R1", "R2", "R4"]            # R3 is dev
    r1 = rows[0]
    assert (r1["heard"], r1["tier"], r1["rule"], r1["tools"], r1["reply"]) == (
        "Play French Dinner Radio.", "kronk", "music", "play_music", "Now playing French Dinner Radio.")
    assert (r1["wrong_action"], r1["wrong_action_after_s"], r1["corrected_by_run"]) == ("stop", 12.5, "R2")
    assert rows[1]["is_correction_of"] == "R1"
    r4 = rows[2]                                     # traced before M12: rule from the Kronk trace
    assert (r4["rule"], r4["direct_action"], r4["tools"], r4["heard"], r4["heard_chars"]) == (
        "playback", "pause", "control_music", "", 11)
    assert r1["trace_id"] == "0a" * 16                 # leading zero restored
    with_dev = VT.fetch_runs(T0 - 60, T0 + 7200, include_dev=True, get_json=Tempo())
    assert "R3" in [a["ha.pipeline_run_id"] for a in with_dev]


def test_one_search_per_day():
    tempo = Tempo()
    VT.fetch_runs(T0, T0 + 3 * 86400, get_json=tempo, enrich=False)
    assert sum("/api/search?" in u for u in tempo.urls) == 3


def test_main_writes_only_the_out_file(tmp_path, capsys):
    out = tmp_path / "runs.csv"
    before = set(os.listdir(tmp_path))
    assert VT.main(["--since", "2026-11-17", "--until", "2026-11-20", "--out", str(out)],
                   get_json=Tempo()) == 0
    assert set(os.listdir(tmp_path)) - before == {"runs.csv"}
    rows = list(csv.DictReader(open(out)))
    assert [r["run_id"] for r in rows] == ["R1", "R2", "R4"]
    assert VT.main(["--since", "2026-11-17", "--until", "2026-11-20", "--wrong-only", "--out", str(out)],
                   get_json=Tempo()) == 0
    assert [r["run_id"] for r in csv.DictReader(open(out))] == ["R1"]


def test_main_stdout_jsonl():
    buf = io.StringIO()
    assert VT.main(["--since", "2026-11-17", "--until", "2026-11-20", "--format", "jsonl"],
                   get_json=Tempo(), stdout=buf) == 0
    assert [json.loads(l)["run_id"] for l in buf.getvalue().splitlines()] == ["R1", "R2", "R4"]


def test_refuses_repo_paths_and_non_loopback_tempo(capsys):
    assert VT.main(["--out", str(SCRIPTS.parent / "runs.csv")], get_json=Tempo()) == 2
    assert not (SCRIPTS.parent / "runs.csv").exists()
    assert VT.main(["--tempo", "http://192.0.2.1:3200"], get_json=Tempo()) == 2
    assert "REFUSED" in capsys.readouterr().err


@pytest.mark.parametrize("s, want", [("7d", T0 - 7 * 86400), ("24h", T0 - 86400), ("90m", T0 - 5400),
                                     ("2026-11-18T00:00:00+00:00", 1794960000.0)])
def test_parse_when(s, want):
    assert VT.parse_when(s, T0) == want


# ── voice_label_sheet ───────────────────────────────────────────────────────

def _wav(path: Path, seconds: float, mtime: float):
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setframerate(16000)
        w.setsampwidth(2)
        w.setnchannels(1)
        w.writeframes(b"\x00\x00" * int(16000 * seconds))
    os.utime(path, (mtime, mtime))


@pytest.fixture
def recdir(tmp_path):
    """HA's layout: <device>/<pipeline>/<monotonic ns>/01_stt-<engine>.wav."""
    root = tmp_path / "recordings"
    _wav(root / DEV_K / "kronk" / "1000001" / "01_stt-stt.faster_whisper.wav", 3.5, T0 + 4.0)   # R1
    _wav(root / DEV_K / "kronk" / "1000002" / "01_stt-stt.faster_whisper.wav", 1.0, T0 + 21.5)  # R2
    _wav(root / DEV_O / "kronk-dev" / "1000003" / "01_stt-stt.faster_whisper.wav", 2.0, T0 + 31)  # R3, dev
    _wav(root / DEV_K / "kronk" / "1000004" / "01_stt-stt.faster_whisper.wav", 2.0, T0 + 900)   # no trace
    (root / DEV_K / "kronk" / "1000005").mkdir(parents=True)                                     # died early
    return root


def _fingerprint(root: Path):
    return {str(p): (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime, p.stat().st_mode)
            for p in sorted(root.rglob("*")) if p.is_file()}


def test_sheet_rows_join_copy_and_never_touch_recordings(recdir, tmp_path):
    before = _fingerprint(recdir)
    runs = VT.fetch_runs(T0 - 60, T0 + 7200, include_dev=True, get_json=Tempo())
    out = tmp_path / "labels"
    m = LS.build(recdir, out, runs, n=0)
    assert _fingerprint(recdir) == before                    # nothing modified, nothing removed
    rows = list(csv.DictReader(open(out / "labels.csv")))
    assert [r["run_id"] for r in rows] == ["R1", "R2", ""]   # dev excluded; one without a trace
    assert rows[0]["heard"] == "Play French Dinner Radio." and rows[0]["correct_text"] == ""
    assert rows[0]["wrong_action"] == "stop" and rows[0]["tier"] == "kronk"
    assert rows[0]["duration_s"] == "3.5" and rows[0]["sample_rate"] == "16000"
    assert rows[2]["notes"] == "no trace" and rows[2]["device_id"] == DEV_K
    for r in rows:
        copied = out / r["audio_file"]
        assert copied.read_bytes() == Path(r["source_audio"]).read_bytes()
    assert m["recordings_found"] == 3 and m["recordings_without_trace"] == 1
    assert m["voice_runs_without_audio"] == 2                  # R3 (dev audio filtered) and R4
    assert json.loads((out / "manifest.json").read_text())["sampled"] == 3


def test_sheet_refuses_to_overwrite_labels_and_repo_paths(recdir, tmp_path):
    out = tmp_path / "labels"
    LS.build(recdir, out, [], n=0)
    with pytest.raises(LS.vt.Refused, match="may hold labels"):
        LS.build(recdir, out, [], n=0)
    with pytest.raises(LS.vt.Refused, match="inside the repo"):
        LS.build(recdir, SCRIPTS.parent / "docs" / "labels", [], n=0)
    assert not (SCRIPTS.parent / "docs" / "labels").exists()


def test_sheet_no_copy_points_at_the_source(recdir, tmp_path):
    out = tmp_path / "labels"
    LS.build(recdir, out, [], n=0, copy=False)
    assert not (out / "audio").exists()
    rows = list(csv.DictReader(open(out / "labels.csv")))
    assert all(r["audio_file"] == r["source_audio"] for r in rows)


def test_sample_spreads_over_days_satellites_and_tiers(tmp_path):
    root = tmp_path / "recordings"
    runs = []
    n = 0
    for day in range(3):
        for dev, sat in ((DEV_K, KITCHEN), (DEV_O, OFFICE)):
            for k in range(6):
                n += 1
                t = T0 + day * 86400 + k * 600 + (0 if sat == KITCHEN else 300)
                _wav(root / dev / "kronk" / str(n) / "01_stt-stt.x.wav", 1.0, t + 2)
                runs.append({"ha.pipeline_name": "kronk", "ha.pipeline_run_id": f"R{n}",
                             "ha.satellite_id": sat, "kronk.origin.device_id": dev,
                             "voice.stages": "stt,intent,tts",
                             "voice.handled_by": "kronk" if k % 2 else "ha_local",
                             "_start_s": t, "_end_s": t + 4, "_trace_id": f"{n:032x}"})
    out = tmp_path / "s"
    LS.build(root, out, runs, n=12, seed=3)
    rows = list(csv.DictReader(open(out / "labels.csv")))
    assert len(rows) == 12
    groups = {(r["time"][:10], r["satellite"], r["tier"]) for r in rows}
    assert len(groups) == 12                                  # 3 days x 2 pucks x 2 tiers, one each
    again = tmp_path / "s2"
    LS.build(root, again, runs, n=12, seed=3)
    assert [r["run_id"] for r in csv.DictReader(open(again / "labels.csv"))] == [r["run_id"] for r in rows]


def test_main_end_to_end(recdir, tmp_path, capsys):
    out = tmp_path / "sheet"
    assert LS.main(["--recordings", str(recdir), "--out", str(out), "--sample", "200"],
                   now=T0 + 7200, get_json=Tempo()) == 0
    assert (out / "labels.csv").exists()
    assert "3 of 3 recordings" in capsys.readouterr().err
