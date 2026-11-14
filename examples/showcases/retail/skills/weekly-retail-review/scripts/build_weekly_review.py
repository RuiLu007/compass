"""Trusted, deterministic weekly Retail report calculations."""

from datetime import date


def build_weekly_review(baseline: dict, current: dict) -> dict:
    windows = (baseline, current)
    boundaries = tuple(
        (date.fromisoformat(window["start"]), date.fromisoformat(window["end"]))
        for window in windows
    )
    if (
        any((end - start).days != 7 for start, end in boundaries)
        or boundaries[0][1] > boundaries[1][0]
    ):
        raise ValueError("invalid_week_windows")
    available = [window for window in windows if window["covered"]]
    totals = [
        {"start": window["start"], "end": window["end"], "paidCent": sum(window["rows"].values())}
        for window in available
    ]
    if len(available) < 2:
        return {
            "status": "needs_data",
            "totals": totals,
            "missing": [
                {"start": window["start"], "end": window["end"]}
                for window in windows
                if not window["covered"]
            ],
        }
    before, after = (window["rows"] for window in windows)
    changes = sorted(
        ((code, after.get(code, 0) - before.get(code, 0)) for code in before.keys() | after.keys()),
        key=lambda item: (-abs(item[1]), item[0]),
    )
    return {"status": "ok", "totals": totals, "changes": changes[:3]}
