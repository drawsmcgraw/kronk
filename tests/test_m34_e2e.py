"""M34 test agent's own checks (docs/sprint/M34-e2e.md), beside the build's
tests/test_m34_work_db.py and built differently from them.

- An item walked through its life another way (criterion 3): two items
  active at once, a stopped step, a second fix round, a follow-up
  promotion, an item queued behind a dropped one, a deploy with no item
  between two item promotions. After every writing command the dump is
  rewritten, loads, and holds what the database holds; the file agrees
  with `resume`.
- Dumps that load as SQL and pass work.py's validate() but are not the
  schema the exporter reads (criterion 8's "not loadable", one step
  further): the file is still written and the exporter exits 0.

Repos and databases are temporary; herdr is the fake. Nothing here names
the live database or its dump, and no test opens a socket.
"""
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from tests.test_m28_agent_tool import commit, git
from tests.test_m33_work_state import (  # noqa: F401  (fixtures)
    CRIT, Tool, active_item, db, fake_herdr, quiet_env, repo, t, wk)
from tests.test_m34_work_db import BASE_PATH, EXPORT, disagreements, we  # noqa: F401

pytestmark = pytest.mark.usefixtures("no_network")

TABLES = ("meta", "item", "step", "promotion", "promotion_item", "open_thing", "log")


def _tables(conn) -> dict:
    return {tb: conn.execute(f"SELECT * FROM {tb} ORDER BY 1, 2").fetchall() for tb in TABLES}


def dump_equals_database(db: Path) -> None:
    """The dump beside the database loads into memory and holds the rows
    the database holds, table for table."""
    text = Path(f"{db}.dump.sql").read_text()
    mem = sqlite3.connect(":memory:")
    mem.executescript(text)
    assert not mem.in_transaction
    real = sqlite3.connect(db)
    try:
        assert _tables(mem) == _tables(real)
    finally:
        mem.close()
        real.close()


def test_a_second_walk_two_active_a_stopped_step_fix_two_a_follow_up(we, t, repo, fake_herdr):
    dump = Path(f"{t.db}.dump.sql")
    seen = {"dump": dump.read_bytes()}

    def wrote(*argv, **kw):
        """A writing command: the dump is another file afterwards (replaced,
        not edited: a new inode), loads, and equals the database."""
        ino = dump.stat().st_ino
        r = t.one(*argv, **kw)
        now = dump.read_bytes()
        assert now != seen["dump"], argv
        assert dump.stat().st_ino != ino, argv
        seen["dump"] = now
        dump_equals_database(t.db)
        return r

    def agree(**want):
        data = we.collect(repo, t.db)
        assert data["database"]["state"] == "ok"
        assert disagreements(data, t.text("resume")) == []
        items = {it["id"]: it for it in data["items"]}
        for i, w in want.items():
            for k, val in w.items():
                got = items[i]["stage"][k[6:]] if k.startswith("stage_") else items[i][k]
                assert got == val, (i, k, got, val)
        # The queue rows `work.py queue` prints are the file's, field for field.
        for q in t.j("queue"):
            it = items[q["id"]]
            for k in ("position", "state", "tier", "after_item", "blocked_by", "next", "next_step", "waits_on",
                      "waits_note"):
                assert it[k] == q[k], (q["id"], k, it[k], q[k])
            assert it["held_why"] == (q["held_why"] if q["state"] == "held" else None)
        return data, items

    wrote("add", "--title", "first", "--tier", "2")
    wrote("add", "--title", "second, dropped while active")
    wrote("add", "--title", "behind the dropped one", "--after", "M2")
    wrote("add", "--title", "behind the first", "--after", "M1")
    commit(repo, {"docs/sprint/M1-criteria.md": CRIT.format(n=1), "docs/sprint/M2-criteria.md": CRIT.format(n=2)})
    wrote("start", "M1")
    wrote("start", "M2")
    wrote("step", "add", "M1", "criteria")
    wrote("step", "add", "M2", "criteria")
    data, items = agree(M1={"state": "active", "stage_stage": "planning"},
                        M2={"state": "active", "stage_stage": "planning"},
                        M3={"blocked_by": "M3 follows M2, which is active, not promoted", "next": False},
                        M4={"blocked_by": "M4 follows M1, which is active, not promoted", "next": False})
    assert data["under_way"] == data["active"] == ["M1", "M2"] and data["queue"] == ["M3", "M4"]

    # A build started and stopped, then started again and finished.
    fake_herdr.add_agent("m1-build", "working", repo)
    wrote("step", "start", "M1", "build", "--agent", "m1-build")
    agree(M1={"stage_stage": "implementing"})
    fake_herdr.set_status("m1-build", "idle")
    wrote("step", "stop", "M1", "build", "--why", "the brief was wrong")
    data, items = agree(M1={"stage_stage": "planning"})
    (b1,) = [s for s in items["M1"]["steps"] if s["kind"] == "build"]
    assert (b1["state"], b1["committed_at"], b1["agent"]) == ("stopped", None, "m1-build") and b1["ended_at"]
    assert "the brief was wrong" not in json.dumps(data)            # a step's note is not exported
    fake_herdr.set_status("m1-build", "working")
    wrote("step", "start", "M1", "build", "--round", "2", "--agent", "m1-build")
    commit(repo, {"docs/sprint/M1-build.md": "# build\n"})
    fake_herdr.set_status("m1-build", "done")
    wrote("step", "finish", "M1", "build", "--round", "2")
    agree(M1={"stage_stage": "waiting", "stage_detail": "build in, not tested"})

    # test 1 FAILS, fix 1, test 2 FAILS, fix 2, test 3 PASSES.
    for n, verdict in ((1, "FAILS"), (2, "FAILS"), (3, "PASSES")):
        name = "M1-e2e.md" if n == 1 else f"M1-e2e{n}.md"
        commit(repo, {f"docs/sprint/{name}": f"# test {n}\n\nVerdict: {verdict}\n"})
        wrote("step", "add", "M1", "test", "--round", n)
        if verdict == "FAILS":
            wrote("grade", "M1", "fail", "--round", n)
            agree(M1={"stage_stage": "waiting", "stage_detail": f"test {n}: FAILS, graded fail"})
            commit(repo, {f"docs/sprint/M1-fix{n}.md": "# fix\n"})
            wrote("step", "add", "M1", "fix", "--round", n)
            agree(M1={"stage_stage": "waiting", "stage_detail": f"fix {n} in, not retested"})
    data, items = agree(M1={"stage_stage": "to_promote", "stage_detail": "test 3: PASSES"})
    kinds = [(s["kind"], s["n"], s["state"]) for s in items["M1"]["steps"]]
    assert ("fix", 2, "finished") in kinds and ("test", 3, "finished") in kinds and ("build", None, "stopped") in kinds

    # The second active item is dropped; the one behind it stays blocked, by a dropped item.
    wrote("drop", "M2", "--why", "overtaken")
    data, items = agree(M2={"state": "dropped", "next_step": None, "position": None},
                        M3={"blocked_by": "M3 follows M2, which is dropped, not promoted", "next": False,
                            "after_item": "M2"})
    assert data["under_way"] == ["M1"] and "overtaken" not in json.dumps(data)

    # A decision that blocks, a leftover, both counted and neither's text in the file.
    d = wrote("matter", "add", "decision", "--owner", "drew", "--item", "M1", "--text", "ship the QZDECISION?",
              "--blocks")
    wrote("matter", "add", "leftover", "--owner", "coordinator", "--item", "M1", "--text", "a QZLEFTOVER")
    data, items = agree()
    raw = json.dumps(data)
    assert "QZDECISION" not in raw and "QZLEFTOVER" not in raw
    assert [(o["id"], o["item"], o["blocks"]) for o in data["decisions_owed"]] == [(d["matter"], "M1", True)]
    assert items["M1"]["open"] == {"leftovers": {"drew": 0, "coordinator": 1, "not_triaged": 0},
                                   "decisions": {"drew": 1, "coordinator": 0, "not_triaged": 0}}
    wrote("matter", "close", d["matter"], "--how", "yes, QZCLOSED")
    data, _ = agree()
    assert data["decisions_owed"] == [] and "QZCLOSED" not in json.dumps(data)

    # Promoted with a tag; a deploy with no item after it; then a follow-up on the item.
    git(repo, "tag", "item-M1")
    commit(repo, {"docs/sprint/M1-promote.md": "# checks\n", "docs/sprint/M1-followup.md": "# later\n",
                  "docs/sprint/other.md": "# a deploy\n"})
    wrote("promote", "M1", "--tag", "item-M1", "--deployed", "no", "--at", "2026-11-20T12:00:00Z")
    data, items = agree(M1={"state": "promoted", "stage_stage": "promoted"},
                        M4={"blocked_by": None, "next": True})
    wrote("promote", "--label", "a deploy of nothing", "--no-tag", "--deployed", "yes", "--verified",
          "docs/sprint/other.md", "--at", "2026-11-21T12:00:00Z")
    wrote("promote", "M1", "--followup", "--no-tag", "--deployed", "yes", "--verified", "docs/sprint/M1-followup.md",
          "--at", "2026-11-22T12:00:00Z")
    data, items = agree()
    (row,) = data["done"]
    assert (row["id"], row["tag"], row["tag_only"], row["deployed"], row["followup"]) == \
        ("M1", "item-M1", True, "no", False)
    (note,) = row["notes"]
    assert note["verified"] == "docs/sprint/M1-followup.md" and note["at"] == "2026-11-22T12:00:00Z"
    assert note["heading"].startswith("follow-up, ")
    (free,) = data["promotions_no_item"]
    assert (free["label"], free["tag"], free["tag_in_git"], free["deployed"]) == \
        ("a deploy of nothing", None, None, "yes")

    wrote("hold", "M4", "--why", "until spring")
    wrote("note", "M3", "--waits", "drew", "--waits-note", "his word", "--next", "ask")
    agree(M4={"state": "held", "held_why": "until spring", "next": False},
          M3={"waits_on": "drew", "waits_note": "his word", "next_step": "ask"})


def _export(repo, db, out):
    env = {**os.environ, "PATH": BASE_PATH}
    return subprocess.run([sys.executable, str(EXPORT), "--repo", str(repo), "--db", str(db), "--out", str(out)],
                          capture_output=True, text=True, env=env, timeout=30)


def _item_without_columns(text: str) -> str:
    start = text.index("CREATE TABLE item")
    end = text.index(";", start)
    text = text[:start] + "CREATE TABLE item (id TEXT PRIMARY KEY)" + text[end:]
    return "\n".join(l for l in text.splitlines() if not l.startswith('INSERT INTO "item"')) + "\n"


BAD = {
    # A table of the right name without the columns the exporter reads.
    "item lacks its columns": _item_without_columns,
    # A promotion's time that is not a time.
    "a time that is not a time": lambda text: text + "UPDATE promotion SET at = 'yesterday';\n"
                                                     "UPDATE open_thing SET opened_at = 'soon';\n",
    # A step whose item has no row (the dump loads with no foreign-key check).
    "a step of no item": lambda text: text + "PRAGMA foreign_keys=OFF;\nUPDATE step SET item_id = 'M99';\n",
    # A promotion of an item that has no row.
    "a promotion of no item": lambda text: text + "PRAGMA foreign_keys=OFF;\n"
                                                  "UPDATE promotion_item SET item_id = 'M98';\n",
}
# S1 of docs/sprint/M34-e2e.md: the exporter dies on this one (exit 1, a
# traceback, no file, so the page goes stale with no reason shown).
KNOWN = {"item lacks its columns": "M34-e2e.md S1: sqlite3.OperationalError out of collect(), exit 1, no file"}


@pytest.mark.parametrize("case", [pytest.param(c, marks=pytest.mark.xfail(strict=True, reason=KNOWN[c]))
                                  if c in KNOWN else c for c in sorted(BAD)])
def test_a_dump_that_loads_and_is_not_the_schema_still_gives_a_file(t, repo, tmp_path, case):
    """Criterion 8 one step further: the dump is SQL and passes validate(),
    and its rows are not what the exporter expects. The file is written
    and the exporter exits 0; a traceback and a stale page is the failure."""
    active_item(t, repo)
    commit(repo, {"docs/sprint/M1-promote.md": "x\n"})
    t("matter", "add", "decision", "--owner", "drew", "--text", "q?")
    t("promote", "M1", "--no-tag", "--deployed", "no")
    dump = Path(f"{t.db}.dump.sql")
    dump.write_text(BAD[case](dump.read_text()))
    out = tmp_path / "out.json"
    r = _export(repo, t.db, out)
    assert r.returncode == 0 and r.stderr == "", (r.returncode, r.stderr[-600:])
    data = json.loads(out.read_text())
    assert data["database"]["state"] in ("ok", "corrupt")
    assert data["backlog"] is not None
