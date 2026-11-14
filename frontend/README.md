# CareFlow frontend

The staff console and the patient portal for CareFlow. React + Vite + TypeScript (strict). Two entry points, `patient/` and `staff/`,
share one token set, one i18n setup (English and Tamil) and one API client generated from `../openapi.snapshot.json`.
The specification is `../BUILD-SPEC.md` section 19; the working rules are in `../CLAUDE.md`.

Scope of this folder: the browser app only. It has no server code, no database access and no Docker. Anything it needs from the
backend is written down in `../docs/frontend-backend-requests.md`; what is built, what is mock-only and what is waiting is in
`../docs/frontend-status.md`.

**Status honesty.** Everything here has been tested against fakes (unit tests with a fake backend, browser tests with fake
Supabase and API hosts). Screens are marked "live-verified" in `../docs/frontend-live-checklist.md` only after someone has run them
against a real backend. Accessibility is a goal tested toward (WCAG 2.2 AA, with axe), not a claim of conformance.

## Stack and versions

Exact versions are pinned in `package.json` (`save-exact=true` in `.npmrc`; install scripts are off with `ignore-scripts=true`).
Node 22.22.0 or newer is required (`engines`).

| Area | Package | Version |
|---|---|---|
| UI | react, react-dom | 19.3.1 |
| Build and dev server | vite, @vitejs/plugin-react | 8.3.4, 6.1.2 |
| Language | typescript (strict) | 6.0.3 |
| Routing | react-router (hash routing) | 8.4.0 |
| Server state | @tanstack/react-query | 5.104.3 |
| Auth | @supabase/supabase-js (publishable key only) | 2.118.0 |
| API client | openapi-fetch, openapi-typescript (types) | 0.17.0, 7.13.0 |
| i18n | i18next, react-i18next | 26.4.7, 17.0.20 |
| Unit tests | vitest, jsdom, Testing Library, vitest-axe | 5.0.5, 30.1.3, 16.3.3, 0.1.0 |
| Browser tests | @playwright/test (Chromium), @axe-core/playwright | 1.64.1, 4.14.0 |
| Lint | eslint, typescript-eslint, eslint-plugin-jsx-a11y | 9.39.5, 8.71.2, 6.10.2 |

TypeScript 7 is not used yet: typescript-eslint and openapi-typescript do not support it. Revisit when both do.

## Folder layout

```
frontend/
  patient/, staff/          HTML entry pages (plus patient/sign-in, sign-up, privacy)
  src/
    patient/                the patient portal (pages, portal components, features.ts "Coming soon" list)
    staff/                  the staff console
      auth/                 Supabase sign-in, second factor, step-up dialog
      session/              active clinic and role
      pages/, screens/      one file per screen
      ui/, components/      shared pieces (confirm dialog, write problems, query states)
      sections.ts           navigation and which role sees which area
      featureFlags.ts       every switch, each OFF by default
      apiState.ts, hooks.ts fixed error messages; idempotent writes
    shared/
      api/                  client.ts (one method per route) and schema.d.ts (generated; do not edit)
      i18n/                 English and Tamil JSON, split by batch (f4 ... f19) and merged in index.ts
      tokens/, config.ts, clinicTime.ts
  tests/                    Vitest: unit, contract, source guards, a11y, i18n, dead-code
  e2e/                      Playwright against the mock-backed build (mockBackend.ts, consoleApi.ts, portalMock.ts)
  e2e-live/                 the manual live pass (never in CI)
  scripts/                  gen-api, bundle check, Tamil list, dead-code check, e2e servers
  env-allowlist.ts, csp.ts  the three exposed environment names; the Content Security Policy
```

## Setup

```
cd frontend
npm ci
npx playwright install chromium   # explicit browser download, because install scripts are disabled
npm run dev                       # http://localhost:5173/patient/  and  /staff/
```

## Environment variables (names only)

Put these three in `frontend/.env.local` (git-ignored; `.env.example` lists the names). Only the publishable key belongs here.

- `SUPABASE_URL`
- `SUPABASE_PUBLISHABLE_KEY`
- `CAREFLOW_API_BASE_URL`

`vite.config.ts` exposes **only** these names, through the explicit allowlist in `env-allowlist.ts`; a test fails if any other
name is exposed, the build stops if an exposed value looks like a secret key, and `npm run check:bundle` fails if a secret-looking
marker appears in `dist`. The Supabase secret key is never a frontend value. In development the API is proxied through the dev
server (`/v1` and `/readyz`), so there is no CORS to configure.

Other names that appear in scripts (set in the shell, never in a file):

- `VITE_DEV_SKIP_STAFF_MFA`: the dev-only skip switch, described next.
- `LIVE_BASE_URL`: optional, where the live pass looks for the dev server (default `http://127.0.0.1:5173`).

## Feature switches and the dev-only skip switch

All are in `src/staff/featureFlags.ts` and `vite.config.ts`, and each default is pinned by a test (`tests/flags.test.ts`,
`tests/env-allowlist.test.ts`). They are compile-time constants, not environment variables, except the one below.

| Switch | Default | Notes |
|---|---|---|
| `devSkipStaffMfa` (dev-only skip switch) | **OFF** | Lets a staff member reach the console with the password alone (no authenticator enrolment, challenge or step-up prompt) on a development project. It is true only for the dev server (`vite serve`, mode `development`) **and** only when the shell that started it set `VITE_DEV_SKIP_STAFF_MFA=1`. It reads the process environment, never a file, and is false in every build. While it is on, every staff page shows a banner. The backend still enforces whatever it enforces. To turn it off, do not set the variable. |
| `mergeActions` | OFF | Merge approve and unmerge. On only in `--mode e2e` (the mock browser build). Stays off until the step-up claim spike is recorded. |
| `erasureActions` | OFF | Ask, approve and cancel an erasure. Same rule as above. |
| `SEND_ACTIVE_ROLE_HEADER` | OFF | The backend does not read the header yet. |

## npm scripts

| Script | What it does |
|---|---|
| `npm run dev` | Vite dev server |
| `npm run typecheck` | `tsc --noEmit`, strict |
| `npm run lint` | ESLint with jsx-a11y |
| `npm test` | Vitest, the whole unit suite (see the note below) |
| `npm run build` | production build of both entries into `dist/` (CSP injected, `dist/csp-policy.txt` written) |
| `npm run check:bundle` | fails if a secret marker is in `dist/` |
| `npm run gen:api` / `npm run check:api` | regenerate `src/shared/api/schema.d.ts`; check that the committed file is not stale |
| `npm run i18n:list` / `npm run check:i18n` | regenerate `TAMIL-UNREVIEWED.md`; check that it is not stale |
| `npm run e2e` | Playwright against a mock-backed build |
| `npm run e2e:live` | the manual live pass |
| `npm run audit:prod` | `npm audit` on production dependencies, failing on high or critical |

Run one heavy tool at a time (build, then Playwright), not in parallel.

## Running the tests

**Unit, contract and guard tests (Vitest, jsdom).**

```
node node_modules/vitest/vitest.mjs run                       # everything
node node_modules/vitest/vitest.mjs run tests/console-rules.test.tsx
```

`npm test` runs the same suite. On the Windows development machine it failed with "Vitest failed to find the current suite" when
started through `npx` or `npm`, and the direct `node` command above (from the `frontend` folder, with the drive letter in upper
case) worked. The suite includes: one contract case per API operation (`tests/contract.test.ts`), a check that every route is named
in the live checklist, source guards, an i18n check, a dead-code check (unused exports and unused strings fail), axe checks, and the
flag and environment pins. These tests use fake backends and prove this app's logic only. The last full run was 1117 tests in 48
files, with lint and typecheck clean.

**Browser tests (Playwright, Chromium).**

```
npm run e2e
node node_modules/@playwright/test/cli.js test e2e/f9.spec.ts -g "audit"    # a subset
```

`scripts/e2e-server.mjs` builds the app with mock origins (`e2e/mock-env.json`) into `dist-e2e` and serves it on 127.0.0.1:4173;
Playwright fakes the Supabase and API hosts at the network layer (`e2e/mockBackend.ts`, `e2e/consoleApi.ts`, `e2e/portalMock.ts`).
They check axe at 320, 768 and 1280 px, keyboard-only use (`e2e/keyboard-zoom.spec.ts`), 200% zoom, overflow, and that the
Content Security Policy is never violated. The full run is slow (roughly 150 tests, about 12 seconds each on the development
machine), so running a subset with `-g` is normal while working.

**Live pass (by hand, never in CI).**

`npm run e2e:live` opens a headed browser against your running dev server and backend. You sign in yourself; it then walks every
screen read-only and writes `frontend/live-report/live-report.md` (git-ignored): each call's status and where the response differs
from the OpenAPI schema, with no values. `npm run e2e:live -- --record` records while you use the console yourself, for the writes
the walk never makes. It refuses to start unless the dev server (`/staff/`) and the backend (`/readyz` through the proxy) answer.
What each screen calls and what to check is in `../docs/frontend-live-checklist.md`; `tests/live-tools.test.ts` pins that no
workflow can start it. Prerequisites: `.env.local` with the three names above, TOTP enabled in the dev Supabase project, and a dev
staff account created by the backend's seed script. Until then everything about sign-in is mock-only.

## Regenerating types from `openapi.snapshot.json`

The backend publishes `../openapi.snapshot.json`; the frontend never edits it.

```
npm run gen:api     # rewrites src/shared/api/schema.d.ts with openapi-typescript
npm run check:api   # fails if the committed schema.d.ts does not match the snapshot
```

`src/shared/api/client.ts` has an `OPERATIONS` table and one method per route. When the snapshot gains a route: regenerate, add the
operation and its method, add its case to `tests/contract.test.ts` (one per operation is required), name its path in
`../docs/frontend-live-checklist.md` (a test requires it), and add a screen that follows the rules in the next section. A route
the snapshot does not contain is never called.

## Rules every screen follows

- Loading, empty, error and "not available to your role" states. Navigation only hides links; the backend decides, and a 403 is
  shown in fixed words.
- Fixed messages per problem kind, never the backend's text (`src/staff/apiState.ts`).
- Every write carries an idempotency key, one per intent, reused on a retry after a network, 429 or 5xx failure and dropped after
  an answer that will not change (`src/staff/hooks.ts`). A step-up challenge asks once and retries once with the same key.
- Nothing is written before a confirmation, and nothing is pre-selected or defaulted for the person.
- No clinical, consent or legal wording is written by the frontend. Lists such as reasons and codes come from the backend; where
  no text has been supplied there is a marked placeholder.
- English and Tamil strings for everything; Tamil is marked unreviewed (next section).

## Tamil review status

**No Tamil string has been reviewed by a native speaker.** `src/shared/i18n/ta-reviewed.json` is empty, so all 1605 Tamil strings
are on `TAMIL-UNREVIEWED.md` (generated by `npm run i18n:list`; CI fails if it is stale). The Tamil was written and second-passed by
AI models, which are not native speakers. A reviewer's pack, with every string next to its English, grading of the likely problems
and a column to fill in, is `../docs/approval-pack/tamil-review.md` (read `../docs/approval-pack/tamil-review-README.md` first).
A key leaves the unreviewed list only when a reviewer adds it to `ta-reviewed.json`.

Left in English on purpose, pending a clinician or a native reviewer: the clinical task kinds, the referral decision words, the
identity-revoke reason words and every `placeholder.*` string. Wording questions for clinicians are on
`../docs/design/clinician-sheet.md`.

## Staff sign-in

- **Flow:** email and password (Supabase Auth), then an authenticator-app code. A user with no verified factor enrols one at first
  login; everyone else gets a challenge at every sign-in. Staff are invitation-only: there is no staff sign-up screen, link or call
  (a test enforces this). Failures show one generic message each and never say what was wrong.
- **Token storage (what supabase-js does by default, used unchanged):** the session, including the access token and the refresh
  token, is kept in the browser's `localStorage` under `sb-<project>-auth-token`, refreshed automatically before it expires and
  shared between tabs. Nothing else here stores a token. The trade-off: script running on the page could read it, which is why the
  production CSP forbids inline and third-party script. Sign-out ends this browser's session only (`scope: local`). The only other
  storage is the chosen language (`localStorage`) and the active role (`sessionStorage`, a clinic id and role name, never a token).
- **Step-up:** a 401 with `WWW-Authenticate: step-up` opens a dialog asking for a fresh authenticator code, then the request is
  retried once with the same idempotency key. Every other 401 drops the local session and shows the sign-in screen. This depends on
  the backend exposing `WWW-Authenticate` to the browser (`../docs/frontend-backend-requests.md`, item 1).
- **Content Security Policy (production build only):** `csp.ts` builds one policy, injected as a `<meta>` tag in every built page
  and written to `dist/csp-policy.txt` for a host that can send it as a header (a header can also carry `frame-ancestors`, which a
  meta tag cannot). `connect-src` is `'self'`, the Supabase origin and the API origin from the build environment.
- **Claim spike (development builds only):** after signing in, the console shows a panel that prints only the structure of the
  token's `aal`, `amr` and `iat` claims, with values blanked. It never shows the token. The production bundle does not contain it
  (checked by `check:bundle`).

## Console areas

The navigation (`src/staff/sections.ts`) shows an area only to the roles that can use it: referrals, tasks, patients (with
medications), identity, scheduling, alerts, dashboard, care, readiness, notes, claims, access, erasure, audit, and admin (accounts,
autonomy, red-flag rules, reviewer, cost, voice, recovery, grievances). The LIVE / TEST badge and the role stay visible on every
screen. The patient portal covers sign-in, account creation, notices, consent, activation, claiming a record, grievances, the voice setting and
download my data. Which phase built what, and which parts are mock-only or switched off, is in `../docs/frontend-status.md`.

## Known notes

- The shared JavaScript chunk is about 770 kB. This was looked at and left as it is on purpose. If the portal ever runs on slow
  mobile data, loading Tamil on demand is the fix (`../docs/frontend-status.md`).
- Backend requests (for example the author flag for rule versions, and withdraw and retire routes) are in
  `../docs/frontend-backend-requests.md`.

## Pre-launch list (owners, not code)

- The owner and counsel write the about text, the privacy promise, the grievance contact and the privacy notice. Until then these
  render as marked `[PLACEHOLDER: ...]` blocks.
- Native Tamil review of all strings, including the 108 emergency banner.
- Clinician approval of the emergency banner and the clinician-sheet items.
- Live verification of each screen against a real backend (`../docs/frontend-live-checklist.md`).
- Features shown with a "Coming soon" label (`src/patient/features.ts`) are switched to built only when their page exists;
  `tests/features.test.tsx` enforces that.
