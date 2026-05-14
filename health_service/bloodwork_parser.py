"""Parse LabCorp PDF bloodwork reports into structured results."""
from __future__ import annotations

import re
from io import BytesIO


# ── Text extraction ───────────────────────────────────────────────────────────

def extract_text(pdf_bytes: bytes) -> str:
    from pypdf import PdfReader
    reader = PdfReader(BytesIO(pdf_bytes))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


# ── Date detection ────────────────────────────────────────────────────────────

_DATE_PATTERNS = [
    re.compile(r'(?:Collected|Collection|Date of Service|Reported)[:\s]+(\d{1,2}/\d{1,2}/\d{4})', re.IGNORECASE),
    re.compile(r'(?:Collected|Collection|Date of Service|Reported)[:\s]+(\d{4}-\d{2}-\d{2})', re.IGNORECASE),
    re.compile(r'(\d{1,2}/\d{1,2}/\d{4})'),  # fallback: first date in document
]

def _parse_date(text: str) -> str:
    for pat in _DATE_PATTERNS:
        m = pat.search(text)
        if m:
            raw = m.group(1)
            # Normalize to YYYY-MM-DD
            if "/" in raw:
                parts = raw.split("/")
                if len(parts) == 3:
                    month, day, year = parts
                    return f"{year}-{int(month):02d}-{int(day):02d}"
            return raw
    return ""


# ── Reference range parsing ───────────────────────────────────────────────────

def _parse_ref(ref_str: str) -> tuple[float | None, float | None]:
    s = ref_str.strip()
    # Simple range: "65-99" or "0.70-1.25" (not negative ranges for now)
    m = re.match(r'^([\d.]+)\s*-\s*([\d.]+)$', s)
    if m:
        return float(m.group(1)), float(m.group(2))
    # Greater/equal: ">39", ">= 60", "> OR = 60"
    m = re.match(r'^[>≥]=?\s*(?:OR\s*=\s*)?([\d.]+)', s, re.IGNORECASE)
    if m:
        return float(m.group(1)), None
    # Less/equal: "<5.7", "<= 200"
    m = re.match(r'^[<≤]=?\s*([\d.]+)', s)
    if m:
        return None, float(m.group(1))
    return None, None


# ── Panel and result parsing ──────────────────────────────────────────────────

# LabCorp panel headers: all-caps lines with no digits, 3+ chars
_PANEL_RE = re.compile(r'^([A-Z][A-Z0-9 ,/\(\)\-]{3,})$')

# Result line: name ... numeric_value [flag] units ref_range
# Handles both compact ("Glucose 89 mg/dL 65-99") and
# spaced ("Glucose                89        mg/dL   65-99") formats.
_RESULT_RE = re.compile(
    r'^(?P<name>[A-Za-z][A-Za-z0-9 ,\.%\(\)\-/]+?)\s{2,}'
    r'(?P<value>\d+\.?\d*)\s*'
    r'(?P<flag>H\*?|L\*?|A\*?|HIGH|LOW|CRITICAL)?\s*'
    r'(?P<unit>[\w/%µuUL]+(?:/[\w]+)?)\s+'
    r'(?P<ref>[^\n]+)',
    re.IGNORECASE,
)

# Compact fallback for when columns run together with single spaces:
# "Glucose 89 mg/dL 65-99"
_COMPACT_RE = re.compile(
    r'^(?P<name>[A-Za-z][A-Za-z0-9 ,\.%\(\)\-/]+?)\s+'
    r'(?P<value>\d+\.?\d*)\s+'
    r'(?P<flag>H\*?|L\*?|HIGH|LOW|CRITICAL\*?)?\s*'
    r'(?P<unit>[\w/%µuUL]+/[\w]+)\s+'
    r'(?P<ref>[\d.<>\-=\s]+)',
    re.IGNORECASE,
)


def _parse_result_line(line: str) -> dict | None:
    for pat in (_RESULT_RE, _COMPACT_RE):
        m = pat.match(line.strip())
        if m:
            d = m.groupdict()
            ref_lo, ref_hi = _parse_ref(d.get("ref", ""))
            flag = (d.get("flag") or "").strip().upper()
            # Normalize flag: H/HIGH → H, L/LOW → L
            if flag in ("HIGH",):
                flag = "H"
            elif flag in ("LOW",):
                flag = "L"
            return {
                "marker":  d["name"].strip().rstrip(","),
                "value":   float(d["value"]),
                "flag":    flag or None,
                "unit":    d["unit"].strip(),
                "ref":     (d.get("ref") or "").strip(),
                "ref_low": ref_lo,
                "ref_high": ref_hi,
            }
    return None


def _parse_panels(text: str) -> list[dict]:
    """Return list of {panel, results: [{marker, value, flag, unit, ref, ref_low, ref_high}]}."""
    panels: list[dict] = []
    current_panel = "General"
    current_results: list[dict] = []

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        panel_m = _PANEL_RE.match(line)
        if panel_m and len(line) > 4:
            # Save previous panel if it had results
            if current_results:
                panels.append({"panel": current_panel, "results": current_results})
                current_results = []
            current_panel = line.title()
            continue

        result = _parse_result_line(line)
        if result:
            current_results.append(result)

    if current_results:
        panels.append({"panel": current_panel, "results": current_results})

    return panels


# ── Public API ────────────────────────────────────────────────────────────────

def parse_labcorp(pdf_bytes: bytes) -> dict:
    """
    Parse a LabCorp PDF.

    Returns:
        {
            date: str (YYYY-MM-DD),
            panels: [{panel, results: [{marker, value, flag, unit, ref, ref_low, ref_high}]}],
            raw_text: str,
            parsed_count: int,
        }
    """
    raw_text = extract_text(pdf_bytes)
    date = _parse_date(raw_text)
    panels = _parse_panels(raw_text)
    parsed_count = sum(len(p["results"]) for p in panels)
    return {
        "date":         date,
        "panels":       panels,
        "raw_text":     raw_text,
        "parsed_count": parsed_count,
    }
