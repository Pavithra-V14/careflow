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

## Staff sign-in (F2)

- **Flow:** email and password (Supabase Auth), then an authenticator-app code. A user with no verified factor enrols one at
  first login; everyone else gets a challenge at every sign-in. Staff are invitation-only: there is no staff sign-up screen,
  link or call (a test enforces this). Failures show one generic message each and never say what was wrong.
- **Token storage (what supabase-js does by default, used unchanged):** the session, including the access token AND the
  refresh token, is kept in the browser's `localStorage` under `sb-<project>-auth-token`, refreshed automatically before it
  expires, and shared between tabs. Nothing else here stores a token. The trade-off: script running on the page could read it,
  which is why the production CSP forbids inline and third-party script. Sign-out ends this browser's session only
  (`scope: local`). The only other storage is the chosen language (`localStorage`) and the active role (`sessionStorage`, a
  clinic id and role name, never a token).
- **Step-up:** a 401 with `WWW-Authenticate: step-up` opens a dialog asking for a fresh authenticator code, then the request
  is retried once with the new token and the same idempotency key. Every other 401 drops the local session and shows the
  sign-in screen with a generic "please sign in again". This depends on the backend exposing `WWW-Authenticate` to the
  browser (see `../docs/frontend-backend-requests.md`, item 1).
- **Content Security Policy (production build only):** `csp.ts` builds one policy, injected as a `<meta>` tag in every built
  page and written to `dist/csp-policy.txt` for a host that can send it as a header (a header can also carry
  `frame-ancestors`, which a meta tag cannot). `connect-src` is `'self'`, the Supabase origin and the API origin, taken from the
  build environment. It was validated report-only first, then enforced; e2e fails on any violation.
- **Claim spike (development builds only):** after signing in, the console shows a panel that decodes the access token in code
  and prints only the STRUCTURE of `aal`, `amr` and `iat` with values blanked, plus whether `amr` timestamps moved after a refresh.
  It never shows the token. The production bundle does not contain it (checked in `check:bundle`).

### Remaining prerequisites for a LIVE sign-in test against the dev project

1. `frontend/.env.local` in place with `SUPABASE_URL`, `SUPABASE_PUBLISHABLE_KEY` and `CAREFLOW_API_BASE_URL` (you create it).
2. TOTP MFA enabled in the dev Supabase project.
3. A dev staff account created by the backend session's seed script.

Until all three exist, everything about sign-in is **mock-only**: the unit tests (`tests/staff-auth.test.tsx`, `claim-shape`,
`staff-a11y`) use an in-memory auth fake, and the browser tests (`e2e/staff.spec.ts`) run the real built app and the real
supabase-js against faked Supabase and API hosts (`e2e/mockBackend.ts`). They prove this app's logic, not Supabase's or the
backend's behaviour. The `aal` / `amr` shape in the mock is a guess; the spike above is what settles it.

## Notes

- TypeScript 7 is not used yet: typescript-eslint (`<6.1.0`) and openapi-typescript (`^5.x`) do not support it. Revisit when both do.
- Every Tamil string is a generic UI label written without a native reviewer and **needs native-speaker review**. No clinical,
  consent, privacy-notice or legal wording is written by the frontend session; those are marked placeholders until supplied.
- Browser storage use is listed under "Token storage" above; the language choice is a per-viewer convenience wrapped in try/catch.
- Backend requests live in `../docs/frontend-backend-requests.md`.

## Pre-launch list (owners, not code)

- The owner and counsel write the about text, the privacy promise, the grievance contact and the privacy notice. Until then
  these render as marked `[PLACEHOLDER: ...]` blocks.
- Native Tamil review of all strings, including the 108 emergency banner and the F1 sentences.
- Clinician approval of the emergency banner.
- Features shown with a "Coming soon" label (`src/patient/features.ts`) are switched to built only when their page exists;
  `tests/features.test.tsx` enforces that.
