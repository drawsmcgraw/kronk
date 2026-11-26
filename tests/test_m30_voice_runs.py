"""Work item M30 step B: the voice-run record (scripts/voice_runs_export.py),
its published copies and status files, its timer, and its backups
(scripts/backups.py). Criteria B1 to B9 of docs/sprint/M30-criteria.md.

Everything runs in tmp_path: --db, --publish and --status in scratch, a fake
Tempo at the function boundary (get_json), recordings in Home Assistant's
layout built here. No socket is opened, no unit started, nothing under the
real data/ or ~/.local/state/kronk-voice/ is touched (conftest fails the run
if it is). Every sentence is invented; MARK is planted in the words and the
replies so a leak is found by search."""
import datetime as dt
import hashlib
import importlib.util
import io
import json
import os
import re
import shlex
import sqlite3
import stat
import subprocess
import sys
import tarfile
import time
import urllib.error
import urllib.parse
import wave
from pathlib import Path

import pytest

from tests import test_m16_backups as M16

pytestmark = pytest.mark.usefixtures("no_network")

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"


def _load(name, alias):
    spec = importlib.util.spec_from_file_location(alias, SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


EX = _load("voice_runs_export", "voice_runs_export_m30")
VT = EX.vt
LS = EX.ls
B = M16.B                      # the backups module the m16 FakeOps and house fixture use
FakeOps = M16.FakeOps
house = M16.house              # the m16 scratch house, as a fixture here

MARK = "qx-marker-7731"
PROD, DEV = "kronk-voice-tracer", "kronk-voice-tracer-dev"
KITCHEN = "assist_satellite.home_assistant_voice_0ac919_assist_satellite"
DEV_K = "e0949788663255952ce7778975527882"
T0 = 1_795_000_000.0           # 2026-11-18 08:26:40 UTC
NOW = T0 + 86400               # a day later: every T0 run is final in Tempo


# ═══ A fake Tempo at the function boundary ══════════════════════════════════

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


def tid_of(rid: str) -> str:
    return hashlib.sha256(rid.encode()).hexdigest()[:32]


def vrun(rid, start, dur=5.0, service=PROD, pipeline="kronk", stages="stt,intent,tts", heard=None,
         reply=None, root=None, children=True, **attrs):
    """One voice run as Tempo holds it. `attrs` are the root span's selected
    attributes (dots as __); `root` adds attributes only the whole trace has."""
    a = {"ha.pipeline_name": pipeline, "ha.pipeline_run_id": rid, "voice.stages": stages}
    if heard is not None:
        a["voice.transcript"] = heard
        a["voice.transcript_chars"] = len(heard)
    if reply is not None:
        a["voice.speech"] = reply
    a.update({k.replace("__", "."): v for k, v in attrs.items()})
    return {"tid": tid_of(rid), "start": start, "dur": dur, "service": service, "attrs": a,
            "root": {"voice.language": "en", "ha.conversation_id": f"C-{rid}", "ha.pipeline_id": "P1",
                     "voice.response_type": "action_done", "voice.speech_chars": len(reply or ""),
                     **(root or {})},
            "children": children}


class FakeTempo:
    """get_json(url, timeout): /api/search (TraceQL stage filter, start,
    end and limit honoured) and /api/v2/traces/<id>."""

    def __init__(self, runs=()):
        self.runs = list(runs)
        self.mode = None
        self.nonsense = None
        self.ignore_filter = False
        self.calls: list[str] = []
        self.timeouts: list = []

    def trace_calls(self):
        return [u for u in self.calls if "/api/v2/traces/" in u]

    def search_calls(self):
        return [u for u in self.calls if "/api/search" in u]

    def __call__(self, url, timeout=None):
        self.calls.append(url)
        self.timeouts.append(timeout)
        assert url.startswith("http://127.0.0.1:3200/")
        if self.mode == "down":
            raise urllib.error.URLError(ConnectionRefusedError(111, "Connection refused"))
        if self.mode == "slow":
            if timeout is None or timeout >= 10:
                time.sleep(10)
            else:
                time.sleep(timeout)
                raise TimeoutError("timed out")
        if self.mode == "not json":
            raise json.JSONDecodeError("Expecting value", "<html>", 0)
        if self.mode == "nonsense":
            return self.nonsense
        u = urllib.parse.urlparse(url)
        if u.path == "/api/search":
            q = urllib.parse.parse_qs(u.query)
            m = re.search(r'span\.voice\.stages =~ "([^"]+)"', q["q"][0])
            s, e, lim = int(q["start"][0]), int(q["end"][0]), int(q["limit"][0])
            out = []
            for r in self.runs:
                if not (s <= r["start"] <= e):
                    continue
                if m and not self.ignore_filter and not re.fullmatch(m.group(1), r["attrs"]["voice.stages"]):
                    continue
                attrs = [_attr(k, v) for k, v in r["attrs"].items()] + [_attr("service.name", r["service"])]
                ss = {"spans": [{"spanID": "1", "name": "voice.run", "startTimeUnixNano": str(int(r["start"] * 1e9)),
                                 "durationNanos": str(int(r["dur"] * 1e9)), "attributes": attrs}], "matched": 1}
                out.append({"traceID": r["tid"].lstrip("0"), "rootServiceName": r["service"],
                            "rootTraceName": "voice.run", "startTimeUnixNano": str(int(r["start"] * 1e9)),
                            "durationMs": int(r["dur"] * 1000), "spanSet": ss, "spanSets": [ss],
                            "serviceStats": {r["service"]: {"spanCount": 6}}})
            return {"traces": out[:lim]}
        tid = u.path.rsplit("/", 1)[1]
        r = next((r for r in self.runs if r["tid"] == tid), None)
        if r is None:
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
        st = int(r["start"] * 1e9)
        ms = 1_000_000
        spans = [{"traceId": tid, "spanId": "01", "name": "voice.run", "startTimeUnixNano": str(st),
                  "endTimeUnixNano": str(st + int(r["dur"] * 1e9)),
                  "attributes": [_attr(k, v) for k, v in {**r["attrs"], **r["root"]}.items()]}]
        if r["children"]:
            spans += [
                {"name": "voice.stt", "startTimeUnixNano": str(st), "endTimeUnixNano": str(st + 2265 * ms),
                 "attributes": [_attr("voice.engine", "stt.faster_whisper")]},
                {"name": "voice.stt.listen", "startTimeUnixNano": str(st), "endTimeUnixNano": str(st + 1963 * ms),
                 "attributes": []},
                {"name": "voice.stt.transcribe", "startTimeUnixNano": str(st + 1963 * ms),
                 "endTimeUnixNano": str(st + 2264 * ms), "attributes": [_attr("voice.engine", "stt.faster_whisper")]},
                {"name": "voice.intent", "startTimeUnixNano": str(st + 2265 * ms),
                 "endTimeUnixNano": str(st + 3302 * ms), "attributes": []},
            ]
        return {"trace": {"resourceSpans": [{"resource": {"attributes": [_attr("service.name", r["service"])]},
                                             "scopeSpans": [{"scope": {"name": "kronk.voice_tracer"},
                                                             "spans": spans}]}]},
                "metrics": {}}


# ═══ Scratch ════════════════════════════════════════════════════════════════

class Scratch:
    def __init__(self, root: Path):
        self.root = root
        self.db = root / "state" / "kronk-voice" / "voice_runs.db"
        self.pub = root / "data" / "voice_runs.db"
        self.pub_dev = root / "data" / "dev" / "voice_runs.db"
        self.status = root / "data" / "voice_runs.json"
        self.status_dev = root / "data" / "dev" / "voice_runs.json"
        self.rec = root / "recordings"
        self.rec.mkdir(parents=True)

    def args(self, *extra):
        return ["--db", str(self.db), "--publish", str(self.pub), "--publish", str(self.pub_dev),
                "--status", str(self.status), "--status", str(self.status_dev), "--recordings", str(self.rec),
                *extra]

    def run(self, tempo, now, *extra, init=None, **kw):
        """One export. A fresh scratch (no record, no status file) gets
        --init, as the coordinator's first export does (M30-e2e2 finding 5:
        without it no record is made); pass init=False to leave it out."""
        if init is None:
            init = not os.path.lexists(self.db) and not self.status.exists()
        out = io.StringIO()
        rc = EX.main(self.args(*extra, *(("--init",) if init else ())), now=now, get_json=tempo, out=out, **kw)
        return rc, out.getvalue()

    def con(self):
        c = sqlite3.connect(f"file:{self.db}?mode=ro", uri=True)
        c.row_factory = sqlite3.Row
        return c

    def rows(self):
        with self.con() as c:
            return {r["run_id"]: dict(r) for r in c.execute("SELECT * FROM runs")}

    def one(self, sql, *a):
        c = self.con()
        try:
            return c.execute(sql, a).fetchone()
        finally:
            c.close()

    def state(self):
        return dict(self.one("SELECT * FROM export_state"))

    def changes(self):
        c = self.con()
        try:
            return [dict(r) for r in c.execute("SELECT * FROM run_changes ORDER BY id")]
        finally:
            c.close()

    def status_json(self, dev=False):
        return json.loads((self.status_dev if dev else self.status).read_text())

    def published(self, dev=False):
        c = sqlite3.connect(f"file:{self.pub_dev if dev else self.pub}?mode=ro&immutable=1", uri=True)
        c.row_factory = sqlite3.Row
        try:
            return {r["run_id"]: dict(r) for r in c.execute("SELECT * FROM runs")}
        finally:
            c.close()

    def person(self, sql, *a):
        """A person's edit of the record (sqlite3 by hand)."""
        c = sqlite3.connect(self.db)
        c.execute(sql, a)
        c.commit()
        c.close()


@pytest.fixture
def sc(tmp_path):
    return Scratch(tmp_path)


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def household(n=3, start=T0, gap=600.0):
    return [vrun(f"R{i}", start + i * gap, heard=f"invented request {i} {MARK}", reply=f"invented answer {i} {MARK}",
                 ha__satellite_id=KITCHEN, kronk__origin__device_id=DEV_K, voice__handled_by="kronk",
                 kronk__rule="music", kronk__tools=["play_music"]) for i in range(1, n + 1)]


# ═══ B1: first run takes every STT run; a second changes nothing ═══════════

def test_b1_first_run_takes_every_stt_run_and_a_second_run_changes_nothing(sc):
    runs = household(2) + [
        vrun("R-stt-only", T0 + 3000, stages="stt", voice__transcript_chars=0, voice__error_code="stt-no-text-recognized"),
        vrun("R-dev-audio", T0 + 4000, service=DEV, pipeline="kronk-dev", stages="wake_word,stt,intent,tts",
             heard=f"invented dev words {MARK}"),
        vrun("R-replay", T0 + 5000, service=DEV, pipeline="kronk-dev", stages="intent,tts", heard="a replay"),
        vrun("R-typed", T0 + 6000, stages="intent,tts", heard="typed text"),
    ]
    tempo = FakeTempo(runs)
    rc, log = sc.run(tempo, NOW)
    assert rc == 0, log
    rows = sc.rows()
    assert sorted(rows) == ["R-dev-audio", "R-stt-only", "R1", "R2"]
    assert sorted(r["seq"] for r in rows.values()) == [1, 2, 3, 4]
    assert rows["R-dev-audio"]["is_dev"] == 1 and rows["R1"]["is_dev"] == 0
    # The first read is the same code with no watermark: all of Tempo, one search per day.
    first = urllib.parse.parse_qs(urllib.parse.urlparse(tempo.search_calls()[0]).query)
    assert int(first["start"][0]) == int(NOW - EX.FIRST_READ_S)
    assert all('span.voice.stages =~ ".*stt.*"' in urllib.parse.unquote_plus(u) for u in tempo.search_calls())
    assert sc.published().keys() == rows.keys() == sc.published(dev=True).keys()
    st_pub, st_dev = os.stat(sc.pub), os.stat(sc.pub_dev)
    before = {k: (r["seq"], r["last_changed_at"]) for k, r in rows.items()}
    status1 = sc.status_json()

    rc, log = sc.run(tempo, NOW + 60)
    assert rc == 0, log
    after = {k: (r["seq"], r["last_changed_at"]) for k, r in sc.rows().items()}
    assert after == before                                         # seq unchanged
    for p, st0 in ((sc.pub, st_pub), (sc.pub_dev, st_dev)):        # the copies not rewritten
        st1 = os.stat(p)
        assert (st1.st_ino, st1.st_mtime_ns, st1.st_size) == (st0.st_ino, st0.st_mtime_ns, st0.st_size)
    status2 = sc.status_json()
    assert status2["ts"] == NOW + 60 > status1["ts"] and status2["this_run"]["published"] == 0
    assert "copies written 0 of 2" in log


def test_the_record_table_matches_the_columns_and_appendix_a(sc):
    sc.run(FakeTempo(household(1)), NOW)
    c = sc.con()
    try:
        cols = tuple(r[1] for r in c.execute("PRAGMA table_info(runs)"))
        tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert c.execute("PRAGMA user_version").fetchone()[0] == EX.SCHEMA
        assert c.execute("PRAGMA journal_mode").fetchone()[0] == "delete"        # rollback journal, not WAL
        assert c.execute("SELECT COUNT(*) FROM labels").fetchone()[0] == 0       # ships empty
    finally:
        c.close()
    assert cols == EX.RUN_COLUMNS
    assert {"runs", "labels", "stt_config", "run_changes", "export_state"} <= tables
    r = sc.rows()["R1"]
    # The listing's columns agree with voice_transcripts.to_row for the same run.
    a = VT.rows_from_search(FakeTempo(household(1))(
        "http://127.0.0.1:3200/api/search?" + urllib.parse.urlencode(
            {"q": VT.search_query(), "start": int(T0), "end": int(T0) + 700, "limit": 10})))[0]
    want = VT.to_row(a)
    assert (r["local_time"], r["satellite_id"], r["heard"], r["tier"], r["rule"], r["reply"], r["trace_id"],
            r["start_s"], r["end_s"], r["service"]) == (
        want["time"], want["satellite"], want["heard"], want["tier"], want["rule"], want["reply"], want["trace_id"],
        want["start_s"], want["end_s"], want["service"])
    assert json.loads(r["tools"]) == ["play_music"]
    # The rest from the fixture trace.
    assert (r["language"], r["conversation_id"], r["pipeline_id"], r["response_type"], r["stt_engine"],
            r["listen_ms"], r["transcribe_ms"], r["intent_ms"]) == (
        "en", "C-R1", "P1", "action_done", "stt.faster_whisper", 1963, 301, 1037)
    assert json.loads(r["raw_trace"])["resourceSpans"][0]["scopeSpans"][0]["spans"][0]["name"] == "voice.run"


# ═══ B2: the ten rules of review point 9 ═══════════════════════════════════

def test_rule1_an_empty_value_never_replaces_a_stored_one_and_a_replaced_one_is_kept(sc):
    run = household(1, start=NOW - 1800)[0]             # inside the next reads' window
    tempo = FakeTempo([run])
    sc.run(tempo, NOW)
    seq0 = sc.rows()["R1"]["seq"]
    # Tempo now holds less: no words, no rule (a failed enrichment, the 90 days passing).
    for k in ("voice.transcript", "voice.transcript_chars", "kronk.rule", "voice.speech", "kronk.tools"):
        run["attrs"].pop(k)
    sc.run(tempo, NOW + 60)
    r = sc.rows()["R1"]
    assert r["heard"] == f"invented request 1 {MARK}" and r["rule"] == "music" and r["seq"] == seq0
    assert json.loads(r["tools"]) == ["play_music"] and sc.changes() == []
    # A value where there was none: filled, the row's seq raised, nothing to keep.
    run["attrs"]["voice.corrected_by"] = "stop"
    sc.run(tempo, NOW + 120)
    r = sc.rows()["R1"]
    assert r["corrected_by"] == "stop" and r["seq"] > seq0 and sc.changes() == []
    # A different non-empty value: replaced, the old row whole in run_changes.
    run["attrs"]["voice.handled_by"] = "ha_local"
    sc.run(tempo, NOW + 180)
    r = sc.rows()["R1"]
    assert r["tier"] == "ha_local"
    (ch,) = sc.changes()
    old = json.loads(ch["old_row"])
    assert ch["why"] == "export" and ch["run_id"] == "R1" and old["tier"] == "kronk"
    assert old["heard"] == r["heard"] and old["seq"] < r["seq"] and set(old) == set(EX.RUN_COLUMNS)


def test_rule2_the_whole_trace_is_read_only_once_the_run_is_15_minutes_old(sc):
    end = NOW - 300                                     # ended 5 minutes ago
    run = vrun("R1", end - 5, heard=f"invented {MARK}", root={"voice.window_cut_short": "shutdown"})
    tempo = FakeTempo([run])
    sc.run(tempo, NOW)
    r = sc.rows()["R1"]
    assert tempo.trace_calls() == [] and r["raw_trace"] is None and r["stt_engine"] is None
    assert r["window_cut_short"] is None
    sc.run(tempo, NOW + 660)                            # 16 minutes after it ended
    r = sc.rows()["R1"]
    assert len(tempo.trace_calls()) == 1 and r["raw_trace"] and r["stt_engine"] == "stt.faster_whisper"
    sc.run(tempo, NOW + 720)
    assert len(tempo.trace_calls()) == 1                # once


def test_rule2_a_failed_trace_read_is_tried_again_and_moves_nothing_else(sc):
    tempo = FakeTempo(household(1))
    real = tempo.__call__

    def flaky(url, timeout=None):
        if "/api/v2/traces/" in url and flaky.fail:
            raise urllib.error.HTTPError(url, 500, "boom", {}, None)
        return real(url, timeout)
    flaky.fail = True
    rc, _ = sc.run(flaky, NOW)
    assert rc == 0 and sc.rows()["R1"]["raw_trace"] is None and sc.rows()["R1"]["heard"]
    assert sc.state()["watermark_s"] == pytest.approx(NOW - EX.FINAL_S)
    assert "trace read(s) failed" in sc.status_json()["last_error"]
    flaky.fail = False
    sc.run(flaky, NOW + 60)
    assert sc.rows()["R1"]["raw_trace"]


class FakeClock:
    """The exporter's budget clock (EX.clock), moved on by the fake Tempo."""

    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def test_rule2_trace_reads_that_keep_failing_wait_for_the_next_run(sc, monkeypatch):
    """A Tempo that answers searches but times out on traces costs the
    trace budget a run, not one timeout per run waiting: the unit's 50 s
    limit is never reached before the record and the status are written.
    (Fix round 1, F3: the limit is the time budget, not three failures.)"""
    clk = FakeClock()
    monkeypatch.setattr(EX, "clock", clk)
    tempo = FakeTempo(household(20))
    real = tempo.__call__

    def no_traces(url, timeout=None):
        if "/api/v2/traces/" in url:
            clk.t += EX.TEMPO_TIMEOUT_S                  # each read takes its whole timeout
            raise TimeoutError("timed out")
        return real(url, timeout)
    t0 = clk.t
    rc, _ = sc.run(no_traces, NOW)
    assert rc == 0 and len(sc.rows()) == 20
    assert clk.t - t0 <= EX.TRACE_BUDGET_S + EX.TEMPO_TIMEOUT_S < 50
    this = sc.status_json()["this_run"]
    assert this["trace_read_failures"] == EX.TRACE_BUDGET_S // EX.TEMPO_TIMEOUT_S + 1
    assert this["trace_reads_deferred"] == 20 - this["trace_read_failures"] > 0


def test_rule3_window_cut_short_is_stored(sc):
    run = vrun("R1", T0, heard="invented", root={"voice.window_cut_short": "ha_disconnected"})
    sc.run(FakeTempo([run]), NOW)
    assert sc.rows()["R1"]["window_cut_short"] == "ha_disconnected"


def test_rule4_a_late_correction_is_not_recomputed(sc):
    """voice_tracer exported R1 before the stop arrived: neither run carries
    the mark, and the exporter does not make one up (the gap is named in
    the feature doc)."""
    runs = [vrun("R1", T0, heard="invented play request", voice__handled_by="kronk"),
            vrun("R2", T0 + 20, heard="stop", voice__handled_by="ha_local")]
    sc.run(FakeTempo(runs), NOW)
    rows = sc.rows()
    assert all(rows[r][c] is None for r in rows for c in ("corrected_by", "correction_kind", "correction_of_run_id"))
    assert sc.status_json()["summary"]["household"]["wrong_24h"] == 0


def test_rule5_a_blueprint_hand_off_keeps_no_rule_and_no_tools(sc):
    run = vrun("R1", T0, heard="invented", voice__handled_by="blueprint_then_kronk", ha__blueprint_run_id="BP1")
    tempo = FakeTempo([run])
    sc.run(tempo, NOW)
    r = sc.rows()["R1"]
    assert (r["rule"], r["tools"], r["kronk_trace_id"], r["blueprint_run_id"]) == (None, None, None, "BP1")
    assert [u for u in tempo.trace_calls() if tid_of("R1") not in u] == []    # no Kronk trace read


def test_rule6_epoch_is_the_truth_local_time_as_written_and_the_house_day(sc):
    old_tz = os.environ.get("TZ")
    os.environ["TZ"] = "America/New_York"
    time.tzset()
    try:
        fall = dt.datetime(2026, 11, 1, 5, 30, tzinfo=dt.timezone.utc).timestamp()   # 01:30 EDT
        runs = [vrun("R-edt", fall + 0.123, heard="invented one"),
                vrun("R-est", fall + 3600.456, heard="invented two"),                 # 01:30 EST, the second one
                vrun("R-late", dt.datetime(2026, 11, 2, 4, 30, tzinfo=dt.timezone.utc).timestamp(),
                     heard="invented three")]                                         # 23:30 on the 1st, house time
        tempo = FakeTempo(runs)
        now = runs[2]["start"] + 1200
        sc.run(tempo, now)
        rows = sc.rows()
        assert rows["R-edt"]["local_time"] == "2026-11-01T01:30:00-04:00"
        assert rows["R-est"]["local_time"] == "2026-11-01T01:30:00-05:00"
        assert rows["R-edt"]["start_s"] == round(fall + 0.123, 3) and rows["R-est"]["start_s"] == round(fall + 3600.456, 3)
        assert {r["local_day"] for r in rows.values()} == {"2026-11-01"}
        assert rows["R-late"]["local_time"] == "2026-11-01T23:30:00-05:00"
        # The host's zone changes later (in UTC R-late is on the 2nd): what was written stays.
        os.environ["TZ"] = "UTC"
        time.tzset()
        runs[2]["attrs"]["voice.handled_by"] = "kronk"                                # a change, so the row is written
        sc.run(tempo, now + 60)
        again = sc.rows()
        assert again["R-late"]["tier"] == "kronk" and again["R-late"]["seq"] > rows["R-late"]["seq"]
        assert {k: (r["local_time"], r["local_day"]) for k, r in again.items()} == \
               {k: (r["local_time"], r["local_day"]) for k, r in rows.items()}
    finally:
        if old_tz is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = old_tz
        time.tzset()


def test_rule7_is_dev_is_decided_on_the_day_and_never_recomputed(sc):
    runs = [vrun("R-test-pipe", NOW - 1800, pipeline="kronk-test", heard="invented"),
            vrun("R-dev-svc", NOW - 1700, service=DEV, pipeline="kronk", heard="invented")]
    tempo = FakeTempo(runs)
    sc.run(tempo, NOW, dev_pipelines=("kronk-dev",))
    assert {k: r["is_dev"] for k, r in sc.rows().items()} == {"R-test-pipe": 0, "R-dev-svc": 1}
    runs[0]["attrs"]["voice.handled_by"] = "kronk"                     # the row is written again
    sc.run(tempo, NOW + 60, dev_pipelines=("kronk-dev", "kronk-test"))  # the pipeline is a dev one now
    rows = sc.rows()
    assert rows["R-test-pipe"]["is_dev"] == 0 and rows["R-test-pipe"]["tier"] == "kronk"
    assert rows["R-test-pipe"]["pipeline"] == "kronk-test" and rows["R-dev-svc"]["service"] == DEV


@pytest.mark.parametrize("ignore_filter", [False, True])
def test_rule8_replays_and_typed_runs_are_out_and_stt_only_runs_are_in(sc, ignore_filter):
    runs = [vrun(f"R-replay-{i}", T0 + i, service=DEV, pipeline="kronk-dev", stages="intent,tts",
                 heard="a corpus line") for i in range(5)]
    runs += [vrun("R-typed", T0 + 100, stages="intent,tts", heard="typed"),
             vrun("R-nothing", T0 + 200, stages="stt", voice__transcript_chars=0),
             vrun("R-err", T0 + 300, stages="stt", voice__error_code="stt-stream-failed")]
    tempo = FakeTempo(runs)
    tempo.ignore_filter = ignore_filter                 # a Tempo that ignores the query filter: same rows
    sc.run(tempo, NOW)
    rows = sc.rows()
    assert sorted(rows) == ["R-err", "R-nothing"]
    assert rows["R-nothing"]["heard_chars"] == 0 and rows["R-nothing"]["heard"] is None
    assert rows["R-err"]["error_code"] == "stt-stream-failed"
    assert sc.status_json()["summary"]["household"]["error_24h"] == 1


def test_rule9_a_truncated_window_holds_the_watermark(sc, monkeypatch):
    tempo = FakeTempo(household(1))
    sc.run(tempo, NOW)
    w1 = sc.state()["watermark_s"]
    burst = NOW + 120
    tempo.runs += [vrun(f"B{i}", burst + i, heard="invented") for i in range(3)]   # 3 in one minute
    monkeypatch.setattr(VT, "SEARCH_LIMIT", 2)
    rc, log = sc.run(tempo, NOW + 3000)
    assert rc == 0
    w2 = sc.state()["watermark_s"]
    assert w1 <= w2 <= burst + 3 and w2 < NOW + 3000 - EX.FINAL_S
    assert "search limit" in sc.status_json()["last_error"] and sc.status_json()["this_run"]["truncated_windows"] >= 1
    monkeypatch.setattr(VT, "SEARCH_LIMIT", 5000)
    sc.run(tempo, NOW + 3060)
    assert {"B0", "B1", "B2"} <= set(sc.rows())
    assert sc.state()["watermark_s"] == pytest.approx(NOW + 3060 - EX.FINAL_S)


def test_rule10_a_run_that_never_reached_tempo_has_no_row_even_with_a_recording(sc):
    wav(sc.rec, DEV_K, "kronk", "999", T0 + 3)          # audio of a run Tempo never got
    sc.run(FakeTempo([]), T0 + 3600)
    assert sc.rows() == {}


# ═══ B3: Tempo down, slow or answering nonsense ════════════════════════════

NONSENSE = [
    "a string",
    [],
    {"no": "traces"},
    {"traces": "x"},
    {"traces": [{"traceID": 5}]},
    {"traces": [{"traceID": "ab", "spanSets": "x"}]},
    {"traces": [{"traceID": "ab", "spanSets": [{"spans": [{"attributes": [], "startTimeUnixNano": "soon"}]}]}]},
    {"traces": [{"traceID": "ab", "startTimeUnixNano": "1", "spanSets": [{"spans": [{"attributes": "bad"}]}]}]},
]


@pytest.mark.parametrize("mode,nonsense", [("down", None), ("slow", None), ("not json", None)]
                         + [("nonsense", n) for n in NONSENSE])
def test_b3_tempo_failing_touches_nothing_and_says_why(sc, mode, nonsense):
    tempo = FakeTempo(household(2))
    sc.run(tempo, NOW)
    before = {p: sha(p) for p in (sc.db, sc.pub, sc.pub_dev)}
    wm = sc.state()["watermark_s"]
    tempo.mode, tempo.nonsense = mode, nonsense
    t = time.monotonic()
    rc, log = sc.run(tempo, NOW + 600, tempo_timeout=0.2)
    assert time.monotonic() - t < 5
    assert rc == 0
    assert {p: sha(p) for p in before} == before                    # the record and the copies untouched
    assert sc.state()["watermark_s"] == wm
    for dev in (False, True):
        st = sc.status_json(dev)
        assert st["ts"] == NOW + 600 and st["ok"] is False and st["last_error"].startswith("Tempo: ")
        assert st["summary"]["household"]["runs_24h"] == 2             # the page still has its counts
        assert MARK not in json.dumps(st)
    assert "the record and the copies are unchanged" in log
    want = {"down": "Connection refused", "slow": "timed out", "not json": "JSONDecodeError"}.get(mode, "")
    assert want in sc.status_json()["last_error"]


def test_b3_the_real_getter_gives_urlopen_a_timeout_under_ten_seconds(monkeypatch):
    seen = {}

    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(url, timeout=None):
        seen["timeout"] = timeout
        return Resp(b'{"traces": []}')
    monkeypatch.setattr(EX.urllib.request, "urlopen", fake_urlopen)
    t = EX.Tempo(VT.TEMPO)
    t.get("http://127.0.0.1:3200/api/search?q=x")
    assert seen["timeout"] == EX.TEMPO_TIMEOUT_S < 10


def test_b3_a_record_that_cannot_be_opened_exits_1_and_says_so(sc):
    sc.db.parent.mkdir(parents=True, mode=0o700)
    sc.db.write_bytes(b"not a database at all" * 100)
    os.chmod(sc.db, 0o600)
    rc, log = sc.run(FakeTempo(household(1)), NOW)
    assert rc == 1 and "cannot be opened" in log
    assert sc.status_json()["ok"] is False and "cannot be opened" in sc.status_json()["last_error"]
    assert sc.db.read_bytes() == b"not a database at all" * 100


# ═══ B4: the exporter stopped longer than the overlap ══════════════════════

def test_b4_after_a_long_stop_the_next_run_takes_every_run_in_the_gap(sc):
    tempo = FakeTempo(household(1, start=NOW - 7200))
    sc.run(tempo, NOW)
    gap = [vrun(f"G{i}", NOW + h * 3600, heard="invented") for i, h in enumerate((1, 2.5, 4.5))]
    tempo.runs += gap
    later = NOW + 5 * 3600
    sc.run(tempo, later)
    assert {"G0", "G1", "G2"} <= set(sc.rows())
    q = urllib.parse.parse_qs(urllib.parse.urlparse(tempo.search_calls()[-1]).query)
    assert sc.state()["watermark_s"] == pytest.approx(later - EX.FINAL_S)


# ═══ B5: labels survive; a removed row stays removed ═══════════════════════

def test_b5_a_label_survives_100_exports_and_a_removed_row_stays_removed(sc):
    runs = household(3, start=NOW - 1800, gap=60)
    tempo = FakeTempo(runs)
    sc.run(tempo, NOW)
    sc.person("INSERT INTO labels VALUES ('R1', 'invented correct text', 'household', 'corpus-77', 'a note', "
              "'2026-11-19T10:00:00+00:00', 'drew')")
    sc.person("UPDATE runs SET removed = '2026-11-19 drew: a guest', heard = NULL, reply = NULL, raw_trace = NULL "
              "WHERE run_id = 'R2'")
    label = sc.one("SELECT * FROM labels")
    for i in range(100):
        runs[i % 3]["attrs"]["voice.handled_by"] = ("kronk", "ha_local")[i % 2]   # rows keep changing
        assert sc.run(tempo, NOW + 60 * (i + 1))[0] == 0
    assert tuple(sc.one("SELECT * FROM labels")) == tuple(label)
    r2 = sc.rows()["R2"]
    assert r2["removed"] == "2026-11-19 drew: a guest" and r2["heard"] is None and r2["reply"] is None
    assert r2["raw_trace"] is None
    for dev in (False, True):
        assert "R2" not in sc.published(dev) and {"R1", "R3"} <= set(sc.published(dev))
    assert sc.status_json()["summary"]["household"]["runs_24h"] == 2


# ═══ B6: where text is, and where it is not ════════════════════════════════

def test_b6_the_record_is_600_in_a_700_directory_and_the_defaults_are_where_they_should_be(sc):
    sc.run(FakeTempo(household(1)), NOW)
    assert stat.S_IMODE(os.stat(sc.db.parent).st_mode) == 0o700
    assert stat.S_IMODE(os.stat(sc.db).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(sc.pub).st_mode) == 0o600
    root = ROOT.resolve()
    assert root not in EX.DEFAULT_DB.resolve().parents
    assert EX.DEFAULT_DB == Path.home() / ".local" / "state" / "kronk-voice" / "voice_runs.db"
    for p in EX.DEFAULT_PUBLISH + EX.DEFAULT_STATUS:
        assert (root / "data") in p.resolve().parents


def test_b6_an_existing_record_others_can_read_is_refused(sc):
    sc.run(FakeTempo(household(1)), NOW)
    os.chmod(sc.db, 0o644)
    rc, log = sc.run(FakeTempo(household(1)), NOW + 60)
    assert rc == 1 and "mode 644" in log
    os.chmod(sc.db, 0o600)
    os.chmod(sc.db.parent, 0o755)
    rc, log = sc.run(FakeTempo(household(1)), NOW + 60)
    assert rc == 1 and "directory" in log


@pytest.mark.parametrize("bad", ["db_in_repo", "publish_in_repo", "status_in_repo", "partial"])
def test_b6_paths_in_the_repo_outside_data_and_partial_arguments_are_refused(sc, bad):
    a = sc.args()
    if bad == "db_in_repo":
        a[a.index("--db") + 1] = str(ROOT / "voice_runs.db")
    elif bad == "publish_in_repo":
        a[a.index("--publish") + 1] = str(ROOT / "docs" / "voice_runs.db")
    elif bad == "status_in_repo":
        a[a.index("--status") + 1] = str(ROOT / "scripts" / "voice_runs.json")
    else:
        i = a.index("--status")
        del a[i:i + 4]
    out = io.StringIO()
    assert EX.main(a, now=NOW, get_json=FakeTempo(household(1)), out=out) == 2
    assert "REFUSED" in out.getvalue()
    assert not sc.db.exists() and not sc.pub.exists() and not sc.status.exists()
    assert not (ROOT / "voice_runs.db").exists() and not (ROOT / "docs" / "voice_runs.db").exists()


def test_b6_no_transcript_text_in_the_log_the_status_or_a_leftover_temporary_file(sc, monkeypatch):
    sc.data = sc.pub.parent
    sc.data.mkdir(parents=True)
    stale = sc.data / ".voice_runs.db.4242.0badc0de.tmp"
    stale.write_text(f"left by a killed run {MARK}")
    os.utime(stale, (NOW - 1000, NOW - 1000))
    young = sc.data / ".voice_runs.db.4243.0badc0df.tmp"
    young.write_text("a run in progress")
    os.utime(young, (NOW - 10, NOW - 10))
    tempo = FakeTempo(household(3) + [vrun("R-dev", T0 + 50, service=DEV, pipeline="kronk-dev",
                                           heard=f"dev words {MARK}", reply=f"dev reply {MARK}")])
    rc, log = sc.run(tempo, NOW, "--check-now")
    tempo.runs[0]["attrs"]["voice.handled_by"] = "ha_local"
    rc2, log2 = sc.run(tempo, NOW + 60)
    assert rc == rc2 == 0
    assert MARK not in log + log2
    assert not stale.exists() and young.exists()           # only ours, and only old
    young.unlink()
    for p in (sc.status, sc.status_dev):
        assert MARK not in p.read_text()
    # Only the record and the two copies hold the words; nothing else in scratch does.
    holders = sorted(str(p.relative_to(sc.root)) for p in sc.root.rglob("*")
                     if p.is_file() and MARK.encode() in p.read_bytes())
    assert holders == ["data/dev/voice_runs.db", "data/voice_runs.db", "state/kronk-voice/voice_runs.db"]
    assert [p.name for p in sc.root.rglob("*") if EX.TEMP_RE.fullmatch(p.name) or p.name.endswith("-journal")] == []


def test_b6_a_publish_that_fails_midway_leaves_no_temporary_file(sc, monkeypatch):
    real = os.replace

    def broken(a, b):
        if str(b).endswith(".db"):
            raise OSError(28, "No space left on device")
        return real(a, b)
    monkeypatch.setattr(EX.os, "replace", broken)
    rc, log = sc.run(FakeTempo(household(1)), NOW)
    assert rc == 1 and "publishing failed" in log
    assert sc.status_json()["ok"] is False and "No space left" in sc.status_json()["last_error"]
    assert [p.name for p in sc.root.rglob("*") if p.name.endswith(".tmp")] == []
    assert not sc.pub.exists()


def test_b6_the_copies_and_status_files_are_gitignored():
    paths = ["data/voice_runs.db", "data/dev/voice_runs.db", "data/voice_runs.json", "data/dev/voice_runs.json",
             "data/.voice_runs.db.1.0badc0de.tmp"]
    p = subprocess.run(["git", "-C", str(ROOT), "check-ignore", "--no-index", *paths], capture_output=True, text=True,
                       env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"})
    assert p.stdout.split() == paths


# ═══ B7: the audio join is voice_label_sheet.join's ════════════════════════

def wav(root: Path, device, pipeline, run_dir, mtime, seconds=1.0, name="01_stt-stt.faster_whisper.wav"):
    d = (root / device / pipeline / run_dir) if device else (root / pipeline / run_dir)
    d.mkdir(parents=True, exist_ok=True)
    p = d / name
    with wave.open(str(p), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\0\0" * int(16000 * seconds))
    os.utime(p, (mtime, mtime))
    return p


def test_b7_the_audio_join_gives_voice_label_sheets_answer(sc):
    now = T0 + 7200
    runs = [
        vrun("R-certain", T0, dur=6, heard="invented", kronk__origin__device_id=DEV_K),
        # Two runs on the same pipeline, no device on either side, one recording that fits both.
        vrun("R-amb-1", T0 + 1000, dur=6, pipeline="kronk-typed", heard="invented"),
        vrun("R-amb-2", T0 + 1003, dur=6, pipeline="kronk-typed", heard="invented"),
        vrun("R-none", T0 + 2000, dur=6, heard="invented", kronk__origin__device_id=DEV_K),
    ]
    p = wav(sc.rec, DEV_K, "kronk", "1203688800703734", T0 + 4)
    wav(sc.rec, None, "kronk-typed", "1203688800709999", T0 + 1005)
    wav(sc.rec, DEV_K, "kronk", "1203688800700001", T0 - 3600)          # someone else's, an hour earlier
    sc.run(FakeTempo(runs), now)
    rows = sc.rows()
    got = {k: r["audio_join"] for k, r in rows.items()}
    assert got == {"R-certain": "certain", "R-amb-1": "ambiguous", "R-amb-2": "ambiguous", "R-none": "none"}
    c = rows["R-certain"]
    assert c["audio_rel"] == f"{DEV_K}/kronk/1203688800703734/01_stt-stt.faster_whisper.wav"
    assert (c["audio_bytes"], c["audio_mtime_ns"]) == (p.stat().st_size, p.stat().st_mtime_ns)
    assert (c["audio_bytes"] - 44) / 32000 == pytest.approx(1.0)
    assert all(rows[k]["audio_rel"] is None for k in ("R-amb-1", "R-amb-2", "R-none"))
    # The same answer from voice_label_sheet.join itself, on the same runs and recordings.
    recs = LS.scan(sc.rec)
    lruns = [EX.run_for_join(r) for r in rows.values()]
    LS.join(recs, lruns)
    certain = {r.run["ha.pipeline_run_id"] for r in recs if r.run}
    ambiguous = {i for r in recs if r.note.startswith("ambiguous")
                 for i in r.note.split("candidate runs ")[1].split(";")[0].split()}
    want = {k: "certain" if k in certain else "ambiguous" if k in ambiguous else "none" for k in rows}
    assert got == want
    assert "audio_join" not in sc.published()["R-certain"]                # not published


def test_b7_an_old_run_is_joined_when_its_recording_is_local_and_not_tried_when_it_is_not(sc):
    """Fix round 1, note 7: no age limit on the join. A row older than seven
    days whose recording is local is joined; one with none is NULL (its
    recording may be on the NAS), and a younger one with none is 'none'.
    Each is tried once."""
    runs = [vrun("R-old", NOW - 8 * 86400, heard="invented", kronk__origin__device_id=DEV_K),
            vrun("R-old-gone", NOW - 9 * 86400, heard="invented", kronk__origin__device_id=DEV_K),
            vrun("R-young-none", NOW - 6 * 86400, heard="invented", kronk__origin__device_id=DEV_K)]
    wav(sc.rec, DEV_K, "kronk", "1", NOW - 8 * 86400 + 3)
    sc.run(FakeTempo(runs), NOW)
    rows = sc.rows()
    assert rows["R-old"]["audio_join"] == "certain" and rows["R-old"]["audio_rel"].startswith(f"{DEV_K}/kronk/1/")
    assert rows["R-old-gone"]["audio_join"] is None and rows["R-old-gone"]["audio_tried_at"] is not None
    assert rows["R-young-none"]["audio_join"] == "none"
    wav(sc.rec, DEV_K, "kronk", "2", NOW - 9 * 86400 + 3)          # appears later: tried once, not again
    sc.run(FakeTempo(runs), NOW + 60)
    assert sc.rows()["R-old-gone"]["audio_join"] is None


def test_b7_an_unreadable_recordings_directory_leaves_the_row_to_be_tried_again(sc):
    """Fix round 1, F1: a directory that cannot be read when the row is
    final leaves it untried, and the next run with the directory back
    joins it."""
    sc.rec.rmdir()
    runs = household(1, start=NOW - 3600)
    sc.run(FakeTempo(runs), NOW)
    r = sc.rows()["R1"]
    assert r["audio_join"] is None and r["audio_tried_at"] is None
    assert "cannot be read" in sc.status_json()["this_run"]["audio_not_tried"]
    assert "audio not joined" in sc.status_json()["last_error"]
    sc.rec.mkdir()
    wav(sc.rec, DEV_K, "kronk", "5", runs[0]["start"] + 3)
    sc.run(FakeTempo(runs), NOW + 60)
    assert sc.rows()["R1"]["audio_join"] == "certain"


# ═══ The hourly check, the published copy ══════════════════════════════════

def test_the_hourly_check_reports_a_difference_rewrites_nothing_and_takes_a_missing_run(sc):
    """A differing value is reported and not applied. A run the record
    should hold and does not is taken (fix round 1, F4: an insert, not a
    rewrite)."""
    runs = household(2)
    tempo = FakeTempo(runs)
    sc.run(tempo, NOW)
    seqs = {k: r["seq"] for k, r in sc.rows().items()}
    runs[0]["attrs"]["voice.corrected_by"] = "rate_down"            # a re-emitted span, long after
    tempo.runs.append(vrun("R-lost", T0 + 5000, heard="invented"))    # a run the record never took
    sc.run(tempo, NOW + 60)                                         # incremental: both outside its window
    assert sc.rows()["R1"]["corrected_by"] is None and "R-lost" not in sc.rows()
    sc.run(tempo, NOW + EX.CHECK_EVERY_S)                           # the hourly read
    rows = sc.rows()
    assert rows["R1"]["corrected_by"] is None and "R-lost" in rows
    assert rows["R-lost"]["seq"] == max(seqs.values()) + 1 and rows["R-lost"]["heard"] == "invented"
    assert {k: r["seq"] for k, r in rows.items() if k != "R-lost"} == seqs
    (ch,) = sc.changes()
    assert ch["why"] == "hourly check" and json.loads(ch["detail"]) == {"tempo": {"corrected_by": "rate_down"}}
    chk = sc.status_json()["last_check"]
    assert (chk["differing"], chk["missing"], chk["taken"], chk["missing_ids"]) == (1, 1, 1, ["R-lost"])
    assert "hourly check" in sc.status_json()["last_error"] and "taken" in sc.status_json()["last_error"]
    sc.run(tempo, NOW + 2 * EX.CHECK_EVERY_S, "--check-now")
    assert len(sc.changes()) == 1                                   # the same difference once
    assert sc.status_json()["last_check"]["missing"] == 0           # taken once, missing no more


def test_the_published_copy_holds_the_page_columns_for_90_days_and_opens_immutable(sc):
    tempo = FakeTempo(household(2))
    sc.run(tempo, NOW)
    c = sqlite3.connect(f"file:{sc.pub}?mode=ro&immutable=1", uri=True)
    try:
        cols = tuple(r[1] for r in c.execute("PRAGMA table_info(runs)"))
        meta = c.execute("SELECT schema, built_s, rows, max_seq FROM meta").fetchone()
        assert c.execute("PRAGMA user_version").fetchone()[0] == EX.PUBLISH_SCHEMA
    finally:
        c.close()
    assert cols == EX.PUBLISHED_COLUMNS
    assert not {"raw_trace", "audio_rel", "removed", "last_seen_at"} & set(cols)
    assert meta == (EX.PUBLISH_SCHEMA, NOW, 2, 2)
    assert sorted(p.name for p in sc.pub.parent.iterdir() if p.is_file()) == ["voice_runs.db", "voice_runs.json"]
    assert sha(sc.pub) == sha(sc.pub_dev)
    # 95 days on: the copy drops them; the record keeps them (nothing is deleted).
    tempo.runs = []
    sc.run(tempo, NOW + 95 * 86400)
    assert sc.published() == {} and set(sc.rows()) == {"R1", "R2"}


def test_an_export_holding_the_lock_makes_a_second_one_do_nothing(sc):
    sc.run(FakeTempo(household(1)), NOW)
    fd = EX.lock_dir(sc.db)
    try:
        before = sha(sc.db)
        tempo = FakeTempo(household(2))
        rc, log = sc.run(tempo, NOW + 60)
        assert rc == 0 and "another export" in log and tempo.calls == [] and sha(sc.db) == before
    finally:
        os.close(fd)


# ═══ B8: backup and restore, and the stale warning ═════════════════════════

def restore_block() -> str:
    text = (ROOT / "docs" / "runbooks" / "restore.md").read_text()
    m = re.search(r"```\n(mkdir -p -m 700 ~/.local/state/kronk-voice\n.*?)```", text, re.S)
    assert m, "the restore runbook's voice-run step is missing"
    return m.group(1)


def restore_into(home: Path, copy: Path) -> str:
    """The runbook's step, run as written, with stale -wal, -shm and
    -journal files planted beside the target first."""
    target = home / ".local" / "state" / "kronk-voice"
    target.mkdir(parents=True, mode=0o700, exist_ok=True)
    (target / "voice_runs.db").write_bytes(b"an older, damaged record")
    for suffix in ("-wal", "-shm", "-journal"):
        (target / f"voice_runs.db{suffix}").write_bytes(b"stale " + suffix.encode() * 200)
    script = restore_block().replace("$VOICE_COPY", shlex.quote(str(copy)))
    p = subprocess.run(["sh", "-ec", script], capture_output=True, text=True,
                       env={"HOME": str(home), "PATH": os.environ["PATH"]})
    assert p.returncode == 0, p.stderr
    assert sorted(x.name for x in target.iterdir()) == ["voice_runs.db"]
    assert stat.S_IMODE((target / "voice_runs.db").stat().st_mode) == 0o600
    return p.stdout


def ids_and_labels(db: Path):
    c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        assert c.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
        return (sorted(r[0] for r in c.execute("SELECT run_id FROM runs")),
                sorted(tuple(r) for r in c.execute("SELECT * FROM labels")))
    finally:
        c.close()


def real_record(house, tmp_path, n=5):
    """A record made by the exporter at the house's path, with a label."""
    M16.no_voice_record(house)                     # the house's stand-in, replaced by a real one
    sc = Scratch(tmp_path / "exp")
    a = sc.args("--init")
    a[a.index("--db") + 1] = str(house.voice_db)
    out = io.StringIO()
    assert EX.main(a, now=NOW, get_json=FakeTempo(household(n)), out=out) == 0, out.getvalue()
    c = sqlite3.connect(house.voice_db)
    c.execute("INSERT INTO labels VALUES ('R2', 'invented', 'household', NULL, NULL, '2026-11-19T00:00:00Z', 'drew')")
    c.commit()
    c.close()
    return ids_and_labels(house.voice_db)


def test_b8_from_the_config_tar(house, tmp_path):
    want = real_record(house, tmp_path)
    ops = FakeOps()
    assert B.cmd_config(house, ops, dry_run=False) == 0
    tar = next((house.dest / "config").iterdir())
    member = "kronk-config-2026-12-10/voice-state/voice_runs.db"
    with tarfile.open(tar, "r:gz") as t:
        assert t.getmember(member).mode == 0o600
        blob = t.extractfile(member).read()
    man = M16.read_member(tar, "MANIFEST").decode()
    assert "sqlite backup\tvoice-state/voice_runs.db\t<- " + str(house.voice_db) in man
    R = tmp_path / "unpacked"
    (R / "voice-state").mkdir(parents=True)
    (R / "voice-state" / "voice_runs.db").write_bytes(blob)
    home = tmp_path / "restored-home"
    out = restore_into(home, R / "voice-state" / "voice_runs.db")
    assert ids_and_labels(home / ".local" / "state" / "kronk-voice" / "voice_runs.db") == want
    assert out.split() == ["ok", "5", "1"]


def test_b8_from_the_weekly_copy_with_the_recordings_archive(house, tmp_path):
    want = real_record(house, tmp_path)
    house.recordings.mkdir()
    ops = FakeOps()
    assert B.cmd_archive(house, ops, dry_run=False) == 0, ops.notices[-1][1]
    copies = sorted((house.dest / "voice-runs").iterdir())
    assert [p.name for p in copies] == ["voice_runs-2026-12-10.db"]
    assert stat.S_IMODE(copies[0].stat().st_mode) == 0o600
    assert stat.S_IMODE((house.dest / "voice-runs").stat().st_mode) == 0o700
    title, body = ops.notices[-1]
    assert title == "Kronk backup: recordings archive OK"
    assert "Voice-run record: voice_runs-2026-12-10.db (5 runs, 1 labels" in body
    st = json.loads((house.state / "status.json").read_text())
    assert st["archive"]["last"]["voice_runs"]["result"] == "ok"
    assert MARK not in json.dumps(st) and all(MARK not in b for _, b in ops.notices)
    home = tmp_path / "restored-home"
    restore_into(home, copies[0])
    assert ids_and_labels(home / ".local" / "state" / "kronk-voice" / "voice_runs.db") == want


def test_b8_weekly_copies_keep_the_newest_eight_dates_and_never_overwrite(house, tmp_path):
    real_record(house, tmp_path)
    d = house.dest / "voice-runs"
    d.mkdir(mode=0o700)
    (d / "notes.txt").write_text("a person's file")
    ops = FakeOps()
    for i in range(10):
        ops.t = M16.NOW + dt.timedelta(days=7 * i)
        assert B.voice_runs_copy(house, ops)["result"] == "ok"
    again = B.voice_runs_copy(house, ops)                     # the same day by hand: .2, never over the first
    assert again["result"] == "ok" and again["file"].endswith(".2.db")
    names = sorted(p.name for p in d.iterdir())
    dates = sorted({B.VOICE_COPY_RE.fullmatch(n).group(1) for n in names if B.VOICE_COPY_RE.fullmatch(n)})
    assert len(dates) == 8 and "notes.txt" in names
    assert dates[0] == (M16.NOW + dt.timedelta(days=14)).date().isoformat()


def test_b8_the_weekly_copy_with_no_record_yet_is_noted_not_a_failure(house):
    M16.no_voice_record(house)
    house.recordings.mkdir()
    ops = FakeOps()
    assert B.cmd_archive(house, ops, dry_run=False) == 0
    assert not (house.dest / "voice-runs").exists()
    assert "Voice-run record: " in ops.notices[-1][1] and "no copy" in ops.notices[-1][1]


def test_b8_a_record_that_will_not_read_fails_the_archive_run(house):
    M16.no_voice_record(house)
    house.voice_db.parent.mkdir(parents=True, mode=0o700)
    house.voice_db.write_bytes(b"not a database" * 300)
    house.recordings.mkdir()
    ops = FakeOps()
    assert B.cmd_archive(house, ops, dry_run=False) == 1
    title, body = ops.notices[-1]
    assert title == "Kronk backup: recordings archive FAILED" and "Voice-run record NOT copied" in body
    assert not (house.dest / "voice-runs").exists() or list((house.dest / "voice-runs").iterdir()) == []


def test_b8_the_weekly_copy_is_made_when_only_the_recordings_directory_is_missing(house, tmp_path):
    """Capture off and the directory gone fails the move (exit 2, as before
    M30); the record still leaves the box. A destination that cannot be
    written gets nothing."""
    real_record(house, tmp_path)
    ops = FakeOps()
    assert B.cmd_archive(house, ops, dry_run=False) == 2
    assert [p.name for p in (house.dest / "voice-runs").iterdir()] == ["voice_runs-2026-12-10.db"]
    house.require_mount = True                       # the share is "not mounted"
    house.mount_wait_s = 0
    ops = FakeOps()
    assert B.cmd_archive(house, ops, dry_run=False) == 2
    assert "voice_runs" not in json.loads((house.state / "status.json").read_text())["archive"]["last"]


@pytest.mark.parametrize("age_days,warns", [(6, False), (8, True), (None, True)])
def test_b8_the_stale_check_warns_when_the_last_good_read_is_over_seven_days_old(house, age_days, warns):
    ok = None if age_days is None else B.iso(M16.NOW - dt.timedelta(days=age_days))
    M16.voice_record(house, last_ok_at=ok)
    w = [x for x in B.stale_warnings(house, FakeOps(), st={}) if "voice-run" in x]
    assert bool(w) == warns
    if age_days == 8:
        assert w == ["the voice-run export's last good read was 8 days ago (limit 7): see journalctl --user -u "
                     "kronk-voice-runs-export"]


def test_b8_the_stale_check_before_the_first_export_is_quiet_and_a_lost_record_is_not(house):
    M16.no_voice_record(house)
    assert [x for x in B.stale_warnings(house, FakeOps(), st={}) if "voice-run" in x] == []
    (house.repo / "data" / "voice_runs.json").write_text('{"ts": 1}')
    w = [x for x in B.stale_warnings(house, FakeOps(), st={}) if "voice-run" in x]
    assert w and "is missing" in w[0]


def test_b8_the_record_is_required_since_the_first_export(house):
    """In required_sources from the first export on (criteria, plumbing 11;
    added in step C, the record being live since 2026-11-26): a tar without
    it is INCOMPLETE, and with it the run is OK."""
    assert str(house.voice_db) in B.required_sources(house)
    assert B.cmd_config(house, FakeOps(), dry_run=False) == 0
    M16.no_voice_record(house)
    ops = FakeOps()
    assert B.cmd_config(house, ops, dry_run=False) == 4
    title, body = ops.notices[-1]
    assert title == "Kronk backup: config backup INCOMPLETE" and "voice_runs.db" in body


# ═══ B9: an incremental run under a second, twenty times ═══════════════════

def test_b9_an_incremental_run_takes_under_a_second_twenty_times(sc):
    year = [vrun(f"Y{i:04d}", NOW - 89 * 86400 + i * 3000, heard=f"invented {i}", reply="invented",
                 voice__handled_by="kronk") for i in range(2500)]
    tempo = FakeTempo(year)
    sc.run(tempo, NOW)
    for k in range(12):                                     # the trace backlog of a first run, drained
        sc.run(tempo, NOW + 1 + k)
        if sc.one("SELECT COUNT(*) FROM runs WHERE raw_trace IS NULL")[0] == 0:
            break
    assert sc.one("SELECT COUNT(*) FROM runs")[0] == 2500
    took = []
    for i in range(20):
        now = NOW + 60 * (i + 1)
        tempo.runs.append(vrun(f"N{i}", now - 200, heard="invented"))
        extra = ("--check-now",) if i == 10 else ()
        t = time.monotonic()
        rc, log = sc.run(tempo, now, *extra)
        took.append(time.monotonic() - t)
        assert rc == 0, log
    assert max(took) < 1.0, took
    assert sc.one("SELECT COUNT(*) FROM runs")[0] == 2520


# ═══ The listing, the units ════════════════════════════════════════════════

def test_voice_transcripts_query_is_unchanged_without_the_filter_and_refuses_a_bad_one():
    sel = ", ".join(f"span.{a}" for a in VT.ATTRS) + ", resource.service.name"
    assert VT.search_query() == '{ name = "voice.run" } | select(' + sel + ")"
    assert VT.search_query(".*stt.*") == '{ name = "voice.run" && span.voice.stages =~ ".*stt.*" } | select(' + sel + ")"
    with pytest.raises(ValueError):
        VT.search_query('stt" } || { true')
    err = io.StringIO()
    old, sys.stderr = sys.stderr, err
    try:
        assert VT.main(["--out", str(ROOT / "x.csv")], now=NOW, get_json=FakeTempo([])) == 2
    finally:
        sys.stderr = old
    assert "REFUSED" in err.getvalue()


def test_the_timer_units():
    svc = (ROOT / "systemd" / "kronk-voice-runs-export.service").read_text()
    tmr = (ROOT / "systemd" / "kronk-voice-runs-export.timer").read_text()
    assert "# Operations:" in svc and "Type=oneshot" in svc and "UMask=0077" in svc
    exe = re.search(r"^ExecStart=/usr/bin/python3 -B %h/git-repos/drawsmcgraw/kronk/(\S+)$", svc, re.M)
    assert exe and (ROOT / exe.group(1)).is_file() and exe.group(1) == "scripts/voice_runs_export.py"
    assert re.search(r"^TimeoutStartSec=(\d+)$", svc, re.M) and int(re.search(r"^TimeoutStartSec=(\d+)$", svc, re.M)
                                                                        .group(1)) < 60
    assert "OnUnitActiveSec=60" in tmr and "WantedBy=timers.target" in tmr
