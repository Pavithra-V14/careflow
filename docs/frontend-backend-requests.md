# Frontend to backend requests

The frontend session never changes `careflow/`, `db/`, `tests/` or `scripts/`. Anything it needs from the backend is written
here, with why it is needed and what the frontend does until it exists.

## Open

### 1. Production CORS (the backend session is already handling it)

In development the Vite dev server proxies `/v1` and `/readyz` to the API, so no CORS is involved. A production browser at the
frontend's origin calling the API's origin needs:

- `Access-Control-Allow-Origin` set to the exact frontend origin(s), never `*` (requests carry a bearer token).
- `Access-Control-Allow-Headers` including `Authorization`, `Content-Type` and `X-Idempotency-Key`.
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

## Decisions recorded here for later readers

- **TypeScript 7 is not used yet** because typescript-eslint (`<6.1.0`) and openapi-typescript (`^5.x`) do not support it;
  revisit when both do. The frontend pins TypeScript 6.0.3, with a scoped npm `overrides` entry satisfying openapi-typescript's
  peer range (not `--legacy-peer-deps`).
- **ESLint is 9.39.5, not 10**, because eslint-plugin-jsx-a11y 6.10.2 (the latest release) supports ESLint up to 9.
- Versions published in the last 7 days were avoided at install time; i18next, react-i18next, vite, react-router,
  @tanstack/react-query and typescript-eslint are therefore one release behind the newest.
