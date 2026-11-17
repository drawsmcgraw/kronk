#!/usr/bin/env python3
"""Generate tests/fixtures/routing_pin.jsonl: utterance -> (route, rule).

Sprint M7, criterion 1. The pin was generated ONCE, from the tree before
routing._classify_inner became a rules table (commit 7c83f36), and is the
reference tests/test_routing_pin.py holds the router to. Re-running this
script regenerates the file from whatever routing.py is now, which proves
nothing about the refactor: only regenerate it for a deliberate routing
change, and say so in the commit.

    tests/.venv/bin/python tests/fixtures/make_routing_pin.py [--seen FILE]

Utterances, deduplicated, in this order:
  1. every row of tests/voice_corpus.jsonl;
  2. every row of docs/bench/routing_eval.jsonl;
  3. --seen: a JSON list of every string the test suite fed to a routing
     regex (recorded once with a pytest plugin that wrapped each compiled
     pattern in routing.py; 308 strings at 7c83f36);
  4. the crafted overlaps and generated variants in tests/test_routing_pin.py
     (OVERLAPS, variants()).
"""
import argparse
import asyncio
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "orchestrator"))
sys.path.insert(0, str(REPO / "tests"))

import routing  # noqa: E402
from test_routing_pin import OVERLAPS, variants  # noqa: E402

OUT = Path(__file__).resolve().parent / "routing_pin.jsonl"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seen", help="JSON list of strings recorded from the test suite")
    args = ap.parse_args()

    texts: list[str] = []
    for line in (REPO / "tests" / "voice_corpus.jsonl").read_text().splitlines():
        if line.strip():
            texts.append(json.loads(line)["text"])
    for line in (REPO / "docs" / "bench" / "routing_eval.jsonl").read_text().splitlines():
        if line.strip():
            texts.append(json.loads(line)["text"])
    if args.seen:
        texts.extend(json.loads(Path(args.seen).read_text()))
    texts.extend(OVERLAPS)
    texts.extend(variants())

    seen: set[str] = set()
    rows = []
    for t in texts:
        if t in seen:
            continue
        seen.add(t)
        route, rule = asyncio.run(routing._classify_inner(t))
        rows.append({"text": t, "route": route, "rule": rule})
    OUT.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
    print(f"{len(rows)} utterances -> {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
