# Deploying MCPWatch

The runbook to take MCPWatch live. Steps needing **your** accounts/secrets are marked 🔑 — those
can't be automated for you. Everything else is wired.

## Status

- ✅ **Postgres data layer** — the app runs on Postgres/Supabase when `DATABASE_URL` is set, and
  on SQLite otherwise. Verified against a real Postgres 16: fresh-DB migration (15 tables, FKs,
  indexes, idempotent), the full 26-test gate, a route-wide authorization audit, and concurrency
  tests (parallel writers, duplicate webhooks, racing incident-open).
- ✅ Connection pooling, transactions, soft-deletes, audit logs, migrations that **auto-apply on
  startup** (no manual SQL step).
- ✅ Dockerfile, `render.yaml` (provisions managed Postgres), `Procfile`, GitHub Actions cron.
- ✅ Hosted security defaults baked in: `MCPWATCH_ALLOW_STDIO=false`,
  `MCPWATCH_SSRF_ALLOW_PRIVATE=false`.

## Environment separation

- **Dev**: no `DATABASE_URL` → SQLite file. Zero setup.
- **Production**: set `DATABASE_URL` → Postgres. Secrets come from the platform's env store; the
  `.env` file is git-ignored and no credentials are committed.

## Option A — Render (managed Postgres), fastest

1. 🔑 Create a [Render](https://render.com) account and connect the GitHub repo.
2. **New → Blueprint**, select this repo. `render.yaml` provisions a Postgres database + the web
   service and wires `DATABASE_URL` between them. `MCPWATCH_SCHEDULER_TOKEN` is auto-generated.
   Migrations apply automatically on first boot.
3. After the first deploy, 🔑 set `MCPWATCH_BASE_URL` to the service URL and redeploy.
4. Verify externally: `GET /health` → `{"ok":true}`, `GET /ready` → `{"ready":true}`,
   `GET /metrics` → Prometheus text.

## Option B — Supabase Postgres + any host

1. 🔑 Create a [Supabase](https://supabase.com) project. Copy the **Connection Pooling** URL
   (Project Settings → Database) into `DATABASE_URL`.
2. Deploy the container anywhere (Railway / Fly / a VPS):

   ```bash
   docker build -t mcpwatch .
   docker run -p 8000:8000 \
     -e DATABASE_URL="postgresql://...supabase.co:6543/postgres" \
     -e MCPWATCH_BASE_URL=https://your-domain \
     -e MCPWATCH_SCHEDULER_TOKEN=$(openssl rand -hex 24) \
     mcpwatch
   ```

   Migrations run on startup; no manual SQL.

## Scheduling the checks 🔑

The app is stateless; an external cron drives checks via `/internal/run-due`.

- **GitHub Actions** (included): add repo secrets `MCPWATCH_URL` and `MCPWATCH_SCHEDULER_TOKEN`;
  `.github/workflows/cron.yml` fires every 15 minutes.
- **or [cron-job.org](https://cron-job.org)**: POST to `<your-url>/internal/run-due` with header
  `x-scheduler-token: <token>`.

## Email alerts 🔑

Create a [Resend](https://resend.com) account, verify a domain, set `RESEND_API_KEY` and
`MCPWATCH_ALERT_FROM`. Until then, alerts are logged (the app still runs).

## Billing 🔑 (LemonSqueezy — pays out to Pakistan)

1. Create a store; add subscription products **Pro $19/mo** and **Team $49/mo**.
2. Set `LEMONSQUEEZY_STORE_URL` and the two `LEMONSQUEEZY_VARIANT_*` ids.
3. Add a webhook → `<your-url>/webhooks/lemonsqueezy`; put its signing secret in
   `LEMONSQUEEZY_WEBHOOK_SECRET`. Webhook handling is signature-verified and idempotent.

## Front-end (Next.js on Vercel) 🔑

The `web-next/` app is the production front-end (Next.js 15 + React 19). It proxies API traffic
to the backend, so cookie auth stays same-origin.

1. 🔑 In [Vercel](https://vercel.com): **New Project → import the repo**, set **Root Directory =
   `web-next`**.
2. Add env var **`API_ORIGIN`** = your deployed backend URL (from Option A/B above).
3. Deploy. Then set the backend's `MCPWATCH_BASE_URL` to the **public https URL** so the session
   cookie is issued `Secure`.

Local: `python run.py` (backend :8000) + `cd web-next && npm install && npm run dev` (:3000).
See `web-next/README.md`. The FastAPI app also still serves the plain-HTML landing/dashboard at
`/` — so the backend is fully usable on its own if you skip Vercel.

## Pushing to GitHub 🔑

```bash
cd MCPWatch
git init && git add -A && git commit -m "MCPWatch: monitoring for MCP servers"
git remote add origin https://github.com/junaidshahid-dev/mcpwatch.git
git push -u origin main
```

## Verifying a Postgres deploy locally (optional)

```bash
docker run -d --name pg -e POSTGRES_PASSWORD=pw -e POSTGRES_DB=mcpwatch -p 55432:5432 postgres:16-alpine
DATABASE_URL="postgresql://postgres:pw@localhost:55432/mcpwatch" python tests/test_mcpwatch.py
```
