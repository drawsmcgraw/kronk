#!/usr/bin/env python3
"""Summarise the M11 shadow log for the review with Drew (sprint M11).

    python3 scripts/music_shadow_report.py [LOG] [--since 2026-11-17] [--json]

LOG defaults to data/music_shadow.jsonl (production's tool_service writes
/data/music_shadow.jsonl; the dev pair writes data/dev/music_shadow.jsonl).
Prints: how many requests were shadowed, compared and skipped; the agreement
rate over the requests the parser read as music (sentences it declines, with
no request verb at the start, are counted apart: they stay on the model path
at cutover); decided_by counts; how often the model question would have fired; and
every disagreement with both outcomes. Reads the file only.

Record format: docs/features/voice-music-control.md, "Shadow mode".
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load(path: Path, since: str | None = None) -> tuple[list[dict], int]:
    rows, bad = [], 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            r = json.loads(line)
        except ValueError:
            bad += 1
            continue
        if since and (r.get("ts") or "") < since:
            continue
        rows.append(r)
    return rows, bad


def _what(side: dict) -> str:
    if not side:
        return "-"
    if side.get("error") and side.get("status") == "error":
        return f"failed: {side['error']}"
    if side.get("action") and side.get("action") != "play":
        why = side.get("error") or side.get("reason") or ""
        return f"{side['action']}" + (f" ({why})" if why else "")
    if side.get("error") or side.get("target_error"):
        return f"failed: {side.get('error') or side.get('target_error')}"
    kind = side.get("kind")
    if kind == "genre":
        return f"style {side.get('name')!r}" + (f" -> {side.get('pick')}" if side.get("pick") else "")
    name = " by ".join(x for x in (side.get("name"), side.get("artist")) if x)
    lit = " (literal words)" if side.get("literal") else ""
    return f"{side.get('media_type') or 'item'} {name!r}{lit} [{side.get('uri')}]"


def summarise(rows: list[dict]) -> dict:
    compared_all = [r for r in rows if "agree" in r]
    # The parser found no request verb: the M11 path declines the sentence,
    # which stays on the model path at cutover (the HA trigger is on request
    # verbs). Counted apart, so they do not swamp the agreement rate.
    declined = [r for r in compared_all if (r.get("new") or {}).get("action") == "not_music"]
    compared = [r for r in compared_all if r not in declined]
    skipped = [r for r in rows if r.get("skipped")]
    errors = [r for r in rows if "agree" not in r and not r.get("skipped")]
    declined_live_played = sum(1 for r in declined if (r.get("live") or {}).get("status") in ("playing", "dry_run"))
    agree = sum(1 for r in compared if r["agree"])
    decided = collections.Counter((r.get("new") or {}).get("decided_by") for r in compared)
    would_ask = sum(1 for r in rows if ((r.get("new") or {}).get("model") or {}).get("would_ask"))
    why = collections.Counter(r.get("why") for r in compared)
    new_ms = sorted((r.get("new") or {}).get("ms") for r in compared if (r.get("new") or {}).get("ms") is not None)
    live_ms = sorted((r.get("live") or {}).get("ms") for r in compared if (r.get("live") or {}).get("ms") is not None)

    def p(xs, q):
        return xs[min(len(xs) - 1, int(q * len(xs)))] if xs else None
    return {
        "shadowed": len(rows), "compared": len(compared), "skipped": len(skipped), "shadow_errors": len(errors),
        "declined_not_music": len(declined), "declined_live_played": declined_live_played,
        "agree": agree, "agreement_rate": round(agree / len(compared), 3) if compared else None,
        "decided_by": dict(decided.most_common()), "model_would_ask": would_ask,
        "why": dict(why.most_common()),
        "skip_reasons": dict(collections.Counter(r["skipped"] for r in skipped)),
        "new_ms": {"p50": p(new_ms, 0.5), "p95": p(new_ms, 0.95)},
        "live_ms": {"p50": p(live_ms, 0.5), "p95": p(live_ms, 0.95)},
        "disagreements": [{"ts": r.get("ts"), "sentence": r.get("sentence"), "why": r.get("why"),
                           "live": _what(r.get("live")), "new": _what(r.get("new")),
                           "decided_by": (r.get("new") or {}).get("decided_by"),
                           "trace_id": r.get("trace_id")}
                          for r in compared if not r["agree"]],
        "errors": [{"ts": r.get("ts"), "sentence": r.get("sentence"), "error": (r.get("new") or {}).get("error")}
                   for r in errors],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("log", nargs="?", default=str(ROOT / "data" / "music_shadow.jsonl"))
    ap.add_argument("--since", help="only records at or after this UTC time (ISO prefix)")
    ap.add_argument("--json", action="store_true", help="print the summary as JSON")
    args = ap.parse_args()
    path = Path(args.log)
    if not path.exists():
        print(f"music_shadow_report: no log at {path}", file=sys.stderr)
        return 2
    rows, bad = load(path, args.since)
    s = summarise(rows)
    s["unreadable_lines"] = bad
    if args.json:
        print(json.dumps(s, indent=1, ensure_ascii=False))
        return 0
    rate = f"{s['agreement_rate']:.0%}" if s["agreement_rate"] is not None else "n/a"
    print(f"{path}: {s['shadowed']} requests shadowed, {s['compared']} compared, {s['skipped']} skipped, "
          f"{s['shadow_errors']} shadow errors, {bad} unreadable lines")
    print(f"agreement: {s['agree']}/{s['compared']} ({rate}) of the requests the parser read as music")
    print(f"declined (no request verb; these stay on the model path): {s['declined_not_music']}, "
          f"of which the live path played {s['declined_live_played']}")
    print(f"decided_by: {s['decided_by']}")
    print(f"model question would have fired: {s['model_would_ask']}")
    print(f"live ms p50/p95: {s['live_ms']['p50']}/{s['live_ms']['p95']}; "
          f"new ms p50/p95: {s['new_ms']['p50']}/{s['new_ms']['p95']}")
    if s["skip_reasons"]:
        print(f"skipped: {s['skip_reasons']}")
    if s["disagreements"]:
        print(f"\n{len(s['disagreements'])} disagreements:")
        for d in s["disagreements"]:
            print(f"- {d['ts']} {d['sentence']!r} ({d['why']}; decided_by {d['decided_by']})\n"
                  f"    live: {d['live']}\n    new:  {d['new']}")
    if s["errors"]:
        print(f"\n{len(s['errors'])} shadow errors:")
        for e in s["errors"]:
            print(f"- {e['ts']} {e['sentence']!r}: {e['error']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
