"""The voice corpus's routing layer, as one module for the test suite and
the running service (sprint M21 step 2, criterion 12).

tests/test_voice_corpus.py asserts, for every row of
tests/voice_corpus.jsonl, that routing lands on the row's (route, rule) and
that a row expecting the direct path gets its direct call. The /settings
page runs the same check against a candidate lead_in.yaml before a save
(settings_corpus.py). Both call row_diff() below, so the check the page
runs is the check the suite runs. Pure: routing.decide reads the config in
effect (or the pinned candidate) and nothing else.
"""
import routing


def outcome(text: str) -> dict:
    d = routing.decide(text)
    return {"route": d.route, "rule": d.rule, "direct": list(d.direct) if d.direct else None}


def describe(o: dict) -> str:
    s = f"{o['route']}/{o['rule']}"
    if o["direct"]:
        tool, args = o["direct"]
        s += f" direct {tool}({', '.join(f'{k}={v}' for k, v in sorted(args.items()))})"
    return s


def routing_diff(row: dict, o: dict) -> str | None:
    """Why the row's routing expectation fails for outcome o, or None."""
    e = row["expect"]
    if (o["route"], o["rule"]) != (e["route"], e["rule"]):
        return f"routed {o['route']}/{o['rule']}, expected {e['route']}/{e['rule']}"
    return None


def direct_diff(row: dict, o: dict) -> str | None:
    """Why the row's direct-call expectation fails for outcome o, or None."""
    e = row["expect"]
    if not e.get("direct"):
        return f"unexpected direct call {o['direct']}" if o["direct"] is not None else None
    if o["direct"] is None:
        return f"no direct call ({o['rule']})"
    tool, args = o["direct"]
    if tool != e["tool"]:
        return f"direct tool {tool}, expected {e['tool']}"
    if "action" in e and args.get("action") != e["action"]:
        return f"direct action {args.get('action')}, expected {e['action']}"
    if "thumb" in e and args.get("thumb") != e["thumb"]:
        return f"direct thumb {args.get('thumb')}, expected {e['thumb']}"
    if e.get("kind") == "another" and args.get("kind") != "another":
        return f"direct kind {args.get('kind')}, expected another"
    return None


def row_diff(row: dict, o: dict) -> str | None:
    """Both checks; None when the row passes. A row marked xfail.routing is
    expected to fail and is the caller's to skip."""
    return routing_diff(row, o) or direct_diff(row, o)
