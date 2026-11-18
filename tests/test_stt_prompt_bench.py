"""scripts/stt_prompt_bench.py (sprint M13, criteria 9 to 11). No socket is
opened (`no_network`): framing runs on in-memory streams, the transcriber
is a stand-in, files go to tmp_path."""
import asyncio
import csv
import importlib.util
import json
import sys
import wave
from pathlib import Path

import numpy as np
import pytest

pytestmark = pytest.mark.usefixtures("no_network")

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def _load():
    spec = importlib.util.spec_from_file_location("stt_prompt_bench_m13", SCRIPTS / "stt_prompt_bench.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


b = _load()


class Writer:
    def __init__(self):
        self.buf = bytearray()

    def write(self, data):
        self.buf.extend(data)

    async def drain(self):
        pass


def _roundtrip(etype, data=None, payload=None):
    async def go():
        w = Writer()
        await b.write_event(w, etype, data, payload)
        r = asyncio.StreamReader()
        r.feed_data(bytes(w.buf))
        r.feed_eof()
        return await b.read_event(r), await b.read_event(r)
    return asyncio.run(go())


def test_wyoming_framing_roundtrip():
    (ev, end) = _roundtrip("audio-chunk", {"rate": 16000, "width": 2, "channels": 1}, b"\x01\x02" * 10)
    assert ev == ("audio-chunk", {"rate": 16000, "width": 2, "channels": 1}, b"\x01\x02" * 10)
    assert end is None
    (ev, _) = _roundtrip("audio-stop")
    assert ev == ("audio-stop", {}, b"")


def test_reads_inline_header_data_too():
    async def go():
        r = asyncio.StreamReader()
        r.feed_data(json.dumps({"type": "transcript", "data": {"text": "hi"}}).encode() + b"\n")
        r.feed_eof()
        return await b.read_event(r)
    assert asyncio.run(go()) == ("transcript", {"text": "hi"}, b"")


@pytest.mark.parametrize("host", ["192.168.1.14", "atlas.local", "0.0.0.0", "example.com"])
def test_loopback_only(host):
    with pytest.raises(b.BenchError, match="loopback only"):
        b.check_host(host)
    with pytest.raises(b.BenchError):
        asyncio.run(b.transcribe(host, 10300, b"\x00\x00"))
    assert b.check_host("127.0.0.1") == "127.0.0.1"


def test_normalize_and_wer():
    assert b.normalize("Play Anime Pop Radio.") == ["play", "anime", "pop", "radio"]
    assert b.normalize("  What’s   up?") == ["what's", "up"]
    assert b.normalize("Earth, Wind & Fire") == ["earth", "wind", "and", "fire"]
    assert b.edit_distance(["a", "b", "c"], ["a", "x", "c", "d"]) == 2
    assert b.edit_distance([], ["a"]) == 1


def test_command_form_error_uses_the_routing_table():
    row = {"sample_id": "s", "correct_text": "pause the music", "variant": "clean"}
    ok = b.score_row(row, " Pause the music.", 0.2, [])
    assert ok["command_error"] is False and ok["ref_route"][0] == "playback"
    bad = b.score_row(row, " Pasta.", 0.2, [])
    assert bad["command_error"] is True and bad["hyp_route"][0] == "default"
    # same rule, different call: next vs pause
    other = b.score_row(row, "skip this song", 0.2, [])
    assert other["command_error"] is True and other["hyp_route"][0] == "playback"


def test_names_added_and_hazard_hits():
    names = ["Kronk", "Mossy Den", "Ola"]
    row = {"sample_id": "s", "correct_text": "play some jazz", "variant": "clean"}
    s = b.score_row(row, "Play some jazz, Kronk.", 0.1, names)
    assert s["names_added"] == ["Kronk"]
    hz = b.score_row({"sample_id": "h", "correct_text": "", "variant": "hazard-hum"}, " Mossy Den.", 0.1, names)
    assert hz["returned_text"] is True and hz["names_hit"] == ["Mossy Den"]
    quiet = b.score_row({"sample_id": "h", "correct_text": "", "variant": "hazard-silence"}, "", 0.1, names)
    assert quiet["returned_text"] is False and quiet["names_hit"] == []


def test_prompt_names_from_the_environment_file(tmp_path):
    f = tmp_path / "p.env"
    f.write_text('# x\nWHISPER_INITIAL_PROMPT="Kronk, Den, Mossy Den."\n')
    assert b.prompt_names(f) == ["Kronk", "Den", "Mossy Den"]
    assert b.prompt_names(None) == []


def test_percentile_nearest_rank():
    v = [float(i) for i in range(1, 21)]
    assert b.percentile(v, 50) == 10.0 and b.percentile(v, 95) == 19.0
    assert b.percentile([], 50) is None


def test_summary_counts():
    rows = [
        {"variant": "clean", "latency_s": 0.2, "ref_words": 4, "word_errors": 1, "command_error": False, "names_added": []},
        {"variant": "clean", "latency_s": 0.4, "ref_words": 4, "word_errors": 0, "command_error": True, "names_added": ["X"]},
        {"variant": "hazard-hum", "latency_s": 0.1, "returned_text": True, "names_hit": ["X"]},
        {"variant": "hazard-hum", "latency_s": 0.1, "returned_text": False, "names_hit": []},
    ]
    s = b.summarize(rows)
    assert s["clean"]["wer"] == 0.125 and s["clean"]["command_errors"] == 1
    assert s["clean"]["rows_with_names_added"] == 1 and s["clean"]["p95_s"] == 0.4
    assert s["hazard-hum"]["returned_text"] == 1 and s["hazard-hum"]["returned_prompt_names"] == 1


def _wav(path, rate=16000, seconds=0.5, ch=1):
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(ch)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * int(rate * seconds) * ch)


def test_reads_an_m12_labelling_sheet_and_runs_it(tmp_path):
    """The M12 sheet's own columns (voice_label_sheet.py); unlabelled rows skipped."""
    cols = ["sample_id", "audio_file", "correct_text", "heard", "time", "satellite", "device_id",
            "pipeline", "tier", "rule", "wrong_action", "duration_s", "sample_rate", "run_id",
            "trace_id", "source_audio", "notes"]
    _wav(tmp_path / "audio" / "a.wav")
    _wav(tmp_path / "audio" / "b.wav", rate=22050, ch=2)
    with open(tmp_path / "labels.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerow({"sample_id": "20261120-kitchen-001", "audio_file": "audio/a.wav", "correct_text": "pause"})
        w.writerow({"sample_id": "20261120-kitchen-002", "audio_file": "audio/b.wav", "correct_text": "Next."})
        w.writerow({"sample_id": "20261120-kitchen-003", "audio_file": "audio/a.wav", "correct_text": ""})
    rows = b.read_sheet(tmp_path / "labels.csv")
    assert [r["sample_id"] for r in rows] == ["20261120-kitchen-001", "20261120-kitchen-002"]
    sent = []

    async def stt(pcm):
        sent.append(len(pcm))
        return (" Pause." if len(sent) == 1 else " Next."), 0.25
    out = asyncio.run(b.run_sheet(rows, stt, []))
    assert sent == [16000, 16000]           # 0.5 s at 16 kHz mono, the 22.05 kHz stereo file resampled
    assert [r["word_errors"] for r in out] == [0, 0] and not any(r["command_error"] for r in out)
    assert out[0]["variant"] == "clip"


def test_rejects_a_sheet_without_the_columns(tmp_path):
    (tmp_path / "x.csv").write_text("a,b\n1,2\n")
    with pytest.raises(b.BenchError, match="not a labelling sheet"):
        b.read_sheet(tmp_path / "x.csv")


@pytest.mark.parametrize("kind", b.HAZARDS)
def test_hazards_are_deterministic_and_the_right_length(kind):
    speech = np.sin(np.arange(8000) / 10.0) * 0.3
    x1 = b.hazard(kind, 2.0, np.random.default_rng(1), speech)
    x2 = b.hazard(kind, 2.0, np.random.default_rng(1), speech)
    assert len(x1) == 32000 and np.array_equal(x1, x2)
    assert np.max(np.abs(x1)) < 1.0
    if kind == "silence":
        assert not np.any(x1)


@pytest.mark.parametrize("variant", ["noise10", "room"])
def test_degraded_variants(variant):
    x = np.sin(2 * np.pi * 300 * np.arange(16000) / 16000) * 0.5
    y = b.degrade(x, variant, np.random.default_rng(2))
    assert len(y) >= len(x) and np.max(np.abs(y)) <= 0.9 + 1e-9
    assert not np.allclose(y[:len(x)], x)


def test_resample_length_and_tone():
    x = np.sin(2 * np.pi * 440 * np.arange(22050) / 22050)
    y = b.resample(x, 22050, 16000)
    assert len(y) == 16000
    peak = np.argmax(np.abs(np.fft.rfft(y)))
    assert abs(peak - 440) <= 1


def test_name_sentences_and_corpus(tmp_path):
    f = tmp_path / "names.tsv"
    f.write_text("station\tMoss Radio\nartist\tOla Brix\nroom\tDen\n")
    assert [t for _, t in b.name_sentences(f)] == ["Play Moss Radio.", "Play some Ola Brix.", "Play music in the Den."]
    (tmp_path / "bad.tsv").write_text("verb\tpause\n")
    with pytest.raises(b.BenchError):
        b.name_sentences(tmp_path / "bad.tsv")
    c = tmp_path / "c.jsonl"
    c.write_text(json.dumps({"id": "a", "text": " Pause  the music. "}) + "\n"
                 + json.dumps({"id": "b", "text": "pause the music."}) + "\n")
    assert b.corpus_sentences(c) == [("corpus:a", "Pause the music.")]


def test_compare_tables_and_differences():
    a = {"label": "A", "summary": {"clean": {"n": 1, "wer": 0.1, "command_errors": 0, "rows_with_names_added": 0,
                                              "p50_s": 0.2, "p95_s": 0.3},
                                    "hazard-hum": {"n": 2, "returned_text": 1, "returned_prompt_names": 0, "p50_s": 0.1}},
         "rows": [{"sample_id": "x", "hyp": "a"}, {"sample_id": "y", "hyp": "b"}]}
    c = {"label": "C", "summary": a["summary"], "rows": [{"sample_id": "x", "hyp": "a"}, {"sample_id": "y", "hyp": "B"}]}
    t = b.compare_tables([a, c])
    assert "| clean | A | 1 | 10.0 % | 0 | 0 | 0.200 | 0.300 |" in t and "| hum | C | 2 | 1 | 0 |" in t
    assert b.differing_rows(a, c) == [("y", "b", "B")]


def test_render_refuses_a_voice_piper_would_download(tmp_path, monkeypatch):
    async def voices(host, port):
        return {"en_US-lessac-medium": True, "en_US-new-voice": False}
    monkeypatch.setattr(b, "piper_voices", voices)
    with pytest.raises(b.BenchError, match="would be downloaded"):
        asyncio.run(b.render(tmp_path, [("x", "hello")], ["en_US-new-voice"], ("127.0.0.1", 10200), 1, 1))
    assert not (tmp_path / "labels.csv").exists()


def test_render_writes_the_sheet(tmp_path, monkeypatch):
    async def voices(host, port):
        return {"v": True}

    async def synth(host, port, text, voice):
        return (np.sin(np.arange(22050) / 5.0) * 8000).astype("<i2").tobytes(), 22050
    monkeypatch.setattr(b, "piper_voices", voices)
    monkeypatch.setattr(b, "synthesize", synth)
    n = asyncio.run(b.render(tmp_path, [("corpus:a", "Pause.")], ["v"], ("127.0.0.1", 10200), 3, 1))
    assert n == 3 + len(b.HAZARDS)
    rows = b.read_sheet(tmp_path / "labels.csv")
    assert {r["variant"] for r in rows} == {"clean", "noise10", "room"} | {f"hazard-{k}" for k in b.HAZARDS}
    with wave.open(rows[0]["_path"]) as w:
        assert w.getframerate() == 16000 and w.getnchannels() == 1
    with pytest.raises(b.BenchError, match="exists"):
        asyncio.run(b.render(tmp_path, [("corpus:a", "Pause.")], ["v"], ("127.0.0.1", 10200), 3, 1))
