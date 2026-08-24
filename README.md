# MCPWatch

[![CI](https://github.com/junaidshahid-dev/mcp-watch/actions/workflows/ci.yml/badge.svg)](https://github.com/junaidshahid-dev/mcp-watch/actions/workflows/ci.yml)

**Uptime, schema-health & incident monitoring for MCP servers.**

MCPWatch pings every [Model Context Protocol](https://modelcontextprotocol.io) server your
agents depend on, grades its tool schemas 0–100, detects breaking schema changes, opens
incidents when it goes down, and alerts you — before your agents do. Every monitored server gets
a public status badge, which is also the product's growth loop.

It wraps [**mcp-probe**](https://github.com/junaidshahid-dev/mcp-probe) as its grading engine —
the moat: a schema-driven auditor already run against 13 public MCP servers.

---

## What's built

**Product**
- **Uptime & latency** (avg + P95), **schema-health grade** (0–100, A–F), tool inventory.
- **Schema-diff engine** — snapshots every check, classifies changes 🟢 non-breaking /
  🟡 potentially-breaking / 🔴 breaking, and alerts on them.
- **Incident management** — opens an incident after N consecutive failures, tracks duration and
  failed-check count, auto-resolves on recovery.
- **Alerts** — email (Resend), Slack, Discord, generic webhook; per-rule triggers with
  cooldown/dedup so an outage doesn't send 500 emails.
- **Public status** — embeddable SVG badge + JSON status endpoint.
- **Programmatic API** (`/api/v1`, Pro/Team) with hashed API keys.

**Platform foundation**
- **Auth** — email/password (scrypt-hashed), cookie sessions, logout, email verification,
  password reset, rate-limited auth endpoints.
- **Multi-tenancy** — `User → Organization → Project → Monitor → Check`; every resource is
  org-scoped and isolation is enforced in the data layer (see the tenant-isolation test).
- **Security** — SSRF guard (private/loopback/reserved IPs refused; DNS resolved before
  connect; redirects not followed), response-size caps, stdio disabled on hosted deployments.
- **Database** — runs on **Postgres** (`DATABASE_URL`, connection-pooled) or **SQLite** from one
  codebase; versioned migration runner (auto-applied on startup), FKs, indexes, timestamps,
  soft-delete, audit logs. Verified end-to-end against a real Postgres.
- **Observability** — `/health`, `/ready` (touches DB + engine), `/metrics` (Prometheus),
  request-id logging.
- **Billing** — LemonSqueezy checkout + signature-verified, **idempotent** webhooks across the
  full subscription lifecycle.

### Two check depths — deliberately separated

| depth | what it does | when |
|---|---|---|
| **liveness** (default) | connect, list tools, statically grade schemas. **No tool calls.** | scheduled, safe against any server |
| **audit** | full adversarial fuzz (hundreds of malformed payloads) | on-demand, only on servers you mark **owned** |

Scheduled monitoring never sends attack traffic to third-party servers, and MCPWatch refuses to
become a network scanner (SSRF guard + no arbitrary command execution on the hosted plane).

---

## Quickstart (local, zero config)

```bash
pip install -r requirements.txt
export MCP_PROBE_PATH=/path/to/mcp-probe    # or: pip install -e ../Building/mcp-probe
python run.py                               # http://localhost:8000
```

Open the dashboard, create an account, and add a monitor. To see a grade instantly, add kind
**stdio** with endpoint `python scripts/demo_server.py`.

Run the full test gate (probes a real MCP server over stdio — no mocks on the engine):

```bash
python tests/test_mcpwatch.py      # or: python -m pytest
```

The gate covers: schema-diff classification, the engine, SSRF, auth, tenant isolation, plan
limits, schema-change detection, incident open/resolve, billing idempotency, metrics, and a full
signup→probe→incident→recovery→badge→status **E2E chain**.

---

## Architecture

```
web-next/                  production front-end — Next.js 15 + React 19 + TS (deploy to Vercel)
web/                       backend-served landing + dashboard (self-contained HTML/CSS/JS)
mcpwatch/
  migrations/001_init.sql  multi-tenant schema (FKs, indexes, soft-delete, audit)
  config.py                settings (plans, security flags, limits)
  crypto.py                scrypt password hashing, token/api-key hashing
  security.py              SSRF guard + stdio gate  ← protects the workers
  db.py                    migration runner + tenant-scoped repositories
  auth.py                  signup/login/session/reset + rate limiting
  engine/                  the moat — wraps mcp-probe
    probe_adapter.py         probe_target() -> ProbeOutcome (grade, schema_hash, latency…)
    http_client.py           MCP client over Streamable HTTP (SSRF-checked)
    schema_diff.py           normalize / hash / diff / classify tool surfaces
  service.py               run_check spine: probe → record → diff → incident → alert
  metrics.py               uptime, availability, avg/P95 latency, grade history
  alerts.py                email / slack / discord / webhook delivery
  billing.py               LemonSqueezy checkout + idempotent webhooks
  observability.py         request-id logging, /metrics registry, readiness
  api.py                   FastAPI: dashboard API, /api/v1, public, billing, health
  scheduler.py             run-due tick (cron target)
scripts/demo_server.py     a tiny real MCP server to monitor out of the box
```

Everything above the engine speaks `ProbeOutcome` and never imports mcp-probe directly.

---

## Continuous integration

`.github/workflows/ci.yml` runs on every push and PR and **fails** the build on any regression:

- **Backend** — the 33-test gate on **SQLite and a real Postgres service container**; migrations
  verified from zero + idempotent (`scripts/verify_pg_migrations.py`); authorization, concurrency,
  SSRF and incident invariants (all inside the gate).
- **Frontend** — `npm ci`, ESLint, `tsc --noEmit`, and the production `next build`.
- **Security** — `pip-audit` (backend deps, fails on any known vuln), `npm audit` (fails on
  critical), and gitleaks secret scanning. The remaining build-time high/moderate npm advisories
  are documented with exact IDs and rationale in
  [web-next/SECURITY-NOTES.md](web-next/SECURITY-NOTES.md).

Every command above is reproducible locally; see DEPLOY.md.

## Deploy & monetize

See **[DEPLOY.md](DEPLOY.md)** for the full runbook (Render/Docker, persistence options,
GitHub Actions cron, Resend, and LemonSqueezy), and **[PRODUCTION.md](PRODUCTION.md)** for the
security/ops hardening posture (SSRF, probe isolation, rate limits, failure recovery, backups,
billing lifecycle — with what's verified vs. deployment-provided). Pricing:

| Plan | Price | Monitors | Frequency | Alerts | API |
|---|---|---|---|---|---|
| Free | $0 | 1 | daily | — | — |
| Pro | $19/mo | 10 | hourly | ✓ | ✓ |
| Team | $49/mo | unlimited | 15 min | ✓ | ✓ |

---

## Roadmap (deferred until customers ask)

Full status pages with custom domains · Microsoft Teams channel · `mcpwatch check` CLI for
CI/CD · GitHub App with PR checks · admin panel · control-plane / probe-worker split.

---

Designed & built by **M. Junaid Shahid** — Lahore, Pakistan ·
[GitHub](https://github.com/junaidshahid-dev) · [Portfolio](https://junaidshahid-dev.github.io)
