# CareFlow frontend

React + Vite + TypeScript (strict). Two entry points, `patient/` and `staff/`, sharing one token set, one i18n setup
(English and Tamil) and one API client generated from `../openapi.snapshot.json`. See `../BUILD-SPEC.md` section 19.

## Setup

```
cd frontend
npm ci                       # ignore-scripts=true and save-exact=true come from .npmrc
npx playwright install chromium   # explicit browser download; install scripts are disabled
cp .env.example .env.local   # then fill in the three names; only the PUBLISHABLE key belongs here
npm run dev                  # http://localhost:5173/patient/  and  /staff/  (the API is proxied; no CORS in dev)
```

`.env.local` holds exactly `SUPABASE_URL`, `SUPABASE_PUBLISHABLE_KEY` and `CAREFLOW_API_BASE_URL`. `vite.config.ts` exposes
**only** those three names through an explicit allowlist (`env-allowlist.ts`); a test fails if any other name is exposed, and the
production-build check fails if a secret-looking marker appears in `dist`. The Supabase secret key is never a frontend value.

## Scripts

| Script | What it does |
|---|---|
| `npm run typecheck` / `lint` / `test` | tsc strict, ESLint (with jsx-a11y), Vitest (unit, contract, env, tokens, i18n, a11y) |
| `npm run build` | production build of both entries into `dist/` |
| `npm run check:bundle` | fails if `sb_secret_`, `SUPABASE_SECRET_KEY`, `DATABASE_URL` or `CAREFLOW_ENC_SECRET` is in `dist/` |
| `npm run gen:api` / `check:api` | regenerate `src/shared/api/schema.d.ts`; check that the committed file is not stale |
| `npm run e2e` | Playwright against the production build: axe (WCAG 2.2 AA), keyboard-only, overflow, screenshots |
| `npm run audit:prod` | `npm audit` on production dependencies, failing on high or critical |

Run one heavy tool at a time (build, then Playwright), not in parallel.

## Notes

- TypeScript 7 is not used yet: typescript-eslint (`<6.1.0`) and openapi-typescript (`^5.x`) do not support it. Revisit when both do.
- Every Tamil string is a generic UI label written without a native reviewer and **needs native-speaker review**. No clinical,
  consent, privacy-notice or legal wording is written by the frontend session; those are marked placeholders until supplied.
- Only the chosen language is remembered in `localStorage` (a per-viewer convenience, wrapped in try/catch).
- Backend requests live in `../docs/frontend-backend-requests.md`.
