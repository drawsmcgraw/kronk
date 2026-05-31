"""Summarize one or two `agent_bench.py` JSONL files into a Markdown report.

    python benchmarks/summarize.py benchmarks/results/v1-baseline-*.jsonl
    python benchmarks/summarize.py v1-baseline-*.jsonl v2-coordinator-*.jsonl

If two files are passed, the second is treated as the "candidate" (v2) and
compared against the first (v1 baseline) per-query.
"""
from __future__ import annotations

import argparse
import json
import statistics
from collections import defaultdict
from pathlib import Path


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


def aggregate(records: list[dict]) -> dict:
    """Group by (query_id, endpoint), compute medians + quality counts."""
    bins: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for r in records:
        bins[(r["query_id"], r["endpoint"])].append(r)
    agg: dict[tuple[str, str], dict] = {}
    for key, trials in bins.items():
        durations = [t["duration_s"] for t in trials if "duration_s" in t]
        ttfts = [t["ttft_s"] for t in trials if t.get("ttft_s") is not None]
        quals = [t.get("quality", "unknown") for t in trials]
        agg[key] = {
            "n": len(trials),
            "duration_med": statistics.median(durations) if durations else None,
            "duration_min": min(durations) if durations else None,
            "duration_max": max(durations) if durations else None,
            "ttft_med": statistics.median(ttfts) if ttfts else None,
            "quality_counts": {q: quals.count(q) for q in set(quals)},
            "majority_quality": max(set(quals), key=quals.count) if quals else "unknown",
            "any_error": any("error" in t for t in trials),
            "sample_response": trials[0].get("response", "")[:200],
        }
    return agg


def fmt_duration(s: float | None) -> str:
    if s is None:
        return "  -  "
    return f"{s:6.2f}s"


def fmt_quality(counts: dict[str, int]) -> str:
    order = ["pass", "suspect", "fail", "unknown"]
    parts = [f"{q[0].upper()}={counts.get(q, 0)}" for q in order if counts.get(q, 0)]
    return " ".join(parts) if parts else "-"


def single_report(label: str, path: Path) -> str:
    records = load_jsonl(path)
    agg = aggregate(records)

    out: list[str] = []
    out.append(f"# {label} — `{path.name}`\n")
    out.append(f"_{len(records)} trials across {len({k[0] for k in agg})} queries_\n")

    # Per-endpoint aggregate
    out.append("## Aggregate timing by endpoint\n")
    by_ep = defaultdict(list)
    for (qid, ep), a in agg.items():
        if a["duration_med"] is not None:
            by_ep[ep].append(a["duration_med"])
    out.append("| endpoint | n queries | p50 | p95 | min | max |")
    out.append("|---|---|---|---|---|---|")
    for ep, vals in sorted(by_ep.items()):
        if not vals:
            continue
        p50 = statistics.median(vals)
        srt = sorted(vals)
        p95 = srt[max(0, int(len(srt) * 0.95) - 1)]
        out.append(f"| `{ep}` | {len(vals)} | {p50:.2f}s | {p95:.2f}s | {min(vals):.2f}s | {max(vals):.2f}s |")
    out.append("")

    # Quality counts
    out.append("## Quality flag distribution\n")
    all_flags = [r.get("quality", "unknown") for r in records]
    flag_counts = {f: all_flags.count(f) for f in set(all_flags)}
    out.append("| flag | count | pct |")
    out.append("|---|---|---|")
    for f in ["pass", "suspect", "fail", "unknown"]:
        n = flag_counts.get(f, 0)
        pct = 100 * n / len(records) if records else 0
        out.append(f"| {f} | {n} | {pct:.1f}% |")
    out.append("")

    # Per-query table
    out.append("## Per-query results\n")
    out.append("| query_id | endpoint | n | duration p50 | quality | sample response |")
    out.append("|---|---|---|---|---|---|")
    for (qid, ep) in sorted(agg.keys()):
        a = agg[(qid, ep)]
        sample = a["sample_response"].replace("\n", " ").replace("|", "\\|")[:80]
        out.append(
            f"| `{qid}` | {ep} | {a['n']} | {fmt_duration(a['duration_med'])} | "
            f"{fmt_quality(a['quality_counts'])} | {sample} |"
        )
    out.append("")

    # Failures + suspects detail
    out.append("## Failures and suspects (full responses)\n")
    bad = [r for r in records if r.get("quality") in {"fail", "suspect"}]
    if not bad:
        out.append("_None._\n")
    else:
        for r in bad:
            out.append(
                f"- **`{r['query_id']}` / {r['endpoint']} / t{r['trial']}** "
                f"({r.get('quality')}: {r.get('quality_reason')})"
            )
            resp = (r.get("response") or "").strip()
            if resp:
                out.append("  ```")
                out.append("  " + resp.replace("\n", "\n  ")[:600])
                out.append("  ```")
            elif "error" in r:
                out.append(f"  *error: {r['error']}*")
        out.append("")

    return "\n".join(out)


def compare_report(baseline_path: Path, candidate_path: Path) -> str:
    base_records = load_jsonl(baseline_path)
    cand_records = load_jsonl(candidate_path)
    base_agg = aggregate(base_records)
    cand_agg = aggregate(cand_records)

    out: list[str] = []
    out.append(f"# Comparison — baseline `{baseline_path.name}` vs candidate `{candidate_path.name}`\n")

    # Per-query side-by-side
    out.append("## Per-query comparison\n")
    out.append("| query_id | endpoint | base p50 | cand p50 | Δ | base quality | cand quality |")
    out.append("|---|---|---|---|---|---|---|")
    all_keys = sorted(set(base_agg.keys()) | set(cand_agg.keys()))
    for key in all_keys:
        qid, ep = key
        b = base_agg.get(key)
        c = cand_agg.get(key)
        b_dur = b["duration_med"] if b else None
        c_dur = c["duration_med"] if c else None
        delta = (c_dur - b_dur) if (b_dur is not None and c_dur is not None) else None
        delta_str = f"{delta:+.2f}s" if delta is not None else "-"
        b_q = fmt_quality(b["quality_counts"]) if b else "-"
        c_q = fmt_quality(c["quality_counts"]) if c else "-"
        out.append(
            f"| `{qid}` | {ep} | {fmt_duration(b_dur)} | {fmt_duration(c_dur)} | "
            f"{delta_str} | {b_q} | {c_q} |"
        )
    out.append("")

    # Aggregate
    out.append("## Aggregate p50/p95 by endpoint\n")
    out.append("| endpoint | base p50 | cand p50 | base p95 | cand p95 |")
    out.append("|---|---|---|---|---|")
    for ep in ["message", "api_chat"]:
        b_durs = [a["duration_med"] for k, a in base_agg.items() if k[1] == ep and a["duration_med"] is not None]
        c_durs = [a["duration_med"] for k, a in cand_agg.items() if k[1] == ep and a["duration_med"] is not None]
        def p95(vals):
            srt = sorted(vals)
            return srt[max(0, int(len(srt) * 0.95) - 1)] if srt else None
        b_p50 = statistics.median(b_durs) if b_durs else None
        c_p50 = statistics.median(c_durs) if c_durs else None
        out.append(
            f"| `{ep}` | {fmt_duration(b_p50)} | {fmt_duration(c_p50)} | "
            f"{fmt_duration(p95(b_durs))} | {fmt_duration(p95(c_durs))} |"
        )
    out.append("")

    return "\n".join(out)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inputs", nargs="+", help="1 or 2 JSONL files")
    parser.add_argument("--out", help="Write report to this file instead of stdout")
    args = parser.parse_args()

    paths = [Path(p) for p in args.inputs]
    if len(paths) == 1:
        report = single_report(paths[0].stem, paths[0])
    elif len(paths) == 2:
        report = compare_report(paths[0], paths[1])
    else:
        raise SystemExit("Pass 1 or 2 JSONL paths")

    if args.out:
        Path(args.out).write_text(report)
        print(f"wrote {args.out}")
    else:
        print(report)


if __name__ == "__main__":
    main()
