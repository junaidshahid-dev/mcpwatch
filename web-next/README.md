# MCPWatch — Next.js front-end

The production front-end: **Next.js 15 (App Router) + React 19 + TypeScript**, deployable to
Vercel. It renders the landing page and the dashboard, and proxies API traffic to the FastAPI
backend so the existing httpOnly cookie session stays same-origin.

## How auth works across the split

The browser only ever talks to **this** origin. `next.config.mjs` rewrites (proxies)
`/api/*`, `/badge/*`, `/status/*`, `/webhooks/*` to the backend at `API_ORIGIN`. Because a
rewrite is a transparent proxy (not a redirect), the backend's `Set-Cookie` lands on this
origin — so no CORS, no `SameSite=None`, no token juggling. Same cookie auth as before.

## Local development

```bash
# 1) run the backend (from the repo root)
python run.py                 # http://localhost:8000

# 2) run the front-end
cd web-next
npm install
npm run dev                   # http://localhost:3000  (API_ORIGIN defaults to :8000)
```

## Build

```bash
npm run build && npm run start
```

## Deploy to Vercel

1. Push the repo to GitHub.
2. In Vercel, **New Project → import the repo**, set **Root Directory = `web-next`**.
3. Add an env var **`API_ORIGIN`** = your deployed backend URL (e.g. `https://mcpwatch.onrender.com`).
4. Deploy. Vercel auto-detects Next.js and applies the rewrites.

On the backend, set `MCPWATCH_BASE_URL` to the **public https URL** so the session cookie is
issued `Secure`.

## Structure

```
app/
  layout.tsx        fonts (Sora/Manrope/IBM Plex Mono) + metadata + favicon
  globals.css       the design system (dark "telemetry" theme, 3D buttons, animations)
  page.tsx          landing (server component)
  CopySnippet.tsx   small client component for the badge copy button
  dashboard/page.tsx  the app (client): auth gate, monitors, checks, badges
next.config.mjs     API proxy rewrites
```
