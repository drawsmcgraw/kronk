"""Benchmark Kronk's `/message` and `/api/chat` endpoints across the suite
defined in `benchmarks/queries.yml`.

Each query runs N trials per endpoint. History is cleared between trials so
runs are independent. Results land in `benchmarks/results/<label>-<ts>.jsonl`,
one JSON object per trial.

Usage
-----
    python benchmarks/agent_bench.py --label v1-baseline
    python benchmarks/agent_bench.py --label v2-coordinator --trials 3
    python benchmarks/agent_bench.py --label debug --query-id weather_default \
        --endpoint message --trials 1
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
import yaml

REPO = Path(__file__).resolve().parent.parent
QUERIES_FILE = REPO / "benchmarks" / "queries.yml"
RESULTS_DIR = REPO / "benchmarks" / "results"
NGINX_URL = "http://localhost"


async def clear_chat_history(client: httpx.AsyncClient) -> None:
    """Best-effort: wipe global history so trials don't leak into each other."""
    try:
        await client.delete(f"{NGINX_URL}/history", timeout=5)
    except Exception:
        pass


async def hit_message(client: httpx.AsyncClient, query: str, timeout: int = 240) -> dict:
    """POST to /message, consume SSE, return timing + response."""
    t0 = time.monotonic()
    ttft: float | None = None
    tokens: list[str] = []
    try:
        async with client.stream(
            "POST",
            f"{NGINX_URL}/message",
            json={"text": query},
            timeout=timeout,
        ) as resp:
            if resp.status_code != 200:
                return {
                    "error": f"HTTP {resp.status_code}",
                    "duration_s": round(time.monotonic() - t0, 3),
                }
            async for line in resp.aiter_lines():
                if not line.startswith("data:"):
                    continue
                payload = line[len("data:"):].strip()
                if payload == "[DONE]":
                    break
                try:
                    chunk = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                if "token" in chunk:
                    if ttft is None:
                        ttft = round(time.monotonic() - t0, 3)
                    tokens.append(chunk["token"])
    except Exception as e:
        return {
            "error": f"{type(e).__name__}: {e}",
            "duration_s": round(time.monotonic() - t0, 3),
        }
    return {
        "duration_s": round(time.monotonic() - t0, 3),
        "ttft_s": ttft,
        "response": "".join(tokens),
    }


async def hit_api_chat(client: httpx.AsyncClient, query: str, timeout: int = 240) -> dict:
    """POST to /api/chat (Ollama shim), non-streaming."""
    t0 = time.monotonic()
    try:
        resp = await client.post(
            f"{NGINX_URL}/api/chat",
            json={
                "model": "kronk",
                "messages": [{"role": "user", "content": query}],
                "stream": False,
            },
            timeout=timeout,
        )
        if resp.status_code != 200:
            return {
                "error": f"HTTP {resp.status_code}",
                "duration_s": round(time.monotonic() - t0, 3),
            }
        data = resp.json()
        return {
            "duration_s": round(time.monotonic() - t0, 3),
            "ttft_s": None,  # non-streaming
            "response": data.get("message", {}).get("content", ""),
        }
    except Exception as e:
        return {
            "error": f"{type(e).__name__}: {e}",
            "duration_s": round(time.monotonic() - t0, 3),
        }


ENDPOINTS = {"message": hit_message, "api_chat": hit_api_chat}


GARBAGE_PATTERNS = (
    re.compile(r"<unused\d+>"),
    re.compile(r"<pad>"),
    re.compile(r"<\|.*?\|>"),  # other special tokens
)


def quality_flag(result: dict, q: dict) -> tuple[str, str | None]:
    """Classify trial quality. Returns (flag, reason_if_not_pass)."""
    if "error" in result:
        return "fail", f"error: {result['error']}"
    resp = (result.get("response") or "").strip()
    if not resp:
        return "fail", "empty response"
    if any(p.search(resp) for p in GARBAGE_PATTERNS):
        return "fail", "garbage tokens"
    qc = q.get("quality_check", {})
    pattern = qc.get("regex")
    if pattern:
        if re.search(pattern, resp):
            return "pass", None
        else:
            return "suspect", f"regex no match: {pattern}"
    return "unknown", "no quality_check defined"


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--label", required=True, help="e.g. 'v1-baseline' or 'v2-coordinator'")
    parser.add_argument("--trials", type=int, default=3, help="trials per (query, endpoint) pair")
    parser.add_argument("--query-id", help="Run only the named query (debug)")
    parser.add_argument(
        "--endpoint",
        choices=list(ENDPOINTS),
        help="Only this endpoint (default: both)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=240,
        help="per-request timeout seconds (default 240)",
    )
    args = parser.parse_args()

    spec = yaml.safe_load(QUERIES_FILE.read_text())
    queries = spec["queries"]
    if args.query_id:
        queries = [q for q in queries if q["id"] == args.query_id]
        if not queries:
            raise SystemExit(f"No query with id={args.query_id}")
    endpoints = [args.endpoint] if args.endpoint else list(ENDPOINTS)

    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = RESULTS_DIR / f"{args.label}-{ts}.jsonl"

    print(f"writing → {out_path}")
    print(f"queries={len(queries)} endpoints={endpoints} trials={args.trials} timeout={args.timeout}s")

    total_runs = len(queries) * len(endpoints) * args.trials
    run_idx = 0
    started_overall = time.monotonic()

    async with httpx.AsyncClient() as client:
        with out_path.open("w") as f:
            for q in queries:
                for endpoint in endpoints:
                    for trial in range(args.trials):
                        run_idx += 1
                        # Wipe chat-UI history before each /message trial; the
                        # /api/chat shim is already stateless so this is for
                        # parity (cheap noop for that endpoint).
                        await clear_chat_history(client)
                        fn = ENDPOINTS[endpoint]
                        t_wall = datetime.now(timezone.utc).isoformat()
                        result = await fn(client, q["query"], timeout=args.timeout)
                        flag, reason = quality_flag(result, q)
                        record = {
                            "query_id": q["id"],
                            "domain": q["domain"],
                            "endpoint": endpoint,
                            "trial": trial,
                            "started_at": t_wall,
                            "query_text": q["query"],
                            "expected": q.get("expected", {}),
                            **result,
                            "quality": flag,
                            "quality_reason": reason,
                        }
                        f.write(json.dumps(record) + "\n")
                        f.flush()
                        dur = result.get("duration_s", -1)
                        print(
                            f"  [{run_idx}/{total_runs}] {q['id']:25s} "
                            f"{endpoint:8s} t{trial}: {dur:6.1f}s  {flag}"
                            + (f"  ({reason})" if reason and flag != "pass" else "")
                        )

    elapsed = time.monotonic() - started_overall
    print(f"\ndone in {elapsed:.1f}s → {out_path}")
    print(f"to summarize:  python benchmarks/summarize.py {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
