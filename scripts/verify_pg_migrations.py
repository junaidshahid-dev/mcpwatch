"""Verify the Postgres schema from zero. Exits non-zero on any failure (a CI gate).

Requires DATABASE_URL to point at a FRESH, empty Postgres database. Checks:
  * every migration applies from zero,
  * exactly the expected 15 tables exist,
  * key indexes (incl. the partial unique incident index) and foreign keys are present,
  * running the migrations again is idempotent (no duplicate versions, no error).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if not os.environ.get("DATABASE_URL"):
    sys.exit("DATABASE_URL must be set to a fresh Postgres database")

from mcpwatch import db  # noqa: E402

EXPECTED_TABLES = {
    "users", "organizations", "memberships", "projects", "monitors", "checks",
    "schema_snapshots", "schema_changes", "incidents", "alert_rules", "api_keys",
    "sessions", "audit_logs", "processed_webhooks", "schema_migrations",
}


def main() -> None:
    print("1) applying migrations from zero...")
    db.init_db()

    with db.connect() as c:
        tables = {r["table_name"] for r in c.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema='public'")}
        missing = EXPECTED_TABLES - tables
        assert not missing, f"MISSING TABLES: {missing}"
        assert len(tables) == 15, f"expected 15 tables, got {len(tables)}: {sorted(tables)}"
        print(f"   tables OK ({len(tables)})")

        idx = {r["indexname"] for r in c.execute(
            "SELECT indexname FROM pg_indexes WHERE schemaname='public'")}
        assert "idx_checks_monitor_time" in idx, "check index missing"
        assert "idx_one_open_incident" in idx, "partial unique incident index missing"
        print(f"   indexes OK ({len(idx)})")

        fks = c.execute("SELECT COUNT(*) AS n FROM information_schema.table_constraints "
                        "WHERE constraint_type='FOREIGN KEY' AND table_schema='public'"
                        ).fetchone()["n"]
        assert fks >= 10, f"expected many FKs, got {fks}"
        print(f"   foreign keys OK ({fks})")

        versions = sorted(r["version"] for r in c.execute("SELECT version FROM schema_migrations"))
        assert versions == [1, 2], f"unexpected migration versions: {versions}"

    print("2) idempotency: applying migrations again...")
    db.init_db()
    with db.connect() as c:
        n = c.execute("SELECT COUNT(*) AS n FROM schema_migrations").fetchone()["n"]
        assert n == 2, f"migrations not idempotent: {n} version rows"
    print("   idempotent OK")

    db.close_pool()
    print("\nPOSTGRES MIGRATION VERIFICATION: PASSED")


if __name__ == "__main__":
    main()
