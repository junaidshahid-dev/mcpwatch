"""Data layer — connection, migrations, and tenant-scoped repositories.

Runs on either backend from one codebase:
  * SQLite (default) — stdlib, zero-config, for local/dev.
  * Postgres/Supabase — when DATABASE_URL is set — for production: persistent, concurrent,
    connection-pooled.

The repository SQL is written once with `?` placeholders and portable types; a thin connection
wrapper translates placeholders for Postgres and the migration runner adapts the DDL. Every
tenant-owned read is filtered by org_id, so authorization is enforced here.
"""
from __future__ import annotations

import json
import re
import secrets
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

from .config import SETTINGS

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


def _is_pg() -> bool:
    url = SETTINGS.database_url
    return bool(url and url.startswith(("postgres://", "postgresql://")))


def _now() -> float:
    return time.time()


def _new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(8)}"


# --------------------------------------------------------------------------- Postgres pool
_pg_pool = None


def _pool():
    global _pg_pool
    if _pg_pool is None:
        from psycopg.rows import dict_row
        from psycopg_pool import ConnectionPool
        _pg_pool = ConnectionPool(
            SETTINGS.database_url, min_size=1, max_size=SETTINGS.pg_pool_max,
            timeout=SETTINGS.pg_pool_timeout,   # fail fast instead of hanging when exhausted
            kwargs={"row_factory": dict_row, "autocommit": False}, open=True)
    return _pg_pool


def close_pool() -> None:
    """Close the Postgres connection pool (call on app shutdown / between test resets)."""
    global _pg_pool
    if _pg_pool is not None:
        _pg_pool.close()
        _pg_pool = None


class _Conn:
    """Uniform connection wrapper: `.execute(sql, params)` -> cursor with dict rows.

    Callers write SQL with `?` placeholders; on Postgres they become `%s`. Both sqlite3.Row and
    psycopg's dict_row rows support `dict(row)`, so the repositories are backend-agnostic.
    """

    def __init__(self, raw, is_pg: bool):
        self.raw = raw
        self.is_pg = is_pg

    def execute(self, sql: str, params=()):
        if self.is_pg:
            sql = sql.replace("?", "%s")
        return self.raw.execute(sql, params)


@contextmanager
def connect():
    if _is_pg():
        # pool.connection() commits on clean exit, rolls back on exception, returns to pool.
        with _pool().connection() as raw:
            yield _Conn(raw, True)
    else:
        raw = sqlite3.connect(SETTINGS.database_path, timeout=15)
        raw.row_factory = sqlite3.Row
        raw.execute("PRAGMA foreign_keys = ON")
        raw.execute("PRAGMA journal_mode = WAL")
        try:
            yield _Conn(raw, False)
            raw.commit()
        finally:
            raw.close()


def _is_unique_violation(e: Exception) -> bool:
    return isinstance(e, sqlite3.IntegrityError) or type(e).__name__ in (
        "UniqueViolation", "IntegrityError")


# --------------------------------------------------------------------------- migrations
def _ddl_for_backend(sql: str, is_pg: bool) -> str:
    """Adapt sqlite-flavoured DDL to Postgres. Only REAL needs care: it is float4 in Postgres,
    too imprecise for epoch timestamps, so it becomes double precision."""
    if not is_pg:
        return sql
    return re.sub(r"\bREAL\b", "double precision", sql)


def _split_statements(sql: str):
    """Split a migration script into individual statements (Postgres executes one at a time)."""
    for chunk in sql.split(";"):
        # drop comment-only / blank chunks
        body = "\n".join(ln for ln in chunk.splitlines() if not ln.strip().startswith("--"))
        if body.strip():
            yield chunk.strip()


def init_db() -> None:
    is_pg = _is_pg()
    if not is_pg:
        Path(SETTINGS.database_path).parent.mkdir(parents=True, exist_ok=True)
    with connect() as conn:
        conn.execute(_ddl_for_backend(
            "CREATE TABLE IF NOT EXISTS schema_migrations "
            "(version INTEGER PRIMARY KEY, applied_at REAL NOT NULL)", is_pg))
        done = {r["version"] for r in conn.execute("SELECT version FROM schema_migrations")}
        for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            version = int(path.name.split("_", 1)[0])
            if version in done:
                continue
            script = _ddl_for_backend(path.read_text(encoding="utf-8"), is_pg)
            if is_pg:
                for stmt in _split_statements(script):
                    conn.execute(stmt)
            else:
                conn.raw.executescript(script)
            conn.execute("INSERT INTO schema_migrations (version, applied_at) VALUES (?,?)",
                         (version, _now()))


def _one(conn, sql, params=()) -> dict | None:
    row = conn.execute(sql, params).fetchone()
    return dict(row) if row else None


def _all(conn, sql, params=()) -> list[dict]:
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def _one(conn, sql, params=()) -> dict | None:
    row = conn.execute(sql, params).fetchone()
    return dict(row) if row else None


def _all(conn, sql, params=()) -> list[dict]:
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


# --------------------------------------------------------------------------- users
def create_user(email: str, password_hash: str, verify_token_hash: str | None = None) -> dict:
    uid = _new_id("usr")
    now = _now()
    with connect() as conn:
        conn.execute(
            "INSERT INTO users (id,email,password_hash,email_verified,verify_token_hash,"
            "created_at,updated_at) VALUES (?,?,?,0,?,?,?)",
            (uid, email.lower().strip(), password_hash, verify_token_hash, now, now))
    return get_user(uid)


def get_user(user_id: str) -> dict | None:
    with connect() as conn:
        return _one(conn, "SELECT * FROM users WHERE id=? AND deleted_at IS NULL", (user_id,))


def get_user_by_email(email: str) -> dict | None:
    with connect() as conn:
        return _one(conn, "SELECT * FROM users WHERE email=? AND deleted_at IS NULL",
                    (email.lower().strip(),))


def mark_email_verified(user_id: str) -> None:
    with connect() as conn:
        conn.execute("UPDATE users SET email_verified=1, verify_token_hash=NULL, updated_at=? "
                     "WHERE id=?", (_now(), user_id))


def get_user_by_verify_token(token_hash: str) -> dict | None:
    with connect() as conn:
        return _one(conn, "SELECT * FROM users WHERE verify_token_hash=?", (token_hash,))


def set_reset_token(user_id: str, token_hash: str, expires_at: float) -> None:
    with connect() as conn:
        conn.execute("UPDATE users SET reset_token_hash=?, reset_expires_at=?, updated_at=? "
                     "WHERE id=?", (token_hash, expires_at, _now(), user_id))


def get_user_by_reset_token(token_hash: str) -> dict | None:
    with connect() as conn:
        return _one(conn, "SELECT * FROM users WHERE reset_token_hash=? AND reset_expires_at>=?",
                    (token_hash, _now()))


def update_password(user_id: str, password_hash: str) -> None:
    with connect() as conn:
        conn.execute("UPDATE users SET password_hash=?, reset_token_hash=NULL, "
                     "reset_expires_at=NULL, updated_at=? WHERE id=?",
                     (password_hash, _now(), user_id))


# --------------------------------------------------------------------------- orgs / membership
def create_org(name: str, owner_user_id: str, plan: str = "free") -> dict:
    oid = _new_id("org")
    now = _now()
    with connect() as conn:
        conn.execute("INSERT INTO organizations (id,name,plan,owner_user_id,created_at,updated_at)"
                     " VALUES (?,?,?,?,?,?)", (oid, name, plan, owner_user_id, now, now))
        conn.execute("INSERT INTO memberships (id,org_id,user_id,role,created_at) "
                     "VALUES (?,?,?,'owner',?)", (_new_id("mem"), oid, owner_user_id, now))
    return get_org(oid)


def get_org(org_id: str) -> dict | None:
    with connect() as conn:
        return _one(conn, "SELECT * FROM organizations WHERE id=? AND deleted_at IS NULL", (org_id,))


def set_org_plan(org_id: str, plan: str) -> None:
    with connect() as conn:
        conn.execute("UPDATE organizations SET plan=?, updated_at=? WHERE id=?",
                     (plan, _now(), org_id))


def set_org_billing(org_id: str, customer_id: str) -> None:
    with connect() as conn:
        conn.execute("UPDATE organizations SET billing_customer_id=?, updated_at=? WHERE id=?",
                     (customer_id, _now(), org_id))


def orgs_for_user(user_id: str) -> list[dict]:
    with connect() as conn:
        return _all(conn,
            "SELECT o.* FROM organizations o JOIN memberships m ON m.org_id=o.id "
            "WHERE m.user_id=? AND o.deleted_at IS NULL ORDER BY o.created_at", (user_id,))


def membership(org_id: str, user_id: str) -> dict | None:
    with connect() as conn:
        return _one(conn, "SELECT * FROM memberships WHERE org_id=? AND user_id=?",
                    (org_id, user_id))


def add_member(org_id: str, user_id: str, role: str = "member") -> dict:
    with connect() as conn:
        existing = _one(conn, "SELECT * FROM memberships WHERE org_id=? AND user_id=?",
                        (org_id, user_id))
        if existing:
            return existing
        conn.execute("INSERT INTO memberships (id,org_id,user_id,role,created_at) "
                     "VALUES (?,?,?,?,?)", (_new_id("mem"), org_id, user_id, role, _now()))
        return _one(conn, "SELECT * FROM memberships WHERE org_id=? AND user_id=?",
                    (org_id, user_id))


# --------------------------------------------------------------------------- projects
def create_project(org_id: str, name: str) -> dict:
    pid = _new_id("prj")
    now = _now()
    with connect() as conn:
        conn.execute("INSERT INTO projects (id,org_id,name,created_at,updated_at) "
                     "VALUES (?,?,?,?,?)", (pid, org_id, name, now, now))
        return _one(conn, "SELECT * FROM projects WHERE id=?", (pid,))


def list_projects(org_id: str) -> list[dict]:
    with connect() as conn:
        return _all(conn, "SELECT * FROM projects WHERE org_id=? AND deleted_at IS NULL "
                    "ORDER BY created_at", (org_id,))


def get_project(project_id: str, org_id: str) -> dict | None:
    with connect() as conn:
        return _one(conn, "SELECT * FROM projects WHERE id=? AND org_id=? AND deleted_at IS NULL",
                    (project_id, org_id))


# --------------------------------------------------------------------------- monitors
def create_monitor(org_id: str, project_id: str, name: str, kind: str, endpoint: str,
                   interval_seconds: int, public: bool = True, owned: bool = False) -> dict:
    mid = _new_id("mon")
    now = _now()
    with connect() as conn:
        conn.execute(
            "INSERT INTO monitors (id,org_id,project_id,name,kind,endpoint,interval_seconds,"
            "public,owned,paused,consecutive_failures,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,0,0,?,?)",
            (mid, org_id, project_id, name, kind, endpoint, interval_seconds,
             int(public), int(owned), now, now))
        return _one(conn, "SELECT * FROM monitors WHERE id=?", (mid,))


def get_monitor(monitor_id: str, org_id: str | None = None) -> dict | None:
    """Fetch a monitor. If org_id is given, enforces tenant ownership (returns None otherwise)."""
    with connect() as conn:
        if org_id is not None:
            return _one(conn, "SELECT * FROM monitors WHERE id=? AND org_id=? AND deleted_at IS NULL",
                        (monitor_id, org_id))
        return _one(conn, "SELECT * FROM monitors WHERE id=? AND deleted_at IS NULL", (monitor_id,))


def list_monitors(org_id: str) -> list[dict]:
    with connect() as conn:
        return _all(conn, "SELECT * FROM monitors WHERE org_id=? AND deleted_at IS NULL "
                    "ORDER BY created_at", (org_id,))


def count_monitors(org_id: str) -> int:
    with connect() as conn:
        return conn.execute("SELECT COUNT(*) AS n FROM monitors WHERE org_id=? AND "
                            "deleted_at IS NULL", (org_id,)).fetchone()["n"]


def due_monitors(now: float | None = None) -> list[dict]:
    now = now if now is not None else _now()
    with connect() as conn:
        return _all(conn,
            "SELECT * FROM monitors WHERE paused=0 AND deleted_at IS NULL AND "
            "(last_checked_at IS NULL OR last_checked_at + interval_seconds <= ?)", (now,))


def set_monitor_paused(monitor_id: str, org_id: str, paused: bool) -> None:
    with connect() as conn:
        conn.execute("UPDATE monitors SET paused=?, updated_at=? WHERE id=? AND org_id=?",
                     (int(paused), _now(), monitor_id, org_id))


def soft_delete_monitor(monitor_id: str, org_id: str) -> None:
    with connect() as conn:
        conn.execute("UPDATE monitors SET deleted_at=?, paused=1 WHERE id=? AND org_id=?",
                     (_now(), monitor_id, org_id))


# --------------------------------------------------------------------------- checks
def record_check(monitor: dict, outcome: dict) -> dict:
    """Persist a check, update the monitor's denormalised latest state + failure streak."""
    cid = _new_id("chk")
    when = _now()
    o = outcome
    mid, org_id = monitor["id"], monitor["org_id"]
    with connect() as conn:
        conn.execute(
            "INSERT INTO checks (id,org_id,monitor_id,status,depth,reachable,score,grade,"
            "tool_count,schema_hash,protocol_version,server_name,server_version,latency_ms,"
            "check_duration_ms,counts_json,findings_json,error,checked_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (cid, org_id, mid, o["status"], o["depth"], int(o["reachable"]), o.get("score"),
             o.get("grade"), o.get("tool_count"), o.get("schema_hash"), o.get("protocol_version"),
             o.get("server_name"), o.get("server_version"), o.get("latency_ms"),
             o.get("check_duration_ms"), json.dumps(o.get("counts") or {}),
             json.dumps(o.get("findings") or []), o.get("error"), when))
        streak = 0 if o["reachable"] else (monitor.get("consecutive_failures", 0) + 1)
        conn.execute(
            "UPDATE monitors SET last_status=?, last_score=?, last_grade=?, last_schema_hash=?, "
            "last_checked_at=?, consecutive_failures=?, updated_at=? WHERE id=?",
            (o["status"], o.get("score"), o.get("grade"), o.get("schema_hash"), when,
             streak, when, mid))
        stored = _one(conn, "SELECT * FROM checks WHERE id=?", (cid,))
    return _decode_check(stored)


def latest_check(monitor_id: str) -> dict | None:
    with connect() as conn:
        row = _one(conn, "SELECT * FROM checks WHERE monitor_id=? ORDER BY checked_at DESC LIMIT 1",
                   (monitor_id,))
    return _decode_check(row) if row else None


def recent_checks(monitor_id: str, limit: int = 50) -> list[dict]:
    with connect() as conn:
        rows = _all(conn, "SELECT * FROM checks WHERE monitor_id=? ORDER BY checked_at DESC LIMIT ?",
                    (monitor_id, limit))
    return [_decode_check(r) for r in rows]


def checks_in_window(monitor_id: str, window_seconds: float) -> list[dict]:
    since = _now() - window_seconds
    with connect() as conn:
        rows = _all(conn, "SELECT reachable,latency_ms,grade,status,checked_at FROM checks "
                    "WHERE monitor_id=? AND checked_at>=? ORDER BY checked_at", (monitor_id, since))
    for r in rows:
        r["reachable"] = bool(r["reachable"])
    return rows


def _decode_check(row: dict | None) -> dict | None:
    if not row:
        return None
    d = dict(row)
    d["reachable"] = bool(d["reachable"])
    d["counts"] = json.loads(d.pop("counts_json") or "{}")
    d["findings"] = json.loads(d.pop("findings_json") or "[]")
    return d


# --------------------------------------------------------------------------- schema snapshots/changes
def latest_snapshot(monitor_id: str) -> dict | None:
    with connect() as conn:
        row = _one(conn, "SELECT * FROM schema_snapshots WHERE monitor_id=? "
                   "ORDER BY created_at DESC LIMIT 1", (monitor_id,))
    if row:
        row["tools"] = json.loads(row.pop("tools_json"))
    return row


def add_snapshot(org_id: str, monitor_id: str, schema_hash: str, tools: list) -> None:
    with connect() as conn:
        conn.execute("INSERT INTO schema_snapshots (id,org_id,monitor_id,schema_hash,tools_json,"
                     "created_at) VALUES (?,?,?,?,?,?)",
                     (_new_id("snp"), org_id, monitor_id, schema_hash, json.dumps(tools), _now()))


def add_schema_change(org_id: str, monitor_id: str, from_hash: str | None, to_hash: str,
                      severity: str, diff: dict) -> dict:
    cid = _new_id("scg")
    with connect() as conn:
        conn.execute("INSERT INTO schema_changes (id,org_id,monitor_id,from_hash,to_hash,severity,"
                     "diff_json,created_at) VALUES (?,?,?,?,?,?,?,?)",
                     (cid, org_id, monitor_id, from_hash, to_hash, severity, json.dumps(diff), _now()))
        row = _one(conn, "SELECT * FROM schema_changes WHERE id=?", (cid,))
    row["diff"] = json.loads(row.pop("diff_json"))
    return row


def recent_schema_changes(monitor_id: str, limit: int = 20) -> list[dict]:
    with connect() as conn:
        rows = _all(conn, "SELECT * FROM schema_changes WHERE monitor_id=? "
                    "ORDER BY created_at DESC LIMIT ?", (monitor_id, limit))
    for r in rows:
        r["diff"] = json.loads(r.pop("diff_json"))
    return rows


# --------------------------------------------------------------------------- incidents
def open_incident(org_id: str, monitor_id: str, cause: str, started_at: float,
                  failed_checks: int) -> dict:
    iid = _new_id("inc")
    now = _now()
    try:
        with connect() as conn:
            conn.execute("INSERT INTO incidents (id,org_id,monitor_id,status,cause,started_at,"
                         "detected_at,failed_checks,created_at) VALUES (?,?,?,'open',?,?,?,?,?)",
                         (iid, org_id, monitor_id, cause, started_at, now, failed_checks, now))
        return get_incident(iid)
    except Exception as e:
        # Lost a race with another worker (partial unique index on one open incident per
        # monitor). The other worker's incident is authoritative.
        if _is_unique_violation(e):
            return get_open_incident(monitor_id)
        raise


def get_incident(incident_id: str) -> dict | None:
    with connect() as conn:
        return _one(conn, "SELECT * FROM incidents WHERE id=?", (incident_id,))


def get_open_incident(monitor_id: str) -> dict | None:
    with connect() as conn:
        return _one(conn, "SELECT * FROM incidents WHERE monitor_id=? AND status='open' "
                    "ORDER BY started_at DESC LIMIT 1", (monitor_id,))


def bump_incident_failures(incident_id: str) -> None:
    with connect() as conn:
        conn.execute("UPDATE incidents SET failed_checks=failed_checks+1 WHERE id=?", (incident_id,))


def resolve_incident(incident_id: str, resolved_at: float) -> dict:
    with connect() as conn:
        inc = _one(conn, "SELECT * FROM incidents WHERE id=?", (incident_id,))
        dur = int(resolved_at - inc["started_at"])
        conn.execute("UPDATE incidents SET status='resolved', resolved_at=?, duration_seconds=? "
                     "WHERE id=?", (resolved_at, dur, incident_id))
        return _one(conn, "SELECT * FROM incidents WHERE id=?", (incident_id,))


def list_incidents(monitor_id: str, limit: int = 50) -> list[dict]:
    with connect() as conn:
        return _all(conn, "SELECT * FROM incidents WHERE monitor_id=? ORDER BY started_at DESC "
                    "LIMIT ?", (monitor_id, limit))


def count_open_incidents(org_id: str) -> int:
    with connect() as conn:
        return conn.execute("SELECT COUNT(*) AS n FROM incidents WHERE org_id=? AND "
                            "status='open'", (org_id,)).fetchone()["n"]


# --------------------------------------------------------------------------- alert rules
def create_alert_rule(org_id: str, channel: str, target: str, monitor_id: str | None = None,
                      **flags) -> dict:
    rid = _new_id("alr")
    with connect() as conn:
        conn.execute(
            "INSERT INTO alert_rules (id,org_id,monitor_id,channel,target,on_down,on_recover,"
            "on_grade_below,on_schema_change,on_breaking_change,created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (rid, org_id, monitor_id, channel, target,
             int(flags.get("on_down", True)), int(flags.get("on_recover", True)),
             flags.get("on_grade_below"), int(flags.get("on_schema_change", False)),
             int(flags.get("on_breaking_change", True)), _now()))
        return _one(conn, "SELECT * FROM alert_rules WHERE id=?", (rid,))


def alert_rules_for_monitor(org_id: str, monitor_id: str) -> list[dict]:
    with connect() as conn:
        return _all(conn, "SELECT * FROM alert_rules WHERE org_id=? AND deleted_at IS NULL AND "
                    "(monitor_id IS NULL OR monitor_id=?)", (org_id, monitor_id))


def touch_alert_rule(rule_id: str) -> None:
    with connect() as conn:
        conn.execute("UPDATE alert_rules SET last_fired_at=? WHERE id=?", (_now(), rule_id))


# --------------------------------------------------------------------------- api keys
def create_api_key(org_id: str, user_id: str, name: str, key_prefix: str, key_hash: str) -> dict:
    kid = _new_id("key")
    with connect() as conn:
        conn.execute("INSERT INTO api_keys (id,org_id,user_id,name,key_prefix,key_hash,created_at) "
                     "VALUES (?,?,?,?,?,?,?)", (kid, org_id, user_id, name, key_prefix, key_hash, _now()))
        return _one(conn, "SELECT * FROM api_keys WHERE id=?", (kid,))


def get_api_key_by_hash(key_hash: str) -> dict | None:
    with connect() as conn:
        row = _one(conn, "SELECT * FROM api_keys WHERE key_hash=? AND revoked_at IS NULL", (key_hash,))
        if row:
            conn.execute("UPDATE api_keys SET last_used_at=? WHERE id=?", (_now(), row["id"]))
    return row


def list_api_keys(org_id: str) -> list[dict]:
    with connect() as conn:
        return _all(conn, "SELECT id,name,key_prefix,last_used_at,created_at,revoked_at "
                    "FROM api_keys WHERE org_id=? ORDER BY created_at DESC", (org_id,))


def revoke_api_key(key_id: str, org_id: str) -> None:
    with connect() as conn:
        conn.execute("UPDATE api_keys SET revoked_at=? WHERE id=? AND org_id=?",
                     (_now(), key_id, org_id))


# --------------------------------------------------------------------------- sessions
def create_session(user_id: str, token_hash: str, ttl_seconds: int) -> dict:
    sid = _new_id("ses")
    now = _now()
    with connect() as conn:
        conn.execute("INSERT INTO sessions (id,user_id,token_hash,created_at,expires_at) "
                     "VALUES (?,?,?,?,?)", (sid, user_id, token_hash, now, now + ttl_seconds))
        return _one(conn, "SELECT * FROM sessions WHERE id=?", (sid,))


def get_session_user(token_hash: str) -> dict | None:
    with connect() as conn:
        s = _one(conn, "SELECT * FROM sessions WHERE token_hash=? AND expires_at>=?",
                 (token_hash, _now()))
        if not s:
            return None
        return _one(conn, "SELECT * FROM users WHERE id=? AND deleted_at IS NULL", (s["user_id"],))


def delete_session(token_hash: str) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM sessions WHERE token_hash=?", (token_hash,))


# --------------------------------------------------------------------------- audit + webhooks
def audit(action: str, *, org_id=None, user_id=None, target_type=None, target_id=None,
          meta=None, ip=None) -> None:
    with connect() as conn:
        conn.execute("INSERT INTO audit_logs (id,org_id,user_id,action,target_type,target_id,"
                     "meta_json,ip,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                     (_new_id("aud"), org_id, user_id, action, target_type, target_id,
                      json.dumps(meta) if meta else None, ip, _now()))


def recent_audit(org_id: str, limit: int = 100) -> list[dict]:
    with connect() as conn:
        return _all(conn, "SELECT * FROM audit_logs WHERE org_id=? ORDER BY created_at DESC LIMIT ?",
                    (org_id, limit))


def webhook_seen(event_id: str, provider: str) -> bool:
    """Return True if already processed; otherwise record it and return False (idempotency).

    Race-safe: the INSERT is the atomic gate (id is the primary key). Two concurrent deliveries
    of the same event both try to insert; exactly one succeeds (returns False = process it), the
    other hits the unique constraint (returns True = skip). No check-then-insert window.
    """
    try:
        with connect() as conn:
            conn.execute("INSERT INTO processed_webhooks (id,provider,processed_at) "
                         "VALUES (?,?,?)", (event_id, provider, _now()))
        return False
    except Exception as e:
        if _is_unique_violation(e):
            return True
        raise
