"""Runtime configuration, read once from the environment (.env supported).

Every knob has a safe default so the app runs with zero configuration for local development.
Production overrides come from environment variables or a .env file next to this project.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:  # dotenv is optional; env vars still work without it
    pass

ROOT = Path(__file__).resolve().parent.parent


def _bool(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class Plan:
    """A billing tier. Limits are enforced in the service layer, not the UI."""
    key: str
    name: str
    price_usd: int
    max_monitors: int          # -1 == unlimited
    min_interval_seconds: int  # fastest allowed schedule
    alerts: bool
    api_access: bool           # programmatic API for Pro/Team


PLANS: dict[str, Plan] = {
    "free": Plan("free", "Free", 0, max_monitors=1, min_interval_seconds=86_400,
                 alerts=False, api_access=False),
    "pro": Plan("pro", "Pro", 19, max_monitors=10, min_interval_seconds=3_600,
                alerts=True, api_access=True),
    "team": Plan("team", "Team", 49, max_monitors=-1, min_interval_seconds=900,
                 alerts=True, api_access=True),
}


@dataclass(frozen=True)
class Settings:
    # --- storage -------------------------------------------------------------
    database_path: str = os.environ.get("MCPWATCH_DB", str(ROOT / "mcpwatch.db"))
    database_url: str | None = os.environ.get("DATABASE_URL")  # production Postgres/Supabase
    pg_pool_max: int = _int("MCPWATCH_PG_POOL_MAX", 10)        # connection pool ceiling
    pg_pool_timeout: float = float(os.environ.get("MCPWATCH_PG_POOL_TIMEOUT", "10"))  # wait-for-conn

    # --- grading engine (mcp-probe) -----------------------------------------
    mcp_probe_path: str = os.environ.get(
        "MCP_PROBE_PATH", str(Path.home() / "Desktop" / "Building" / "mcp-probe"))
    probe_timeout_seconds: float = float(os.environ.get("MCPWATCH_PROBE_TIMEOUT", "25"))
    # Hard wall-clock cap for a whole probe, above the per-operation timeouts. Bounds a tick
    # (or a manual check) even if a server hangs mid-response.
    probe_overall_timeout_seconds: float = float(os.environ.get("MCPWATCH_PROBE_OVERALL_TIMEOUT", "40"))
    probe_max_response_bytes: int = _int("MCPWATCH_MAX_RESPONSE_BYTES", 5_000_000)  # 5 MB cap

    # --- auth & sessions -----------------------------------------------------
    session_ttl_seconds: int = _int("MCPWATCH_SESSION_TTL", 30 * 86_400)
    # sliding-window rate limit on auth endpoints (per-IP+email):
    auth_max_attempts: int = _int("MCPWATCH_AUTH_MAX_ATTEMPTS", 8)
    auth_window_seconds: int = _int("MCPWATCH_AUTH_WINDOW", 300)

    # rate limits: (max events, window seconds). Keyed by IP or org as noted in limits.py.
    rl_signup: tuple = (_int("MCPWATCH_RL_SIGNUP_MAX", 5), _int("MCPWATCH_RL_SIGNUP_WINDOW", 3600))
    rl_monitor: tuple = (_int("MCPWATCH_RL_MONITOR_MAX", 20), _int("MCPWATCH_RL_MONITOR_WINDOW", 3600))
    rl_probe: tuple = (_int("MCPWATCH_RL_PROBE_MAX", 30), _int("MCPWATCH_RL_PROBE_WINDOW", 300))
    rl_api: tuple = (_int("MCPWATCH_RL_API_MAX", 120), _int("MCPWATCH_RL_API_WINDOW", 60))
    rl_webhook: tuple = (_int("MCPWATCH_RL_WEBHOOK_MAX", 240), _int("MCPWATCH_RL_WEBHOOK_WINDOW", 60))

    # probe isolation
    max_probes_per_tenant: int = _int("MCPWATCH_MAX_PROBES_PER_TENANT", 4)
    scheduler_concurrency: int = _int("MCPWATCH_SCHEDULER_CONCURRENCY", 8)

    # --- security ------------------------------------------------------------
    # SSRF: monitoring user-supplied URLs is the product, so private/loopback/reserved IP
    # ranges are refused for http monitors by default. Self-host/dev can opt in to reach
    # localhost servers. NEVER enable this on the hosted control plane.
    ssrf_allow_private: bool = _bool("MCPWATCH_SSRF_ALLOW_PRIVATE", False)
    # Running a user's arbitrary launch command is remote code execution on our workers. Safe
    # on a self-hosted box; the hosted control plane MUST set this False and only run stdio in
    # an isolated worker sandbox.
    allow_stdio_monitors: bool = _bool("MCPWATCH_ALLOW_STDIO", True)

    # --- alerts (Resend) -----------------------------------------------------
    resend_api_key: str | None = os.environ.get("RESEND_API_KEY")
    alert_from: str = os.environ.get("MCPWATCH_ALERT_FROM", "MCPWatch <alerts@mcpwatch.dev>")
    alert_cooldown_seconds: int = _int("MCPWATCH_ALERT_COOLDOWN", 900)  # dedup window per rule

    # --- incidents -----------------------------------------------------------
    # Consecutive failed checks before an incident opens (avoids flapping on one blip).
    incident_open_after_failures: int = _int("MCPWATCH_INCIDENT_THRESHOLD", 2)

    # --- public URLs & scheduler --------------------------------------------
    base_url: str = os.environ.get("MCPWATCH_BASE_URL", "http://localhost:8000")
    scheduler_token: str = os.environ.get("MCPWATCH_SCHEDULER_TOKEN", "dev-scheduler-token")

    plans: dict[str, Plan] = field(default_factory=lambda: PLANS)


SETTINGS = Settings()
