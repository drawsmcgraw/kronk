"""The shape of data/upgrades/status.json (sprint M18): one small schema,
checked whole at load by scripts/upgrades.py, by GET /api/upgrades
(upgrades_api.py) and by scripts/deploy_tag.sh's interrupted-run check
alike, so the three agree on what is corrupt (sprint M18 fix 3, H9).

No imports beyond the standard library: the script runs on the host's
/usr/bin/python3, the API in the orchestrator image.

The schema gives the type of every field the script or the API reads.
Keys it does not name are left alone (not read by anyone), but the whole
document is held to MAX_DEPTH. A spec is one of:
    str, bool          that type (bool is not an int)
    COUNT              a non-negative integer
    Opt(spec)          spec, or null
    [spec]             a list of spec
    {"*": spec}        an object whose every value is spec
    {"k": spec, ...}   an object; a key ending in "!" is required; "_closed"
                       true refuses keys the spec does not name
"""
from __future__ import annotations

MAX_DEPTH = 8
COUNT = "count"


class Opt:
    def __init__(self, spec):
        self.spec = spec


OUTCOME = {"_closed": True, "name!": str, "frm!": str, "to!": str, "result!": str, "detail": str,
           "commit": Opt(str), "state": str}
BLOCK = {"component!": str, "at": str, "state": str, "detail": str}
ATTENTION = {"id!": str, "at": str, "text!": str}
INTENT = {"component!": str, "from!": str, "to!": str, "step!": str, "deploy": Opt(str), "run": str, "at": str,
          "prev_tag": Opt(str), "tag": Opt(str), "ha_old_id": Opt(str), "head": Opt(str), "hash_from": Opt(str),
          "hash_to": Opt(str), "commit": Opt(str), "noticed": Opt(str), "notices": [str], "blocked": str,
          "ha_stopped": bool, "ha_started_at": str}
SCHEMA = {
    "updated": str,
    "months": {"*": {"attempts!": COUNT, "done": bool, "held_back": {"*": str},
                     "components": {"*": {"result!": str, "to!": str}}, "mismatch_noticed": [str]}},
    "intent": Opt(INTENT),
    "rollback_failed": Opt(BLOCK),
    "out_of_step": Opt(BLOCK),
    "last_run": Opt({"kind": str, "at": str, "result": str, "summary": str, "log": str, "outcomes": [OUTCOME]}),
    "major": Opt({"at": str, "report": str, "components": [str]}),
    "acknowledged": Opt({"at": str, "attention": [ATTENTION], "rollback_failed": Opt(BLOCK),
                         "out_of_step": Opt(BLOCK), "intent": Opt({})}),
    "unreported": Opt({"run": str, "log": str, "redeploys": COUNT, "outcomes": [OUTCOME]}),
    "attention": [ATTENTION],
    "pending_notices": [{"id!": str, "tag!": str, "title!": str, "body!": str, "created": str, "last_try": str,
                         "attempts": COUNT}],
}


def _depth(data) -> int:
    """The nesting depth, without recursion (a hand-made file can nest deep)."""
    deepest, stack = 0, [(data, 1)]
    while stack:
        v, d = stack.pop()
        if isinstance(v, (dict, list)):
            deepest = max(deepest, d)
            if deepest > MAX_DEPTH:
                return deepest
            stack.extend((x, d + 1) for x in (v.values() if isinstance(v, dict) else v))
    return deepest


def _check(path: str, v, spec, bad: list[str]) -> None:
    if isinstance(spec, Opt):
        if v is not None:
            _check(path, v, spec.spec, bad)
        return
    if spec is COUNT:
        if not (isinstance(v, int) and not isinstance(v, bool) and v >= 0):
            bad.append(f"{path} is {v!r}, not a count")
        return
    if spec in (str, bool):
        if not isinstance(v, spec):
            bad.append(f"{path} is {type(v).__name__}, not {spec.__name__}")
        return
    if isinstance(spec, list):
        if not isinstance(v, list):
            bad.append(f"{path} is {type(v).__name__}, not a list")
            return
        for i, x in enumerate(v):
            _check(f"{path}[{i}]", x, spec[0], bad)
        return
    if not isinstance(v, dict):
        bad.append(f"{path} is {type(v).__name__}, not an object")
        return
    if "*" in spec:
        for k, x in v.items():
            _check(f"{path}.{k}", x, spec["*"], bad)
        return
    names = {k.rstrip("!") for k in spec if k != "_closed"}
    for k, sub in spec.items():
        if k == "_closed":
            continue
        key = k.rstrip("!")
        if key in v:
            _check(f"{path}.{key}" if path else key, v[key], sub, bad)
        elif k.endswith("!"):
            bad.append(f"{path}.{key} is missing" if path else f"{key} is missing")
    if spec.get("_closed") and set(v) - names:
        bad.append(f"{path} has keys {sorted(set(v) - names)}")


def problems(data) -> list[str]:
    """What in a parsed status file is not shaped as the script writes it;
    empty when it is."""
    if not isinstance(data, dict):
        return [f"the file holds a {type(data).__name__}, not an object"]
    d = _depth(data)
    if d > MAX_DEPTH:
        return [f"nested deeper than {MAX_DEPTH} levels"]
    bad: list[str] = []
    _check("", data, SCHEMA, bad)
    return bad
