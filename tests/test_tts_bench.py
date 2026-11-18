"""scripts/tts_bench.py (sprint M20, criteria 3 and 4). No socket is opened
(`no_network`): the Wyoming service is faked in memory, the transcriber is a
stand-in, files go to tmp_path."""
import asyncio
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
    spec = importlib.util.spec_from_file_location("tts_bench_m20", SCRIPTS / "tts_bench.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


b = _load()
SENTENCES = b.load_sentences(b.DEFAULT_SENTENCES)["sentences"]
BY_ID = {s["id"]: s for s in SENTENCES}


# ── a fake Wyoming TTS service ──────────────────────────────────────────────

def frame(etype, data=None, payload=None):
    header = {"type": etype}
    d = json.dumps(data).encode() if data else b""
    if d:
        header["data_length"] = len(d)
    if payload:
        header["payload_length"] = len(payload)
    return json.dumps(header).encode() + b"\n" + d + (payload or b"")


class FakeService:
    """Answers each connection from what the client writes. `script(kind)`
    returns the frames to send for a `describe` or a synthesis, and whether
    to close the connection after them."""

    def __init__(self, streaming=True, rate=22050, samples=(4410, 4410), mode="ok"):
        self.streaming, self.rate, self.samples, self.mode = streaming, rate, samples, mode
        self.requests = []            # list of event-type lists, one per connection

    def audio(self):
        out = [frame("audio-start", {"rate": self.rate, "width": 2, "channels": 1})]
        for n in self.samples:
            out.append(frame("audio-chunk", {"rate": self.rate, "width": 2, "channels": 1},
                             (b"\x10\x00" * n)))
        out.append(frame("audio-stop"))
        return out

    def reply(self, etype):
        m = self.mode
        if etype == "describe":
            return [frame("info", {"tts": [{"name": "fake", "supports_synthesize_streaming": self.streaming,
                                            "voices": [{"name": "v1"}]}]})], False
        if m == "error":
            return [frame("error", {"text": "voice not found"})], False
        if m == "empty":
            body = [frame("audio-start", {"rate": self.rate, "width": 2, "channels": 1}), frame("audio-stop")]
        elif m == "stops":
            body = self.audio()[:2]                       # start and one chunk, then the line drops
            return body, True
        elif m == "silent":
            return [], False                              # never answers
        elif m == "wide":
            body = [frame("audio-start", {"rate": self.rate, "width": 4, "channels": 1}),
                    frame("audio-chunk", {"rate": self.rate, "width": 4, "channels": 1}, b"\0" * 8),
                    frame("audio-stop")]
        else:
            body = self.audio()
        if self.streaming and m != "eof":
            body.append(frame("synthesize-stopped"))
        return body, m == "eof"

    def connect(self):
        svc = self

        async def go():
            reader = asyncio.StreamReader()
            events = []
            svc.requests.append(events)

            class W:
                def write(self, buf):
                    header = json.loads(buf.split(b"\n", 1)[0])
                    events.append((header["type"], buf))
                    t = header["type"]
                    trigger = (t == "describe" or (t == "synthesize" and not svc.streaming)
                               or t == "synthesize-stop")
                    if trigger:
                        frames, close = svc.reply(t)
                        for f in frames:
                            reader.feed_data(f)
                        if close:
                            reader.feed_eof()

                async def drain(self):
                    pass

                def close(self):
                    pass
            return reader, W()
        return go


class Clock:
    def __init__(self, step=0.1):
        self.t, self.step = 0.0, step

    def __call__(self):
        self.t += self.step
        return self.t


def synth(svc, sid="c01", **kw):
    kw.setdefault("voice", "v1")
    kw.setdefault("streaming", svc.streaming)
    kw.setdefault("timeout", 2)
    return asyncio.run(b.synth_once(svc.connect(), BY_ID[sid], 1, **kw))


# ── the sentence set ────────────────────────────────────────────────────────

def test_sentence_set_covers_the_plan():
    cats = {}
    for s in SENTENCES:
        cats.setdefault(s["category"], []).append(s)
    assert 18 <= len(SENTENCES) <= 24
    assert set(cats) == {"confirm", "music", "weather2", "answer3", "long", "numbers"}
    assert all(len(b.split_sentences(s["text"])) == 2 for s in cats["weather2"])
    assert all(len(b.split_sentences(s["text"])) == 3 for s in cats["answer3"])
    assert all(len(b.split_sentences(s["text"])) == 1 for s in cats["confirm"])
    assert max(len(s["text"]) for s in cats["long"]) > 300
    assert any(ch.isdigit() for s in cats["numbers"] for ch in s["text"])


def test_split_sentences_joins_back():
    for s in SENTENCES:
        assert "".join(b.split_sentences(s["text"])) == s["text"]
    assert b.split_sentences("Rain after 3 p.m., with a high of 61. Next.") == [
        "Rain after 3 p.m., with a high of 61.", " Next."]


# ── timing arithmetic ───────────────────────────────────────────────────────

def test_timing_of_a_plain_request():
    svc = FakeService(streaming=False, rate=22050, samples=(11025, 11025))
    row, pcm = synth(svc, clock=Clock(0.1))
    # clock reads: t0 0.1; the sender's `synthesize` written 0.2 (the reader
    # waits for data, so the sender runs first); first chunk 0.3; end 0.4
    assert row["ok"] and row["failure"] is None
    assert row["last_chunk_s"] == pytest.approx(0.1)
    assert row["ttfa_s"] == pytest.approx(0.2)
    assert row["total_s"] == pytest.approx(0.3)
    assert row["audio_s"] == pytest.approx(1.0)            # 22050 samples at 22050 Hz
    assert row["rtf"] == pytest.approx(0.3)
    assert len(pcm) == 22050 * 2
    assert [e for e, _ in svc.requests[0]] == ["synthesize"]


def test_streaming_request_is_has_sequence():
    svc = FakeService(streaming=True)
    row, _ = synth(svc, "a01")
    assert row["ok"] and row["end"] == "synthesize-stopped"
    assert [e for e, _ in svc.requests[0]] == ["synthesize-start", "synthesize-chunk", "synthesize",
                                               "synthesize-stop"]
    chunk = json.loads(svc.requests[0][1][1].split(b"\n", 1)[1])
    assert chunk["text"] == BY_ID["a01"]["text"]


def test_sentence_chunking_sends_one_chunk_per_sentence():
    svc = FakeService(streaming=True)
    row, _ = synth(svc, "a01", chunking="sentence")
    kinds = [e for e, _ in svc.requests[0]]
    assert kinds.count("synthesize-chunk") == 3 and row["chunks_sent"] == 3


def test_eof_after_finished_audio_counts_as_the_end_when_streaming():
    row, _ = synth(FakeService(streaming=True, mode="eof"))
    assert row["ok"] and row["end"] == "eof"


def test_percentiles_and_summary():
    rows = []
    for i, (cat, t) in enumerate([("confirm", 0.1), ("confirm", 0.2), ("confirm", 0.3), ("confirm", 0.4),
                                  ("long", 2.0)]):
        r = b.make_row({"id": f"x{i}", "category": cat}, 1)
        r.update(ok=True, ttfa_s=t, total_s=t * 2, audio_s=1.0, rtf=t * 2)
        rows.append(r)
    bad = b.make_row({"id": "y", "category": "long"}, 1)
    bad.update(failure="timeout")
    rows.append(bad)
    s = b.summarize_rows(rows)
    assert s["by_category"]["confirm"]["ttfa_s"] == {"n": 4, "p50": 0.2, "p95": 0.4, "max": 0.4}
    assert s["by_category"]["long"]["requests"] == 2 and s["by_category"]["long"]["ok"] == 1
    assert s["overall"]["ttfa_s"]["p95"] == 2.0
    assert s["failures"] == {"timeout": 1}
    assert b.stats([]) == {"n": 0, "p50": None, "p95": None, "max": None}


# ── failure handling ────────────────────────────────────────────────────────

def test_refused_connection_is_a_failure_row():
    async def refuse():
        raise ConnectionRefusedError(111, "Connection refused")
    row, pcm = asyncio.run(b.synth_once(refuse, BY_ID["c01"], 1, voice=None, streaming=False))
    assert not row["ok"] and row["failure"] == "refused" and pcm == b""


def test_a_stream_that_stops():
    row, _ = synth(FakeService(streaming=False, mode="stops"))
    assert row["failure"] == "stream-stopped" and not row["ok"]
    row, _ = synth(FakeService(streaming=True, mode="stops"))
    assert row["failure"] == "stream-stopped"


def test_an_empty_reply():
    row, _ = synth(FakeService(streaming=True, mode="empty"))
    assert row["failure"] == "empty"


def test_server_error_and_timeout_and_bad_format():
    assert synth(FakeService(mode="error"))[0]["failure"] == "server-error"
    row, _ = synth(FakeService(mode="silent"), timeout=0.05)
    assert row["failure"] == "timeout"
    assert synth(FakeService(streaming=False, mode="wide"))[0]["failure"] == "bad-format"
    assert set(b.FAILURE_KINDS) >= {"refused", "timeout", "stream-stopped", "empty", "server-error"}


# ── the result file ─────────────────────────────────────────────────────────

def test_run_writes_wavs_and_a_result_with_the_schema(tmp_path, monkeypatch):
    svc = FakeService(streaming=True, rate=24000, samples=(2400,))
    monkeypatch.setattr(b, "tcp_connect", lambda host, port: svc.connect())
    out = tmp_path / "r.json"
    monkeypatch.chdir(tmp_path)
    rc = b.main(["run", "--port", "10210", "--engine", "fake", "--voice", "v1", "--reps", "2",
                 "--category", "confirm", "--wav-dir", "wav", "--out", str(out)])
    assert rc == 0
    res = json.loads(out.read_text())
    assert res["schema"] == "tts_bench/1" and res["kind"] == "run" and res["mode"] == "stream"
    assert res["describe"]["supports_synthesize_streaming"] is True
    assert len(res["rows"]) == 2 * 5
    for r in res["rows"]:
        assert set(r) == set(b.ROW_KEYS)
        assert Path(r["wav"]).is_absolute()          # a relative --wav-dir is stored resolved
        with wave.open(r["wav"]) as w:
            assert (w.getframerate(), w.getnchannels(), w.getsampwidth(), w.getnframes()) == (24000, 1, 2, 2400)
    assert set(res["summary"]) == {"by_category", "overall", "failures"}
    assert res["summary"]["overall"]["ok"] == 10 and res["summary"]["failures"] == {}
    assert res["sentences_version"] == 1 and res["reps"] == 2


def test_run_refuses_a_lan_host():
    with pytest.raises(b.BenchError, match="loopback only"):
        b.tcp_connect("192.168.1.14", 10200)


def test_rests_between_bursts():
    svc = FakeService(streaming=False)
    clock = Clock(1.0)
    slept = []

    async def sleep(s):
        slept.append(s)
    rows, rests = asyncio.run(b.run_set(svc.connect(), SENTENCES[:4], engine="x", voice=None, reps=1,
                                        wav_dir=None, streaming=False, rest_every_s=5, rest_s=75,
                                        log=lambda m: None, sleep=sleep, clock=clock))
    assert len(rows) == 4 and slept and all(s == 75 for s in slept) and len(rests) == len(slept)


# ── the stand-in check ──────────────────────────────────────────────────────

@pytest.mark.parametrize("ref,hyp", [
    ("Your appointment is on Tuesday, March 3rd, at 10:45 a.m.",
     "Your appointment is on Tuesday, March 3, at 10.45 AM."),
    ("The total comes to $1,284.50.", "The total comes to $1284.50"),
    ("Shuffling Khruangbin and Sigur Rós in the bedroom.", "Shuffling Khruangbin and Sigur Ros in the bedroom"),
    ("Timer set for ten minutes.", "Timer set for 10 minutes."),
    ("Volume set to thirty percent in the living room.", "Volume set to 30% in the living room."),
    ("Expect light rain after 3 p.m.", "Expect light rain after 3pm."),
])
def test_canon_makes_spelling_variants_equal(ref, hyp):
    assert b.canon(ref) == b.canon(hyp)


def test_canon_keeps_real_differences():
    assert b.canon("Paused in the kitchen.") != b.canon("Paused in the chicken.")
    assert b.int_words(1284) == ["one", "thousand", "two", "hundred", "eighty", "four"]


def _speechish(seconds, rate=24000, seed=0):
    rng = np.random.default_rng(seed)
    t = np.arange(int(seconds * rate)) / rate
    env = 0.5 + 0.5 * np.abs(np.sin(2 * np.pi * 3 * t))
    return 0.3 * env * np.sin(2 * np.pi * 180 * t) + 0.01 * rng.standard_normal(len(t))


def test_audio_checks_flags():
    rate = 24000
    gap = np.zeros(int(0.2 * rate))
    speech = _speechish(1.2)
    clean = np.concatenate([gap, speech, gap])
    text = "Paused in the kitchen."                       # 4 words, about 1.5 s
    assert b.audio_checks(clean, rate, text)["flags"] == []
    blip = np.concatenate([clean, np.zeros(int(0.9 * rate)), _speechish(0.1, seed=1), gap])
    assert "trailing-sound" in b.audio_checks(blip, rate, text)["flags"]
    lead = np.concatenate([gap, _speechish(0.1, seed=2), np.zeros(int(0.9 * rate)), speech, gap])
    assert "leading-sound" in b.audio_checks(lead, rate, text)["flags"]
    clipped = clean.copy()
    clipped[5000:5010] = 1.0
    assert "clipping" in b.audio_checks(clipped, rate, text)["flags"]
    long_text = " ".join(["word"] * 40)
    assert "length" in b.audio_checks(clean, rate, long_text)["flags"]
    assert b.audio_checks(np.zeros(rate), rate, text)["flags"] == ["silent"]


def test_check_uses_dev_whisper_only_and_scores(tmp_path):
    with pytest.raises(b.BenchError, match="10300"):
        b.check_stt_port(10300)
    rate = 24000
    x = np.concatenate([np.zeros(4800), _speechish(1.2), np.zeros(4800)])
    wav = tmp_path / "x.wav"
    b.write_pcm_wav(wav, b.spb.to_pcm(x), rate)
    good = b.make_row(BY_ID["c01"], 1)
    good.update(ok=True, wav=str(wav))
    bad = dict(good, rep=2)
    result = {"engine": "fake", "label": "fake", "rows": [good, bad]}
    heard = iter(["Paused in the kitchen.", "Paused in the chicken."])

    async def fake_transcribe(host, port, pcm):
        assert port == 10301 and len(pcm) == int(len(x) / 1.5) * 2
        return next(heard), 0.1
    rows = asyncio.run(b.check_result(result, {"c01": BY_ID["c01"]["text"]}, "127.0.0.1", 10301,
                                      transcribe=fake_transcribe, log=lambda m: None))
    assert rows[0]["word_errors"] == 0 and rows[0]["flags"] == []
    assert rows[1]["word_errors"] == 1 and "words" in rows[1]["flags"]
    with pytest.raises(b.BenchError):
        asyncio.run(b.check_result(result, {}, "127.0.0.1", 10300))


# ── slowdown guards, the sampler's guard, the relay ─────────────────────────

def _sat(state, changed):
    return {"entity_id": "assist_satellite.puck_assist_satellite", "state": state, "last_changed": changed}


def test_ha_quiet():
    now = 1_800_000_000.0
    old = "2027-01-15T07:50:00+00:00"                     # long before `now`
    import datetime as dt
    recent = dt.datetime.fromtimestamp(now - 60, dt.timezone.utc).isoformat()
    music = {"entity_id": "media_player.office", "state": "playing", "last_changed": recent}
    assert b.ha_quiet([_sat("idle", old), music], now) == (True, [])
    ok, why = b.ha_quiet([_sat("idle", recent)], now)
    assert not ok and "changed 60 s ago" in why[0]
    ok, why = b.ha_quiet([_sat("responding", old)], now)
    assert not ok and "responding" in why[0]
    assert b.ha_quiet([_sat("unavailable", recent)], now)[0]


def test_llm_burst_limits():
    with pytest.raises(b.BenchError, match="at most"):
        asyncio.run(b.llm_burst(b.LLM_URL, 120, 64))
    with pytest.raises(b.BenchError, match="loopback only"):
        asyncio.run(b.llm_burst("http://192.168.1.14:11438", 30, 64))


def test_spin_guard_fires_only_on_idle_spin():
    killed = []
    g = b.SpinGuard([4242], busy=90, hold_s=180, kill=lambda p, s: killed.append(p))
    eng = 0
    for t in range(0, 300, 10):
        msg = g.feed(float(t), 100, eng)                   # busy, no model engine time
        if msg:
            break
    assert killed == [4242] and t == 180   # busy since the t=0 sample and "SIGTERM" in msg
    g2 = b.SpinGuard([1], kill=lambda p, s: killed.append(p))
    for t in range(0, 600, 10):
        eng += 9 * 10**9                                    # the model servers are working
        assert g2.feed(float(t), 100, eng) is None
    g3 = b.SpinGuard([1], kill=lambda p, s: killed.append(p))
    for t in range(0, 600, 10):
        assert g3.feed(float(t), 100 if (t // 10) % 15 else 0, 0) is None   # drops reset it
    assert killed == [4242]


def test_summarize_samples():
    rows = [{"t": i * 10.0, "busy": b_, "sclk_mhz": 600.0, "power_w": 9.0 + i, "gtt_gb": 51.0,
             "memavail_gb": 48.0, "memfree_gb": 9.0, "fleet_engine_ns": i * 10**9}
            for i, b_ in enumerate([0, 0, 2, 100])]
    s = b.summarize_samples(rows)
    assert s["n"] == 4 and s["busy_max"] == 100 and s["busy_ge_90_samples"] == 1
    assert s["power_mean_w"] == 10.5 and s["fleet_engine_s"] == 3.0 and s["sclk_at_max_samples"] == 0


def test_relay_binds_loopback_only(monkeypatch):
    # The no_network fixture blocks connect and send, not bind or listen. If
    # the host check were ever removed this test would open a LAN-visible
    # listener and serve forever (M20 e2e finding 2), so listening is made to
    # fail here whatever the code under test does.
    def _no_listen(*a, **k):
        raise AssertionError("the relay tried to listen")
    monkeypatch.setattr(b.asyncio, "start_server", _no_listen)
    monkeypatch.setattr(b.asyncio, "start_unix_server", _no_listen, raising=False)
    with pytest.raises(b.BenchError, match="loopback only"):
        asyncio.run(b.relay_main("0.0.0.0", 10210, "/nonexistent.sock"))
    with pytest.raises(b.BenchError, match="loopback only"):
        asyncio.run(b.bridge_main("/nonexistent.sock", "192.168.1.14", 10222))
    with pytest.raises(SystemExit):
        b.main(["relay", "--port", "10210"])


# ── the blind set ───────────────────────────────────────────────────────────

def test_blind_set_hides_the_engine(tmp_path):
    results = []
    for k, label in enumerate(["piper", "pocket"]):
        rows = []
        for s in SENTENCES[:3]:
            wav = tmp_path / f"{label}_{s['id']}.wav"
            b.write_pcm_wav(wav, b.spb.to_pcm(_speechish(0.5, seed=k)), 22050)
            r = b.make_row(s, 1)
            r.update(ok=True, wav=str(wav))
            rows.append(r)
        f = tmp_path / f"{label}.json"
        f.write_text(json.dumps({"label": label, "rows": rows}))
        results.append(str(f))
    out, key = tmp_path / "samples", tmp_path / "key" / "KEY.json"
    assert b.main(["blind", *results, "--out", str(out), "--key", str(key)]) == 0
    names = sorted(p.name for p in out.glob("*.wav"))
    assert len(names) == 6 and not any("piper" in n or "pocket" in n for n in names)
    k = json.loads(key.read_text())
    assert {v["engine"] for v in k["files"].values()} == {"piper", "pocket"}
    assert not (out / "KEY.json").exists() and (out / "rating_sheet.md").exists()
    assert "c01-A.wav" in (out / "rating_sheet.md").read_text()


def test_token_rates_split_the_boost_window():
    reqs = [{"t_s": t, "predicted_per_second": v} for t, v in
            [(0, 99.0), (5, 99.0), (10, 99.0), (15, 85.0), (20, 85.0), (25, 86.0)]]
    r = b.token_rates(reqs)
    assert r["tok_s"]["n"] == 6 and r["tok_s_sustained"]["n"] == 3
    assert r["tok_s_sustained"]["p50"] == 85.0 and r["tok_s_sustained_mean"] == 85.333


def test_idle_summary_skips_the_load(tmp_path, capsys):
    f = tmp_path / "s.jsonl"
    rows = [{"event": "start", "t": 0}] + [
        {"t": i * 60.0, "busy": 100 if i < 2 else 0, "sclk_mhz": 600.0, "power_w": 9.0, "gtt_gb": 51.0,
         "memavail_gb": 48.0, "memfree_gb": 9.0, "fleet_engine_ns": 0} for i in range(10)]
    f.write_text("\n".join(json.dumps(r) for r in rows))
    assert b.main(["idle", str(f), "--skip-min", "2"]) == 0
    out = json.loads(capsys.readouterr().out.split(": ", 1)[1])
    assert out["n"] == 8 and out["busy_max"] == 0
