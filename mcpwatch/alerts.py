"""Alert delivery across channels.

Channel senders are thin and independent (email via Resend, Slack/Discord/generic webhook via
HTTP POST). Routing — which rule fires on which event, and cooldown/dedup — lives in the
service layer; this module only delivers. Every sender fails soft: a delivery error is logged
and never breaks a check run.
"""
from __future__ import annotations

import logging

import httpx

from .config import SETTINGS

log = logging.getLogger("mcpwatch.alerts")


def deliver(channel: str, target: str, subject: str, text: str, payload: dict | None = None) -> bool:
    """Send one alert on the given channel. Returns True if delivered."""
    try:
        if channel == "email":
            return _email(target, subject, text)
        if channel == "slack":
            return _post_json(target, {"text": f"*{subject}*\n{text}"})
        if channel == "discord":
            return _post_json(target, {"content": f"**{subject}**\n{text}"})
        if channel == "webhook":
            return _post_json(target, {"subject": subject, "text": text, **(payload or {})})
        log.warning("unknown alert channel %r", channel)
        return False
    except Exception as e:  # never let a delivery error break a check run
        log.error("alert delivery failed (%s -> %s): %s", channel, target, e)
        return False


def _post_json(url: str, body: dict) -> bool:
    resp = httpx.post(url, json=body, timeout=12)
    resp.raise_for_status()
    return True


def _email(to_email: str, subject: str, text: str) -> bool:
    html = _wrap_html(subject, text)
    if not SETTINGS.resend_api_key:
        log.warning("ALERT (email disabled) to=%s | %s", to_email, subject)
        return False
    resp = httpx.post(
        "https://api.resend.com/emails",
        headers={"Authorization": f"Bearer {SETTINGS.resend_api_key}"},
        json={"from": SETTINGS.alert_from, "to": [to_email], "subject": subject, "html": html},
        timeout=15,
    )
    resp.raise_for_status()
    return True


def _wrap_html(subject: str, text: str) -> str:
    body = text.replace("&", "&amp;").replace("<", "&lt;").replace("\n", "<br>")
    return (f'<div style="font-family:system-ui,Segoe UI,sans-serif;max-width:540px;margin:auto">'
            f'<h2 style="margin:0 0 10px">{subject}</h2>'
            f'<div style="color:#334155;font-size:14px;line-height:1.6">{body}</div>'
            f'<p style="color:#94a3b8;font-size:12px;margin-top:22px">You receive this because you '
            f'monitor this server on MCPWatch.</p></div>')


# convenience renderers used by the service layer -----------------------------
def render_down(monitor: dict, check: dict) -> tuple[str, str]:
    return (f"[MCPWatch] {monitor['name']} is DOWN",
            f"{monitor['name']} became unreachable.\n\nError: {check.get('error') or 'unreachable'}\n"
            f"Endpoint kind: {monitor['kind']}")


def render_recover(monitor: dict, incident: dict | None) -> tuple[str, str]:
    dur = f"\nDowntime: {incident['duration_seconds']}s" if incident and incident.get("duration_seconds") else ""
    return (f"[MCPWatch] {monitor['name']} recovered",
            f"{monitor['name']} is back online.{dur}")


def render_grade(monitor: dict, prev_grade: str, grade: str, score) -> tuple[str, str]:
    return (f"[MCPWatch] {monitor['name']} grade dropped {prev_grade} → {grade}",
            f"Schema health for {monitor['name']} fell from {prev_grade} to {grade} (score {score}).")


def render_schema_change(monitor: dict, severity: str, diff_text: str) -> tuple[str, str]:
    icon = {"breaking": "🔴 BREAKING", "potentially_breaking": "🟡 Potentially breaking",
            "non_breaking": "🟢 Non-breaking"}.get(severity, severity)
    return (f"[MCPWatch] {monitor['name']} schema change — {icon}",
            f"{icon} schema change on {monitor['name']}.\n\n{diff_text}")
