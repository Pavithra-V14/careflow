# Clinic set-up routes (pre-approved 2026-11-05, built)

`POST /v1/staff/scheduling/locations`, `/providers`, `/visit-types`, `/slots`. Front desk and admin of the clinic only (404 if the clinic is not visible, 403 for nurse, doctor and every other role that can see it). `X-Idempotency-Key` required: a replay answers the original (`replayed: true`), the same key with different content is a 409. One audit row each (`setup.create_*`, ids only). Nothing is deleted. A slot that overlaps the same provider's live slot is a 409 (the database's exclusion constraint, `slots_no_provider_overlap`); a provider, location or visit type of another clinic is a 404; a time without a zone, an end before the start, or more than 24 hours is a 422.

**No migration, no grant, no policy.** Migration 0002 already lets front desk and admin insert these rows and `careflow_app` hold INSERT on the tables; this adds only the HTTP routes. Priority levels are not created here (clinician-owned, items 16 to 22). A provider is a display name only: linking a provider to a sign-in account is a later, separate step. Editing and deactivating are not built yet.

Tests: `tests/test_setup_routes.py` (offline, scripted connection) and the four route families in the role matrix (`setup_create_cases`).
