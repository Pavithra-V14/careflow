"""Build the hosted providers (Gemini vlm, Jev decision, Sarvam translation) behind the gateway, DEMO PROFILE WITH SYNTHETIC DATA ONLY (owner decision 2026-10-30; docs/design/hosted-providers.md).

THE GATES, all of which must hold for a provider to be built; anything missing is a logged reason (names only, never a value) and that provider is simply unavailable, so the feature degrades visibly:
  * `CAREFLOW_ENV` is not `ci`: CI never calls a real provider (contract tests use recorded-shape fakes; the live check is a script the owner runs by hand);
  * `CAREFLOW_PROFILE=demo` AND `CAREFLOW_DATA_POLICY=synthetic_only` (the same gate as the other demo-only features);
  * the provider's API key is present in its own environment name;
  * `CAREFLOW_<PROVIDER>_VERIFIED` holds a date that is not in the future: the day the owner ran `scripts/provider_live_check.py` and saw the real service behave as the adapter assumes (Appendix B);
  * the provider's role has a REQUIRED daily spend ceiling in USD in `CAREFLOW_SPEND_CEILING_<ROLE>_USD`: NO default (a missing, non-numeric, negative or infinite value means "not configured");
  * a REQUIRED per-run spend cap in USD in `CAREFLOW_SPEND_CAP_PER_RUN_USD` (the most one job run may spend through the gateway): NO default, same rule for a bad value; the gateway refuses a call that would pass it.
Every registry row must say `synthetic_only` (spec 16.5 still forbids a hosted row from ever being `phi_approved` without a new decision): a file that says otherwise is refused whole.

The clinic's daily total is kept in the DURABLE ledger (`ops.spend_ledger`, written by the queue worker's identity) when `build_providers` is given one and in memory otherwise; the per-ROLE daily ceilings are still in memory. A restart then resets only the role ceilings. A spend stop surfaces as `SpendCeilingReached`, which callers already turn into a visible hold or a manual task; the safety
class is never budget-limited (careflow.gateway). Nothing here makes a network call: the clients are constructed with their keys and used by whatever feature routes a request to the gateway.
"""

import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Final

import yaml

from careflow import demo_phase3
from careflow.adapters.decision import JevAdapter
from careflow.adapters.gemini import GeminiVisionAdapter
from careflow.adapters.openrouter import JEV_MODEL, JevRouterClient
from careflow.adapters.sarvam import MODEL as SARVAM_MODEL
from careflow.adapters.sarvam import SarvamTranslation
from careflow.adapters.vision import GatewayVision, vlm_row
from careflow.gateway import (
    Gateway,
    InMemorySpendLedger,
    ModelRow,
    RoleDailyCeilings,
    SpendLedger,
    SpendLimits,
)
from careflow.ports import DataClass

log = logging.getLogger("careflow.providers")

CONFIG_FILE: Final = Path(__file__).resolve().parent.parent / "config" / "demo-providers.yaml"
LABEL: Final = "DEMO ONLY - SYNTHETIC DATA, PRICES TO BE CONFIRMED (Appendix B)"
KEY_VARIABLES: Final = {"gemini": "GEMINI_API_KEY", "jev": "OPENROUTER_API_KEY", "sarvam": "SARVAM_API_KEY"}
VERIFIED_VARIABLES: Final = {"gemini": "CAREFLOW_GEMINI_VERIFIED", "jev": "CAREFLOW_OPENROUTER_VERIFIED", "sarvam": "CAREFLOW_SARVAM_VERIFIED"}
RUN_CAP_VARIABLE: Final = "CAREFLOW_SPEND_CAP_PER_RUN_USD"
CEILING_VARIABLES: Final = {"vlm": "CAREFLOW_SPEND_CEILING_VLM_USD", "decision": "CAREFLOW_SPEND_CEILING_DECISION_USD", "translation": "CAREFLOW_SPEND_CEILING_TRANSLATION_USD"}
_ROW_KEYS: Final = {
    "role", "provider", "model_id", "data_policy", "priority", "timeout_seconds", "free_tier", "price_in_per_1k", "price_out_per_1k", "shadow_in_per_1k", "shadow_out_per_1k", "label",
}
_TRANSLATION_KEYS: Final = {"provider", "model_id", "data_policy", "timeout_seconds", "free_tier", "price_per_1k_chars_usd", "label"}


class ProviderConfigRefused(Exception):
    """The provider configuration breaks a rule. Fixed words; never a value."""


@dataclass(frozen=True)
class TranslationSpec:
    model_id: str
    timeout_seconds: float
    price_per_1k_chars_usd: Decimal


@dataclass(frozen=True)
class ProviderConfig:
    rows: tuple[ModelRow, ...]
    translation: TranslationSpec


def _decimal(value: Any) -> Decimal:
    try:
        d = Decimal(str(value))
    except InvalidOperation:
        raise ProviderConfigRefused("a price in the provider file is not a number") from None
    if not d.is_finite() or d < 0:
        raise ProviderConfigRefused("a price in the provider file is not a finite, non-negative number")
    return d


def _check_row_common(raw: Mapping[str, Any], allowed: set[str]) -> None:
    if set(raw) != allowed:
        raise ProviderConfigRefused("a provider entry has missing or unknown keys")
    if raw["data_policy"] != "synthetic_only":
        raise ProviderConfigRefused("every hosted provider row must be synthetic_only (BUILD-SPEC.md 16.5)")
    if raw["label"] != LABEL:
        raise ProviderConfigRefused("a provider entry is not labelled as a demo placeholder")
    if isinstance(raw["free_tier"], bool) is False:
        raise ProviderConfigRefused("free_tier must be true or false")


def load_provider_config(path: Path = CONFIG_FILE) -> ProviderConfig:
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(doc, dict) or doc.get("label") != LABEL or set(doc) != {"label", "rows", "translation"}:
        raise ProviderConfigRefused("the provider file is not the expected shape")
    rows: list[ModelRow] = []
    for raw in doc["rows"]:
        _check_row_common(raw, set(_ROW_KEYS))
        p_in, p_out = _decimal(raw["price_in_per_1k"]), _decimal(raw["price_out_per_1k"])
        if raw["free_tier"] and (p_in != 0 or p_out != 0):
            raise ProviderConfigRefused("a free-tier row has an actual price of zero")
        if not raw["free_tier"] and (p_in == 0 or p_out == 0):
            raise ProviderConfigRefused("a paid row needs a non-zero price, or its spend ceiling could never bite")
        s_in, s_out = _decimal(raw["shadow_in_per_1k"]), _decimal(raw["shadow_out_per_1k"])
        if raw["role"] == "vlm":
            rows.append(vlm_row(provider=raw["provider"], model_id=raw["model_id"], data_policy=raw["data_policy"], priority=raw["priority"], timeout_seconds=float(raw["timeout_seconds"]),
                                price_in_per_1k=p_in, price_out_per_1k=p_out, shadow_in_per_1k=s_in, shadow_out_per_1k=s_out))
        elif raw["role"] == "decision":
            rows.append(ModelRow("decision", raw["provider"], raw["model_id"], raw["data_policy"], raw["priority"], p_in, p_out, s_in, s_out, timeout_seconds=float(raw["timeout_seconds"])))
        else:
            raise ProviderConfigRefused("the provider file has a role this setup does not build")
    t = doc["translation"]
    _check_row_common(t, set(_TRANSLATION_KEYS))
    price = _decimal(t["price_per_1k_chars_usd"])
    if not t["free_tier"] and price == 0:
        raise ProviderConfigRefused("a paid translation route needs a non-zero price")
    if t["model_id"] != SARVAM_MODEL or float(t["timeout_seconds"]) <= 0:
        raise ProviderConfigRefused("the translation entry is not the pinned Sarvam model with a positive timeout")
    return ProviderConfig(tuple(rows), TranslationSpec(t["model_id"], float(t["timeout_seconds"]), price))


def read_ceiling(environ: Mapping[str, str], role: str) -> Decimal | None:
    """The required daily ceiling for `role` in USD, or None when it is missing or not a finite non-negative number. NO default."""
    raw = (environ.get(CEILING_VARIABLES[role]) or "").strip()
    if not raw:
        return None
    try:
        d = Decimal(raw)
    except InvalidOperation:
        return None
    return d if d.is_finite() and d >= 0 else None


def read_run_cap(environ: Mapping[str, str]) -> Decimal | None:
    """The required per-run spend cap in USD, or None when it is missing or not a finite number above zero. NO default."""
    raw = (environ.get(RUN_CAP_VARIABLE) or "").strip()
    if not raw:
        return None
    try:
        d = Decimal(raw)
    except InvalidOperation:
        return None
    return d if d.is_finite() and d > 0 else None


def _verified(environ: Mapping[str, str], provider: str) -> bool:
    try:
        return date.fromisoformat((environ.get(VERIFIED_VARIABLES[provider]) or "").strip()) <= datetime.now(UTC).date()
    except ValueError:
        return False


@dataclass
class ProviderSet:
    gateway: Gateway | None = None
    vision: GatewayVision | None = None
    translation: SarvamTranslation | None = None
    unavailable: dict[str, str] = field(default_factory=dict)  # provider -> a fixed reason (never a value)


def build_providers(
    environ: Mapping[str, str] | None = None, *, profile: str, config: ProviderConfig | None = None, http: Any = None, ledger: SpendLedger | None = None
) -> ProviderSet:  # `http`: an httpx.AsyncClient (contract tests pass a MockTransport one)
    """The providers that pass every gate; the others are named in `unavailable`. `http` is for contract tests (a MockTransport client): the real run builds each adapter's own client."""
    environ = os.environ if environ is None else environ
    out = ProviderSet()
    if (environ.get("CAREFLOW_ENV") or "").strip().lower() == "ci":
        out.unavailable = dict.fromkeys(KEY_VARIABLES, "CI never calls a real provider")
        return out
    if not demo_phase3.demo_allowed(profile, demo_phase3.data_policy(environ)):
        out.unavailable = dict.fromkeys(KEY_VARIABLES, "hosted providers are for profile=demo with data_policy=synthetic_only only")
        return out
    cfg = config or load_provider_config()
    ceilings = {role: c for role in CEILING_VARIABLES if (c := read_ceiling(environ, role)) is not None}
    role_ceilings = RoleDailyCeilings(ceilings)
    run_cap = read_run_cap(environ)
    rows: list[ModelRow] = []
    adapters: dict[tuple[str, str], Any] = {}

    def usable(provider: str, role: str) -> str | None:
        if not (environ.get(KEY_VARIABLES[provider]) or "").strip():
            return f"{KEY_VARIABLES[provider]} is not set"
        if not _verified(environ, provider):
            return f"{VERIFIED_VARIABLES[provider]} is not a date in the past: the live check has not been recorded"
        if role not in ceilings:
            return f"{CEILING_VARIABLES[role]} is not set to a number (no default)"
        if run_cap is None:
            return f"{RUN_CAP_VARIABLE} is not set to a number above zero (no default)"
        return None

    for row in cfg.rows:
        provider = row.provider
        why = usable(provider, row.role)
        if why:
            out.unavailable[provider] = why
            continue
        key = environ[KEY_VARIABLES[provider]].strip()
        if provider == "gemini":
            adapters[row.key] = GeminiVisionAdapter(key, row.model_id, timeout_seconds=row.timeout_seconds, http=http)
        elif provider == "jev" and row.model_id == JEV_MODEL:
            adapters[row.key] = JevAdapter(JevRouterClient(key, timeout_seconds=row.timeout_seconds, http=http))
        else:
            out.unavailable[provider] = "no adapter for this registry row"
            continue
        rows.append(row)
    if rows:
        total = sum((ceilings[r] for r in {row.role for row in rows}), Decimal(0))
        assert run_cap is not None  # (every row was built through `usable`, which requires it)
        out.gateway = Gateway(rows, adapters, ledger or InMemorySpendLedger(), SpendLimits(clinic_daily_usd=total, per_request_usd=run_cap), role_ceilings=role_ceilings, origin="test")
        if any(r.role == "vlm" for r in rows):
            out.vision = GatewayVision(out.gateway, data_class=DataClass.SYNTHETIC)
    why = usable("sarvam", "translation")
    if why:
        out.unavailable["sarvam"] = why
    else:
        spec = cfg.translation
        out.translation = SarvamTranslation(environ[KEY_VARIABLES["sarvam"]].strip(), timeout_seconds=spec.timeout_seconds, price_per_1k_chars_usd=spec.price_per_1k_chars_usd, ceilings=role_ceilings, http=http)
    for provider, reason in out.unavailable.items():
        log.warning("hosted provider %s is unavailable: %s", provider, reason)
    return out
