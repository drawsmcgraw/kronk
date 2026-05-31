"""N-way comparison of bench result files. Produces a per-query and aggregate
side-by-side table across all inputs.

Usage:
    python benchmarks/multi_compare.py <run_a.jsonl> <run_b.jsonl> [<run_c.jsonl> ...] \
        --labels v1 v2-gemma v2-nemo v2-nemo-fp \
        --out benchmarks/results/comparison-all.md
"""
from __future__ import annotations

import argparse
import json
import statistics
from collections import defaultdict
from pathlib import Path


def load(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


def aggregate(records: list[dict]):
    bins = defaultdict(list)
    for r in records:
        bins[(r["query_id"], r["endpoint"])].append(r)
    agg = {}
    for key, trials in bins.items():
        durs = [t["duration_s"] for t in trials if "duration_s" in t]
        quals = [t.get("quality", "unknown") for t in trials]
        agg[key] = {
            "n": len(trials),
            "med": statistics.median(durs) if durs else None,
            "fails": sum(1 for q in quals if q == "fail"),
            "suspects": sum(1 for q in quals if q == "suspect"),
            "passes": sum(1 for q in quals if q == "pass"),
        }
    return agg


def fmt(s):
    return f"{s:6.2f}s" if isinstance(s, (int, float)) else "  -  "


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("inputs", nargs="+")
    p.add_argument("--labels", nargs="+", required=True)
    p.add_argument("--out", required=True)
    args = p.parse_args()
    if len(args.inputs) != len(args.labels):
        raise SystemExit("number of inputs must equal number of labels")

    paths = [Path(x) for x in args.inputs]
    aggs = [aggregate(load(path)) for path in paths]

    all_keys = sorted(set().union(*[a.keys() for a in aggs]))
    endpoints = sorted(set(k[1] for k in all_keys))

    out = []
    out.append("# Multi-way comparison\n")
    for i, label in enumerate(args.labels):
        out.append(f"- **{label}** = `{paths[i].name}`")
    out.append("")

    # Per-endpoint aggregate (p50)
    for ep in endpoints:
        out.append(f"## Aggregate (median per-query) — `{ep}`\n")
        out.append("| label | p50 | p95 | n_pass | n_suspect | n_fail |")
        out.append("|---|---|---|---|---|---|")
        for i, label in enumerate(args.labels):
            a = aggs[i]
            ep_meds = [v["med"] for k, v in a.items() if k[1] == ep and v["med"] is not None]
            ep_meds.sort()
            p50 = statistics.median(ep_meds) if ep_meds else None
            p95 = ep_meds[max(0, int(len(ep_meds) * 0.95) - 1)] if ep_meds else None
            total = sum(v["passes"] + v["suspects"] + v["fails"] for k, v in a.items() if k[1] == ep)
            n_pass = sum(v["passes"] for k, v in a.items() if k[1] == ep)
            n_sus = sum(v["suspects"] for k, v in a.items() if k[1] == ep)
            n_fail = sum(v["fails"] for k, v in a.items() if k[1] == ep)
            pct_pass = 100 * n_pass / total if total else 0
            out.append(
                f"| {label} | {fmt(p50)} | {fmt(p95)} | {n_pass} ({pct_pass:.0f}%) | {n_sus} | {n_fail} |"
            )
        out.append("")

    # Per-query side-by-side
    for ep in endpoints:
        out.append(f"## Per-query — `{ep}`\n")
        header = ["query_id"] + args.labels + ["best", "worst"]
        out.append("| " + " | ".join(header) + " |")
        out.append("|" + "|".join("---" for _ in header) + "|")
        qids = sorted(set(k[0] for k in all_keys if k[1] == ep))
        for qid in qids:
            cells = [f"`{qid}`"]
            durs = []
            for a in aggs:
                v = a.get((qid, ep))
                if v is None:
                    cells.append("-")
                    continue
                qual_marks = ""
                if v["fails"]:
                    qual_marks = f" ❌{v['fails']}"
                elif v["suspects"]:
                    qual_marks = f" ⚠️{v['suspects']}"
                cells.append(f"{fmt(v['med'])}{qual_marks}")
                if v["med"] is not None:
                    durs.append(v["med"])
            if durs:
                cells.append(fmt(min(durs)))
                cells.append(fmt(max(durs)))
            else:
                cells.append("-")
                cells.append("-")
            out.append("| " + " | ".join(cells) + " |")
        out.append("")

    Path(args.out).write_text("\n".join(out))
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
