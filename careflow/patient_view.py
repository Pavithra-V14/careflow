"""Decrypting a patient's demographics for staff, under the owner's rules (docs/design/patient-view-fields.md, decisions of 2026-10-30). DEMO PROFILE WITH SYNTHETIC DATA ONLY.

* LEAST PRIVILEGE. A LIST ROW shows the name, the YEAR of birth and the LAST FOUR digits of the phone (within the caller's own clinic). The FULL date of birth and the FULL phone appear only when
  ONE record is OPENED, which decrypts it for front desk, nurse and doctor of that clinic and writes an audit row (who, role, patient id; never the contents).
* NO BULK DECRYPTION. The only multi-record decrypt is the label read: at most `page_limit` rows (a hard cap of 25, an adjustable engineering setting below it), no total count, no export.
* Per-user LIMITS are REQUIRED settings with NO default: opens per minute (`CAREFLOW_PATIENT_VIEW_MAX_OPENS_PER_MINUTE`) and list ROWS per minute (`CAREFLOW_PATIENT_VIEW_MAX_LABEL_ROWS_PER_MINUTE`, charged by the label read AND by the labels on search hits, so
  a loop of 25-row pages is not an unthrottled bulk path). Unset, opening a record / the label read is unavailable (503) and search returns hits without labels; never unlimited.
* OFF unless `CAREFLOW_PROFILE=demo` AND `CAREFLOW_DATA_POLICY=synthetic_only` (the same gate as the demo Phase 3 placeholders). Anywhere else the routes answer 503 and nothing is decrypted.
* Nothing decrypted is logged, cached or kept: values are built per request and dropped; responses carry `Cache-Control: no-store`. A value that cannot be decrypted (key not loaded, tampered) is a fixed
  503, never a partial answer. The rate limiter is in memory and PER PROCESS (several processes multiply the limit): acceptable for the demo; a shared limiter is a later change.
* PRE-LAUNCH BLOCKER (owner decision 2026-10-30): written confirmation of this field list from the clinic's privacy / data-protection owner, before any real data.
"""

import time
import uuid
from collections import defaultdict, deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Final

from pydantic import Field
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
    model_config = SettingsConfigDict(env_file=".env", env_prefix="CAREFLOW_PATIENT_VIEW_", extra="ignore")

    # REQUIRED, NO DEFAULT (owner decision 2026-10-30): None means NOT CONFIGURED and opening a record is unavailable. Only the CAREFLOW_PATIENT_VIEW_* names are read (a generic PAGE_LIMIT in the host
    # environment must not change a privacy limit).
    max_opens_per_minute: int | None = Field(default=None, gt=0, le=10_000)
    max_label_rows_per_minute: int | None = Field(default=None, gt=0, le=10_000)
    # An engineering setting, adjustable DOWN from the hard cap of 25 rows per page; it can never be raised above it.
    page_limit: int = Field(default=HARD_PAGE_CAP, ge=1, le=HARD_PAGE_CAP)


class OpenRateLimiter:
    """At most `limit` opens per account in any 60 seconds (a sliding window, in memory)."""

    def __init__(self, limit: int, clock: Callable[[], float] = time.monotonic) -> None:
        self._limit, self._clock = limit, clock
        self._opens: dict[uuid.UUID, deque[float]] = defaultdict(deque)

    def allow(self, account_id: uuid.UUID, cost: int = 1) -> bool:
        """Charge `cost` units (an open is 1; a label page is its row count). A refused call charges nothing."""
        now, window = self._clock(), self._opens[account_id]
        while window and now - window[0] >= 60.0:
            window.popleft()
        if len(window) + cost > self._limit:
            return False
        window.extend([now] * cost)
        return True


@dataclass(frozen=True)
class PatientViewConfig:
    """What a request needs: whether the feature is on in this deployment, the settings, and the limiter (one per process)."""

    enabled: bool
    settings: PatientViewSettings
    limiter: OpenRateLimiter | None
    label_limiter: OpenRateLimiter | None = None
    misconfigured: bool = False  # a view setting has an invalid value: the view routes answer a fixed 503 (search still works, without labels)


def feature_enabled(profile: str, environ: Mapping[str, str]) -> bool:
    return profile == "demo" and (environ.get("CAREFLOW_DATA_POLICY") or "").strip().lower() == "synthetic_only"


@dataclass(frozen=True)
class PatientLabel:
    patient_id: uuid.UUID
    name: str = field(repr=False)
    birth_year: int = field(repr=False)
    phone_last4: str | None = field(repr=False)


@dataclass(frozen=True)
class PatientFull:
    patient_id: uuid.UUID
    clinic_id: uuid.UUID
    name: str = field(repr=False)
    date_of_birth: str = field(repr=False)
    phone: str | None = field(repr=False)


def _last4(phone: str | None) -> str | None:
    digits = "".join(c for c in (phone or "") if c.isdigit())
    return digits[-4:] if len(digits) >= 4 else None


def _birth_year(dob: str) -> int:
    try:
        return int(dob[:4])
    except ValueError:
        raise CannotShow("this record cannot be shown") from None  # never the value in the message


def _decrypt(vault: DemographicVault, row, *, with_phone: bool) -> tuple[str, str, str | None]:
    try:
        name = vault.decrypt(bytes(row.name_ct), field="name", key_version=row.name_key_version)
        dob = vault.decrypt(bytes(row.dob_ct), field="dob", key_version=row.dob_key_version)
        phone = vault.decrypt(bytes(row.phone_ct), field="phone", key_version=row.phone_key_version) if with_phone and row.phone_ct is not None else None
    except (CryptoError, ValueError, TypeError):
        raise CannotShow("this record cannot be shown") from None  # fixed words: the error text could echo key or value details
    return name, dob, phone


_COLUMNS = "id, clinic_id, name_ct, name_key_version, dob_ct, dob_key_version, phone_ct, phone_key_version"


def require_ready(config: PatientViewConfig, *, needs_open_limit: bool = False, needs_label_limit: bool = False) -> None:
    if not config.enabled:
        raise ViewDisabled("patient view is disabled outside the demo profile with synthetic data")
    if config.misconfigured:
        raise ViewNotConfigured("a patient view setting has an invalid value")
    if needs_open_limit and (config.settings.max_opens_per_minute is None or config.limiter is None):
        raise ViewNotConfigured("the per-user open rate limit is not configured")
    if needs_label_limit and (config.settings.max_label_rows_per_minute is None or config.label_limiter is None):
        raise ViewNotConfigured("the per-user list-row limit is not configured")


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
        out.append(PatientLabel(row.id, name, _birth_year(dob), _last4(phone)))
    return out
