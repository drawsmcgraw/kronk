"""A stand-in for Home Assistant's template engine over a fake registry, for
rendering the blueprint's real Jinja offline (sprint M22).

HA renders templates with Jinja2 (the same version is pinned in
tests/requirements.txt: 3.1.6, as in homeassistant 2026.5.4) plus its own
globals, filters and tests. This module supplies the ones the Kronk music
blueprint and scripts/satellite_area_check.py use, answered from a small
in-memory registry: areas, devices (area, MAC `connections`, `identifiers`)
and entities (device, optional own area, platform, attributes).

It is not HA. What it reproduces, and nothing more:

- area_name / area_id / area_entities / areas with HA's rule that an entity
  without its own area is in its device's area;
- device_id / device_attr / integration_entities / states / state_attr /
  is_state_attr / expand;
- the `match` test (re.match, `ignorecase`), the `is_state_attr` test and
  the to_json filter;
- how a script `variables:` value comes back: the rendered text, stripped,
  read as a Python literal when it is a list, dict or bool, else the string.

Nothing here reaches the network.
"""
from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import jinja2
import jinja2.sandbox
import yaml

ROOT = Path(__file__).resolve().parent.parent
BLUEPRINT = ROOT / "ha" / "blueprints" / "mass_assist_kronk.yaml"


@dataclass
class Device:
    name: str
    area_id: str | None = None
    connections: list[tuple[str, str]] = field(default_factory=list)
    identifiers: list[tuple[str, str]] = field(default_factory=list)
    name_by_user: str | None = None


@dataclass
class Entity:
    device_id: str | None
    platform: str
    attributes: dict = field(default_factory=dict)
    state: str = "idle"
    area_id: str | None = None          # the entity's own area; None = its device's


@dataclass
class Registry:
    areas: dict[str, str]               # area_id -> name
    devices: dict[str, Device]
    entities: dict[str, Entity]


class _State:
    def __init__(self, entity_id: str, ent: Entity):
        self.entity_id = entity_id
        self.state = ent.state
        self.attributes = dict(ent.attributes)
        self.name = ent.attributes.get("friendly_name") or entity_id
        self.domain = entity_id.split(".", 1)[0]


class _States:
    """`states('x.y')` and `states.domain` like HA's."""

    def __init__(self, reg: Registry):
        self._reg = reg

    def __call__(self, entity_id):
        ent = self._reg.entities.get(entity_id)
        return ent.state if ent else "unknown"

    def __getattr__(self, domain):
        if domain.startswith("_"):
            raise AttributeError(domain)
        return [_State(e, ent) for e, ent in sorted(self._reg.entities.items())
                if e.split(".", 1)[0] == domain]


def environment(reg: Registry) -> jinja2.Environment:
    env = jinja2.sandbox.ImmutableSandboxedEnvironment(extensions=["jinja2.ext.loopcontrols", "jinja2.ext.do"])

    def entity_area(entity_id):
        ent = reg.entities.get(entity_id)
        if not ent:
            return None
        if ent.area_id:
            return ent.area_id
        dev = reg.devices.get(ent.device_id) if ent.device_id else None
        return dev.area_id if dev else None

    def area_id(lookup):
        if not lookup:
            return None
        if lookup in reg.areas:
            return lookup
        if lookup in reg.devices:
            return reg.devices[lookup].area_id
        if lookup in reg.entities:
            return entity_area(lookup)
        for aid, name in reg.areas.items():
            if name.lower() == str(lookup).lower():
                return aid
        return None

    def area_name(lookup):
        aid = area_id(lookup)
        return reg.areas.get(aid) if aid else None

    def area_entities(area):
        aid = area_id(area)
        if not aid:
            return []
        return [e for e in sorted(reg.entities) if entity_area(e) == aid]

    def device_id(entity_id):
        ent = reg.entities.get(entity_id)
        return ent.device_id if ent else None

    def device_attr(dev_id, attr):
        dev = reg.devices.get(dev_id) if dev_id else None
        return getattr(dev, attr, None) if dev else None

    def integration_entities(platform):
        return [e for e in sorted(reg.entities) if reg.entities[e].platform == platform]

    def state_attr(entity_id, name):
        ent = reg.entities.get(entity_id)
        return ent.attributes.get(name) if ent else None

    def is_state_attr(entity_id, name, value):
        return state_attr(entity_id, name) == value

    def expand(*args):
        ids = []
        for a in args:
            ids.extend([a] if isinstance(a, str) else list(a))
        return sorted((_State(e, reg.entities[e]) for e in set(ids) if e in reg.entities),
                      key=lambda s: s.entity_id)

    def regex_match(value, find="", ignorecase=False):
        return bool(re.match(find, str(value), re.IGNORECASE if ignorecase else 0))

    funcs = {"area_id": area_id, "area_name": area_name, "area_entities": area_entities,
             "areas": lambda: list(reg.areas), "device_id": device_id, "device_attr": device_attr,
             "integration_entities": integration_entities, "state_attr": state_attr,
             "is_state_attr": is_state_attr, "expand": expand}
    env.globals.update(funcs, states=_States(reg))
    env.filters.update({k: v for k, v in funcs.items() if k != "areas"},
                       to_json=lambda v: json.dumps(v, ensure_ascii=False))
    env.tests.update(match=regex_match, is_state_attr=is_state_attr)
    return env


def native(text: str):
    """A rendered `variables:` value the way HA hands it on."""
    text = text.strip()
    try:
        v = ast.literal_eval(text)
    except (ValueError, SyntaxError, MemoryError, TypeError):
        return text
    return v if isinstance(v, (list, dict, bool)) else text


def render(env: jinja2.Environment, template: str, ctx: dict, parse: bool = True):
    out = env.from_string(template).render(**ctx)
    return native(out) if parse else out


# ── the blueprint file ───────────────────────────────────────────────────────

class Input:
    def __init__(self, name):
        self.name = name


class _Loader(yaml.SafeLoader):
    pass


_Loader.add_constructor("!input", lambda loader, node: Input(loader.construct_scalar(node)))


def blueprint() -> dict:
    return yaml.load(BLUEPRINT.read_text(), Loader=_Loader)


def blueprint_variable_blocks(bp: dict | None = None) -> list[dict]:
    """Every top-level `variables:` step of the blueprint's actions, in order."""
    bp = bp or blueprint()
    return [a["variables"] for a in bp["actions"] if isinstance(a, dict) and "variables" in a]


class Trigger(dict):
    """`trigger` as HA's conversation trigger gives it (attribute access)."""
    def __getattr__(self, k):
        try:
            return self[k]
        except KeyError:
            raise AttributeError(k)


def run_variables(env, blocks: list[dict], ctx: dict, inputs: dict, stop_after: str) -> dict:
    """Render the blueprint's variables in file order, as HA does (each value
    sees the ones before it), until `stop_after` is set. A `!input` value is
    taken from `inputs`; a mapping value (action_data) is skipped: nothing
    kronk_text reads depends on it."""
    ctx = dict(ctx)
    for block in blocks:
        for name, value in block.items():
            if isinstance(value, Input):
                ctx[name] = inputs[value.name]
            elif isinstance(value, str):
                ctx[name] = render(env, value, ctx)
            if name == stop_after:
                return ctx
    raise KeyError(stop_after)
