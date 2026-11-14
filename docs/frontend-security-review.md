# Frontend security review (2026-11-14)

Scope: the React app in `frontend/` (staff console and patient portal). One row per check. **Pass** = checked and fine. **Fixed** = a gap was found and closed in this pass. **Listed** = left as it is, with the reason. Source-level checks are pinned by `tests/security-patterns.test.ts`, which fails if one of them regresses.

Test runs for this pass (from the uppercase `E:\` path): unit suite 1278 tests in 58 files, all passing. Whole Playwright suite: 390 passed, 1 failed (see row 1.2).

## 1. Test health

| # | Check | Result | Detail |
|---|---|---|---|
| 1.1 | Whole unit suite | pass | 1278 of 1278 (58 files). |
| 1.2 | Whole Playwright suite | pass, one stall explained | 390 of 391. The one failure was `polish.spec.ts` "nurse: patient-record-medications [ta]": a `page.evaluate` call stalled for the full 30 s. It passes alone (3.8 s) and the screen has no loop or wait that could hang. The run overlapped with a second `npm audit` I had started, so the cause is machine contention in that window, not the screen. No timeout was raised. The whole polish spec was then re-run on its own with nothing else running: 92 of 92 passed. |
| 1.3 | Type check, lint, generated API types | pass | `tsc`, `eslint` clean; `check:api` in sync with `openapi.snapshot.json`. |

## 2. Dependencies

| # | Check | Result | Detail |
|---|---|---|---|
| 2.1 | `npm audit --omit=dev` (what ships) | pass | 0 vulnerabilities. |
| 2.2 | `npm audit` (including dev tools) | listed | 7 moderate findings, all in the lint toolchain: `typescript-eslint` and its packages, through `minimatch` (a denial of service in glob matching, GHSA-g3hv-75wg-m249). None of it is in the browser bundle or runs on user input. The offered fix moves `typescript-eslint` to a different major version; it was not forced because that changes the lint rules for the whole repo. Revisit when `typescript-eslint` publishes a release that depends on a patched `minimatch`. |

## 3. Content Security Policy

| # | Check | Result | Detail |
|---|---|---|---|
| 3.1 | Policy against what the app loads | pass | `default-src 'none'`; scripts, styles, fonts and images from the site itself (`data:` images only for the authenticator QR code); `connect-src` is the site, the Supabase project and the API only; `object-src 'none'`; `base-uri` and `form-action` the site. `e2e/csp.spec.ts` runs every built page with the policy enforced and fails on any violation; the polish and portal specs also fail on a violation on every screen. |
| 3.2 | Inline styles | fixed | Two new screens used a React `style` prop. That works under the policy (React sets styles through the style object, which the policy allows), but it is the one place a policy change would have broken silently. Both now use a CSS class (`.pre-wrap`), and a test fails if an inline style is added again. |
| 3.3 | `frame-ancestors`, `report-uri` | listed | A `<meta>` policy cannot carry them. `dist/csp-policy.txt` is the same policy for a host that sends it as a header; the host should send it with `frame-ancestors 'none'` (clickjacking). A deployment step, not frontend code. |
| 3.4 | Voice | listed | No audio connection exists. Opening one needs new `connect-src` and microphone permissions and the owner's approval (a data-processing agreement); the policy is deliberately not widened in advance. |

## 4. Unsafe code patterns (`tests/security-patterns.test.ts`)

| # | Check | Result | Detail |
|---|---|---|---|
| 4.1 | `innerHTML`, `outerHTML`, `dangerouslySetInnerHTML`, `insertAdjacentHTML`, `document.write`, `eval`, `new Function` | pass | None in `src/`. Notices, messages and complaint text are shown as plain text (tests pin that `<b>` in a notice or message is not interpreted). |
| 4.2 | URLs built by joining strings | pass | All API calls go through the typed client with path and query parameters. No `window.open`, no assignment to `location`, no template-string `href`. The only generated link is the download of the patient's own file, made from a Blob and revoked at once. |
| 4.3 | Console output | pass | No `console.*` call in `src/`, so no token or patient data can reach a log. |
| 4.4 | Page titles | pass | Fixed translated names only; no patient data. |
| 4.5 | Browser storage | pass | Only: the language choice (`localStorage`), the active role for the tab (`sessionStorage`: a clinic id and a role, no patient data, cleared at sign-out), and supabase-js's own session record. A test fails if any other file touches storage, IndexedDB or cookies. |
| 4.6 | Patient data in URLs | listed | The staff app routes by hash and puts record, referral and task ids (UUIDs) in the address, so they show in browser history. They are identifiers, not names or contact details, and a fragment is never sent to a server or to a referrer. Patient search is a POST so names and numbers stay out of every URL. Moving to opaque handles would need backend work and is not planned. |
| 4.7 | Referrers and external links | pass | No external link or navigation exists; every request goes to the three allowed origins. |

## 5. Error messages

| # | Check | Result | Detail |
|---|---|---|---|
| 5.1 | Backend text never shown | pass | Every failure becomes a fixed problem kind (`toProblem`); the backend's `detail` is kept only to match a short list of known phrases that the screen then says in its own words. Every unit and browser fixture puts a sentinel string in its error bodies and fails if it reaches the page. A test fails if any screen reads `error.message` or `stack`. |
| 5.2 | Audit page values | listed | The admin audit page shows recorded values as data (that is its job), not as error text. |
| 5.3 | Error boundary | pass | Shows a fixed message; nothing about the error. |

## 6. Data kept in the browser

| # | Check | Result | Detail |
|---|---|---|---|
| 6.1 | Data of a closed screen | fixed | Both query caches kept a closed screen's data in memory for five minutes. Both now keep nothing once a screen closes (`gcTime: 0` for reads and writes), and a test pins it. Cost: a screen read again after navigating back is fetched again. |
| 6.2 | Sign-out clears everything | pass | Staff and patient sign-out (pressed, ended elsewhere, a 401, or idle) clear the whole query cache, the active role and every screen's own state (the screens unmount). Existing browser tests check that the session does not come back on reload and that the browser holds only the sign-in session afterwards. |
| 6.3 | Download of my data | pass | The file is built in memory, handed to the browser and dropped; the screen keeps only counts and the consent history, which go with the screen. |
| 6.4 | Text typed into forms | pass | Complaint, message and request text is cleared from the page once sent. |
| 6.5 | Active role cell after staff sign-out | listed | A small in-memory value (a clinic id and a role) stays until the next role is chosen. It is not patient data and the header that would send it is off. |
| 6.6 | Sign-out scope (staff) | listed | Sign-out ends this browser's session only (`scope: local`); the same person's other devices stay signed in until their sessions end. Revoking all sessions is a Supabase call that belongs with the devices-and-sessions route (backend request 23). |

## 7. Idle timeout and sign-out (BUILD-SPEC 18 to 21)

| # | Check | Result | Detail |
|---|---|---|---|
| 7.1 | What the spec says | listed | Sections 18 to 21 require a mandatory second factor for staff, step-up before sensitive actions and a device and session list for patients. They give NO idle duration for staff or patients. |
| 7.2 | Idle sign-out mechanism | fixed (built, OFF) | There was none. It is built for both audiences: after the configured minutes with no pointer, key, touch or scroll activity (checked by clock, so a hidden tab is handled when it returns) the session ends like any other sign-out and the sign-in screen says so ("Please sign in again" for staff; a plain note for patients). The duration is a privacy and safety decision, so none is chosen: `IDLE_SIGN_OUT_MINUTES` in `src/shared/idle.ts` is `null` for both, and a test pins that. Backend request 33 asks the owner for the numbers. |
| 7.3 | Step-up and second factor | pass | Built earlier: a mandatory authenticator code, enrolment at first sign-in, and a step-up prompt that shares one dialog among concurrent requests. |
| 7.4 | Warning before an idle sign-out | listed | Not built: whether a person gets a "still there?" prompt, and how long it lasts, belongs with the duration the owner chooses. |

## 8. Other

| # | Check | Result | Detail |
|---|---|---|---|
| 8.1 | Secrets in the bundle | pass | Only `SUPABASE_URL`, `SUPABASE_PUBLISHABLE_KEY` and `CAREFLOW_API_BASE_URL` are exposed (an allowlist); the build stops if a value looks like a secret key. |
| 8.2 | Dev-only switches | pass | The dev skip of the staff second factor is off unless the shell sets it, only in the dev server, never in a build; the dev claim panel is absent from the production bundle (a browser test checks). |
| 8.3 | Idempotency keys on writes | pass | The contract test fails if any write lacks one (the two exceptions are the routes that take none). |
| 8.4 | Role-based visibility | pass | Checked per screen by the unit tests; the backend enforces roles regardless. |
