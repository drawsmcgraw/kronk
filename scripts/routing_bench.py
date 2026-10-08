#!/usr/bin/env python3
"""Offline routing bench (INTENT_ROUTER_PLAN, 2026-10-05).

Arms score the same eval set (docs/bench/routing_eval.jsonl): the regex pins
as they are (baseline), an embedding kNN router behind the pins, and any
/v1/systemone decision model behind the pins. Items first seen before
SPLIT_DATE are exemplars / threshold-fitting data; later ones are the test.
Pass rule (plan): wrong-lane <= baseline on the test set AND 0 on the named
cases; coverage >= 50% of the home traffic the pins leave to the coordinator;
p95 classify latency < 50 ms.

  ./tests/.venv/bin/python scripts/routing_bench.py <label> [arm ...]
  arms: regex knn systemone:<name>=<port> ...
"""
import asyncio, json, math, statistics, sys, time, urllib.request
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "orchestrator"))
import routing  # noqa: E402

EVAL = ROOT / "docs" / "bench" / "routing_eval.jsonl"
SPLIT_DATE = "2026-09-01"
SPECIALISTS = ("home", "research", "devops", "health", "finance", "talkie")
KNN_K = 3

ROUTE_DESCRIPTIONS = {
    "home": "home devices and personal data: weather and forecasts, playing or controlling music on the speakers, what song is playing, the shopping list, solar panels and inverters, the hot tub, updating the magic mirror",
    "research": "needs the live web: current news, prices, officeholders, schedules, flight status, recent events, a specific recipe or page, or the user explicitly asks to search or look something up",
    "devops": "investigating or checking the magic mirror machine itself: its uptime, OS, modules, logs, services",
    "health": "the user's own health data: heart rate, sleep, workouts, step counts",
    "finance": "the user's own finances: accounts, positions, retirement projections",
    "direct": "general knowledge, math, definitions, explanations, chit-chat, acknowledgements, meaningless fragments, or a follow-up that only makes sense with earlier conversation; also a question that needs more than one of the other lanes at once",
}


def load():
    items = [json.loads(l) for l in EVAL.open()]
    train = [i for i in items if not i["bench"] and i["first"] < SPLIT_DATE]
    test = [i for i in items if not i["bench"] and i["first"] >= SPLIT_DATE]
    return items, train, test


# ── arms ─────────────────────────────────────────────────────────────────────
def regex_route(text):
    route, rule = asyncio.run(routing._classify_inner(text))
    return route, rule


class KNN:
    def __init__(self, exemplars):
        from fastembed import TextEmbedding
        self.model = TextEmbedding("BAAI/bge-small-en-v1.5")
        self.ex = exemplars
        self.vecs = self._embed([e["text"] for e in exemplars])
        self.thr = {}

    def _embed(self, texts):
        import numpy as np
        v = np.array(list(self.model.embed(texts)), dtype="float32")
        return v / (np.linalg.norm(v, axis=1, keepdims=True) + 1e-9)

    def predict(self, texts, exclude_self=False):
        import numpy as np
        q = self._embed(texts)
        sims = q @ self.vecs.T
        out = []
        for i in range(len(texts)):
            row = sims[i].copy()
            if exclude_self:
                row[i] = -1
            idx = np.argsort(-row)[:KNN_K]
            votes = Counter(self.ex[j]["label"] for j in idx)
            label, _ = votes.most_common(1)[0]
            best = max(float(row[j]) for j in idx if self.ex[j]["label"] == label)
            nn = self.ex[int(idx[0])]["text"]
            out.append((label, best, nn))
        return out

    def fit_thresholds(self, allowed_wrong_rate=0.0):
        """Per-class threshold from leave-one-out predictions on the exemplars: the
        lowest score such that, above it, the class's wrong-lane rate stays within
        allowed_wrong_rate (0 = never wrong on the exemplars)."""
        preds = self.predict([e["text"] for e in self.ex], exclude_self=True)
        by = defaultdict(list)
        for e, (label, sim, _) in zip(self.ex, preds):
            by[label].append((sim, e["label"] == label))
        self.thr = fit_class_thresholds(by, allowed_wrong_rate)
        return self.thr

    def route(self, text):
        label, sim, nn = self.predict([text])[0]
        if label == "direct" or sim < self.thr.get(label, 1.01):
            return "direct", f"knn:abstain sim={sim:.2f} nn={nn[:40]!r}"
        return label, f"knn sim={sim:.2f} nn={nn[:40]!r}"


class SystemOne:
    def __init__(self, name, port):
        self.name, self.url = name, f"http://127.0.0.1:{port}/v1/systemone"
        self.thr = {}

    def ask(self, text):
        body = {"state": text, "questions": {"lane": {"type": "choice", "instructions": "Which lane should answer this request from a home assistant user? Pick direct when the request needs no specialist, is a follow-up, or needs more than one lane.", "criteria": ROUTE_DESCRIPTIONS}}}
        req = urllib.request.Request(self.url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as r:
            ans = json.load(r)["answers"]["lane"]
        probs = ans["probabilities"]
        top = max(probs, key=probs.get)
        return top, float(probs[top]), float(ans.get("confidence", probs[top]))

    def fit_thresholds(self, exemplars, allowed_wrong_rate=0.0):
        if not hasattr(self, "_fit_cache"):
            self._fit_cache = [(e, self.ask(e["text"])) for e in exemplars]
        by = defaultdict(list)
        for e, (top, p, _) in self._fit_cache:
            by[top].append((p, e["label"] == top))
        self.thr = fit_class_thresholds(by, allowed_wrong_rate)
        return self.thr

    def route(self, text):
        top, p, conf = self.ask(text)
        if top == "direct" or p < self.thr.get(top, 1.01):
            return "direct", f"{self.name}:abstain p={p:.2f} top={top}"
        return top, f"{self.name} p={p:.2f}"


def fit_class_thresholds(by, allowed_wrong_rate):
    """by: class -> [(score, correct)]. Walk each class from the highest score down,
    keeping the lowest threshold whose running wrong rate stays within budget."""
    thr = {}
    for label, rows in by.items():
        best, wrong, seen = 1.01, 0, 0
        for score, ok in sorted(rows, reverse=True):
            seen += 1; wrong += 0 if ok else 1
            if wrong / seen <= allowed_wrong_rate:
                best = score
        thr[label] = best
    return thr


def behind_pins(fn):
    def route(text):
        r, rule = regex_route(text)
        if rule != "default":
            return r, f"pin:{rule}"
        return fn(text)
    return route


# ── scoring ──────────────────────────────────────────────────────────────────
def score(name, route_fn, test, baseline_default_home):
    rows, lat = [], []
    for it in test:
        t0 = time.perf_counter()
        pred, why = route_fn(it["text"])
        lat.append((time.perf_counter() - t0) * 1000)
        pinned = pred != "direct"
        wrong = pinned and pred != it["label"]          # a specialist that is not the lane, incl. label=direct (composites)
        rows.append({**it, "pred": pred, "why": why, "wrong": wrong})
    wrong = [r for r in rows if r["wrong"]]
    named_wrong = [r for r in wrong if r["named"]]
    cov_pool = [r for r in rows if r["text"] in baseline_default_home]
    covered = [r for r in cov_pool if r["pred"] == "home"]
    lat.sort()
    p95 = lat[int(len(lat) * 0.95) - 1] if lat else 0
    return {"arm": name, "n": len(rows), "wrong_lane": len(wrong), "named_wrong": len(named_wrong),
            "coverage": f"{len(covered)}/{len(cov_pool)}", "abstain": sum(1 for r in rows if r["pred"] == "direct"),
            "p95_ms": round(p95, 1), "median_ms": round(statistics.median(lat), 1),
            "wrong_rows": [(r["text"][:70], r["label"], r["pred"], r["why"][:50]) for r in wrong],
            "rows": rows}


def main():
    label = sys.argv[1]; arms = sys.argv[2:] or ["regex", "knn"]
    items, train, test = load()
    # the coverage pool: home-labelled test items the pins leave to the coordinator
    baseline_default_home = {it["text"] for it in test if it["label"] == "home" and regex_route(it["text"])[1] == "default"}
    results = []
    for arm in arms:
        if arm == "regex":
            results.append(score("regex", regex_route, test, baseline_default_home))
        elif arm == "knn":
            k = KNN(train)
            for rate in (0.0, 0.02, 0.05):
                thr = k.fit_thresholds(rate)
                print(f"knn@{int(rate*100)} thresholds:", {a: round(b, 3) for a, b in thr.items()})
                results.append(score(f"regex+knn@{int(rate*100)}", behind_pins(k.route), test, baseline_default_home))
        elif arm.startswith("systemone:"):
            name, port = arm.split(":", 1)[1].split("=")
            s = SystemOne(name, int(port))
            for rate in (0.0, 0.02, 0.05):
                thr = s.fit_thresholds(train, rate)
                print(f"{name}@{int(rate*100)} thresholds:", {a: round(b, 3) for a, b in thr.items()})
                results.append(score(f"regex+{name}@{int(rate*100)}", behind_pins(s.route), test, baseline_default_home))
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    out = ROOT / "docs" / "bench" / f"routing_bench_{stamp}_{label}"
    md = [f"# Routing bench — {label} ({stamp})", "", f"test items {len(test)} (first seen ≥ {SPLIT_DATE}, bench prompts excluded); exemplars {len(train)}; coverage pool = home-labelled test items the pins send to the coordinator ({len(baseline_default_home)})", "",
          "| Arm | wrong-lane | named wrong | coverage | abstain | median ms | p95 ms |", "|---|---|---|---|---|---|---|"]
    for r in results:
        md.append(f"| {r['arm']} | {r['wrong_lane']} | {r['named_wrong']} | {r['coverage']} | {r['abstain']} | {r['median_ms']} | {r['p95_ms']} |")
    md.append(""); md.append("Pass rule: wrong-lane ≤ regex baseline AND named wrong = 0 AND coverage ≥ 50% AND p95 < 50 ms. `@N` = thresholds fit allowing N% wrong-lane on the exemplars (leave-one-out).")
    for r in results:
        if r["wrong_rows"]:
            md.append(f"\n## {r['arm']}: wrong-lane rows"); md += [f"- {t!r} label={l} pred={p} ({w})" for t, l, p, w in r["wrong_rows"]]
    md.append("\n## Coverage pool (home-labelled test items the pins leave to the coordinator), per arm")
    pool = [it for it in test if it["text"] in baseline_default_home]
    for it in pool:
        line = f"- {it['text'][:60]!r} [{it['sub']}]: " + "; ".join(f"{r['arm'].replace('regex+','')}={row['pred']} ({row['why'][:28]})" for r in results if r["arm"] != "regex" for row in r["rows"] if row["text"] == it["text"])
        md.append(line)
    out.with_suffix(".md").write_text("\n".join(md) + "\n")
    out.with_suffix(".json").write_text(json.dumps(results, indent=1, ensure_ascii=False))
    print("\n".join(md)); print("wrote", out.with_suffix(".md"))


if __name__ == "__main__":
    main()
