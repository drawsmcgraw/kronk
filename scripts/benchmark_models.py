#!/usr/bin/env python3
"""
Benchmark llama.cpp server instances: measures TTFT and generation tok/s.
Hits each server directly (bypasses LiteLLM) on its native port.
"""
import json
import time
import httpx

MODELS = [
    {"name": "gemma-3-4b",   "port": 11439, "quant": "Q4_K_M"},
    {"name": "gemma-4-e4b",  "port": 11438, "quant": "Q4_K_M"},
    {"name": "mistral-nemo", "port": 11435, "quant": "Q8_0"},
    {"name": "bonsai-8b",    "port": 11437, "quant": "Q1_0"},
]

PROMPTS = [
    {
        "label": "short_gen",
        "desc": "Short factual answer",
        "messages": [{"role": "user", "content": "What is the capital of France? Answer in one sentence."}],
    },
    {
        "label": "medium_gen",
        "desc": "Paragraph explanation",
        "messages": [{"role": "user", "content": "Explain how a CPU cache works. Be thorough but concise, about 3 paragraphs."}],
    },
    {
        "label": "long_gen",
        "desc": "Long code generation",
        "messages": [{"role": "user", "content": "Write a Python function that implements a binary search tree with insert, search, and in-order traversal methods. Include docstrings and type hints."}],
    },
    {
        "label": "tool_routing",
        "desc": "Routing/classification",
        "messages": [{"role": "user", "content": "What was my average HRV last week and how does it compare to the prior week?"}],
    },
]

MAX_TOKENS = 512
TIMEOUT = 120


def benchmark_model(model: dict) -> list[dict]:
    base_url = f"http://127.0.0.1:{model['port']}"
    results = []

    # Check if server is up
    try:
        r = httpx.get(f"{base_url}/health", timeout=3)
        if r.status_code != 200:
            print(f"  [SKIP] {model['name']} — health check failed ({r.status_code})")
            return []
    except Exception as e:
        print(f"  [SKIP] {model['name']} — unreachable: {e}")
        return []

    for prompt in PROMPTS:
        print(f"  [{model['name']}] {prompt['label']} ... ", end="", flush=True)

        payload = {
            "messages": prompt["messages"],
            "max_tokens": MAX_TOKENS,
            "stream": True,
            "temperature": 0.0,
        }

        ttft = None
        token_count = 0
        start = time.perf_counter()

        try:
            with httpx.Client(timeout=TIMEOUT) as client:
                with client.stream("POST", f"{base_url}/v1/chat/completions", json=payload) as resp:
                    resp.raise_for_status()
                    for raw_line in resp.iter_lines():
                        line = raw_line.strip()
                        if not line or not line.startswith("data:"):
                            continue
                        data_str = line[5:].strip()
                        if data_str == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data_str)
                        except json.JSONDecodeError:
                            continue

                        delta = chunk.get("choices", [{}])[0].get("delta", {})
                        content = delta.get("content", "")

                        if content:
                            if ttft is None:
                                ttft = time.perf_counter() - start
                            token_count += 1  # rough: 1 SSE chunk ≈ 1 token for llama.cpp

            elapsed = time.perf_counter() - start
            gen_time = elapsed - (ttft or 0)
            tps = token_count / gen_time if gen_time > 0 else 0

            print(f"TTFT={ttft:.3f}s  tokens={token_count}  tok/s={tps:.1f}")

            results.append({
                "model": model["name"],
                "quant": model["quant"],
                "prompt": prompt["label"],
                "desc": prompt["desc"],
                "ttft_s": round(ttft, 3) if ttft else None,
                "tokens": token_count,
                "gen_time_s": round(gen_time, 3),
                "tok_per_s": round(tps, 1),
            })

        except Exception as e:
            elapsed = time.perf_counter() - start
            print(f"ERROR after {elapsed:.1f}s: {e}")
            results.append({
                "model": model["name"],
                "quant": model["quant"],
                "prompt": prompt["label"],
                "desc": prompt["desc"],
                "error": str(e),
            })

    return results


def print_table(all_results: list[dict]):
    # Pivot: rows = models, columns = prompts
    models_seen = []
    prompts_seen = []
    data = {}

    for r in all_results:
        if "error" in r:
            continue
        key = r["model"]
        if key not in models_seen:
            models_seen.append(key)
        if r["prompt"] not in prompts_seen:
            prompts_seen.append(r["prompt"])
        data[(key, r["prompt"])] = r

    if not models_seen:
        print("No successful results to display.")
        return

    # TTFT table
    print("\n--- TTFT (seconds) ---")
    col_w = 14
    header = f"{'Model':<18} {'Quant':<10}" + "".join(f"{p:>{col_w}}" for p in prompts_seen) + f"{'avg TTFT':>{col_w}}"
    print(header)
    print("-" * len(header))
    for m in models_seen:
        quant = data.get((m, prompts_seen[0]), {}).get("quant", "")
        vals = [data.get((m, p), {}).get("ttft_s") for p in prompts_seen]
        valid = [v for v in vals if v is not None]
        avg = sum(valid) / len(valid) if valid else None
        row = f"{m:<18} {quant:<10}"
        for v in vals:
            row += f"{v if v is not None else 'N/A':>{col_w}}"
        row += f"{avg:.3f}" if avg else "N/A"
        print(row)

    # Tok/s table
    print("\n--- Generation tok/s ---")
    header2 = f"{'Model':<18} {'Quant':<10}" + "".join(f"{p:>{col_w}}" for p in prompts_seen) + f"{'avg tok/s':>{col_w}}"
    print(header2)
    print("-" * len(header2))
    for m in models_seen:
        quant = data.get((m, prompts_seen[0]), {}).get("quant", "")
        vals = [data.get((m, p), {}).get("tok_per_s") for p in prompts_seen]
        valid = [v for v in vals if v is not None]
        avg = sum(valid) / len(valid) if valid else None
        row = f"{m:<18} {quant:<10}"
        for v in vals:
            row += f"{v if v is not None else 'N/A':>{col_w}}"
        row += f"{avg:.1f}" if avg else "N/A"
        print(row)


def main():
    print(f"Benchmarking {len(MODELS)} models × {len(PROMPTS)} prompts\n")
    all_results = []

    for model in MODELS:
        print(f"\n=== {model['name']} (port {model['port']}, {model['quant']}) ===")
        results = benchmark_model(model)
        all_results.extend(results)

    print_table(all_results)

    out_path = "/tmp/bench_results.json"
    with open(out_path, "w") as f:
        json.dump(all_results, f, indent=2)
    print(f"\nFull results saved to {out_path}")


if __name__ == "__main__":
    main()
