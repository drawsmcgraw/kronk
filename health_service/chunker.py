"""Convert normalized health records to searchable prose chunks."""
from __future__ import annotations


def _fmt_min(seconds: int | None) -> str | None:
    if not seconds:
        return None
    h, m = divmod(int(seconds) // 60, 60)
    return f"{h}h {m}m" if h else f"{m}m"


def chunk_daily(r: dict) -> str:
    date = r.get("date", "?")
    parts: list[str] = []
    if r.get("steps"):
        parts.append(f"{r['steps']:,} steps")
    if r.get("calories_total"):
        parts.append(f"{r['calories_total']} kcal total")
    if r.get("calories_active"):
        parts.append(f"{r['calories_active']} kcal active")
    if r.get("distance_meters"):
        parts.append(f"{r['distance_meters'] / 1000:.1f} km distance")
    if r.get("resting_hr"):
        parts.append(f"resting HR {r['resting_hr']} bpm")
    if r.get("avg_stress"):
        parts.append(f"avg stress {r['avg_stress']}")
    bb_h = r.get("body_battery_high")
    bb_l = r.get("body_battery_low")
    if bb_h is not None or bb_l is not None:
        parts.append(f"body battery {bb_l or '?'}–{bb_h or '?'}")
    body = ", ".join(parts) if parts else "no data recorded"
    return f"Daily wellness on {date}: {body}."


def chunk_sleep(r: dict) -> str:
    date = r.get("date", "?")
    dur = r.get("duration_seconds") or 0
    parts: list[str] = []
    if dur:
        parts.append(_fmt_min(dur) or "")
    for key, label in [
        ("deep_seconds", "deep"),
        ("rem_seconds", "REM"),
        ("light_seconds", "light"),
        ("awake_seconds", "awake"),
    ]:
        v = _fmt_min(r.get(key))
        if v:
            parts.append(f"{label} {v}")
    if r.get("score"):
        parts.append(f"sleep score {r['score']}/100")
    if r.get("avg_hrv"):
        parts.append(f"avg HRV {r['avg_hrv']:.0f}ms")
    body = ", ".join(p for p in parts if p) or "no data recorded"
    return f"Sleep on {date}: {body}."


def chunk_hrv(r: dict) -> str:
    date = r.get("date", "?")
    parts: list[str] = []
    if r.get("last_night"):
        parts.append(f"last night {r['last_night']:.0f}ms")
    if r.get("weekly_avg"):
        parts.append(f"weekly avg {r['weekly_avg']:.0f}ms")
    bl, bh = r.get("baseline_low"), r.get("baseline_high")
    if bl and bh:
        parts.append(f"baseline {bl:.0f}–{bh:.0f}ms")
    if r.get("status"):
        parts.append(f"status {r['status']}")
    body = ", ".join(parts) or "no data recorded"
    return f"HRV on {date}: {body}."


def chunk_bloodwork_panel(date: str, panel: str, results: list[dict]) -> str:
    if not results:
        return f"Bloodwork on {date} — {panel}: no results parsed."
    items: list[str] = []
    for r in results:
        marker = r.get("marker", "?")
        value = r.get("value")
        unit = r.get("unit", "")
        flag = r.get("flag") or ""
        ref = r.get("raw_ref") or r.get("ref", "")
        val_str = f"{value} {unit}".strip() if value is not None else "?"
        flag_str = f" [{flag}]" if flag else " [normal]"
        ref_str = f" (ref {ref})" if ref else ""
        items.append(f"{marker}: {val_str}{flag_str}{ref_str}")
    body = "; ".join(items)
    return f"Bloodwork on {date} — {panel}: {body}."


def chunk_activity(r: dict) -> str:
    date = r.get("date", "?")
    label = r.get("name") or r.get("type") or "activity"
    parts: list[str] = []
    dur = _fmt_min(r.get("duration_seconds"))
    if dur:
        parts.append(dur)
    if r.get("distance_meters"):
        parts.append(f"{r['distance_meters'] / 1000:.1f} km")
    if r.get("avg_hr"):
        parts.append(f"avg HR {r['avg_hr']} bpm")
    if r.get("max_hr"):
        parts.append(f"max HR {r['max_hr']} bpm")
    if r.get("calories"):
        parts.append(f"{r['calories']} cal")
    body = ", ".join(parts) or "no data recorded"
    return f"Activity on {date} — {label}: {body}."
