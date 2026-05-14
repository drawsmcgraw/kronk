"""
Tests for bloodwork_parser.py — date detection, ref range parsing, result line parsing.

All tests are pure-Python (no PDF files needed): they exercise the parsing
functions directly with realistic synthetic LabCorp-format text.
"""
import os
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HEALTH_SVC = os.path.join(REPO_ROOT, "health_service")
if HEALTH_SVC not in sys.path:
    sys.path.insert(0, HEALTH_SVC)

from bloodwork_parser import _parse_date, _parse_ref, _parse_result_line, _parse_panels


# ── Date parsing ──────────────────────────────────────────────────────────────

def test_parse_date_collected():
    text = "Collected: 04/22/2026\nPatient: MALONE, DREW"
    assert _parse_date(text) == "2026-04-22"


def test_parse_date_reported():
    text = "Reported: 04/23/2026\nSpecimen ID: XYZ"
    assert _parse_date(text) == "2026-04-23"


def test_parse_date_iso():
    text = "Date of Service: 2026-04-22"
    assert _parse_date(text) == "2026-04-22"


def test_parse_date_fallback():
    text = "Some random text with 04/22/2026 in it"
    assert _parse_date(text) == "2026-04-22"


def test_parse_date_missing():
    assert _parse_date("No date here at all.") == ""


# ── Reference range parsing ───────────────────────────────────────────────────

def test_parse_ref_simple_range():
    lo, hi = _parse_ref("65-99")
    assert lo == 65.0 and hi == 99.0


def test_parse_ref_decimal_range():
    lo, hi = _parse_ref("0.70-1.25")
    assert lo == pytest.approx(0.70) and hi == pytest.approx(1.25)


def test_parse_ref_greater_than():
    lo, hi = _parse_ref(">39")
    assert lo == 39.0 and hi is None


def test_parse_ref_greater_equal_spelled():
    lo, hi = _parse_ref("> OR = 60")
    assert lo == 60.0 and hi is None


def test_parse_ref_less_than():
    lo, hi = _parse_ref("<5.7")
    assert lo is None and hi == pytest.approx(5.7)


def test_parse_ref_qualitative():
    lo, hi = _parse_ref("Negative")
    assert lo is None and hi is None


# ── Result line parsing ───────────────────────────────────────────────────────

_LABCORP_LINES = [
    "Glucose                                        89              mg/dL        65-99",
    "Creatinine                                     0.93            mg/dL        0.70-1.25",
    "eGFR NonAfrican American                       108             mL/min       > OR = 60",
    "Cholesterol, Total                             185             mg/dL        100-199",
    "LDL Cholesterol Calc                           110         H   mg/dL        0-99",
    "HDL Cholesterol                                58              mg/dL        >39",
    "Triglycerides                                  87              mg/dL        0-149",
]

def test_parse_glucose():
    r = _parse_result_line(_LABCORP_LINES[0])
    assert r is not None
    assert "Glucose" in r["marker"]
    assert r["value"] == 89.0
    assert r["unit"] == "mg/dL"
    assert r["ref_low"] == 65.0 and r["ref_high"] == 99.0
    assert r["flag"] is None


def test_parse_ldl_flagged():
    r = _parse_result_line(_LABCORP_LINES[4])
    assert r is not None
    assert "LDL" in r["marker"]
    assert r["value"] == 110.0
    assert r["flag"] == "H"


def test_parse_hdl_gt_ref():
    r = _parse_result_line(_LABCORP_LINES[5])
    assert r is not None
    assert r["value"] == 58.0
    assert r["ref_low"] == 39.0 and r["ref_high"] is None


def test_parse_egfr_spelled_ref():
    r = _parse_result_line(_LABCORP_LINES[2])
    assert r is not None
    assert r["value"] == 108.0
    assert r["ref_low"] == 60.0


def test_unrecognized_line_returns_none():
    assert _parse_result_line("") is None
    assert _parse_result_line("COMPREHENSIVE METABOLIC PANEL") is None
    assert _parse_result_line("Page 1 of 2") is None


# ── Panel parsing ─────────────────────────────────────────────────────────────

SAMPLE_REPORT = """
Collected: 04/22/2026

COMPREHENSIVE METABOLIC PANEL
Glucose                                        89              mg/dL        65-99
BUN                                            15              mg/dL        7-25
Creatinine                                     0.93            mg/dL        0.70-1.25

LIPID PANEL
Cholesterol, Total                             185             mg/dL        100-199
Triglycerides                                  87              mg/dL        0-149
HDL Cholesterol                                58              mg/dL        >39
LDL Cholesterol Calc                           110         H   mg/dL        0-99
"""


def test_parse_panels_finds_two_panels():
    panels = _parse_panels(SAMPLE_REPORT)
    panel_names = [p["panel"] for p in panels]
    assert any("Metabolic" in n or "METABOLIC" in n.upper() for n in panel_names)
    assert any("Lipid" in n or "LIPID" in n.upper() for n in panel_names)


def test_parse_panels_result_counts():
    panels = _parse_panels(SAMPLE_REPORT)
    total = sum(len(p["results"]) for p in panels)
    assert total == 7


def test_parse_panels_ldl_flagged_in_output():
    panels = _parse_panels(SAMPLE_REPORT)
    all_results = [r for p in panels for r in p["results"]]
    ldl = next((r for r in all_results if "LDL" in r["marker"]), None)
    assert ldl is not None
    assert ldl["flag"] == "H"
    assert ldl["value"] == 110.0
