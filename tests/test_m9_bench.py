"""Guards and arithmetic of scripts/m9_lock_bench.py (sprint M9 criterion 2).

The bench must refuse any base URL but the dev path, and its cache-reuse
figure is cached / prompt tokens over every generation that reported both.
"""
import importlib.util
from pathlib import Path

import pytest

pytestmark = pytest.mark.usefixtures("no_network")

_PATH = Path(__file__).resolve().parent.parent / "scripts" / "m9_lock_bench.py"
_spec = importlib.util.spec_from_file_location("m9_lock_bench", _PATH)
bench = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bench)


@pytest.mark.parametrize("base", ["http://localhost/dev", "http://127.0.0.1/dev", "http://localhost/dev/"])
def test_dev_bases_are_accepted(base):
    assert bench.check_base(base) == base.rstrip("/")


@pytest.mark.parametrize("base", [
    "http://localhost", "http://localhost/", "http://127.0.0.1", "https://localhost/dev",
    "http://localhost/devx", "http://localhost/dev/voice", "http://localhost:8000/dev",
    "http://192.168.1.10/dev", "http://kronk.local/dev", "http://localhost/dev/../",
    "http://localhost/prod", "",
])
def test_every_other_base_is_refused(base):
    with pytest.raises(SystemExit):
        bench.check_base(base)


def test_cache_ratio_counts_only_generations_that_report_both():
    rows = [
        {"ev": {"rounds": [{"prompt": 100, "cached": 90}, {"prompt": 200, "cached": 0}]}},
        {"ev": {"rounds": [{"prompt": 50, "cached": None}]}},
        {"ev": {}},
        {},
    ]
    c = bench.cache_ratio(rows)
    assert c == {"generations": 2, "prompt_tokens": 300, "cached_tokens": 90,
                 "evaluated_tokens": 210, "reuse": 0.3}


def test_routes_land_where_the_bench_says():
    import routing
    want = {"direct": ("home", "now_playing"), "specialist": ("home", "weather"),
            "coord": ("direct", "default"), "delegate": ("direct", "default"),
            "search": ("research", "search_phrase")}
    for key, text in bench.ROUTES.items():
        d = routing.decide(text)
        assert (d.route, d.rule) == want[key], key
    assert routing.decide(bench.TWO_VOICE).rule == "weather"
    assert routing.decide(bench.TWO_RESEARCH).rule == "search_phrase"
    assert len(bench.LOAD) <= 8
    assert len({t for _, t in bench.LOAD}) == len(bench.LOAD)
