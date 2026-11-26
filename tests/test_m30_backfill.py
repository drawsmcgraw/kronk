"""Work item M30 step D: the words of the early runs, from the Whisper
journal (scripts/voice_runs_backfill.py). Criteria D1 to D5 of
docs/sprint/M30-criteria.md, and plumbing 5 (the audio join against the
NAS layout).

Everything runs in tmp_path: a scratch record made by the exporter against
the fake Tempo of test_m30_voice_runs, a fixture journal file, recordings in
HA's layout and in the archive's. No journalctl is run (one test replaces
subprocess.run), no socket is opened, nothing under the real data/ or
~/.local/state/kronk-voice/ is touched. Every sentence is invented; MARK is
planted in each so a leak is found by search."""
import datetime as dt
import fcntl
import hashlib
import io
import json
import os
import subprocess
from pathlib import Path

import pytest

from tests import test_m30_voice_runs as M30

pytestmark = pytest.mark.usefixtures("no_network")
sc = M30.sc                      # the scratch record, as a fixture here

EX = M30.EX
LS = M30.LS
BF = M30._load("voice_runs_backfill", "voice_runs_backfill_m30d")
MARK = M30.MARK
HH, DEVU = BF.HOUSEHOLD_UNIT, BF.DEV_UNIT
NOW = M30.NOW
TB = NOW - 10 * 86400            # the early runs: ten days before NOW, older than the archive's week
GAP = 600.0
STT_END = 2.265                  # the fake Tempo's voice.stt span ends this long after the run starts
LOGGER = "INFO:wyoming_faster_whisper.dispatch_handler:"


def said(n: int) -> str:
    """An invented transcript as Whisper logs it: a leading space."""
    return f" Invented sentence number {n} for the backfill {MARK}."


def entry(unit, t, msg, own=True):
    e = {"__REALTIME_TIMESTAMP": str(int(t * 1e6)), "MESSAGE": msg, "_TRANSPORT": "stdout",
         "PRIORITY": "6", "SYSLOG_IDENTIFIER": "python"}
    if own:
        e["_SYSTEMD_USER_UNIT"] = unit
    else:                                           # systemd's own line about the unit
        e["USER_UNIT"] = unit
        e["_SYSTEMD_UNIT"] = "user@1000.service"
    return e


class Journal:
    """A fixture journalctl -o json export, both units, covering
    [TB - 1 h, NOW] unless told otherwise."""

    def __init__(self, path: Path, start=TB - 3600, end=NOW):
        self.path = path
        self.entries = []
        for u in (HH, DEVU):
            self.entries.append(entry(u, start, f"Started {u}.", own=False))
            self.entries.append(entry(u, start + 1, "INFO:__main__:Ready"))
            self.entries.append(entry(u, end, "INFO:faster_whisper:Processing audio with duration 00:01.500"))

    def say(self, unit, t, text):
        self.entries.append(entry(unit, t - 0.3, "INFO:faster_whisper:Processing audio with duration 00:02.000"))
        self.entries.append(entry(unit, t, LOGGER + text))
        return text

    def write(self):
        self.entries.sort(key=lambda e: int(e["__REALTIME_TIMESTAMP"]))
        self.path.write_text("".join(json.dumps(e) + "\n" for e in self.entries))
        return self.path


def early(rid, i, chars, **kw):
    """A run of the early days: no words in Tempo, the count only."""
    kw = {"ha__satellite_id": M30.KITCHEN, "kronk__origin__device_id": M30.DEV_K, "voice__handled_by": "kronk",
          **({} if chars is None else {"voice__transcript_chars": chars}), **kw}
    return M30.vrun(rid, TB + i * GAP, **kw)


def at(i):
    """When Whisper logged run i's transcript: just after its STT span."""
    return TB + i * GAP + STT_END + 0.05


def bf(sc, journal: Path, *extra, now=NOW + 60):
    out = io.StringIO()
    rc = BF.main(["--db", str(sc.db), "--journal", str(journal), "--recordings", str(sc.rec), *extra],
                 now=now, out=out)
    return rc, out.getvalue()


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def lines_for(log: str, rid: str) -> list[str]:
    return [ln for ln in log.splitlines() if f" {rid}  " in ln]


@pytest.fixture
def house(sc, tmp_path):
    """The D1 house: a record with one run of each kind and a journal that
    holds their transcripts."""
    j = Journal(tmp_path / "whisper.json")
    texts = {}
    runs = []
    # 1. A certain match.
    texts["R-fill"] = j.say(HH, at(1), said(1))
    runs.append(early("R-fill", 1, len(said(1))))
    # 2. Two transcripts in the window.
    j.say(HH, at(2), said(2))
    j.say(HH, at(2) + 0.5, said(22))
    runs.append(early("R-two", 2, len(said(2))))
    # 3. A length that differs.
    j.say(HH, at(3), said(3) + " more")
    runs.append(early("R-len", 3, len(said(3))))
    # 4. A removed row, its transcript there.
    j.say(HH, at(4), said(4))
    runs.append(early("R-removed", 4, len(said(4))))
    # 5. A row that already has text (Tempo's, stripped; the count unstripped).
    j.say(HH, at(5), said(5))
    runs.append(M30.vrun("R-has", TB + 5 * GAP, heard=said(5).strip(), voice__transcript_chars=len(said(5)),
                         ha__satellite_id=M30.KITCHEN))
    # 6. A dev row; the only transcript in its window is the household unit's.
    j.say(HH, at(6), said(6))
    runs.append(M30.vrun("R-dev", TB + 6 * GAP, service=M30.DEV, pipeline="kronk-dev",
                         stages="wake_word,stt,intent,tts", voice__transcript_chars=len(said(6))))
    # 7. A dev row with its own unit's transcript: filled.
    texts["R-dev-own"] = j.say(DEVU, at(7), said(7))
    runs.append(M30.vrun("R-dev-own", TB + 7 * GAP, service=M30.DEV, pipeline="kronk-dev",
                         stages="wake_word,stt,intent,tts", voice__transcript_chars=len(said(7))))
    # 8. No stored count, its transcript alone in the journal: left (fix round 1 of step D).
    j.say(HH, at(8), said(8))
    runs.append(early("R-nocount", 8, None))
    # 9. A stored count and its transcript, but no voice.stt span in the trace: left (no
    #    whole-run window, fix round 1 of step D).
    j.say(HH, at(9), said(9))
    runs.append(early("R-nospan", 9, len(said(9)), children=False))
    # 10. Whisper heard nothing.
    runs.append(early("R-zero", 10, 0, voice__error_code="stt-no-text-recognized"))
    # 11. No transcript at all in the window.
    runs.append(early("R-none", 11, 40))
    tempo = M30.FakeTempo(runs)
    rc, log = sc.run(tempo, NOW)
    assert rc == 0, log
    rc, log = sc.run(tempo, NOW + 10, "--remove", "R-removed")
    assert rc == 0, log
    return {"journal": j.write(), "texts": texts, "tempo": tempo, "runs": runs}


# ═══ D1: a certain match is filled; everything else left, with its reason ═══

def test_d1_a_certain_match_is_filled_and_each_other_row_is_left_with_its_reason(sc, house):
    before = sc.rows()
    rc, log = bf(sc, house["journal"], "--apply", "--no-audio")
    assert rc == 0, log
    rows = sc.rows()
    for rid in ("R-fill", "R-dev-own"):
        r = rows[rid]
        assert r["heard"] == house["texts"][rid].strip(), rid
        assert r["heard_source"] == "whisper_journal"
        assert r["heard_chars"] == before[rid]["heard_chars"]            # Tempo's count, untouched
        assert r["seq"] > max(b["seq"] for b in before.values())
        assert r["last_changed_at"] == EX.now_iso(NOW + 60)
        assert lines_for(log, rid)[0].startswith("voice_runs_backfill: fill ")
    reasons = {
        "R-two": "several transcripts in the window: 2 from wyoming-whisper.service",
        "R-len": f"length differs: journal {len(said(3) + ' more')} characters, row {len(said(3))}",
        "R-removed": "removed",
        "R-dev": "no transcript in the window: none from wyoming-whisper-dev.service within 1 s of the "
                 "stt span's end; 1 from wyoming-whisper.service",
        "R-nocount": "no stored count",
        "R-nospan": "no speech-to-text span: no voice.stt span in the trace",
        "R-zero": "Whisper heard nothing",
        "R-none": "no transcript in the window: none from wyoming-whisper.service",
    }
    for rid, why in reasons.items():
        assert rows[rid] == before[rid], rid                             # left as it is, every column
        got = lines_for(log, rid)
        assert len(got) == 1 and got[0].startswith(f"voice_runs_backfill: left  {rid}  {why}"), (rid, got)
    # The row with text is left too, and the journal agrees with what Tempo gave it.
    assert rows["R-has"] == before["R-has"]
    assert "the journal agrees" in lines_for(log, "R-has")[0]
    assert "filled 2;" in log and "already with text 1 (the journal agrees on 1); left 8" in log
    assert rows["R-removed"]["heard"] is None and rows["R-removed"]["removed"] is not None
    # Nothing replaced, so nothing in run_changes.
    assert sc.changes() == []


def test_d1_the_window_is_the_end_of_the_stt_span_one_second_either_side(sc, tmp_path):
    """A transcript logged just inside a second either side of the STT
    span's end is the run's; one just outside is not. Without a voice.stt
    span there is no window, and the row is left (fix round 1 of step D)."""
    assert BF.STT_END_S == 1.0
    j = Journal(tmp_path / "w.json")
    j.say(HH, TB + GAP + STT_END + BF.STT_END_S - 0.01, said(1))
    j.say(HH, TB + 2 * GAP + STT_END + BF.STT_END_S + 0.01, said(2))
    j.say(HH, TB + 3 * GAP + STT_END - BF.STT_END_S + 0.01, said(3))
    j.say(HH, TB + 4 * GAP + STT_END - BF.STT_END_S - 0.01, said(4))
    j.say(HH, at(5), said(5))
    runs = [early("R-in", 1, len(said(1))), early("R-out", 2, len(said(2))),
            early("R-in-before", 3, len(said(3))), early("R-out-before", 4, len(said(4))),
            early("R-nospan", 5, len(said(5)), children=False)]
    sc.run(M30.FakeTempo(runs), NOW)
    rc, log = bf(sc, j.write(), "--apply", "--no-audio")
    assert rc == 0, log
    rows = sc.rows()
    assert rows["R-in"]["heard_source"] == rows["R-in-before"]["heard_source"] == "whisper_journal"
    for rid in ("R-out", "R-out-before"):
        assert rows[rid]["heard"] is None and "no transcript in the window" in lines_for(log, rid)[0]
    assert rows["R-nospan"]["heard"] is None and "no speech-to-text span" in lines_for(log, "R-nospan")[0]


def test_d1_a_journal_that_does_not_cover_the_window_leaves_the_row(sc, tmp_path):
    j = Journal(tmp_path / "w.json", start=TB + GAP + STT_END)  # begins inside R-1's window
    j.say(HH, at(1), said(1))
    sc.run(M30.FakeTempo([early("R-1", 1, len(said(1)))]), NOW)
    rc, log = bf(sc, j.write(), "--apply", "--no-audio")
    assert rc == 0
    assert sc.rows()["R-1"]["heard"] is None
    assert "left  R-1  the journal does not cover the run: wyoming-whisper.service from" in log


def test_systemds_own_lines_and_other_units_are_not_transcripts(tmp_path):
    p = tmp_path / "j.json"
    good = [
        entry(HH, TB, LOGGER + said(1), own=False),                     # systemd's line, not Whisper's
        entry("kronk-voice-runs-export.service", TB + 1, LOGGER + said(2)),
        entry(HH, TB + 2, "INFO:wyoming_faster_whisper.handler:" + said(3)),   # the older module name
        entry(HH, TB + 3, "INFO:faster_whisper:Processing audio with duration 00:02.000"),
        {"MESSAGE": "no time"},
    ]
    p.write_text("".join(json.dumps(e) + "\n" for e in good) + "not json\n")
    j = BF.read_journal(str(p))
    assert [x.text for x in j.lines[HH]] == [said(3)]
    assert j.first[HH] == TB and j.unreadable == 2
    assert "kronk-voice-runs-export.service" not in j.first


def test_without_journal_the_two_units_are_read_by_journalctl_read_only(monkeypatch):
    seen = []

    def run(cmd, **kw):
        seen.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout=(json.dumps(entry(HH, TB, LOGGER + said(1))) + "\n").encode())
    monkeypatch.setattr(BF.subprocess, "run", run)
    j = BF.read_journal(None)
    assert seen == [["journalctl", "--user", "-u", "wyoming-whisper.service", "-u", "wyoming-whisper-dev.service",
                     "-o", "json", "--no-pager"]]
    assert [x.text for x in j.lines[HH]] == [said(1)]
    monkeypatch.setattr(BF.subprocess, "run",
                        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, stdout=b"", stderr=b"no access"))
    with pytest.raises(BF.JournalError, match="exited 1"):
        BF.read_journal(None)


# ═══ D2: without --apply the record's bytes do not change ══════════════════

def test_d2_a_dry_run_does_not_change_the_records_bytes(sc, house, tmp_path):
    nas = tmp_path / "nas" / "recordings"
    nas.mkdir(parents=True)
    before = sha(sc.db)
    st0 = os.stat(sc.db)
    listing = sorted(os.listdir(sc.db.parent))
    rc, log = bf(sc, house["journal"], "--nas-recordings", str(nas))
    assert rc == 0, log
    assert "dry run (no --apply)" in log and "would fill 2;" in log
    assert sha(sc.db) == before
    st1 = os.stat(sc.db)
    assert (st1.st_mtime_ns, st1.st_size, st1.st_ino) == (st0.st_mtime_ns, st0.st_size, st0.st_ino)
    assert sorted(os.listdir(sc.db.parent)) == listing                  # no -journal left beside it
    assert all(r["heard_source"] != "whisper_journal" for r in sc.rows().values())


def test_d2_a_dry_run_works_while_an_export_holds_the_lock_and_apply_waits_for_it(sc, house, monkeypatch):
    fd = os.open(sc.db.parent, os.O_RDONLY | os.O_DIRECTORY)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        rc, log = bf(sc, house["journal"], "--no-audio")
        assert rc == 0 and "would fill 2;" in log
        monkeypatch.setattr(BF.ve, "LOCK_WAIT_S", 0.3)
        before = sha(sc.db)
        rc, log = bf(sc, house["journal"], "--apply", "--no-audio")
        assert rc == 1 and "held the record's lock" in log and "nothing written" in log
        assert sha(sc.db) == before
    finally:
        os.close(fd)


# ═══ D3: a second --apply changes no row and no seq ════════════════════════

def test_d3_a_second_apply_changes_nothing(sc, house, tmp_path):
    nas = tmp_path / "nas" / "recordings"
    nas.mkdir(parents=True)
    rc, log = bf(sc, house["journal"], "--apply", "--nas-recordings", str(nas))
    assert rc == 0 and "filled 2;" in log
    rows1, sha1 = sc.rows(), sha(sc.db)
    rc, log = bf(sc, house["journal"], "--apply", "--nas-recordings", str(nas), now=NOW + 120)
    assert rc == 0, log
    assert "filled 0;" in log and "already with text 3 (the journal agrees on 3)" in log
    assert sc.rows() == rows1
    assert sha(sc.db) == sha1                                           # nothing written: rolled back


# ═══ D4: no transcript text in the output ══════════════════════════════════

def test_d4_no_transcript_text_in_the_output(sc, house, tmp_path):
    nas = tmp_path / "nas" / "recordings"
    nas.mkdir(parents=True)
    words = [said(n) for n in (1, 2, 22, 3, 4, 5, 6, 7, 8, 9)]
    for extra in ((), ("--apply",)):
        rc, log = bf(sc, house["journal"], "--nas-recordings", str(nas), *extra)
        assert rc == 0
        assert MARK not in log and "Invented" not in log and "sentence" not in log
        assert not any(w.strip() in log for w in words)
    # The fixture journal holds only invented sentences (the marker in each).
    for e in house["journal"].read_text().splitlines():
        msg = json.loads(e)["MESSAGE"]
        if msg.startswith(LOGGER):
            assert MARK in msg and msg[len(LOGGER):].startswith(" Invented sentence number")


# ═══ D5: an export after the backfill keeps the words; the hourly check agrees ═

def test_d5_an_export_and_the_hourly_check_after_the_backfill_keep_the_words(sc, house):
    rc, log = bf(sc, house["journal"], "--apply", "--no-audio")
    assert rc == 0
    filled = {k: r for k, r in sc.rows().items() if r["heard_source"] == "whisper_journal"}
    assert sorted(filled) == ["R-dev-own", "R-fill"]
    rc, log = sc.run(house["tempo"], NOW + 120, "--check-now")
    assert rc == 0, log
    rows = sc.rows()
    for rid, r in filled.items():
        assert rows[rid]["heard"] == r["heard"] and rows[rid]["heard_source"] == "whisper_journal"
        assert rows[rid]["seq"] == r["seq"]                             # Tempo's empty text changed nothing
    check = sc.status_json()["last_check"]
    assert check["missing"] == 0 and check["differing"] == 0, check
    assert [c for c in sc.changes() if c["why"] == "hourly check"] == []
    # And the export published them.
    pub = sc.published()
    assert pub["R-fill"]["heard"] == house["texts"]["R-fill"].strip()
    assert pub["R-fill"]["heard_source"] == "whisper_journal"
    assert "R-dev-own" in sc.published(dev=True)
    # A later export with the run's words in Tempo after all would be rule 1's business; the
    # ordinary export leaves the row alone.
    rc, log = sc.run(house["tempo"], NOW + 180)
    assert sc.rows()["R-fill"] == rows["R-fill"]


def test_the_backfill_refuses_a_record_inside_the_repo_and_a_missing_record(sc, house, tmp_path):
    out = io.StringIO()
    rc = BF.main(["--db", str(M30.ROOT / "data" / "nope.db"), "--journal", str(house["journal"])], out=out)
    assert rc == 2 and "REFUSED" in out.getvalue()
    out = io.StringIO()
    rc = BF.main(["--db", str(tmp_path / "gone" / "voice_runs.db"), "--journal", str(house["journal"])], out=out)
    assert rc == 1 and "no record" in out.getvalue()
    rc, log = bf(sc, tmp_path / "missing.json")
    assert rc == 1 and "the journal could not be read" in log


# ═══ Plumbing 5: the audio join against the NAS layout ═════════════════════

def nas_wav(nas: Path, device, pipeline, run_dir, mtime, seconds=1.0):
    month = dt.datetime.fromtimestamp(mtime).strftime("%Y-%m")         # backups.month_of
    p = M30.wav(nas / device / pipeline, None, month, run_dir, mtime, seconds)
    return p


def test_voice_label_sheet_scan_reads_the_archives_month_layout(tmp_path):
    nas = tmp_path / "recordings"
    p = nas_wav(nas, M30.DEV_K, "kronk", "1203688800703734", TB + 3)
    M30.wav(nas, M30.DEV_K, "kronk", "1203688800700001", TB + 9)                  # HA's own layout, still read
    # A five-part path whose third part is not a month is not the archive's layout.
    M30.wav(nas / M30.DEV_K / "kronk", None, "not-a-month", "1203688800700002", TB + 12)
    recs = {r.run_dir: r for r in LS.scan(nas)}
    assert sorted(recs) == ["1203688800700001", "1203688800703734"]
    r = recs["1203688800703734"]
    assert (r.device_id, r.pipeline, r.path, r.mtime) == (M30.DEV_K, "kronk", p, pytest.approx(TB + 3))


@pytest.fixture
def old_house(sc, tmp_path):
    """Rows the first export stored "not tried": older than a week, their
    recordings archived to the NAS before the export ran."""
    nas = tmp_path / "nas" / "recordings"
    nas.mkdir(parents=True)
    runs = [early("R-old", 1, 30), early("R-amb-1", 2, 30, pipeline="kronk-typed", kronk__origin__device_id=None),
            early("R-amb-2", 2.005, 30, pipeline="kronk-typed", kronk__origin__device_id=None),
            early("R-gone", 3, 30), early("R-near-local", 4, 30)]
    for r in runs:
        if r["attrs"].get("kronk.origin.device_id") is None:
            r["attrs"].pop("kronk.origin.device_id", None)
    sc.run(M30.FakeTempo(runs), NOW)
    rows = sc.rows()
    assert all(r["audio_join"] is None and r["audio_tried_at"] is not None for r in rows.values())
    p = nas_wav(nas, M30.DEV_K, "kronk", "1203688800703734", TB + GAP + 3)
    # The ambiguous pair: one recording with no device in HA's own no-device layout is not
    # archived (the archive takes <device>/<pipeline>/<run>/<file> only), so give it a device.
    nas_wav(nas, "0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a", "kronk-typed", "1203688800709999", TB + 2 * GAP + 4)
    nas_wav(nas, M30.DEV_K, "kronk", "1203688800704444", TB + 4 * GAP + 3)
    M30.wav(sc.rec, M30.DEV_K, "kronk", "1203688800705555", TB + 4 * GAP + 2)      # one still local, near R-near-local
    return {"nas": nas, "certain": p, "journal": Journal(tmp_path / "w.json").write()}


def test_plumbing5_the_nas_join_fills_the_rows_stored_not_tried(sc, old_house):
    nas = old_house["nas"]
    rc, log = bf(sc, old_house["journal"], "--nas-recordings", str(nas))
    assert rc == 0, log
    assert "audio: 5 row(s) stored not tried; would join 3 (1 certain, 2 ambiguous); left 2" in log
    before = sc.rows()
    rc, log = bf(sc, old_house["journal"], "--apply", "--nas-recordings", str(nas))
    assert rc == 0, log
    rows = sc.rows()
    r = rows["R-old"]
    p = old_house["certain"]
    assert r["audio_join"] == "certain"
    assert r["audio_rel"] == f"{M30.DEV_K}/kronk/1203688800703734/01_stt-stt.faster_whisper.wav"   # the month dropped
    assert (r["audio_bytes"], r["audio_mtime_ns"]) == (p.stat().st_size, p.stat().st_mtime_ns)
    assert r["audio_tried_at"] == EX.now_iso(NOW + 60)
    assert r["seq"] == before["R-old"]["seq"]                           # the audio raises no seq
    assert rows["R-amb-1"]["audio_join"] == rows["R-amb-2"]["audio_join"] == "ambiguous"
    assert rows["R-gone"] == before["R-gone"] and "left: no recording on the NAS either" in lines_for(log, "R-gone")[-1]
    assert rows["R-near-local"] == before["R-near-local"]
    assert "still under" in lines_for(log, "R-near-local")[-1]
    # The same answer as voice_label_sheet.join over the archive's recordings and the runs.
    recs = LS.scan(nas)
    LS.join(recs, [EX.run_for_join(x) for x in rows.values()])
    assert {x.run["ha.pipeline_run_id"] for x in recs if x.run} == {"R-old", "R-near-local"}
    assert sum(1 for x in recs if x.note.startswith("ambiguous")) == 1
    # Repeatable: nothing more to do.
    sha1 = sha(sc.db)
    rc, log = bf(sc, old_house["journal"], "--apply", "--nas-recordings", str(nas), now=NOW + 120)
    assert rc == 0 and "audio: 2 row(s) stored not tried; joined 0" in log
    assert sha(sc.db) == sha1


def test_plumbing5_an_unreadable_nas_joins_nothing_and_says_so(sc, old_house, tmp_path):
    before = sc.rows()
    rc, log = bf(sc, old_house["journal"], "--apply", "--nas-recordings", str(tmp_path / "not-mounted"))
    assert rc == 0, log
    assert "none joined: the NAS recordings archive" in log and "cannot be read" in log
    assert sc.rows() == before


def test_plumbing5_reads_the_nas_only(sc, old_house):
    """Read-only on the NAS path: no file under it changes."""
    nas = old_house["nas"]
    snap = {p: (p.stat().st_mtime_ns, p.stat().st_size, sha(p)) for p in nas.rglob("*") if p.is_file()}
    dirs = sorted(str(p) for p in nas.rglob("*"))
    rc, _ = bf(sc, old_house["journal"], "--apply", "--nas-recordings", str(nas))
    assert rc == 0
    assert {p: (p.stat().st_mtime_ns, p.stat().st_size, sha(p)) for p in nas.rglob("*") if p.is_file()} == snap
    assert sorted(str(p) for p in nas.rglob("*")) == dirs
