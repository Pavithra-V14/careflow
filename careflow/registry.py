"""Load the model registry (`ai.model_registry`, migration 0015) as `ModelRow`s (BUILD-SPEC.md §5.1).

Read as a worker or admin account: row-level security lets only an active admin or queue_worker see the table, and the application role can
never write it. The origin filter keeps a `test` row out of a `live` gateway and the other way round. A row that fails `ModelRow`'s own
validation (an unpinned model id, say) stops the load with an error naming the row's id only: a bad registry must not silently shrink.
"""

import uuid
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from careflow.db import set_request_claims
from careflow.gateway import ModelRow

COLUMNS = (
    "id, role, provider, model_id, data_policy, priority, enabled, unit, price_in_per_1k, price_out_per_1k, "
    "shadow_in_per_1k, shadow_out_per_1k, timeout_seconds"
)


class RegistryError(Exception):
    """A registry row could not be turned into a ModelRow. The message carries the row id, never a value."""


async def load_registry(engine: AsyncEngine, account_id: uuid.UUID, *, origins: tuple[str, ...]) -> list[ModelRow]:
    if not origins:
        raise ValueError("name at least one origin to load")
    async with engine.connect() as conn, conn.begin():
        await set_request_claims(conn, {"sub": str(account_id), "role": "authenticated"})
        rows = (await conn.execute(text(f"select {COLUMNS} from ai.model_registry where origin = any(:o) order by role, priority"), {"o": list(origins)})).all()
    out = []
    for r in rows:
        try:
            out.append(
                ModelRow(
                    r.role, r.provider, r.model_id, r.data_policy, r.priority, Decimal(r.price_in_per_1k), Decimal(r.price_out_per_1k),
                    Decimal(r.shadow_in_per_1k), Decimal(r.shadow_out_per_1k), unit=r.unit, timeout_seconds=float(r.timeout_seconds),
                    enabled=r.enabled, id=r.id,
                )
            )
        except ValueError as exc:
            raise RegistryError(f"registry row {r.id} is not valid") from exc
    return out
