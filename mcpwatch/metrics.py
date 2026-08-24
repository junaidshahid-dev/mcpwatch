"""Derived metrics computed from the raw check history.

Uptime/availability, average and P95 latency, and grade history — the numbers a dashboard and a
status page show. Kept pure (no DB writes) so they are trivial to test.
"""
from __future__ import annotations

from . import db

DAY = 86_400
WINDOWS = {"7d": 7 * DAY, "30d": 30 * DAY, "90d": 90 * DAY}


def _percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    s = sorted(values)
    if len(s) == 1:
        return s[0]
    # nearest-rank
    k = max(0, min(len(s) - 1, int(round(pct / 100 * len(s) + 0.5)) - 1))
    return s[k]


def uptime_ratio(monitor_id: str, window_seconds: float = 30 * DAY) -> float | None:
    rows = db.checks_in_window(monitor_id, window_seconds)
    if not rows:
        return None
    up = sum(1 for r in rows if r["reachable"])
    return up / len(rows)


def latency_stats(monitor_id: str, window_seconds: float = 30 * DAY) -> dict:
    rows = db.checks_in_window(monitor_id, window_seconds)
    lat = [r["latency_ms"] for r in rows if r["reachable"] and r["latency_ms"] is not None]
    if not lat:
        return {"count": len(rows), "avg_ms": None, "p95_ms": None}
    return {
        "count": len(rows),
        "avg_ms": round(sum(lat) / len(lat)),
        "p95_ms": round(_percentile(lat, 95)),
    }


def grade_history(monitor_id: str, window_seconds: float = 30 * DAY) -> list[dict]:
    rows = db.checks_in_window(monitor_id, window_seconds)
    return [{"at": r["checked_at"], "grade": r["grade"], "status": r["status"]}
            for r in rows if r["grade"]]


def monitor_metrics(monitor_id: str) -> dict:
    """Everything a monitor-detail page needs, in one call."""
    return {
        "uptime": {k: uptime_ratio(monitor_id, w) for k, w in WINDOWS.items()},
        "latency_30d": latency_stats(monitor_id, WINDOWS["30d"]),
        "grade_history_30d": grade_history(monitor_id, WINDOWS["30d"]),
    }
