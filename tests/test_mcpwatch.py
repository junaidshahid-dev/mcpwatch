"""Comprehensive test gate for MCPWatch.

Runs the real engine against the real demo MCP server (no mocks on the moat) and exercises
auth, tenant isolation, security (SSRF), schema-diff, incidents, billing idempotency, metrics,
and the full end-to-end chain. Run directly (`python tests/test_mcpwatch.py`) or via pytest.
"""
import dataclasses
import os
import sys
import tempfile
from pathlib import Path

# Isolate the database BEFORE importing anything that reads config.
_TMP_DB = Path(tempfile.gettempdir()) / "mcpwatch_gate.db"
for suffix in ("", "-wal", "-shm"):
    p = Path(str(_TMP_DB) + suffix)
    if p.exists():
        p.unlink()
os.environ["MCPWATCH_DB"] = str(_TMP_DB)

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient                         # noqa: E402
from mcpwatch import auth, billing, crypto, db, metrics, security, service  # noqa: E402
from mcpwatch.api import app                                      # noqa: E402
from mcpwatch.engine import schema_diff                           # noqa: E402
from mcpwatch.engine.probe_adapter import probe_target            # noqa: E402

DEMO = f'"{sys.executable}" "{ROOT / "scripts" / "demo_server.py"}"'
BROKEN = f'"{sys.executable}" -c "import sys;sys.exit(1)"'


def _fresh_db():
    if os.environ.get("DATABASE_URL"):
        # Postgres: drop and recreate the public schema for a clean slate.
        db.close_pool()
        import psycopg
        with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn:
            conn.execute("DROP SCHEMA public CASCADE")
            conn.execute("CREATE SCHEMA public")
    else:
        for suffix in ("", "-wal", "-shm"):
            p = Path(str(_TMP_DB) + suffix)
            if p.exists():
                p.unlink()
    db.init_db()


def setup_module(_m=None):
    _fresh_db()


# ============================================================ engine: schema_diff
def test_schema_hash_stable_and_order_independent():
    a = [{"name": "x", "description": "d", "inputSchema": {"properties": {"p": {"type": "string"}}}},
         {"name": "y", "description": "d2", "inputSchema": {}}]
    b = list(reversed(a))
    assert schema_diff.schema_hash(a) == schema_diff.schema_hash(b)


def _norm(tools):
    return schema_diff.normalize_tools(tools)


def test_diff_added_tool_non_breaking():
    old = _norm([{"name": "a", "description": "", "inputSchema": {}}])
    new = _norm([{"name": "a", "description": "", "inputSchema": {}},
                 {"name": "b", "description": "", "inputSchema": {}}])
    diff, sev, changed = schema_diff.diff_tools(old, new)
    assert changed and sev == "non_breaking" and diff["added_tools"] == ["b"]


def test_diff_removed_tool_breaking():
    old = _norm([{"name": "a", "description": "", "inputSchema": {}},
                 {"name": "b", "description": "", "inputSchema": {}}])
    new = _norm([{"name": "a", "description": "", "inputSchema": {}}])
    _, sev, changed = schema_diff.diff_tools(old, new)
    assert changed and sev == "breaking"


def test_diff_type_change_breaking():
    old = _norm([{"name": "t", "description": "", "inputSchema": {"properties": {"limit": {"type": "integer"}}}}])
    new = _norm([{"name": "t", "description": "", "inputSchema": {"properties": {"limit": {"type": "string"}}}}])
    diff, sev, _ = schema_diff.diff_tools(old, new)
    assert sev == "breaking"
    assert diff["changed_tools"]["t"]["changed_params"]["limit"]["kind"] == "type"


def test_diff_required_param_added_breaking():
    old = _norm([{"name": "t", "description": "", "inputSchema": {"properties": {}}}])
    new = _norm([{"name": "t", "description": "",
                  "inputSchema": {"properties": {"q": {"type": "string"}}, "required": ["q"]}}])
    _, sev, _ = schema_diff.diff_tools(old, new)
    assert sev == "breaking"


def test_diff_optional_added_non_breaking_and_removed_potentially():
    old = _norm([{"name": "t", "description": "", "inputSchema": {"properties": {"a": {"type": "string"}}}}])
    new = _norm([{"name": "t", "description": "", "inputSchema": {"properties": {"b": {"type": "string"}}}}])
    # 'a' removed (potentially_breaking) + 'b' added optional (non_breaking) => overall potentially
    _, sev, _ = schema_diff.diff_tools(old, new)
    assert sev == "potentially_breaking"


# ============================================================ engine: probe adapter
def test_liveness_grades_real_stdio_server():
    out = probe_target("stdio", DEMO, depth="liveness", label="demo")
    assert out.reachable and out.status() in ("up", "degraded")
    assert out.tool_count == 3 and out.grade in "ABCDF"
    assert out.schema_hash and len(out.tools) == 3
    assert out.check_duration_ms is not None
    assert {f["check"] for f in out.findings} >= {"no-description"}


def test_unreachable_is_down():
    out = probe_target("stdio", BROKEN, depth="liveness", label="broken")
    assert not out.reachable and out.status() == "down" and out.error


# ============================================================ security: SSRF
def test_ssrf_blocks_private_and_metadata_and_scheme():
    for bad in ("http://127.0.0.1/", "http://10.0.0.5/", "http://169.254.169.254/latest/meta"):
        try:
            security.validate_http_target(bad)
            assert False, f"should have blocked {bad}"
        except security.SecurityError:
            pass
    try:
        security.validate_http_target("file:///etc/passwd")
        assert False, "should reject non-http scheme"
    except security.SecurityError:
        pass


def test_ssrf_allows_public_ip():
    ips = security.validate_http_target("http://8.8.8.8/mcp")  # literal, no DNS, public
    assert "8.8.8.8" in ips


def test_stdio_can_be_disabled():
    original = security.SETTINGS
    try:
        security.SETTINGS = dataclasses.replace(original, allow_stdio_monitors=False)
        try:
            security.validate_target("stdio", "python x.py")
            assert False, "stdio should be disabled"
        except security.SecurityError:
            pass
    finally:
        security.SETTINGS = original


# ============================================================ auth
def test_signup_bootstraps_tenant_and_login():
    r = auth.signup("alice@example.com", "password123")
    assert r["user"]["email"] == "alice@example.com"
    org = auth.primary_org(r["user"])
    assert db.list_projects(org["id"])                      # default project
    assert db.alert_rules_for_monitor(org["id"], "")        # default alert rule
    login = auth.login("alice@example.com", "password123")
    assert auth.user_from_session(login["session_token"])["id"] == r["user"]["id"]


def test_login_wrong_password_and_duplicate_email():
    auth.signup("bob@example.com", "password123")
    try:
        auth.login("bob@example.com", "wrongpass")
        assert False
    except auth.InvalidCredentials:
        pass
    try:
        auth.signup("bob@example.com", "password123")
        assert False
    except auth.EmailExists:
        pass


def test_rate_limit_trips():
    lim = auth.RateLimiter(max_events=3, window_seconds=60)
    for _ in range(3):
        lim.check("k")
    try:
        lim.check("k")
        assert False, "should rate limit"
    except auth.RateLimited:
        pass


def test_logout_invalidates_session():
    auth.signup("carol@example.com", "password123")
    tok = auth.login("carol@example.com", "password123")["session_token"]
    auth.logout(tok)
    assert auth.user_from_session(tok) is None


def test_password_reset_flow():
    auth.signup("dave@example.com", "password123")
    token = auth.request_password_reset("dave@example.com")
    assert token and auth.reset_password(token, "newpassword1")
    assert auth.login("dave@example.com", "newpassword1")["session_token"]


# ============================================================ tenant isolation
def test_tenant_isolation():
    a = auth.signup("iso-a@example.com", "password123")
    b = auth.signup("iso-b@example.com", "password123")
    org_a, org_b = auth.primary_org(a["user"]), auth.primary_org(b["user"])
    mon = service.create_monitor_for_org(org_a, "a-mon", "stdio", DEMO)
    # B cannot fetch A's monitor when scoped to B's org
    assert db.get_monitor(mon["id"], org_b["id"]) is None
    assert db.get_monitor(mon["id"], org_a["id"])["id"] == mon["id"]
    assert mon["id"] not in [m["id"] for m in db.list_monitors(org_b["id"])]


# ============================================================ plan limits
def test_free_plan_limit():
    u = auth.signup("free-lim@example.com", "password123")
    org = auth.primary_org(u["user"])
    service.create_monitor_for_org(org, "one", "stdio", DEMO)
    try:
        service.create_monitor_for_org(org, "two", "stdio", DEMO)
        assert False
    except service.PlanLimitError:
        pass


# ============================================================ schema change detection
def test_run_check_detects_schema_change():
    u = auth.signup("schema@example.com", "password123")
    org = auth.primary_org(u["user"])
    mon = service.create_monitor_for_org(org, "schema-mon", "stdio", DEMO)
    # Seed a PRIOR snapshot missing the demo's 'lookup' tool, so the next real check diffs.
    seeded = [{"name": "ping", "description": "Health check. Returns 'pong'.", "params": {}}]
    db.add_snapshot(org["id"], mon["id"], "seededhash000000", seeded)
    service.run_check(mon)
    changes = db.recent_schema_changes(mon["id"])
    assert changes, "expected a schema change to be recorded"
    assert changes[0]["severity"] in ("non_breaking", "potentially_breaking", "breaking")


# ============================================================ incidents
def test_incident_opens_and_resolves():
    u = auth.signup("inc@example.com", "password123")
    org = auth.primary_org(u["user"])
    mon = service.create_monitor_for_org(org, "inc-mon", "stdio", BROKEN)
    service.run_check(db.get_monitor(mon["id"], org["id"]))      # fail 1, streak=1, no incident
    assert db.get_open_incident(mon["id"]) is None
    service.run_check(db.get_monitor(mon["id"], org["id"]))      # fail 2 -> incident opens
    inc = db.get_open_incident(mon["id"])
    assert inc and inc["status"] == "open"
    # Simulate recovery by repointing the endpoint to the working demo server.
    with db.connect() as conn:
        conn.execute("UPDATE monitors SET endpoint=? WHERE id=?", (DEMO, mon["id"]))
    service.run_check(db.get_monitor(mon["id"], org["id"]))      # up -> resolves
    assert db.get_open_incident(mon["id"]) is None
    resolved = db.list_incidents(mon["id"])[0]
    assert resolved["status"] == "resolved" and resolved["duration_seconds"] is not None


# ============================================================ billing
def test_billing_signature_and_idempotency():
    import hashlib
    import hmac
    billing.WEBHOOK_SECRET = "testsecret"
    body = b'{"hello":1}'
    sig = hmac.new(b"testsecret", body, hashlib.sha256).hexdigest()
    assert billing.verify_webhook(body, sig)
    assert not billing.verify_webhook(body, "deadbeef")
    assert db.webhook_seen("evt-1", "lemonsqueezy") is False
    assert db.webhook_seen("evt-1", "lemonsqueezy") is True     # second time: duplicate


def test_billing_upgrades_org_plan():
    u = auth.signup("pay@example.com", "password123")
    org = auth.primary_org(u["user"])
    payload = {"meta": {"event_name": "subscription_created", "custom_data": {"org_id": org["id"]}},
               "data": {"id": "sub_1", "attributes": {"variant_id": "pro-variant", "status": "active"}}}
    assert billing.apply_webhook_event(payload) == "pro"
    assert db.get_org(org["id"])["plan"] == "pro"


# ============================================================ metrics
def test_metrics_uptime_and_p95():
    u = auth.signup("metric@example.com", "password123")
    org = auth.primary_org(u["user"])
    mon = service.create_monitor_for_org(org, "m", "stdio", DEMO)
    for _ in range(4):
        service.run_check(db.get_monitor(mon["id"], org["id"]))
    assert metrics.uptime_ratio(mon["id"]) == 1.0
    stats = metrics.latency_stats(mon["id"])
    assert stats["count"] >= 4 and stats["p95_ms"] is not None


# ============================================================ full E2E chain (via HTTP API)
def test_full_e2e_chain():
    with TestClient(app) as c:
        # signup -> cookie session
        r = c.post("/api/auth/signup", json={"email": "e2e@example.com", "password": "password123"})
        assert r.status_code == 200
        assert c.get("/api/me").json()["plan"] == "free"
        # create monitor -> probe -> store -> grade
        mon = c.post("/api/monitors", json={"name": "e2e", "kind": "stdio", "endpoint": DEMO}).json()
        chk = c.post(f"/api/monitors/{mon['id']}/check", json={"depth": "liveness"}).json()
        assert chk["status"] == "up" and chk["grade"] and chk["schema_hash"]
        # dashboard list reflects it
        assert any(m["id"] == mon["id"] for m in c.get("/api/monitors").json())
        # schema snapshot stored
        detail = c.get(f"/api/monitors/{mon['id']}").json()
        assert detail["tools"] and detail["metrics"]["uptime"]["30d"] is not None
        # badge + public status
        assert c.get(f"/badge/{mon['id']}.svg").headers["content-type"] == "image/svg+xml"
        assert c.get(f"/status/{mon['id']}").json()["status"] in ("up", "degraded")
        # observability endpoints
        assert c.get("/health").json()["ok"]
        assert c.get("/ready").json()["ready"] is True
        assert "mcpwatch_requests_total" in c.get("/metrics").text
        # unauthenticated access is refused
        c.cookies.clear()
        assert c.get("/api/monitors").status_code == 401


# ============================================================ route-wide authorization audit
_ipn = [0]


def _client_for(email):
    """A signed-up client, each from a distinct IP so per-IP signup limits don't collide."""
    _ipn[0] += 1
    c = TestClient(app, headers={"X-Forwarded-For": f"198.51.100.{_ipn[0]}"})
    assert c.post("/api/auth/signup", json={"email": email, "password": "password123"}).status_code == 200
    return c


def test_authorization_audit():
    """Every protected route must reject another tenant and the unauthenticated caller.

    Data-layer isolation is necessary but not sufficient — this proves the guarantee holds at
    the HTTP boundary, so org B can never reach org A's monitor/checks/incidents/schema.
    """
    cA = _client_for("authz-a@example.com")
    monA = cA.post("/api/monitors", json={"name": "a", "kind": "stdio", "endpoint": DEMO}).json()["id"]

    cB = _client_for("authz-b@example.com")
    orgB = auth.primary_org(db.get_user_by_email("authz-b@example.com"))
    db.set_org_plan(orgB["id"], "pro")                    # so B's API key is usable
    keyB = cB.post("/api/keys", json={"name": "k"}).json()["key"]
    HB = {"Authorization": f"Bearer {keyB}"}

    # --- cross-tenant via SESSION (B's cookies) must 404 on A's monitor
    session_routes = [
        ("GET", f"/api/monitors/{monA}", None),
        ("POST", f"/api/monitors/{monA}/check", {"depth": "liveness"}),
        ("POST", f"/api/monitors/{monA}/pause", None),
        ("POST", f"/api/monitors/{monA}/resume", None),
        ("DELETE", f"/api/monitors/{monA}", None),
    ]
    for method, path, body in session_routes:
        r = cB.request(method, path, json=body)
        assert r.status_code == 404, f"SESSION cross-tenant {method} {path} -> {r.status_code}"
    # alert rule referencing A's monitor must 404; A's monitor never appears in B's list
    assert cB.post("/api/alert-rules",
                   json={"channel": "email", "target": "x@x.com", "monitor_id": monA}).status_code == 404
    assert monA not in [m["id"] for m in cB.get("/api/monitors").json()]

    # --- cross-tenant via API KEY must 404 on A's monitor
    for path in (f"/api/v1/monitors/{monA}", f"/api/v1/checks?monitor_id={monA}",
                 f"/api/v1/incidents?monitor_id={monA}", f"/api/v1/schema?monitor_id={monA}"):
        assert cB.get(path, headers=HB).status_code == 404, f"APIKEY cross-tenant {path}"

    # --- unauthenticated must 401
    cN = TestClient(app)
    assert cN.get("/api/me").status_code == 401
    assert cN.get("/api/monitors").status_code == 401
    assert cN.post("/api/monitors",
                   json={"name": "x", "kind": "http", "endpoint": "http://8.8.8.8/"}).status_code == 401
    assert cN.get("/api/v1/monitors").status_code == 401

    # --- a Free-plan API key is refused from the paid v1 API (403, not 200)
    cF = _client_for("authz-free@example.com")
    keyF = cF.post("/api/keys", json={"name": "k"}).json()["key"]
    assert cF.get("/api/v1/monitors", headers={"Authorization": f"Bearer {keyF}"}).status_code == 403


# ============================================================ concurrency
def test_concurrency():
    """The database must hold its invariants under simultaneous writers."""
    import time as _time
    from concurrent.futures import ThreadPoolExecutor

    ua = auth.signup("conc-a@example.com", "password123")
    ub = auth.signup("conc-b@example.com", "password123")
    oa, ob = auth.primary_org(ua["user"]), auth.primary_org(ub["user"])
    db.set_org_plan(oa["id"], "pro"); db.set_org_plan(ob["id"], "pro")
    oa, ob = db.get_org(oa["id"]), db.get_org(ob["id"])

    # 1) two orgs creating monitors simultaneously — no cross-contamination
    def mk(org, i):
        return service.create_monitor_for_org(org, f"m{i}", "stdio", DEMO)
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = [ex.submit(mk, oa, i) for i in range(4)] + [ex.submit(mk, ob, i) for i in range(4)]
        [f.result() for f in futs]
    assert db.count_monitors(oa["id"]) == 4 and db.count_monitors(ob["id"]) == 4
    assert all(m["org_id"] == oa["id"] for m in db.list_monitors(oa["id"]))

    # 2) concurrent checks on the same monitor — all recorded, no crash
    mon = db.list_monitors(oa["id"])[0]
    with ThreadPoolExecutor(max_workers=4) as ex:
        [f.result() for f in [ex.submit(service.run_check, db.get_monitor(mon["id"], oa["id"]))
                              for _ in range(4)]]
    assert len(db.recent_checks(mon["id"], limit=50)) >= 4

    # 3) concurrent identical billing webhooks — processed exactly once
    with ThreadPoolExecutor(max_workers=8) as ex:
        seen = [f.result() for f in [ex.submit(db.webhook_seen, "dup-evt", "lemonsqueezy")
                                     for _ in range(8)]]
    assert seen.count(False) == 1, f"webhook not idempotent under concurrency: {seen}"

    # 4) multiple workers opening an incident on the same monitor — exactly one
    downmon = service.create_monitor_for_org(oa, "down", "stdio", BROKEN)
    with ThreadPoolExecutor(max_workers=6) as ex:
        incs = [f.result() for f in [ex.submit(db.open_incident, oa["id"], downmon["id"],
                                               "outage", _time.time(), 2) for _ in range(6)]]
    assert len({i["id"] for i in incs}) == 1, "more than one incident opened for the same outage"
    assert db.count_open_incidents(oa["id"]) == 1


# ============================================================ production hardening
def test_api_signup_rate_limited():
    c = TestClient(app, headers={"X-Forwarded-For": "203.0.113.9"})
    codes = [c.post("/api/auth/signup", json={"email": f"rl{i}@x.com", "password": "password123"}).status_code
             for i in range(7)]
    assert 429 in codes, f"signup was never rate-limited: {codes}"


def test_probe_concurrency_guard():
    from mcpwatch.limits import TenantProbeGuard, TooManyProbes
    guard = TenantProbeGuard(per_tenant=2)
    import contextlib
    with contextlib.ExitStack() as stack:
        stack.enter_context(guard.slot("org1"))
        stack.enter_context(guard.slot("org1"))          # 2 concurrent = at the cap
        try:
            stack.enter_context(guard.slot("org1"))
            assert False, "should have refused the 3rd concurrent probe"
        except TooManyProbes:
            pass
        # a different tenant is unaffected
        stack.enter_context(guard.slot("org2"))
    # after release, the tenant can probe again
    with guard.slot("org1"):
        pass


def test_response_body_cap():
    from mcp_probe.client import ToolError
    from mcpwatch.engine.http_client import HttpMCPClient

    class _BigResp:
        def iter_bytes(self):
            for _ in range(10):
                yield b"x" * 1000
    client = HttpMCPClient("http://example.com/mcp", max_response_bytes=2000)
    try:
        client._read_capped(_BigResp())
        assert False, "oversized body should be refused"
    except ToolError:
        pass


def test_secrets_not_leaked_in_errors():
    # credentials in a URL must never appear in an error surfaced to the user
    out = probe_target("http", "http://user:s3cr3t@127.0.0.1:9/mcp", depth="liveness", label="creds")
    assert out.reachable is False
    assert "s3cr3t" not in (out.error or ""), f"secret leaked: {out.error}"
    from mcpwatch import security
    assert security.scrub_text("x http://u:p@h/y z") == "x http://h/y z"
    assert security.sanitize_url("https://u:p@h:8443/x") == "https://h:8443/x"


def test_billing_full_lifecycle():
    u = auth.signup("life@example.com", "password123")
    org = auth.primary_org(u["user"])

    def event(name, variant="pro-variant", status="active", eid="e"):
        return {"meta": {"event_name": name, "custom_data": {"org_id": org["id"]}},
                "data": {"id": eid, "attributes": {"variant_id": variant, "status": status,
                                                    "updated_at": eid}}}
    assert billing.apply_webhook_event(event("subscription_created", eid="c1")) == "pro"
    assert db.get_org(org["id"])["plan"] == "pro"
    # renewal (updated) keeps Pro; duplicate of the same event is idempotent
    assert billing.apply_webhook_event(event("subscription_updated", eid="u1")) == "pro"
    assert billing.apply_webhook_event(event("subscription_updated", eid="u1")) is None  # dup
    # failed payment does not downgrade (dunning)
    assert billing.apply_webhook_event(event("subscription_payment_failed", eid="f1")) is None
    assert db.get_org(org["id"])["plan"] == "pro"
    # cancellation downgrades to free
    assert billing.apply_webhook_event(event("subscription_cancelled", eid="x1")) == "free"
    assert db.get_org(org["id"])["plan"] == "free"


def test_db_unavailable_returns_503(monkeypatch=None):
    import sqlite3 as _sq
    c = _client_for("dbdown@example.com")
    orig = db.list_monitors
    db.list_monitors = lambda *a, **k: (_ for _ in ()).throw(_sq.OperationalError("db is down"))
    try:
        assert c.get("/api/monitors").status_code == 503
    finally:
        db.list_monitors = orig
    # recovers once the DB is back
    assert c.get("/api/monitors").status_code == 200


def test_e2e_incident_alert_recover():
    """Full production chain: down -> incident -> alert delivered -> recover -> resolved."""
    import mcpwatch.alerts as alerts_mod
    sent = []
    orig = alerts_mod.deliver
    alerts_mod.deliver = lambda channel, target, subject, text, payload=None: (sent.append((channel, subject)) or True)
    try:
        u = auth.signup("chain@example.com", "password123")
        org = auth.primary_org(u["user"])
        db.set_org_plan(org["id"], "pro")                 # alerts are a paid feature
        org = db.get_org(org["id"])
        mon = service.create_monitor_for_org(org, "chain", "stdio", BROKEN)

        service.run_check(db.get_monitor(mon["id"], org["id"]))   # fail 1
        service.run_check(db.get_monitor(mon["id"], org["id"]))   # fail 2 -> incident + alert
        assert db.get_open_incident(mon["id"]) is not None
        assert any("DOWN" in s for _, s in sent), f"no down alert delivered: {sent}"

        with db.connect() as conn:
            conn.execute("UPDATE monitors SET endpoint=? WHERE id=?", (DEMO, mon["id"]))
        service.run_check(db.get_monitor(mon["id"], org["id"]))   # up -> resolve + recover alert
        assert db.get_open_incident(mon["id"]) is None
        assert any("recovered" in s for _, s in sent), f"no recovery alert delivered: {sent}"
        # badge + public status reflect the recovery
        assert db.get_monitor(mon["id"])["last_status"] == "up"
    finally:
        alerts_mod.deliver = orig


if __name__ == "__main__":
    setup_module()
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    for fn in fns:
        fn()
        print(f"  ok  {fn.__name__}")
        passed += 1
    print(f"\n{passed}/{len(fns)} passed")
    db.close_pool()  # clean shutdown so the Postgres pool doesn't warn at exit
