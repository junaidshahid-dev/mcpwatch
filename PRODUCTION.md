# MCPWatch — production hardening

What was hardened before the first production deploy, how it's verified, and — honestly — what
is implemented in-process vs. what the deployment/platform must provide. The test gate
(`tests/test_mcpwatch.py`, 33 tests) runs on **both SQLite and Postgres**.

## 1. SSRF / untrusted targets
- Private, loopback, link-local, multicast, reserved and unspecified IPs are refused for `http`
  monitors (`security.validate_http_target`). Verified: `test_ssrf_*`.
- **DNS rebinding**: `GuardedTransport` re-resolves and re-validates the host at *connect* time,
  not just at monitor creation — closing the check→connect window.
- Redirects are **not followed** (a 3xx can't escape the checks).
- Granular connect/read/write/pool timeouts; response body is **streamed and capped** so a huge
  body can't exhaust memory. Verified: `test_response_body_cap`.
- `stdio` monitors run a command list via `subprocess` (never `shell=True`) and are **disabled
  on the hosted plane** (`MCPWATCH_ALLOW_STDIO=false`). Verified: `test_stdio_can_be_disabled`.

## 2. Probe isolation
- **Per-tenant concurrency cap** (`limits.probe_guard`) — one org can't starve workers or
  amplify traffic at a third party. Verified: `test_probe_concurrency_guard`.
- **Overall wall-clock timeout** per probe (`_guarded_probe`) returns control even if a server
  hangs mid-response.
- *Deployment-provided*: for true CPU/memory isolation and reliable process reaping, run probes
  in a **separate worker service/container** with platform resource limits (cgroups). The code
  is structured for this — `service.run_check` is the unit a worker would call off a queue.

## 3. Secrets
- Passwords: scrypt-hashed. Sessions & **API keys: only SHA-256 hashes stored**; plaintext shown
  once. API-key **rotation** endpoint (`POST /api/keys/{id}/rotate`).
- URL credentials are scrubbed from errors/logs (`security.scrub_text`). Verified:
  `test_secrets_not_leaked_in_errors`.
- Webhook signatures are HMAC-verified (`billing.verify_webhook`). `.env` and `*.db` are
  git-ignored; no secrets in metrics (route labels collapse ids) or frontend responses.

## 4. Rate limiting
Sliding-window limits on **signup, monitor creation, manual probes, the v1 API, and webhooks**
(`limits.py`, wired in `api.py`). Verified: `test_api_signup_rate_limited`, `test_rate_limit_trips`.
*Deployment-provided*: for multiple app instances, back the limiters with Redis (same call sites).

## 5. Background jobs
- The scheduler runs due checks **bounded-parallel** (`run_due_checks`, thread pool), each under
  its tenant's probe cap. A single slow monitor no longer blocks the batch.
- *Deployment-provided*: the durable `API → queue → worker → DB → alert` topology. Today the
  cron endpoint drives checks; moving to a queue (e.g. Redis/RQ) is a drop-in for `run_due_checks`.

## 6. Failure recovery (verified with real injection)
- **Postgres disappears** → `/health` stays up; DB endpoints and `/ready` return **503**, not a
  500 or a hang; when Postgres returns the pool **self-heals to 200**. Verified live by stopping
  and starting the Postgres container under a running server.
- Duplicate webhook → processed once (`test_billing_full_lifecycle`, `test_billing_signature_and_idempotency`).
- Email provider failure → caught, never breaks a check (`alerts.deliver`).
- Scheduler/worker crash → next tick reprocesses due monitors; per-monitor errors are isolated.
- Connection-pool exhaustion → `MCPWATCH_PG_POOL_TIMEOUT` makes requests fail fast (→503) instead
  of hanging.
- Deployment restart → migrations are idempotent; sessions live in the DB.

## 7. Observability
`/health`, `/ready` (touches DB + engine), `/metrics` (Prometheus), structured logs with a
per-request correlation id, and a `mcpwatch_db_unavailable_total` counter.

## 8. Backups + migrations (verified)
- Migrations **auto-apply on startup** and are **idempotent** (re-running is a no-op).
- Fresh-DB migration produces exactly 15 tables, correct indexes/FKs.
- **Backup/restore drill**: `pg_dump` → drop schema → restore → data intact (verified).
- Never edit production DBs by hand — schema changes ship as new migration files.

```bash
# backup / restore (Postgres)
pg_dump "$DATABASE_URL" > backup.sql
psql "$DATABASE_URL" < backup.sql
```

## 9. Billing lifecycle
The full sequence — created → renewal(updated) → failed payment (no downgrade) → cancellation
(downgrade) — drives the right plan state, and every webhook is **idempotent**. Verified:
`test_billing_full_lifecycle`. *Deployment-provided*: run the same flow once in the LemonSqueezy
sandbox against your live store ids.

## 10. End-to-end production chain
`test_e2e_incident_alert_recover` exercises: register → monitor → probe → store → **down →
incident → alert delivered → recover → incident resolved** → badge/status updated. After deploy,
run the same flow against the public URL.

---

### What "33/33" does and doesn't mean
The gate proves the tested invariants — including SSRF, tenant isolation, concurrency, failure
recovery, backups and billing. It does **not** replace, and this pass does not claim: a
separate hardened worker tier with OS-level resource limits, Redis-backed limits for horizontal
scale, or a live billing-sandbox run. Those are called out above as deployment-provided and are
the next steps once MCPWatch is running publicly.
