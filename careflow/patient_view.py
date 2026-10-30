"""Decrypting a patient's demographics for staff, under the owner's rules (docs/design/patient-view-fields.md, decisions of 2026-10-30). DEMO PROFILE WITH SYNTHETIC DATA ONLY.

* LEAST PRIVILEGE. A LIST ROW shows the name, the YEAR of birth and the LAST FOUR digits of the phone (within the caller's own clinic). The FULL date of birth and the FULL phone appear only when
  ONE record is OPENED, which decrypts it for front desk, nurse and doctor of that clinic and writes an audit row (who, role, patient id; never the contents).
* NO BULK DECRYPTION. The only multi-record decrypt is the label read: at most `page_limit` rows (a hard cap of 25, an adjustable engineering setting below it), no total count, no export.
* A per-user OPEN RATE LIMIT is a REQUIRED setting with NO default (`CAREFLOW_PATIENT_VIEW_MAX_OPENS_PER_MINUTE`); unset, opening a record is unavailable (503), never unlimited.
* OFF unless `CAREFLOW_PROFILE=demo` AND `CAREFLOW_DATA_POLICY=synthetic_only` (the same gate as the demo Phase 3 placeholders). Anywhere else the routes answer 503 and nothing is decrypted.
* Nothing decrypted is logged, cached or kept: values are built per request and dropped; responses carry `Cache-Control: no-store`. A value that cannot be decrypted (key not loaded, tampered) is a fixed
  503, never a partial answer. The rate limiter is in memory and PER PROCESS (several processes multiply the limit): acceptable for the demo; a shared limiter is a later change.
* PRE-LAUNCH BLOCKER (owner decision 2026-10-30): written confirmation of this field list from the clinic's privacy / data-protection owner, before any real data.
"""

import time
import uuid
from collections import defaultdict, deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Final

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from careflow.crypto import CryptoError, DemographicVault

HARD_PAGE_CAP: Final = 25  # owner decision: a hard cap, whatever the setting says


class ViewError(Exception):
    """Fixed words only."""


class ViewDisabled(ViewError):
    """Not the demo profile with synthetic data: nothing is decrypted."""


class ViewNotConfigured(ViewError):
    """A required setting has no value."""


class RateLimited(ViewError):
    """This user has opened too many records in the last minute."""


class CannotShow(ViewError):
    """A stored value could not be decrypted (a key version that is not loaded, or a damaged value). Nothing partial is returned."""


class PatientNotFound(ViewError):
    """No such live patient is visible to the caller."""


class PatientViewSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", populate_by_name=True)

    # REQUIRED, NO DEFAULT (owner decision 2026-10-30): None means NOT CONFIGURED and opening a record is unavailable.
    max_opens_per_minute: int | None = Field(default=None, gt=0, le=10_000, validation_alias=AliasChoices("CAREFLOW_PATIENT_VIEW_MAX_OPENS_PER_MINUTE", "max_opens_per_minute"))
    # An engineering setting, adjustable DOWN from the hard cap of 25 rows per page; it can never be raised above it.
    page_limit: int = Field(default=HARD_PAGE_CAP, ge=1, le=HARD_PAGE_CAP, validation_alias=AliasChoices("CAREFLOW_PATIENT_VIEW_PAGE_LIMIT", "page_limit"))


class OpenRateLimiter:
    """At most `limit` opens per account in any 60 seconds (a sliding window, in memory)."""

    def __init__(self, limit: int, clock: Callable[[], float] = time.monotonic) -> None:
        self._limit, self._clock = limit, clock
        self._opens: dict[uuid.UUID, deque[float]] = defaultdict(deque)

    def allow(self, account_id: uuid.UUID) -> bool:
        now, window = self._clock(), self._opens[account_id]
        while window and now - window[0] >= 60.0:
            window.popleft()
        if len(window) >= self._limit:
            return False
        window.append(now)
        return True


@dataclass(frozen=True)
class PatientViewConfig:
    """What a request needs: whether the feature is on in this deployment, the settings, and the limiter (one per process)."""

    enabled: bool
    settings: PatientViewSettings
    limiter: OpenRateLimiter | None


def feature_enabled(profile: str, environ: Mapping[str, str]) -> bool:
    return profile == "demo" and (environ.get("CAREFLOW_DATA_POLICY") or "").strip().lower() == "synthetic_only"


@dataclass(frozen=True)
class PatientLabel:
    patient_id: uuid.UUID
    name: str
    birth_year: int
    phone_last4: str | None


@dataclass(frozen=True)
class PatientFull:
    patient_id: uuid.UUID
    clinic_id: uuid.UUID
    name: str
    date_of_birth: str
    phone: str | None


def _last4(phone: str | None) -> str | None:
    digits = "".join(c for c in (phone or "") if c.isdigit())
    return digits[-4:] if len(digits) >= 4 else None


def _decrypt(vault: DemographicVault, row, *, with_phone: bool) -> tuple[str, str, str | None]:
    try:
        name = vault.decrypt(bytes(row.name_ct), field="name", key_version=row.name_key_version)
        dob = vault.decrypt(bytes(row.dob_ct), field="dob", key_version=row.dob_key_version)
        phone = vault.decrypt(bytes(row.phone_ct), field="phone", key_version=row.phone_key_version) if with_phone and row.phone_ct is not None else None
    except (CryptoError, ValueError, TypeError):
        raise CannotShow("this record cannot be shown") from None  # fixed words: the error text could echo key or value details
    return name, dob, phone


_COLUMNS = "id, clinic_id, name_ct, name_key_version, dob_ct, dob_key_version, phone_ct, phone_key_version"


def require_ready(config: PatientViewConfig, *, needs_open_limit: bool) -> None:
    if not config.enabled:
        raise ViewDisabled("patient view is disabled outside the demo profile with synthetic data")
    if needs_open_limit and (config.settings.max_opens_per_minute is None or config.limiter is None):
        raise ViewNotConfigured("the per-user open rate limit is not configured")


async def open_patient(conn: AsyncConnection, vault: DemographicVault, patient_id: uuid.UUID) -> PatientFull:
    """ONE record, decrypted in full. Row-level security decides visibility; a missing, erased or invisible record is one answer. The caller has already checked the role, the limit and the gate."""
    row = (await conn.execute(text(f"select {_COLUMNS} from mpi.patients where id = :i and erased_at is null"), {"i": patient_id})).first()
    if row is None:
        raise PatientNotFound("not found")
    name, dob, phone = _decrypt(vault, row, with_phone=True)
    return PatientFull(row.id, row.clinic_id, name, dob, phone)


async def patient_labels(conn: AsyncConnection, vault: DemographicVault, clinic_id: uuid.UUID, patient_ids: list[uuid.UUID], *, page_limit: int) -> list[PatientLabel]:
    """The list-row view of at most `page_limit` records of ONE clinic: name, year of birth, last four digits of the phone. The ids beyond the cap are refused by the caller, not truncated here."""
    if len(patient_ids) > min(page_limit, HARD_PAGE_CAP):
        raise ViewError("too many records for one page")
    if not patient_ids:
        return []
    rows = (
        await conn.execute(
            text(f"select {_COLUMNS} from mpi.patients where clinic_id = :c and erased_at is null and id = any(cast(:ids as uuid[])) order by id"), {"c": clinic_id, "ids": patient_ids}
        )
    ).all()
    out = []
    for row in rows:
        name, dob, phone = _decrypt(vault, row, with_phone=True)
        out.append(PatientLabel(row.id, name, int(dob[:4]), _last4(phone)))
    return out
