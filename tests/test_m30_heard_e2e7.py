"""M30 step C, the narrow retest of fix round 1 (docs/sprint/M30-e2e7.md).

Four in-process writes that conftest's write fence does not refuse and the
fix report (docs/sprint/M30-fix4.md, "Not done") does not name. Each is
tried on a fenced file in tmp_path, never a live one, and each is a strict
xfail: when the fence refuses the write, the mark comes off.

None raises `seq` or moves a count, so the end-of-session comparison does
not see what they change either (shown on a scratch house in the report).
"""
import io
import os
import sqlite3

import pytest

from tests import conftest as C
from tests.test_m30_heard_e2e6 import fenced  # noqa: F401  (the fixture)

WHY = "M30-e2e7 B1: the fence lets this write of a live path through"


def _a_record(path) -> bytes:
    """A small database at path, made before the path is fenced off: the
    fixture fences the directory, so it is written through the real connect."""
    c = C._real_sqlite_connect(str(path))
    c.execute("CREATE TABLE runs (run_id TEXT PRIMARY KEY, seq INTEGER, heard TEXT)")
    c.execute("INSERT INTO runs VALUES ('R-1', 1, 'invented words')")
    c.commit()
    c.close()
    return path.read_bytes()


@pytest.mark.xfail(strict=True, reason=WHY + ": a hard link to it, opened by its other name")
def test_e2e7_a_write_through_a_hard_link_to_a_live_file_is_refused(fenced, tmp_path):
    live_dir, copy, forget = fenced
    rec = live_dir / "voice_runs.db"
    before = _a_record(rec)
    alias = tmp_path / "alias.db"
    try:
        with pytest.raises(C.LiveVoiceFileBlocked):
            os.link(rec, alias)                              # refuse the link, or the open of it below
            c = sqlite3.connect(alias)
            try:
                c.execute("UPDATE runs SET heard = 'changed by a test'")
                c.commit()
            finally:
                c.close()
        assert rec.read_bytes() == before
    finally:
        forget()


@pytest.mark.xfail(strict=True, reason=WHY + ": sqlite3.Connection() called directly, not sqlite3.connect()")
def test_e2e7_sqlite3_connection_called_directly_on_a_live_path_is_refused(fenced):
    live_dir, copy, forget = fenced
    rec = live_dir / "voice_runs.db"
    before = _a_record(rec)
    try:
        with pytest.raises(C.LiveVoiceFileBlocked):
            c = sqlite3.Connection(str(rec))
            try:
                c.execute("UPDATE runs SET heard = 'changed by a test'")
                c.commit()
            finally:
                c.close()
        assert rec.read_bytes() == before
    finally:
        forget()


@pytest.mark.xfail(strict=True, reason=WHY + ": io.FileIO opens it without open() or os.open()")
def test_e2e7_io_fileio_in_a_write_mode_on_a_live_path_is_refused(fenced):
    live_dir, copy, forget = fenced
    try:
        with pytest.raises(C.LiveVoiceFileBlocked):
            with io.FileIO(str(copy), "ab") as f:
                f.write(b"x")
        assert copy.read_bytes() == b""
    finally:
        forget()


@pytest.mark.xfail(strict=True, reason=WHY + ": os.open of a bare name with dir_fd on the live directory")
def test_e2e7_os_open_for_writing_relative_to_a_live_directory_is_refused(fenced):
    live_dir, copy, forget = fenced
    rec = live_dir / "voice_runs.db"
    before = _a_record(rec)
    dfd = os.open(live_dir, os.O_RDONLY)
    try:
        with pytest.raises(C.LiveVoiceFileBlocked):
            fd = os.open("voice_runs.db", os.O_WRONLY, dir_fd=dfd)
            try:
                os.write(fd, b"x")
            finally:
                os.close(fd)
        assert rec.read_bytes() == before
    finally:
        os.close(dfd)
        forget()
