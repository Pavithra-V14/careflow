# Frontend to backend requests

The frontend session never changes `careflow/`, `db/`, `tests/` or `scripts/`. Anything it needs from the backend is written
here, with why it is needed and what the frontend does until it exists.

## Open

### 1. Production CORS (the backend session is already handling it)

In development the Vite dev server proxies `/v1` and `/readyz` to the API, so no CORS is involved. A production browser at the
frontend's origin calling the API's origin needs:

- `Access-Control-Allow-Origin` set to the exact frontend origin(s), never `*` (requests carry a bearer token).
- `Access-Control-Allow-Headers` including `Authorization`, `Content-Type`, `X-Idempotency-Key` and **`X-CareFlow-Active-Role`**
  (the active-role request header proposed in `docs/design/active-role.md`; without it the browser blocks every staff request at preflight).
- `Access-Control-Allow-Methods` including `GET`, `POST`.
- **`Access-Control-Expose-Headers: WWW-Authenticate`.** Without it the browser hides this header from the page's JavaScript,
  so the frontend cannot tell a step-up 401 from any other 401. This is the one that is easy to miss.

### 2. `GET /v1/iam/me`: clinic name, clinic origin, and an operating-profile indicator

Today each role grant is `{clinic_id, role}` only. The staff shell needs, per role grant:

- `clinic_name` (string): shown in the shell. Until it exists the frontend shows a shortened clinic id.
- `clinic_origin` (`"live"` or `"test"`): the persistent LIVE / TEST badge (BUILD-SPEC 19.3) is derived from this. Until it
  exists the frontend shows **"environment unknown"**: it never guesses LIVE or TEST (CLAUDE.md rule 3, unknown is not a value).

And one field at the top level of the response (or on `/readyz`, whichever fits the backend better):

- an operating-profile indicator, `"demo"` or `"clinical"` (BUILD-SPEC 2), so the frontend can degrade visibly and not silently
  when running `clinical` without an approved external route.

`/readyz` currently documents an empty response schema, so nothing there can be generated into the client either.

### 3. The active role must reach the server for least privilege to be real

The staff shell makes a person with several role grants pick ONE active role (BUILD-SPEC 19.3), but today that choice only
changes what the frontend shows: the API still evaluates all of the account's grants on every request. For the choice to
limit what the person can do, the backend would need to accept the chosen grant (for example a request header naming the
clinic and role) and authorise against only that grant. The frontend sends nothing until the backend defines this; please
decide the mechanism. Until then "active role" is a display and navigation choice, not a permission boundary.

A design proposal (no code yet, awaiting the owner's OK) is in `docs/design/active-role.md`: an `X-CareFlow-Active-Role` header, verified
against the live grants in the database and used only to narrow. If adopted, production CORS must also allow that request header.

### 4. Step-up claim shape (not a code change: a spike)

Tracked in `docs/design/step-up-spike.md`. The frontend will decode a real access token's `aal` / `amr` / `iat` structure in
code once F2 sign-in works, and report the structure only (values blanked) so it can be pasted to the backend session.

### 5. F3 console: what the screens need that the snapshot does not give them yet

None of these blocks the F3 screens (they handle the absence), but each leaves a screen showing less than it could, or
depends on a guess. All F3 screens run only against mocks until a live backend is reachable.

- **Patient search labels.** The search route returns ids, `verified`, `registered_via` and `matched_kinds` only. The screen also
  understands `label` (per hit) and `labels_withheld` / `truncated` (on the response), as the per-row label read in
  `docs/design/patient-view-fields.md` would add them. **Those three names are my guess, not a contract.** Please confirm
  the names and types, or tell me what to read instead. Until then they are mock-only.
- **Offer draft.** `GET /v1/staff/scheduling/offers/draft` returns an `Envelope` whose `data` is `object | array` and whose `reason`
  is free text (it can name clinician-owned settings). The screen shows only the `status` word and marks candidate details and
  sending "Coming soon". A typed `data` (the ranked candidates) and a closed vocabulary for `reason` would let it show more
  without ever showing backend text.
- **Scan status after the upload.** `scan` (`clean`, `not_scanned`, `not_checked`) comes back only from `documents/register`. The
  referral list and detail do not carry it, so once the person leaves the upload screen the console cannot show it again.
  Adding it to `ReferralOut` / `ReferralDetailOut` would let "not scanned yet" stay visible. The screen treats every word
  except `clean` as not clean.
- **A signed read URL** for the document a reviewer is looking at (item 10 in `frontend-contract.md`): the detail screen shows no
  file today.
- **Typed task provenance and kinds.** `kind` is a free string and `provenance` an untyped object. The inbox ships a
  hard-coded list of the kinds the resolve route closes (`referral_hold`, `unmerge_review`, `referral_dead_letter`,
  `identity_confirmation_revoked`) and of the urgent kinds (`red_flag`, `screening_unassessable`), copied from
  `careflow/review_tasks.py` and the 0013 migration. If the backend exposed them (an enum, or a `resolvable` flag per task),
  the copy could go. The backend still decides: a wrong copy only shows or hides a button.
- **Resolve takes no reason.** The screen shows "PLACEHOLDER - clinician to define" where reason codes would go and sends none.
  If a clinician decides a reason is required, the route needs a body field first.
- **Closed words for translation.** `run_state` and the proposal `mode` are strings; the console shows them as given (humanised).
  An enum in the schema would let each get a reviewed Tamil label.
- **Upload storage origin.** The console refuses to send a file to any host other than the Supabase project origin
  (`SUPABASE_URL`), and the CSP's `connect-src` allows only that and the API origin. If storage will live elsewhere, say so:
  `csp.ts` and the check in `UploadPage.tsx` both change.
- **Names.** Lists show short ids (patients, holds, tasks); names are item 1 of `frontend-contract.md`.

### 6. Type `Envelope.data` for tasks and offers, and name the reason words (requested by the owner)

The backend should give the console typed shapes instead of free text, so the UI never has to treat them as strings:

- **`Envelope.data`** (currently `object | array | null`) should be a typed schema per route: for the offer draft, the ranked
  candidates and the draft message; for review tasks, a typed `provenance` per task kind (the document id of a referral hold,
  the rule id of a red flag, and so on) in place of the untyped object.
- **`Envelope.reason`** (currently free text, for example `not_configured: ...` plus the names of missing clinician-owned settings)
  should be a closed set of reason words, listed as an enum in the schema, with any setting names in a separate typed field.
  Each word then gets a reviewed English and Tamil label in the console, and the console shows no backend text.
- **`kind`** on review tasks (and on holds and merges where applicable) should be an enum, so the console can label each kind
  and stop carrying its own copy of which kinds can be resolved or are urgent.

Until then the console shows only the `status` word of an envelope, never `reason` or `data`, and marks candidate details and
sending "Coming soon".

## Decisions recorded here for later readers

- **TypeScript 7 is not used yet** because typescript-eslint (`<6.1.0`) and openapi-typescript (`^5.x`) do not support it;
  revisit when both do. The frontend pins TypeScript 6.0.3, with a scoped npm `overrides` entry satisfying openapi-typescript's
  peer range (not `--legacy-peer-deps`).
- **ESLint is 9.39.5, not 10**, because eslint-plugin-jsx-a11y 6.10.2 (the latest release) supports ESLint up to 9.
- Versions published in the last 7 days were avoided at install time; i18next, react-i18next, vite, react-router,
  @tanstack/react-query and typescript-eslint are therefore one release behind the newest.
