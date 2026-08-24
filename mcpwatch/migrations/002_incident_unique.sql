-- 002 — enforce at most one OPEN incident per monitor.
-- A partial unique index (supported by both SQLite and Postgres) makes the "one open incident"
-- invariant hold even when concurrent workers probe the same down monitor at the same time:
-- the second INSERT loses on the unique constraint and the code falls back to the existing one.
CREATE UNIQUE INDEX idx_one_open_incident ON incidents(monitor_id) WHERE status = 'open';
