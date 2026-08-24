"""HTTP API + static hosting for MCPWatch.

Two auth mechanisms:
  * a session cookie (`mcpw_session`) for the dashboard, set on signup/login;
  * a bearer API key (`Authorization: Bearer mcpw_live_...`) for the programmatic /api/v1 API,
    available to Pro/Team.

Public endpoints (badge, status) need no auth — they are the growth loop. Tenant isolation is
enforced by scoping every resource read to the caller's org.
"""
from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import __version__, alerts, auth, billing, crypto, db, limits, metrics, observability, service
from .badge import render_badge
from .config import SETTINGS

WEB = Path(__file__).resolve().parent.parent / "web"
log = logging.getLogger("mcpwatch.api")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    observability.setup_logging()
    db.init_db()
    log.info("MCPWatch %s started", __version__)
    yield
    db.close_pool()


app = FastAPI(title="MCPWatch", version=__version__, lifespan=lifespan)
app.add_middleware(observability.RequestIdMiddleware)


# When the database is unreachable (down, restarting, or the pool is exhausted), answer 503
# rather than leaking a 500/stack trace — so clients back off and load balancers can react.
def _db_unavailable(request: Request, exc: Exception) -> JSONResponse:
    log.error("database unavailable: %s: %s", type(exc).__name__, exc)
    observability.METRICS.inc("mcpwatch_db_unavailable_total")
    return JSONResponse({"error": "database temporarily unavailable"}, status_code=503)


import sqlite3 as _sqlite3  # noqa: E402
app.add_exception_handler(_sqlite3.OperationalError, _db_unavailable)
try:
    import psycopg as _psycopg  # noqa: E402
    from psycopg_pool import PoolTimeout as _PoolTimeout  # noqa: E402
    app.add_exception_handler(_psycopg.OperationalError, _db_unavailable)
    app.add_exception_handler(_PoolTimeout, _db_unavailable)
except Exception:  # psycopg not installed (sqlite-only deployment) — fine
    pass


# ============================================================ auth helpers
def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    return fwd.split(",")[0].strip() if fwd else (request.client.host if request.client else "-")


def _session_token(request: Request) -> str:
    cookie = request.cookies.get("mcpw_session")
    if cookie:
        return cookie
    authz = request.headers.get("authorization", "")
    if authz.startswith("Bearer ") and not authz[7:].startswith("mcpw_"):
        return authz[7:]  # a session token passed as bearer (not an API key)
    return ""


def current_user(request: Request) -> dict:
    user = auth.user_from_session(_session_token(request))
    if not user:
        raise HTTPException(401, "Not signed in")
    return user


def current_ctx(request: Request) -> dict:
    """{user, org} for the dashboard/session API."""
    user = current_user(request)
    return {"user": user, "org": auth.primary_org(user)}


def api_ctx(request: Request) -> dict:
    """{user, org, key} for the programmatic /api/v1 API (bearer API key, Pro/Team only)."""
    authz = request.headers.get("authorization", "")
    key = authz[7:] if authz.startswith("Bearer ") else ""
    ctx = auth.api_key_context(key)
    if not ctx:
        raise HTTPException(401, "Invalid or missing API key")
    if not SETTINGS.plans[ctx["org"]["plan"]].api_access:
        raise HTTPException(403, "The API is available on Pro and Team plans")
    _rl(limits.api_limiter, ctx["key"]["id"])
    return ctx


def _set_cookie(resp: Response, token: str) -> None:
    resp.set_cookie("mcpw_session", token, httponly=True, samesite="lax",
                    secure=SETTINGS.base_url.startswith("https"),
                    max_age=SETTINGS.session_ttl_seconds, path="/")


def _tenant_monitor(monitor_id: str, org: dict) -> dict:
    m = db.get_monitor(monitor_id, org["id"])
    if not m:
        raise HTTPException(404, "Monitor not found")
    return m


def _rl(limiter, key: str) -> None:
    """Enforce a rate limit, mapping the overflow to HTTP 429."""
    try:
        limiter.check(key)
    except limits.RateLimited as e:
        raise HTTPException(429, str(e))


# ============================================================ schemas
class SignupIn(BaseModel):
    email: str
    password: str


class LoginIn(BaseModel):
    email: str
    password: str


class MonitorIn(BaseModel):
    name: str
    kind: str
    endpoint: str
    project_id: str | None = None
    interval_seconds: int | None = None
    public: bool = True
    owned: bool = False


class CheckIn(BaseModel):
    depth: str = "liveness"


class KeyIn(BaseModel):
    name: str = "default"


class AlertRuleIn(BaseModel):
    channel: str
    target: str
    monitor_id: str | None = None
    on_down: bool = True
    on_recover: bool = True
    on_grade_below: int | None = None
    on_schema_change: bool = False
    on_breaking_change: bool = True


class ResetReq(BaseModel):
    email: str


class ResetDo(BaseModel):
    token: str
    password: str


# ============================================================ meta / observability
@app.get("/health")
def health() -> dict:
    return {"ok": True, "version": __version__}


@app.get("/ready")
def ready() -> JSONResponse:
    ok, checks = observability.readiness()
    return JSONResponse({"ready": ok, "checks": checks}, status_code=200 if ok else 503)


@app.get("/metrics")
def prometheus_metrics() -> Response:
    return Response(observability.METRICS.render(), media_type="text/plain; version=0.0.4")


# ============================================================ auth
@app.post("/api/auth/signup")
def signup(body: SignupIn, request: Request) -> JSONResponse:
    ip = _client_ip(request)
    _rl(limits.signup_limiter, ip)
    try:
        result = auth.signup(body.email, body.password, ip=ip)
    except auth.AuthError as e:
        raise HTTPException(getattr(e, "status", 400), str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    # open a session immediately
    login = auth.login(body.email, body.password, ip=ip)
    _send_verification(result["user"], result["verify_token"])
    observability.METRICS.inc("mcpwatch_signups_total")
    resp = JSONResponse({"user": _pub_user(result["user"]), "org": _pub_org(result["org"])})
    _set_cookie(resp, login["session_token"])
    return resp


@app.post("/api/auth/login")
def login(body: LoginIn, request: Request) -> JSONResponse:
    try:
        result = auth.login(body.email, body.password, ip=_client_ip(request))
    except auth.AuthError as e:
        raise HTTPException(getattr(e, "status", 400), str(e))
    resp = JSONResponse({"user": _pub_user(result["user"])})
    _set_cookie(resp, result["session_token"])
    return resp


@app.post("/api/auth/logout")
def logout(request: Request) -> JSONResponse:
    auth.logout(_session_token(request))
    resp = JSONResponse({"ok": True})
    resp.delete_cookie("mcpw_session", path="/")
    return resp


@app.get("/api/auth/verify")
def verify_email(token: str) -> Response:
    ok = auth.verify_email(token)
    return Response(status_code=302, headers={"Location": f"/dashboard?verified={int(ok)}"})


@app.post("/api/auth/request-reset")
def request_reset(body: ResetReq) -> dict:
    token = auth.request_password_reset(body.email)
    if token:
        link = f"{SETTINGS.base_url}/dashboard?reset={token}"
        alerts.deliver("email", body.email, "[MCPWatch] Reset your password",
                       f"Use this link to reset your password:\n{link}")
    return {"ok": True}  # never reveal whether the account exists


@app.post("/api/auth/reset")
def do_reset(body: ResetDo) -> dict:
    if not auth.reset_password(body.token, body.password):
        raise HTTPException(400, "Invalid or expired reset link")
    return {"ok": True}


@app.get("/api/me")
def me(ctx: dict = Depends(current_ctx)) -> dict:
    org = ctx["org"]
    plan = SETTINGS.plans[org["plan"]]
    return {
        "user": _pub_user(ctx["user"]),
        "org": _pub_org(org),
        "plan": org["plan"],
        "limits": {"max_monitors": plan.max_monitors,
                   "min_interval_seconds": plan.min_interval_seconds,
                   "alerts": plan.alerts, "api_access": plan.api_access},
        "usage": {"monitors": db.count_monitors(org["id"]),
                  "open_incidents": db.count_open_incidents(org["id"])},
    }


# ============================================================ monitors (session)
@app.post("/api/monitors")
def create_monitor(body: MonitorIn, request: Request, ctx: dict = Depends(current_ctx)) -> dict:
    from .security import SecurityError
    _rl(limits.monitor_limiter, ctx["org"]["id"])
    try:
        m = service.create_monitor_for_org(
            ctx["org"], body.name, body.kind, body.endpoint,
            project_id=body.project_id, interval_seconds=body.interval_seconds,
            public=body.public, owned=body.owned)
    except service.PlanLimitError as e:
        raise HTTPException(402, str(e))
    except SecurityError as e:
        raise HTTPException(400, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return m


@app.get("/api/monitors")
def list_monitors(ctx: dict = Depends(current_ctx)) -> list[dict]:
    out = []
    for m in db.list_monitors(ctx["org"]["id"]):
        m["uptime_30d"] = metrics.uptime_ratio(m["id"])
        out.append(m)
    return out


@app.get("/api/monitors/{monitor_id}")
def monitor_detail(monitor_id: str, ctx: dict = Depends(current_ctx)) -> dict:
    m = _tenant_monitor(monitor_id, ctx["org"])
    snapshot = db.latest_snapshot(monitor_id)
    return {
        "monitor": m,
        "latest": db.latest_check(monitor_id),
        "recent": db.recent_checks(monitor_id, limit=50),
        "metrics": metrics.monitor_metrics(monitor_id),
        "incidents": db.list_incidents(monitor_id, limit=20),
        "schema_changes": db.recent_schema_changes(monitor_id, limit=20),
        "tools": snapshot["tools"] if snapshot else [],
    }


@app.post("/api/monitors/{monitor_id}/check")
def check_now(monitor_id: str, body: CheckIn, ctx: dict = Depends(current_ctx)) -> dict:
    m = _tenant_monitor(monitor_id, ctx["org"])
    _rl(limits.probe_limiter, ctx["org"]["id"])
    observability.METRICS.inc("mcpwatch_checks_total", source="manual")
    try:
        return service.run_check(m, depth=body.depth)
    except limits.TooManyProbes as e:
        raise HTTPException(429, str(e))


@app.post("/api/monitors/{monitor_id}/pause")
def pause(monitor_id: str, ctx: dict = Depends(current_ctx)) -> dict:
    _tenant_monitor(monitor_id, ctx["org"])
    db.set_monitor_paused(monitor_id, ctx["org"]["id"], True)
    return {"ok": True, "paused": True}


@app.post("/api/monitors/{monitor_id}/resume")
def resume(monitor_id: str, ctx: dict = Depends(current_ctx)) -> dict:
    _tenant_monitor(monitor_id, ctx["org"])
    db.set_monitor_paused(monitor_id, ctx["org"]["id"], False)
    return {"ok": True, "paused": False}


@app.delete("/api/monitors/{monitor_id}")
def delete_monitor(monitor_id: str, ctx: dict = Depends(current_ctx)) -> dict:
    _tenant_monitor(monitor_id, ctx["org"])
    db.soft_delete_monitor(monitor_id, ctx["org"]["id"])
    db.audit("monitor.deleted", org_id=ctx["org"]["id"], user_id=ctx["user"]["id"],
             target_type="monitor", target_id=monitor_id)
    return {"ok": True}


# ============================================================ api keys
@app.post("/api/keys")
def create_key(body: KeyIn, ctx: dict = Depends(current_ctx)) -> dict:
    plaintext, prefix, key_hash = crypto.new_api_key()
    row = db.create_api_key(ctx["org"]["id"], ctx["user"]["id"], body.name, prefix, key_hash)
    # plaintext is shown exactly once
    return {"id": row["id"], "name": row["name"], "key": plaintext, "prefix": prefix}


@app.get("/api/keys")
def list_keys(ctx: dict = Depends(current_ctx)) -> list[dict]:
    return db.list_api_keys(ctx["org"]["id"])


@app.delete("/api/keys/{key_id}")
def revoke_key(key_id: str, ctx: dict = Depends(current_ctx)) -> dict:
    db.revoke_api_key(key_id, ctx["org"]["id"])
    return {"ok": True}


@app.post("/api/keys/{key_id}/rotate")
def rotate_key(key_id: str, ctx: dict = Depends(current_ctx)) -> dict:
    """Revoke a key and mint a fresh one with the same name. New plaintext is shown once."""
    old = next((k for k in db.list_api_keys(ctx["org"]["id"])
                if k["id"] == key_id and not k["revoked_at"]), None)
    if not old:
        raise HTTPException(404, "Key not found")
    db.revoke_api_key(key_id, ctx["org"]["id"])
    plaintext, prefix, key_hash = crypto.new_api_key()
    row = db.create_api_key(ctx["org"]["id"], ctx["user"]["id"], old["name"], prefix, key_hash)
    db.audit("apikey.rotated", org_id=ctx["org"]["id"], user_id=ctx["user"]["id"],
             target_type="api_key", target_id=key_id)
    return {"id": row["id"], "name": row["name"], "key": plaintext, "prefix": prefix}


# ============================================================ alert rules
@app.post("/api/alert-rules")
def create_alert_rule(body: AlertRuleIn, ctx: dict = Depends(current_ctx)) -> dict:
    if body.monitor_id:
        _tenant_monitor(body.monitor_id, ctx["org"])
    return db.create_alert_rule(
        ctx["org"]["id"], body.channel, body.target, monitor_id=body.monitor_id,
        on_down=body.on_down, on_recover=body.on_recover, on_grade_below=body.on_grade_below,
        on_schema_change=body.on_schema_change, on_breaking_change=body.on_breaking_change)


@app.get("/api/alert-rules")
def list_alert_rules(ctx: dict = Depends(current_ctx)) -> list[dict]:
    return db.alert_rules_for_monitor(ctx["org"]["id"], "")  # org-wide + none-specific


# ============================================================ public
@app.get("/badge/{monitor_id}.svg")
def badge(monitor_id: str) -> Response:
    m = db.get_monitor(monitor_id)
    if not m or not m["public"]:
        svg = render_badge("mcp", "unknown")
    else:
        svg = render_badge(m["name"][:24], m.get("last_status") or "unknown", m.get("last_grade"))
    return Response(svg, media_type="image/svg+xml", headers={"Cache-Control": "max-age=60, public"})


@app.get("/status/{monitor_id}")
def public_status(monitor_id: str) -> dict:
    m = db.get_monitor(monitor_id)
    if not m or not m["public"]:
        raise HTTPException(404, "No public status for this monitor")
    view = service.monitor_public_view(m)
    view["incidents"] = [i for i in db.list_incidents(monitor_id, limit=10)]
    return view


# ============================================================ billing
@app.get("/api/billing/checkout")
def checkout(plan: str, ctx: dict = Depends(current_ctx)) -> dict:
    if plan not in ("pro", "team"):
        raise HTTPException(400, "plan must be 'pro' or 'team'")
    return {"url": billing.checkout_url(ctx["org"], ctx["user"], plan)}


@app.post("/webhooks/lemonsqueezy")
async def lemonsqueezy_webhook(request: Request) -> JSONResponse:
    _rl(limits.webhook_limiter, _client_ip(request))
    raw = await request.body()
    sig = request.headers.get("x-signature", "")
    if not billing.verify_webhook(raw, sig):
        raise HTTPException(401, "Invalid signature")
    plan = billing.apply_webhook_event(json.loads(raw or b"{}"))
    return JSONResponse({"ok": True, "plan": plan})


# ============================================================ scheduler
@app.post("/internal/run-due")
def run_due(request: Request) -> dict:
    if request.headers.get("x-scheduler-token") != SETTINGS.scheduler_token:
        raise HTTPException(401, "Bad scheduler token")
    results = service.run_due_checks()
    observability.METRICS.inc("mcpwatch_checks_total", source="scheduled", by=len(results) or 0)
    return {"ran": len(results), "results": results}


# ============================================================ programmatic API (v1, api key)
@app.get("/api/v1/monitors")
def v1_monitors(ctx: dict = Depends(api_ctx)) -> list[dict]:
    return db.list_monitors(ctx["org"]["id"])


@app.get("/api/v1/monitors/{monitor_id}")
def v1_monitor(monitor_id: str, ctx: dict = Depends(api_ctx)) -> dict:
    m = db.get_monitor(monitor_id, ctx["org"]["id"])
    if not m:
        raise HTTPException(404, "Monitor not found")
    return {"monitor": m, "latest": db.latest_check(monitor_id),
            "metrics": metrics.monitor_metrics(monitor_id)}


@app.get("/api/v1/checks")
def v1_checks(monitor_id: str, limit: int = 50, ctx: dict = Depends(api_ctx)) -> list[dict]:
    if not db.get_monitor(monitor_id, ctx["org"]["id"]):
        raise HTTPException(404, "Monitor not found")
    return db.recent_checks(monitor_id, limit=min(limit, 200))


@app.get("/api/v1/incidents")
def v1_incidents(monitor_id: str, ctx: dict = Depends(api_ctx)) -> list[dict]:
    if not db.get_monitor(monitor_id, ctx["org"]["id"]):
        raise HTTPException(404, "Monitor not found")
    return db.list_incidents(monitor_id)


@app.get("/api/v1/schema")
def v1_schema(monitor_id: str, ctx: dict = Depends(api_ctx)) -> dict:
    if not db.get_monitor(monitor_id, ctx["org"]["id"]):
        raise HTTPException(404, "Monitor not found")
    snap = db.latest_snapshot(monitor_id)
    return {"schema_hash": snap["schema_hash"] if snap else None,
            "tools": snap["tools"] if snap else [],
            "changes": db.recent_schema_changes(monitor_id)}


# ============================================================ helpers / static
def _pub_user(u: dict) -> dict:
    return {"id": u["id"], "email": u["email"], "email_verified": bool(u["email_verified"])}


def _pub_org(o: dict) -> dict:
    return {"id": o["id"], "name": o["name"], "plan": o["plan"]}


def _send_verification(user: dict, token: str) -> None:
    link = f"{SETTINGS.base_url}/api/auth/verify?token={token}"
    alerts.deliver("email", user["email"], "[MCPWatch] Verify your email",
                   f"Welcome to MCPWatch. Confirm your email:\n{link}")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(WEB / "index.html")


@app.get("/dashboard")
def dashboard() -> FileResponse:
    return FileResponse(WEB / "dashboard.html")


_assets = WEB / "assets"
if _assets.exists():
    app.mount("/assets", StaticFiles(directory=str(_assets)), name="assets")
