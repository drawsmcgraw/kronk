"""The voice-corpus check before a save of the music policy or the lead-in
(sprint M21 step 2, criterion 12).

Runs the corpus's offline layer, as tests/test_voice_corpus.py does,
twice: with the values in effect and with the candidate, and lists every
row whose outcome changes. A row that passed its expectation with the
values in effect and fails with the candidate "newly fails" and blocks the
save unless the editor overrides that row (settings_save records each
override in the commit message).

  - Routing and the direct call (corpus_routing.py, the module the test
    suite uses): here, in the orchestrator, with config_loader.pinned() on
    a copy of the config holding the candidate lead-in. Pure: no network,
    no event, nothing else sees the candidate.
  - The parser and the decision order (tool_service/corpus_check.py, the
    module the test suite uses): in a child process of tool_service (POST
    /config/corpus), Music Assistant and Home Assistant replayed from the
    recorded snapshot, dry run on, every socket blocked, so nothing is
    reached and nothing plays. For a music.yaml candidate it runs with the
    candidate policy; for a lead-in candidate, only when the lead-in
    changes the sentence a row hands on (origin.said), with that sentence.

The corpus reaches the running services in their images, copied from the
repo's tests/ at the same commit (Dockerfiles, compose additional context
`corpus`); the answer carries the corpus's sha256 so the page shows which
corpus was used.
"""
import dataclasses
import hashlib
import json
import os
from pathlib import Path

import config_files
import config_loader
import corpus_routing
import routing

CORPUS_FILE = Path(os.getenv("VOICE_CORPUS_FILE", "/app/corpus/voice_corpus.jsonl"))
APPLIES = frozenset({config_loader.LEAD_IN_FILE, "music.yaml"})
TIMEOUT_S = 150


def applies(rel: str) -> bool:
    return rel in APPLIES


def load_rows() -> list[dict]:
    return [json.loads(x) for x in CORPUS_FILE.read_text().splitlines() if x.strip()]


def digest() -> str:
    return hashlib.sha256(CORPUS_FILE.read_bytes()).hexdigest()[:12]


def _said(rows, cfg) -> dict[str, str]:
    """The sentence each music row hands on (origin.said): the request
    without a lead-in, under cfg's lead-in words."""
    out = {}
    with config_loader.pinned(cfg):
        for r in rows:
            if r["expect"]["tool"] == "play_music" or "non-music" in r.get("tags", []):
                out[r["id"]] = routing.lead_in_request(r["text"]) or r["text"]
    return out


def _routing(rows, cfg) -> dict[str, dict]:
    out = {}
    with config_loader.pinned(cfg):
        for r in rows:
            o = corpus_routing.outcome(r["text"])
            out[r["id"]] = {"outcome": corpus_routing.describe(o), "why": corpus_routing.row_diff(r, o)}
    return out


async def _parser(body: dict) -> dict:
    import settings_api
    try:
        async with settings_api.ts_client(TIMEOUT_S) as c:
            r = await c.post("/config/corpus", json=body)
        answer = r.json()
    except Exception as e:
        return {"error": f"tool_service did not answer the corpus check ({config_files.safe(e, 120)})"}
    if r.status_code != 200 or "error" in answer:
        return {"error": answer.get("error") or f"tool_service answered {r.status_code}",
                "errors": answer.get("errors")}
    return answer


async def check(rel: str, text: str) -> dict:
    """{"rows_checked", "corpus_digest", "changed": [...], "newly_failing":
    [...], "note"} or {"error": why}. Each changed row: id, text, layer,
    before, after, newly_fails, why."""
    try:
        rows = load_rows()
        corpus_digest = digest()
    except OSError as e:
        return {"error": f"the voice corpus is not in this image ({CORPUS_FILE}: {e.strerror})"}
    cfg = config_loader.latest()
    if rel == config_loader.LEAD_IN_FILE:
        try:
            candidate = dataclasses.replace(cfg, lead_in=config_loader.check_candidate(rel, text))
        except config_loader.ConfigInvalid as e:
            return {"error": f"the candidate does not validate: {e}"}
    else:
        candidate = cfg
    by_id = {r["id"]: r for r in rows}
    changed = []

    before, after = _routing(rows, cfg), _routing(rows, candidate)
    for rid, b in before.items():
        a = after[rid]
        if a["outcome"] != b["outcome"] or bool(a["why"]) != bool(b["why"]):
            changed.append({"id": rid, "text": by_id[rid]["text"], "layer": "routing",
                            "before": b["outcome"], "after": a["outcome"],
                            "newly_fails": b["why"] is None and a["why"] is not None, "why": a["why"] or ""})

    said_b, said_c = _said(rows, cfg), _said(rows, candidate)
    note = ""
    parser_rows = 0
    if rel == "music.yaml" or said_b != said_c:
        body = {"candidate_yaml": text if rel == "music.yaml" else None,
                "baseline_said": said_b, "candidate_said": said_c}
        answer = await _parser(body)
        if "error" in answer:
            return {"error": answer["error"], "errors": answer.get("errors")}
        if answer.get("corpus_digest") not in (None, corpus_digest):
            note = (f"tool_service's corpus ({answer['corpus_digest']}) differs from the orchestrator's "
                    f"({corpus_digest}): the two images were built from different commits")
        base, cand = answer["baseline"], answer["candidate"]
        parser_rows = len(base)
        for rid, b in base.items():
            a = cand[rid]
            if a["outcome"] != b["outcome"] or a["ok"] != b["ok"]:
                changed.append({"id": rid, "text": said_c.get(rid, by_id[rid]["text"]), "layer": "parser and decision",
                                "before": b["outcome"], "after": a["outcome"],
                                "newly_fails": b["ok"] and not a["ok"], "why": a["why"]})
    else:
        note = "parser and decision: no row's sentence changes with this lead-in, so that layer was not rerun"
    return {"rows_checked": len(rows), "parser_rows": parser_rows, "corpus_digest": corpus_digest,
            "changed": changed, "newly_failing": [c for c in changed if c["newly_fails"]], "note": note}
