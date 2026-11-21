"""The blueprint's hand-off text to Kronk, rendered from the real blueprint
file and resolved by tool_service (sprint M22, criterion 1;
docs/incidents/INVESTIGATION_2026-11-19_basement_aerosmith.md, fix 2).

conversation.process cannot carry the satellite's device id, so the Kronk
music blueprint writes the target into the words (`kronk_text`, fork
changes 9 and 14). "The two tiers must agree" (tests/test_music_players.py)
covered resolution order but not this text: the basement satellite's room
was written into every hand-off although no Music Assistant player was in
it, and tool_service refused the room.

Each test renders the blueprint's own variables, in file order, through a
stand-in for HA's template engine over a fake registry
(tests/ha_template_env.py), then does what Kronk does with the text: the
model passes the words after "on the" / "in the" as play_music's `player`
(or nothing, for the bare sentence), and tool_service's resolve_players
picks the speakers from the player list its own template renders over the
same registry. The hand-off carries no origin.
"""
import re

import pytest
from fastapi import HTTPException

import tool_service.main as ts
from tests import ha_template_env as hte
from tests.ha_template_env import Device, Entity, Registry

KITCHEN_MA = "media_player.home_assistant_voice_0ac919_pe_01"
BASEMENT_MA = "media_player.home_assistant_voice_0ab117"
OFFICE_MA = "media_player.satellite1_d8d1bc"
DEFAULT = KITCHEN_MA


KITCHEN_OTHER = "media_player.kitchen_sonos"
OFFICE_GROUP = "media_player.office_sync_group"


def _registry(basement_ma_area=None, kitchen_other=False, office_group=False) -> Registry:
    """Kitchen and office pairs as in HA; the basement pair with its MA
    device's area as given (None: the registry before fix 1); a second
    office satellite with no MA player of its own; a garage satellite with
    no MA player in a room with none. `kitchen_other` adds a second MA
    player (not a group) in the Kitchen; `office_group` adds an MA sync
    group in the Office."""
    areas = {"kitchen": "Kitchen", "office": "Office", "basement_theater": "Basement Theater",
             "garage": "Garage"}
    dev, ent = {}, {}

    def satellite(key, name, area, mac, ma=None, ma_name=None, ma_area="same"):
        dev[f"{key}-esp"] = Device(name, area, connections=[("mac", mac)],
                                   identifiers=[("esphome", mac.replace(":", "").upper())])
        ent[f"assist_satellite.{key}"] = Entity(f"{key}-esp", "esphome")
        ent[f"media_player.{key}_esphome"] = Entity(f"{key}-esp", "esphome",
                                                    {"friendly_name": f"{name} media player"})
        if ma:
            dev[f"{key}-ma"] = Device(ma_name, area if ma_area == "same" else ma_area,
                                      identifiers=[("music_assistant", "up" + mac.replace(":", ""))])
            ent[ma] = Entity(f"{key}-ma", "music_assistant",
                             {"friendly_name": ma_name, "mass_player_type": "player"})

    satellite("kitchen_pe", "kitchen voice pe", "kitchen", "20:f8:3b:0a:c9:19", KITCHEN_MA, "kitchen voice pe ma")
    satellite("office_sat1", "satellite 1", "office", "14:c1:9f:d8:d1:bc", OFFICE_MA, "satellite 1 ma")
    satellite("basement_pe", "basement voice pe", "basement_theater", "20:f8:3b:0a:b1:17", BASEMENT_MA,
              "basement voice pe ma", ma_area=basement_ma_area)
    satellite("office_sat2", "office satellite 2", "office", "aa:bb:cc:00:00:02")
    satellite("garage_sat", "garage satellite", "garage", "aa:bb:cc:00:00:03")
    # A Music Assistant player in no area (a TV): listed, never a room.
    dev["tv"] = Device("SHIELD", None, identifiers=[("music_assistant", "up00044b82ea7c")])
    ent["media_player.shield_2"] = Entity("tv", "music_assistant",
                                          {"friendly_name": "SHIELD", "mass_player_type": "player"})
    if kitchen_other:
        dev["kitchen-sonos"] = Device("Kitchen Sonos", "kitchen",
                                      identifiers=[("music_assistant", "RINCON_000E58000001")])
        ent[KITCHEN_OTHER] = Entity("kitchen-sonos", "music_assistant",
                                    {"friendly_name": "Kitchen Sonos", "mass_player_type": "player"})
    if office_group:
        dev["office-group"] = Device("Office Sync", "office",
                                     identifiers=[("music_assistant", "syncgroup_office")])
        ent[OFFICE_GROUP] = Entity("office-group", "music_assistant",
                                   {"friendly_name": "Office Sync", "mass_player_type": "group"})
    return Registry(areas, dev, ent)


def _handoff(reg: Registry, satellite: str, sentence: str, named: str | None = None) -> dict:
    """The blueprint's variables up to kronk_text, for one request."""
    env = hte.environment(reg)
    slots = {"media_name": "x"}
    if named:
        slots["area_or_player_name"] = named
    trig = hte.Trigger(id="album", sentence=sentence, slots=slots, device_id=f"{satellite}-esp")
    return hte.run_variables(env, hte.blueprint_variable_blocks(), {"trigger": trig},
                             {"default_player_entity_id_input": DEFAULT}, stop_after="kronk_text")


_SUFFIX = re.compile(r"^ (?:on|in) the (?P<target>.+)$")


def _kronk_resolves(reg: Registry, sentence: str, text: str, named: str | None) -> list[str]:
    """What Kronk plays the hand-off text on: the target words the text
    carries (the model's play_music `player`), through resolve_players
    against the player list tool_service's template gives for `reg`."""
    env = hte.environment(reg)
    raw = env.from_string(ts._players_template(None)).render()
    players, _, ha_areas = ts.parse_player_payload_areas(raw)
    assert text.startswith(sentence), text
    if text == sentence:
        spoken = named                   # the bare sentence, or a target the speaker named
    else:
        m = _SUFFIX.match(text[len(sentence):])
        assert m, f"unexpected hand-off suffix: {text!r}"
        spoken = m.group("target")
    targets, _ = ts.resolve_players(spoken, None, players, DEFAULT, ha_areas=ha_areas)
    return [t["entity_id"] for t in targets]


GRIT = "Play the album Get a Grit by Aerosmith."         # tests/voice_corpus.jsonl, inc-1119-…

SHAPES = [
    # id, registry, satellite, sentence, named target, expected hand-off text, expected player
    # Rung 2: the room's MA players are exactly the own player -> the room.
    ("own-player-alone-in-room", _registry("basement_theater"), "kitchen_pe", GRIT, None,
     f"{GRIT} in the Kitchen", KITCHEN_MA),
    ("own-player-alone-in-room-office", _registry("basement_theater"), "office_sat1", GRIT, None,
     f"{GRIT} in the Office", OFFICE_MA),
    ("own-player-after-fix-1", _registry("basement_theater"), "basement_pe", GRIT, None,
     f"{GRIT} in the Basement Theater", BASEMENT_MA),
    # Rung 3: an own player the room does not hold alone -> the player by name.
    ("own-player-room-without-player", _registry(None), "basement_pe", GRIT, None,
     f"{GRIT} on the basement voice pe ma", BASEMENT_MA),
    ("own-player-plus-another-in-room", _registry("basement_theater", kitchen_other=True), "kitchen_pe",
     GRIT, None, f"{GRIT} on the kitchen voice pe ma", KITCHEN_MA),
    ("own-player-room-with-sync-group", _registry("basement_theater", office_group=True), "office_sat1",
     GRIT, None, f"{GRIT} on the satellite 1 ma", OFFICE_MA),
    ("own-player-in-another-room", _registry("kitchen"), "basement_pe", GRIT, None,
     f"{GRIT} on the basement voice pe ma", BASEMENT_MA),
    # Rung 4: no own player, the room holds one -> the room (group-preferred).
    ("no-own-player-room-with-player", _registry(None), "office_sat2", GRIT, None,
     f"{GRIT} in the Office", OFFICE_MA),
    ("no-own-player-room-with-sync-group", _registry(None, office_group=True), "office_sat2", GRIT, None,
     f"{GRIT} in the Office", OFFICE_GROUP),
    # Rung 5: nothing to name -> the bare sentence, Kronk's default.
    ("no-own-player-room-without-player", _registry(None), "garage_sat", GRIT, None,
     GRIT, DEFAULT),
    # Rung 1: the speaker named a target.
    ("named-target", _registry(None), "kitchen_pe", "Play the artist Aerosmith in the office", "office",
     "Play the artist Aerosmith in the office", OFFICE_MA),
]


@pytest.mark.parametrize("shape", SHAPES, ids=[s[0] for s in SHAPES])
def test_handoff_text_reaches_the_right_player(shape):
    _, reg, sat, sentence, named, want_text, want_player = shape
    v = _handoff(reg, sat, sentence, named)
    assert v["kronk_text"] == want_text
    assert _kronk_resolves(reg, sentence, v["kronk_text"], named) == [want_player]


@pytest.mark.parametrize("shape", SHAPES, ids=[s[0] for s in SHAPES])
def test_handoff_target_is_the_blueprints_own_target(shape):
    """The two tiers agree: what Kronk resolves the hand-off to is what the
    blueprint itself targeted (mass_player_entity_id) for the same request."""
    _, reg, sat, sentence, named, _, want_player = shape
    v = _handoff(reg, sat, sentence, named)
    assert v["mass_player_entity_id"] == [want_player]
    assert _kronk_resolves(reg, sentence, v["kronk_text"], named) == v["mass_player_entity_id"]


def test_the_basement_room_is_never_written_while_it_holds_no_player():
    v = _handoff(_registry(None), "basement_pe", GRIT)
    assert v["player_entity_id_by_assist_area"] == []
    assert "Basement Theater" not in v["kronk_text"]


def test_a_room_with_no_player_is_refused_with_the_cause():
    """Fix 3's side: had the old text reached tool_service, it now says why."""
    reg = _registry(None)
    raw = hte.environment(reg).from_string(ts._players_template(None)).render()
    players, _, ha_areas = ts.parse_player_payload_areas(raw)
    with pytest.raises(HTTPException) as ei:
        ts.resolve_players("Basement Theater", None, players, DEFAULT, ha_areas=ha_areas)
    assert ei.value.detail == ("The Basement Theater has no Music Assistant speaker. "
                               "Rooms with a speaker: Kitchen, Office.")


def test_blueprint_version_names_change_14():
    blocks = hte.blueprint_variable_blocks()
    assert blocks[0]["version"] == "20250404-kronk14"
