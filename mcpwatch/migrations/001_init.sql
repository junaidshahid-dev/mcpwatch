-- 001_init — multi-tenant core schema.
-- Tenancy chain: users -(memberships)- organizations -> projects -> monitors -> checks.
-- org_id is denormalised onto every tenant-owned row so authorization is one indexed compare.
-- Timestamps are epoch seconds (REAL). Soft deletion via deleted_at (NULL = live).

CREATE TABLE users (
    id             TEXT PRIMARY KEY,
    email          TEXT UNIQUE NOT NULL,
    password_hash  TEXT NOT NULL,
    email_verified INTEGER NOT NULL DEFAULT 0,
    verify_token_hash TEXT,
    reset_token_hash  TEXT,
    reset_expires_at  REAL,
    created_at     REAL NOT NULL,
    updated_at     REAL NOT NULL,
    deleted_at     REAL
);

CREATE TABLE organizations (
    id             TEXT PRIMARY KEY,
    name           TEXT NOT NULL,
    plan           TEXT NOT NULL DEFAULT 'free',
    owner_user_id  TEXT NOT NULL REFERENCES users(id),
    billing_customer_id TEXT,          -- LemonSqueezy customer/subscription id
    created_at     REAL NOT NULL,
    updated_at     REAL NOT NULL,
    deleted_at     REAL
);

CREATE TABLE memberships (
    id             TEXT PRIMARY KEY,
    org_id         TEXT NOT NULL REFERENCES organizations(id),
    user_id        TEXT NOT NULL REFERENCES users(id),
    role           TEXT NOT NULL DEFAULT 'owner',   -- owner | admin | member
    created_at     REAL NOT NULL,
    UNIQUE(org_id, user_id)
);

CREATE TABLE projects (
    id             TEXT PRIMARY KEY,
    org_id         TEXT NOT NULL REFERENCES organizations(id),
    name           TEXT NOT NULL,
    created_at     REAL NOT NULL,
    updated_at     REAL NOT NULL,
    deleted_at     REAL
);

CREATE TABLE monitors (
    id               TEXT PRIMARY KEY,
    org_id           TEXT NOT NULL REFERENCES organizations(id),
    project_id       TEXT NOT NULL REFERENCES projects(id),
    name             TEXT NOT NULL,
    kind             TEXT NOT NULL,                 -- stdio | http
    endpoint         TEXT NOT NULL,
    interval_seconds INTEGER NOT NULL DEFAULT 3600,
    public           INTEGER NOT NULL DEFAULT 1,
    owned            INTEGER NOT NULL DEFAULT 0,    -- operator asserts ownership (enables audit)
    paused           INTEGER NOT NULL DEFAULT 0,
    -- denormalised latest state for fast dashboards/badges:
    last_status      TEXT,
    last_score       INTEGER,
    last_grade       TEXT,
    last_schema_hash TEXT,
    last_checked_at  REAL,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    created_at       REAL NOT NULL,
    updated_at       REAL NOT NULL,
    deleted_at       REAL
);

CREATE TABLE checks (
    id              TEXT PRIMARY KEY,
    org_id          TEXT NOT NULL REFERENCES organizations(id),
    monitor_id      TEXT NOT NULL REFERENCES monitors(id),
    status          TEXT NOT NULL,                 -- up | degraded | down
    depth           TEXT NOT NULL,                 -- liveness | audit
    reachable       INTEGER NOT NULL,
    score           INTEGER,
    grade           TEXT,
    tool_count      INTEGER,
    schema_hash     TEXT,
    protocol_version TEXT,
    server_name     TEXT,
    server_version  TEXT,
    latency_ms      INTEGER,
    check_duration_ms INTEGER,
    counts_json     TEXT,
    findings_json   TEXT,
    error           TEXT,
    checked_at      REAL NOT NULL
);

CREATE TABLE schema_snapshots (
    id              TEXT PRIMARY KEY,
    org_id          TEXT NOT NULL REFERENCES organizations(id),
    monitor_id      TEXT NOT NULL REFERENCES monitors(id),
    schema_hash     TEXT NOT NULL,
    tools_json      TEXT NOT NULL,                 -- normalised tool schemas, for diffing
    created_at      REAL NOT NULL
);

CREATE TABLE schema_changes (
    id              TEXT PRIMARY KEY,
    org_id          TEXT NOT NULL REFERENCES organizations(id),
    monitor_id      TEXT NOT NULL REFERENCES monitors(id),
    from_hash       TEXT,
    to_hash         TEXT NOT NULL,
    severity        TEXT NOT NULL,                 -- non_breaking | potentially_breaking | breaking
    diff_json       TEXT NOT NULL,
    created_at      REAL NOT NULL
);

CREATE TABLE incidents (
    id              TEXT PRIMARY KEY,
    org_id          TEXT NOT NULL REFERENCES organizations(id),
    monitor_id      TEXT NOT NULL REFERENCES monitors(id),
    status          TEXT NOT NULL DEFAULT 'open',  -- open | resolved
    cause           TEXT,
    started_at      REAL NOT NULL,
    detected_at     REAL NOT NULL,
    resolved_at     REAL,
    duration_seconds INTEGER,
    failed_checks   INTEGER NOT NULL DEFAULT 1,
    created_at      REAL NOT NULL
);

CREATE TABLE alert_rules (
    id              TEXT PRIMARY KEY,
    org_id          TEXT NOT NULL REFERENCES organizations(id),
    monitor_id      TEXT REFERENCES monitors(id),  -- NULL = applies to all monitors in org
    channel         TEXT NOT NULL,                 -- email | slack | discord | webhook
    target          TEXT NOT NULL,                 -- email address or webhook URL
    on_down         INTEGER NOT NULL DEFAULT 1,
    on_recover      INTEGER NOT NULL DEFAULT 1,
    on_grade_below  INTEGER,                        -- NULL = off, else threshold 0-100
    on_schema_change   INTEGER NOT NULL DEFAULT 0,
    on_breaking_change INTEGER NOT NULL DEFAULT 1,
    last_fired_at   REAL,
    created_at      REAL NOT NULL,
    deleted_at      REAL
);

CREATE TABLE api_keys (
    id              TEXT PRIMARY KEY,
    org_id          TEXT NOT NULL REFERENCES organizations(id),
    user_id         TEXT NOT NULL REFERENCES users(id),
    name            TEXT NOT NULL,
    key_prefix      TEXT NOT NULL,                 -- shown in listings
    key_hash        TEXT UNIQUE NOT NULL,          -- sha256 of the plaintext key
    last_used_at    REAL,
    created_at      REAL NOT NULL,
    revoked_at      REAL
);

CREATE TABLE sessions (
    id              TEXT PRIMARY KEY,
    user_id         TEXT NOT NULL REFERENCES users(id),
    token_hash      TEXT UNIQUE NOT NULL,
    created_at      REAL NOT NULL,
    expires_at      REAL NOT NULL
);

CREATE TABLE audit_logs (
    id              TEXT PRIMARY KEY,
    org_id          TEXT,
    user_id         TEXT,
    action          TEXT NOT NULL,                 -- e.g. monitor.created, auth.login
    target_type     TEXT,
    target_id       TEXT,
    meta_json       TEXT,
    ip              TEXT,
    created_at      REAL NOT NULL
);

CREATE TABLE processed_webhooks (
    id              TEXT PRIMARY KEY,              -- provider event id, for idempotency
    provider        TEXT NOT NULL,
    processed_at    REAL NOT NULL
);

-- indexes
CREATE INDEX idx_memberships_user ON memberships(user_id);
CREATE INDEX idx_projects_org ON projects(org_id);
CREATE INDEX idx_monitors_org ON monitors(org_id);
CREATE INDEX idx_monitors_project ON monitors(project_id);
CREATE INDEX idx_monitors_due ON monitors(paused, last_checked_at);
CREATE INDEX idx_checks_monitor_time ON checks(monitor_id, checked_at DESC);
CREATE INDEX idx_checks_org ON checks(org_id);
CREATE INDEX idx_snapshots_monitor ON schema_snapshots(monitor_id, created_at DESC);
CREATE INDEX idx_changes_monitor ON schema_changes(monitor_id, created_at DESC);
CREATE INDEX idx_incidents_monitor ON incidents(monitor_id, status);
CREATE INDEX idx_alert_rules_org ON alert_rules(org_id);
CREATE INDEX idx_api_keys_hash ON api_keys(key_hash);
CREATE INDEX idx_sessions_token ON sessions(token_hash);
CREATE INDEX idx_audit_org ON audit_logs(org_id, created_at DESC);
