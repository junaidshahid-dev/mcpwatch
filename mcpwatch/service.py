"""Orchestration — the layer the API and scheduler call.

`run_check` is the spine: validate the target, probe it, record the result, snapshot and diff
the schema, open/resolve incidents, and fire alerts. All tenant rules (plan limits, ownership)
are enforced here.
"""
from __future__ import annotations

import concurrent.futures
import threading
import time

from . import alerts, db, limits, security
from .config import SETTINGS
from .engine import schema_diff
from .engine.probe_adapter import ProbeOutcome, probe_target

_GRADE_ORDER = {"A": 5, "B": 4, "C": 3, "D": 2, "F": 1}


class PlanLimitError(Exception):
    """Raised when an action would exceed the org's plan."""


# --------------------------------------------------------------------------- monitors
def create_monitor_for_org(org: dict, name: str, kind: str, endpoint: str,
                           project_id: str | None = None, interval_seconds: int | None = None,
                           public: bool = True, owned: bool = False) -> dict:
    plan = SETTINGS.plans[org["plan"]]
    if plan.max_monitors != -1 and db.count_monitors(org["id"]) >= plan.max_monitors:
        raise PlanLimitError(
            f"The {plan.name} plan allows {plan.max_monitors} monitor(s). Upgrade to add more.")
    interval = interval_seconds or plan.min_interval_seconds
    if interval < plan.min_interval_seconds:
        raise PlanLimitError(
            f"The {plan.name} plan checks at most every {plan.min_interval_seconds // 60} minute(s).")
    # Security gate: refuse SSRF-prone / disallowed targets before we ever store them.
    security.validate_target(kind, endpoint)  # raises security.SecurityError

    if not project_id:
        projects = db.list_projects(org["id"])
        project_id = projects[0]["id"] if projects else db.create_project(org["id"], "Default")["id"]
    monitor = db.create_monitor(org["id"], project_id, name, kind, endpoint, interval, public, owned)
    db.audit("monitor.created", org_id=org["id"], target_type="monitor", target_id=monitor["id"],
             meta={"kind": kind})
    return monitor


# --------------------------------------------------------------------------- the check spine
def run_check(monitor: dict, depth: str = "liveness") -> dict:
    org_id, mid = monitor["org_id"], monitor["id"]
    prev = db.latest_check(mid)

    # Re-validate at check time (mitigates DNS rebinding / a since-disabled target kind).
    try:
        security.validate_target(monitor["kind"], monitor["endpoint"])
    except security.SecurityError as e:
        outcome = ProbeOutcome(reachable=False, depth=depth, label=monitor["name"],
                               error=f"blocked: {e}")
        stored = db.record_check(monitor, outcome.to_dict())
        fresh = db.get_monitor(mid) or monitor
        inc_event = _incident_transition(fresh, stored)
        _fire_alerts(monitor, prev, stored, None, inc_event)
        return stored

    allow_remote = bool(monitor.get("owned"))
    if depth == "audit" and not monitor.get("owned"):
        depth = "liveness"

    prev_snapshot = db.latest_snapshot(mid)
    outcome = _guarded_probe(monitor, depth, allow_remote)
    stored = db.record_check(monitor, outcome.to_dict())

    change = _snapshot_and_diff(org_id, mid, outcome, prev_snapshot)

    fresh = db.get_monitor(mid) or monitor
    inc_event = _incident_transition(fresh, stored)

    _fire_alerts(monitor, prev, stored, change, inc_event)
    return stored


def _guarded_probe(monitor: dict, depth: str, allow_remote: bool) -> ProbeOutcome:
    """Run a probe under a per-tenant concurrency cap and a hard wall-clock timeout.

    The per-tenant slot stops one org from starving the workers or being used to amplify traffic
    at a third party. The wall-clock cap returns control (marking the monitor down) even if a
    server hangs mid-response beyond the per-operation timeouts.
    """
    def _do():
        return probe_target(
            monitor["kind"], monitor["endpoint"], depth=depth, label=monitor["name"],
            timeout=SETTINGS.probe_timeout_seconds, allow_remote=allow_remote,
            max_response_bytes=SETTINGS.probe_max_response_bytes)

    with limits.probe_guard.slot(monitor["org_id"]):
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            fut = ex.submit(_do)
            try:
                return fut.result(timeout=SETTINGS.probe_overall_timeout_seconds)
            except concurrent.futures.TimeoutError:
                return ProbeOutcome(reachable=False, depth=depth, label=monitor["name"],
                                    error="probe exceeded the overall time limit")


def run_due_checks(now: float | None = None) -> list[dict]:
    """Run all due monitors, bounded-parallel. Each check also respects its tenant's probe cap."""
    due = db.due_monitors(now)
    results: list[dict] = []
    if not due:
        return results
    workers = max(1, min(SETTINGS.scheduler_concurrency, len(due)))
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        future_to_mon = {ex.submit(_run_check_safe, m): m for m in due}
        for fut in concurrent.futures.as_completed(future_to_mon):
            results.append(fut.result())
    return results


def _run_check_safe(monitor: dict) -> dict:
    try:
        return run_check(monitor)
    except Exception as e:  # one bad monitor must not stop the batch
        return {"monitor_id": monitor["id"], "error": str(e)}


# --------------------------------------------------------------------------- schema diff
def _snapshot_and_diff(org_id: str, mid: str, outcome: ProbeOutcome, prev_snapshot):
    if not outcome.reachable or not outcome.schema_hash:
        return None
    if not prev_snapshot:
        db.add_snapshot(org_id, mid, outcome.schema_hash, outcome.tools)
        return None
    if prev_snapshot["schema_hash"] == outcome.schema_hash:
        return None
    diff, severity, has_changes = schema_diff.diff_tools(prev_snapshot["tools"], outcome.tools)
    db.add_snapshot(org_id, mid, outcome.schema_hash, outcome.tools)
    if not has_changes:
        return None
    return db.add_schema_change(org_id, mid, prev_snapshot["schema_hash"], outcome.schema_hash,
                                severity, diff)


# --------------------------------------------------------------------------- incidents
def _incident_transition(monitor: dict, check: dict):
    """Open an incident after N consecutive failures; resolve it on recovery."""
    mid, org_id = monitor["id"], monitor["org_id"]
    open_inc = db.get_open_incident(mid)

    if not check["reachable"]:
        if open_inc:
            db.bump_incident_failures(open_inc["id"])
            return None
        streak = monitor.get("consecutive_failures", 0)
        if streak >= SETTINGS.incident_open_after_failures:
            started_at = _streak_start(mid, streak, check["checked_at"])
            inc = db.open_incident(org_id, mid, cause=check.get("error") or "unreachable",
                                   started_at=started_at, failed_checks=streak)
            return ("opened", inc)
        return None

    if open_inc:
        resolved = db.resolve_incident(open_inc["id"], resolved_at=check["checked_at"])
        return ("resolved", resolved)
    return None


def _streak_start(mid: str, streak: int, fallback: float) -> float:
    """When the current down-streak actually began — the oldest of the last `streak` checks."""
    recent = db.recent_checks(mid, limit=streak)
    downs = [c["checked_at"] for c in recent if not c["reachable"]]
    return min(downs) if downs else fallback


# --------------------------------------------------------------------------- alerts
def _fire_alerts(monitor: dict, prev, check: dict, change, inc_event) -> None:
    org = db.get_org(monitor["org_id"])
    if not org or not SETTINGS.plans[org["plan"]].alerts:
        return  # alerts are a paid feature; state changes are still recorded

    events: dict[str, tuple[str, str]] = {}
    if inc_event and inc_event[0] == "opened":
        events["down"] = alerts.render_down(monitor, check)
    if inc_event and inc_event[0] == "resolved":
        events["recover"] = alerts.render_recover(monitor, inc_event[1])
    if change:
        text = schema_diff.render_text(change["diff"])
        events["schema_change"] = alerts.render_schema_change(monitor, change["severity"], text)
        if change["severity"] == "breaking":
            events["breaking_change"] = events["schema_change"]
    if prev and prev.get("grade") and check.get("grade"):
        if _GRADE_ORDER.get(check["grade"], 0) < _GRADE_ORDER.get(prev["grade"], 0):
            events["grade_drop"] = alerts.render_grade(monitor, prev["grade"], check["grade"],
                                                       check.get("score"))

    rules = db.alert_rules_for_monitor(monitor["org_id"], monitor["id"])
    now = time.time()
    for rule in rules:
        event_key = _match_rule(rule, events, check)
        if not event_key:
            continue
        # Cooldown is per (rule, event-type): it dedups a burst of the SAME event during an
        # outage, but never lets a recent 'down' suppress the following 'recover'.
        cdkey = (rule["id"], event_key)
        with _alert_lock:
            if now - _alert_last.get(cdkey, 0) < SETTINGS.alert_cooldown_seconds:
                continue
            _alert_last[cdkey] = now
        subject, text = events[event_key]
        alerts.deliver(rule["channel"], rule["target"], subject, text)
        db.touch_alert_rule(rule["id"])


_alert_last: dict[tuple, float] = {}
_alert_lock = threading.Lock()


def _match_rule(rule: dict, events: dict, check: dict) -> str | None:
    """The single highest-priority event-type this rule should fire on, or None."""
    if "down" in events and rule.get("on_down"):
        return "down"
    if "recover" in events and rule.get("on_recover"):
        return "recover"
    if "breaking_change" in events and rule.get("on_breaking_change"):
        return "breaking_change"
    if "schema_change" in events and rule.get("on_schema_change"):
        return "schema_change"
    if "grade_drop" in events and rule.get("on_grade_below") is not None:
        if check.get("score") is not None and check["score"] < rule["on_grade_below"]:
            return "grade_drop"
    return None


# --------------------------------------------------------------------------- views
def monitor_public_view(monitor: dict) -> dict:
    from . import metrics
    return {
        "id": monitor["id"],
        "name": monitor["name"],
        "status": monitor.get("last_status") or "unknown",
        "grade": monitor.get("last_grade"),
        "score": monitor.get("last_score"),
        "uptime": {k: metrics.uptime_ratio(monitor["id"], w) for k, w in metrics.WINDOWS.items()},
        "last_checked_at": monitor.get("last_checked_at"),
    }
