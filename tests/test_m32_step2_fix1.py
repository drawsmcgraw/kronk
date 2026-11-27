"""M32 step 2, fix round 1 (docs/sprint/M32-criteria.md, "Rulings on step
2's test"; docs/sprint/M32-step2-e2e.md B1 and S1).

B1: a ROADMAP Shipped entry links the items its body cites in a
parenthesis that opens with their ids ("(M1, M1b)"), as well as those its
lead names, so the "Refactor sprint" entry reaches its old rows; an id
named in passing ("since sprint M2", "removed ..., sprint M1") links
nothing. S1: an item's source is Drew's words whole. No socket is opened.
"""
import json
import re
from pathlib import Path

import pytest

from tests.test_m28_agent_tool import commit
from tests.test_m33_work_state import (  # noqa: F401  (fixtures)
    ROADMAP, Tool, active_item, db, fake_herdr, quiet_env, repo, t, wk)
from tests.test_m34_work_db import we  # noqa: F401

pytestmark = pytest.mark.usefixtures("no_network")

REPO = Path(__file__).resolve().parent.parent

# The items the "Refactor sprint" entry cites, and the fifteen old Done rows
# M32-step2-e2e.md B1 found without a Shipped line (M1b has two rows). M1
# showed "Financial expert, phase 1" before this fix: that entry names M1
# only as the item that later removed it.
REFACTOR_IDS = ["M1", "M1b", "M2", "M3", "M4", "M4b", "M5", "M7", "M8", "M9", "M11", "M12", "M14",
                "M17", "M19"]
B1_ROWS = ["done:16:M12", "done:15:M9", "done:14:M8", "done:13:M11", "done:12:M14", "done:11:M19",
           "done:10:M7", "done:7:M2", "done:6:M5", "done:5:M1b", "done:5:M3", "done:5:M4", "done:5:M4b",
           "done:4:M1b", "done:1:M17"]


def _shipped(we):
    nr = []
    secs = we.parse_roadmap((REPO / "ROADMAP.md").read_text(), nr)
    return {e["title"]: e for s in secs if not s["open"] for e in s["entries"]}


def _entry(we, text, open_=False):
    return we.roadmap_entry(1, text.splitlines(), open_)


# ═══ B1: the rule ══════════════════════════════════════════════════════════

def test_a_body_citation_links_its_ids(we):
    e = _entry(we, "**Big run** *(2026-11-15 to 2026-11-21)* — in short: deploys (M17);\n"
                   "removal (M1, M1b); guards (M3, M4 and M4b; `docs/x.md`); dispatch (M7, `docs/y.md`).")
    assert e["links"] == ["M17", "M1", "M1b", "M3", "M4", "M4b", "M7"]


def test_an_id_in_passing_links_nothing(we):
    for text in (
        "**Errors** *(item 2, 2026-07-05)* — a toggle (since sprint M2, voice only).",
        "**Health tool** (removed 2026-11-15, sprint M1); Infisical retired.",
        "**Old phase** *(2026-07-10; removed 2026-11-15, sprint M1)* — positions store.",
        "**A page** *(2026-11-26, work item M34)* — see `docs/sprint/M33-promote.md`, \"M33\" (as M33 did).",
    ):
        links = _entry(we, text)["links"]
        assert "M1" not in links and "M2" not in links and "M33" not in links, text


def test_a_leads_first_clause_still_links(we):
    assert _entry(we, "**Backups** *(2026-11-20, sprint M16 and M16b)* — a tar.")["links"] == ["M16", "M16b"]
    assert _entry(we, "**Thing** *(2026-11-26, work item M34; repaired 2026-11-27)* — x.")["links"] == ["M34"]


def test_an_open_entry_is_read_as_before(we):
    """The body rule and the first-clause rule are Shipped's; an open entry
    links only by its lead, whole."""
    e = _entry(we, "**Open** *(after work item M2; then work item M3)* — parts (M4, M5).", open_=True)
    assert e["links"] == ["M2", "M3"]


# ═══ B1: the real ROADMAP and the real rows ════════════════════════════════

def test_the_refactor_entry_cites_its_items(we):
    assert sorted(_shipped(we)["Refactor sprint"]["links"], key=we.id_key) == sorted(REFACTOR_IDS, key=we.id_key)


def test_entries_that_name_an_item_in_passing_link_nothing(we):
    sh = _shipped(we)
    assert sh["Financial expert, phase 1"]["links"] == []
    assert sh["Verbose error reporting"]["links"] == []
    assert sh["`query_health` tool + `/health` dashboard"]["links"] == []


def test_every_other_shipped_entry_links_what_its_lead_names(we):
    """The before table of M32-step2-fix1.md, less Financial expert's M1:
    the body rule adds nothing to any entry but Refactor sprint."""
    got = {t: e["links"] for t, e in _shipped(we).items() if e["links"]}
    assert got.pop("Refactor sprint")
    assert got == {
        "`/work` drawn from the work-state database": ["M34"],
        "A kept record of what Whisper heard, and \"Heard\" on `/work`": ["M30"],
        "Time and date in the house's zone": ["M35"],
        "Work-item state and the queue in a database": ["M33"],
        "Devstral retired": ["M31"],
        "A tool for launching and checking on agents": ["M28"],
        "`/work`: the work items in a page": ["M27"],
        "/resources shows real usage": ["M25"],
        "Kronk's own ROCm libraries": ["M26"],
        "Reclaim storage and memory; Talkie kept": ["M24"],
        "The queue incident": ["M23"],
        "The basement hand-off": ["M22"],
        "Backups": ["M16", "M16b"],
        "Upgrade cadence": ["M18"],
        "`/settings` page and live config directories": ["M21"],
    }


def test_a_promoted_item_cited_in_a_body_gets_the_entry(we, t, repo):
    active_item(t, repo, 1)
    active_item(t, repo, 2)
    commit(repo, {"docs/sprint/M2-promote.md": "x\n"})
    t("promote", "M2", "--no-tag", "--deployed", "no", "--untested", "nothing")
    commit(repo, {"ROADMAP.md": ROADMAP + (
        "- **The run** *(2026-11-15 to 2026-11-21)* — in short: the thing (M2,\n"
        "  `docs/x.md`).\n"
        "- **Errors** *(item 2, 2026-07-05)* — since sprint M2, voice only.\n"
        "- **Old** *(2026-07-10; removed 2026-11-15, sprint M2)* — gone.\n")})
    data = we.collect(repo, t.db)
    m2 = {i["id"]: i for i in data["items"]}["M2"]
    assert [s["title"] for s in m2["shipped"]] == ["The run"]
    assert {u["title"] for u in data["shipped_unlinked"]} >= {"Errors", "Old"}
    assert "The run" not in {u["title"] for u in data["shipped_unlinked"]}


def test_the_fifteen_old_rows_have_their_shipped_line():
    """B1 on the export the host last wrote to dev's data (read only)."""
    p = REPO / "data" / "dev" / "work.json"
    if not p.is_file():
        pytest.skip("no dev export here")
    d = json.loads(p.read_text())
    if (d.get("database") or {}).get("state") != "ok":
        pytest.skip(f"dev's export: database {(d.get('database') or {}).get('state')}")
    by = {i["id"]: i for i in d["items"]}
    rows = {f"done:{r['promotion']}:{r['id']}": r["id"] for r in d["done"]}
    missing = [k for k in B1_ROWS if k not in rows]
    if missing:
        pytest.skip(f"dev's export has no rows {missing}")
    if not any(s["title"] == "Refactor sprint" for s in by["M17"]["shipped"]):
        pytest.skip("dev's export predates the fix (M17 has no Refactor sprint)")
    for k in B1_ROWS:
        assert [s["title"] for s in by[rows[k]]["shipped"]] == ["Refactor sprint"], k
    assert all(u["title"] != "Refactor sprint" for u in d["shipped_unlinked"])


# ═══ S1: Drew's words whole ════════════════════════════════════════════════

def _source_in_file(path: Path) -> str:
    lines = path.read_text().splitlines()
    i = next(n for n, l in enumerate(lines) if l.startswith("Source:"))
    para = [lines[i][len("Source:"):]]
    for l in lines[i + 1:]:
        if not l.strip() or l.startswith("#"):
            break
        para.append(l)
    return re.sub(r"\s+", " ", " ".join(x.strip() for x in para)).strip()


@pytest.mark.parametrize("item,size", [("M32", 685), ("M33", 722)])
def test_the_live_sources_arrive_whole(we, item, size):
    path = f"docs/sprint/{item}-criteria.md"
    whole = _source_in_file(REPO / path)
    assert len(whole) == size
    assert we.work_tool().criteria_head(REPO, path)["source"] == whole


def test_a_long_source_is_whole_up_to_the_bound(we, t, repo):
    wp = we.work_tool()
    assert wp.SOURCE_MAX == 4000
    words = " ".join(["word"] * 700)                     # 3,499 characters
    commit(repo, {"docs/sprint/M1-criteria.md": f"# M1 pass criteria: x\n\nSource: {words}\n\n## Criteria\n"})
    assert wp.criteria_head(repo, "docs/sprint/M1-criteria.md")["source"] == words
    t("add", "--title", "x")
    assert {i["id"]: i for i in we.collect(repo, t.db)["items"]}["M1"]["source"] == words
    commit(repo, {"docs/sprint/M1-criteria.md": f"# M1 pass criteria: x\n\nSource: {'y' * 5000}\n"})
    s = wp.criteria_head(repo, "docs/sprint/M1-criteria.md")["source"]
    assert len(s) == 4000 and s.endswith("…")
