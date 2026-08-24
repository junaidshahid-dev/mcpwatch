# Frontend dependency advisories — documented exceptions

The CI `npm audit` step **blocks on `critical`** (none are present). The advisories below are
`high`/`moderate`, all **transitive within Next.js's build toolchain**. This file records the
exact IDs, dependency paths, why they are not reachable in MCPWatch's deployment, and the
remediation — so the accept decision is explicit and reviewable, not hand-waved.

Snapshot: `next@15.5.23`, audited on 2026-08-24. Re-evaluate on every dependency bump.

## Advisories

| ID | Severity | Package (range) | Path | Nature |
|---|---|---|---|---|
| [GHSA-6g55-p6wh-862q](https://github.com/advisories/GHSA-6g55-p6wh-862q) | high | postcss `<=8.5.11` | `next › postcss` | Arbitrary file read via attacker-controlled `sourceMappingURL` in CSS comments |
| [GHSA-r28c-9q8g-f849](https://github.com/advisories/GHSA-r28c-9q8g-f849) | high | postcss `<=8.5.17` | `next › postcss` | Path traversal in source-map auto-loading → arbitrary `.map` disclosure |
| [GHSA-qx2v-qp2m-jg93](https://github.com/advisories/GHSA-qx2v-qp2m-jg93) | moderate | postcss `<8.5.10` | `next › postcss` | XSS via unescaped `</style>` in stringify output |
| [GHSA-fxqj-rqcc-2cmp](https://github.com/advisories/GHSA-fxqj-rqcc-2cmp) | moderate | postcss `<=8.5.22` | `next › postcss` | Incomplete fix of GHSA-6g55-p6wh-862q |
| [GHSA-f88m-g3jw-g9cj](https://github.com/advisories/GHSA-f88m-g3jw-g9cj) | high | sharp `<0.35.0` | `next › sharp` | Inherited libvips CVEs: CVE-2026-33327/33328/35590/35591 |

The `next` package itself is also flagged `high` — solely because it depends on the above.
`npm audit` reports the only automated fix as `next@16.3.2` (**semver-major**).

## Why these are not reachable in MCPWatch

**PostCSS (all four):** PostCSS runs only at **build time**, over CSS **we author**
(`app/globals.css`). Every one of these advisories requires PostCSS to process
**attacker-controlled CSS** — a malicious `sourceMappingURL` comment, or untrusted `</style>`
content. MCPWatch has no build step or runtime path that feeds user-supplied stylesheets to
PostCSS. There is no PostCSS at runtime. → not exploitable.

**sharp:** `sharp` is Next's **image-optimization** dependency. MCPWatch uses no `next/image`
with untrusted or remote images — the UI is inline SVG + CSS, so `sharp` is never invoked on
attacker-controlled image input. On Vercel, image optimization runs on Vercel's platform, not
our bundled `sharp`. → not exploitable.

## Decision & remediation

- **Decision:** ACCEPT for this release. The gate blocks on `critical`; these build-time,
  non-reachable advisories are documented here rather than blocking the launch.
- **Remediation (post-launch, in a dedicated change, re-running the full CI matrix):** upgrade to
  **Next 16.x** (React 19 compatible), which pulls patched `postcss` and `sharp`. Alternatively,
  add npm `overrides` to force patched `postcss` (`>8.5.22`) and `sharp` (`>=0.35.0`) if
  compatible versions land within the Next 15.x line.
- **Review trigger:** any dependency bump, any Next upgrade, or if a new advisory reaches
  `critical` (CI will then fail, as intended).
