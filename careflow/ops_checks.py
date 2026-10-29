"""Read-only operational checks that need no application state (never writes, never prints more than opaque account ids).

`WORKER_EXCLUSIVITY_QUERY` is the stop-gap for migration 0024's trigger: it finds ACTIVE grants where an account holds `queue_worker` together with another role. The trigger refuses
new violations; this reports old ones (a trigger cannot see rows written before it existed). Used by scripts/verify_app_role.py and by the CI migrate job
(scripts/check_worker_accounts.py).
"""

WORKER_EXCLUSIVITY_QUERY = (
    "select account_id from iam.staff_roles where revoked_at is null "
    "and ($1::text is null or account_id in (select id from iam.accounts where origin = $1)) "
    "group by account_id having bool_or(role = 'queue_worker') and count(distinct role) > 1"
)


async def accounts_holding_worker_and_another_role(fetch, origin: str | None = None) -> list[str]:
    """`fetch(sql, *args)` is an asyncpg-style coroutine returning rows with an `account_id`. `origin=None` checks every origin; a given origin selects ACCOUNTS of that origin (all of their active grants count, whatever the grant's own origin)."""
    rows = await fetch(WORKER_EXCLUSIVITY_QUERY, origin)
    return sorted(str(r["account_id"]) for r in rows)
