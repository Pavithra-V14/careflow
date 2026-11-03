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

## Console screens (F3)

After sign-in the staff console has a role-aware navigation (`src/staff/sections.ts`) and four areas. **The navigation only hides
links a role cannot use; the backend decides.** Every page still calls the API and shows "not available to your role" on a 403.
The LIVE / TEST badge and the role stay visible on every screen.

| Area | Screens | API routes it calls (all in `openapi.snapshot.json` on `main`) |
|---|---|---|
| Referrals | queue, referral detail, document upload | `GET /v1/staff/referrals`, `GET /v1/staff/referrals/{id}`, `POST /v1/staff/documents/uploads` and `/register` |
| Tasks | inbox, task detail (acknowledge, resolve) | `GET /v1/staff/review-tasks`, `GET .../{id}`, `POST .../{id}/acknowledge` and `/resolve` |
| Patients | search, record, confirm identity | `POST /v1/staff/patients/search`, `GET .../{id}`, `POST .../{id}/confirm-identity` |
| Scheduling | providers, locations, visit types, priority levels (read-only), holds (release), offers (read-only draft) | `GET /v1/staff/scheduling/{providers,locations,visit-types,priority-levels,holds,offers/draft}`, `POST .../holds/{id}/release` |

Rules every screen follows: loading, empty, error and "not available to your role" states; fixed messages per problem kind and
never the backend's text (`src/staff/apiState.ts`); one idempotency key per intent, reused on a retry after a network, 429 or
5xx failure and dropped after an answer that will not change (`src/staff/hooks.ts`); nothing is written before a confirmation;
nothing is pre-selected; no clinical, consent or legal wording (marked placeholders instead).

- **Active-role header:** `SEND_ACTIVE_ROLE_HEADER` in `src/staff/featureFlags.ts` is `false`, pinned by a test. The backend does
  not read the header yet (`docs/design/active-role.md` is a proposal) and production CORS does not allow it.
- **Tamil:** all new strings are on `TAMIL-UNREVIEWED.md` (generated; CI fails if it is stale). The clinical task kinds stay in English.
- **Everything is mock-only until a live backend is reachable.** Unit tests use a fake backend (`tests/helpers/console.tsx`); browser
  tests use fake Supabase and API hosts (`e2e/mockBackend.ts`, `e2e/consoleApi.ts`). Fields the snapshot lacks (patient search
  `label`, `labels_withheld`, `truncated`) are mock-only guesses; see `../docs/frontend-backend-requests.md` item 5.

## F4: decisions, identity and merges

| Screen | Who | Routes | State |
|---|---|---|---|
| Referral decision (approve, reject; "edit" shown as Coming soon, clinician item 26) on the referral detail | nurse, doctor | `POST /v1/staff/referrals/{id}/review` | built, mock-only |
| Revoke an identity confirmation on the patient record | front desk | `POST /v1/staff/patients/{id}/revoke-identity` | built, mock-only |
| Possible duplicates, merges, one merge (read-only) | duplicates: front desk; merges: front desk, nurse, doctor | `GET /v1/staff/identity/possible-duplicates`, `.../merges`, `.../merges/{id}` | built, mock-only |
| Merge approve and unmerge | front desk | `POST /v1/staff/merges/{id}/approve`, `.../unmerge` | built, **OFF** behind `mergeActions`, mock-only |

Rules: nothing is chosen for the person and nothing is written before a confirmation; the decision and revoke-reason word lists
are copied from the schema and pinned to it (`tests/f4-schema.test.ts`); decision reasons are only a marked placeholder and none
is sent; a cancelled step-up says "a fresh code is needed, nothing was changed" and does not sign the person out.

**The merge-actions flag** (`src/staff/featureFlags.ts`, `mergeActionsEnabled()`) is a compile-time constant set in
`vite.config.ts`: true only for `vite build --mode e2e`, the build the browser tests use, and false for development and
production. It is not an environment variable of any name (`tests/flags.test.ts` plants several and checks). Turn it on only after
the step-up claim spike is recorded.

## Live integration pass (F5), by hand, never in CI

`npm run e2e:live` opens a headed browser against the running dev server and backend. You sign in yourself; it then walks every
screen read-only and writes `frontend/live-report/live-report.md` (git-ignored): each call's status and where the response differs
from the OpenAPI schema, with no values. `npm run e2e:live -- --record` records while you use the console yourself, for the writes
the walk never makes. It refuses to start unless the dev server and the backend answer. What each screen calls and what to check
is in `../docs/frontend-live-checklist.md`; `tests/live-tools.test.ts` pins that no workflow can start it.

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
