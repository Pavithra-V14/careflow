"""SMS in India: templates, registration status and the send guard (BUILD-SPEC.md 18.2; Appendix D Phase 10; docs/runbooks/sms-dlt-registration.md). MECHANISM ONLY: no provider is called.

WHY A GUARD: a message that does not match a pre-registered DLT template is SILENTLY blocked at the carrier, with no error returned. So the sender refuses, loudly and in-process, anything the carrier would
swallow, and reports a delivery failure the way the rest of the system does instead of pretending it sent.

  * A TEMPLATE is registered data (`config/sms-templates.yaml`): an id, a language, a count of variables and each variable's KIND, and its DLT status. The WORDING is the hospital's, registered by the
    hospital as the principal entity; this file carries no wording and no template is invented here. Tamil and English are separate templates (separate registration).
  * VARIABLES ARE CLOSED KINDS (link, brand, slot_time, short_code), at most MAX_VARIABLES of them, and each value is checked against its kind. There is no free-text kind: SMS never carries health
    details (18.2), and a free-text slot could carry one.
  * ONLY A REGISTERED TEMPLATE IS SENT in the `clinical` profile. A template that is `not_registered` or `pending` is refused (raised, not dropped). In the `demo` profile everything goes to the SANDBOX
    SINK, never a provider.
  * ABUSE LIMITS are required settings with no default (a missing one refuses to send): numbers allowed by country prefix (the spec's default is +91 only, so that is the one stated default), a per-phone
    count per window, and a daily spend cap with the per-message price. Unknown spend or an unset cap refuses (unknown is not a value).
"""

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Final

import yaml

from careflow.ports import SendResult

TEMPLATES_FILE: Final = Path(__file__).resolve().parent.parent / "config" / "sms-templates.yaml"
MAX_VARIABLES: Final = 5  # the spec says "at most 5-6"; the lower bound is used
KINDS: Final = ("link", "brand", "slot_time", "short_code")
DLT_STATUSES: Final = ("not_registered", "pending", "registered")
LANGUAGES: Final = ("en", "ta")
DEFAULT_ALLOWED_PREFIXES: Final = ("+91",)  # BUILD-SPEC.md 18.2: a +91-only allowlist by default
_ID = re.compile(r"^[a-z][a-z0-9_]{0,59}$")
_PHONE = re.compile(r"^\+[1-9][0-9]{7,14}$")
_LINK = re.compile(r"^https://[A-Za-z0-9.-]+(/[A-Za-z0-9._~:/?#@!$&()*+,;=%-]*)?$")
_SHORT = re.compile(r"^[A-Za-z0-9]{4,12}$")
_BRAND = re.compile(r"^[A-Za-z0-9 .&'-]{2,40}$")


class SmsError(Exception):
    """Fixed words only; never a number, a value or a body."""


@dataclass(frozen=True)
class SmsTemplate:
    template_id: str
    language: str
    variable_kinds: tuple[str, ...]
    dlt_status: str


def template_problems(t: SmsTemplate) -> list[str]:
    problems: list[str] = []
    if not _ID.match(t.template_id):
        problems.append("template_id")
    if t.language not in LANGUAGES:
        problems.append("language")
    if len(t.variable_kinds) > MAX_VARIABLES:
        problems.append("too_many_variables")
    if any(k not in KINDS for k in t.variable_kinds):
        problems.append("variable_kind")
    if "brand" not in t.variable_kinds:
        problems.append("no_brand_variable")  # a registered template must carry the brand name
    if t.dlt_status not in DLT_STATUSES:
        problems.append("dlt_status")
    return problems


def load_templates(path: Path = TEMPLATES_FILE) -> dict[str, SmsTemplate]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) if path.is_file() else {}
    items = data.get("templates") if isinstance(data, dict) else None
    out: dict[str, SmsTemplate] = {}
    for raw in items or []:
        t = SmsTemplate(str(raw.get("id")), str(raw.get("language")), tuple(raw.get("variables") or ()), str(raw.get("dlt_status", "not_registered")))
        if template_problems(t) or t.template_id in out:
            raise SmsError("a template in the file is malformed or repeated")
        out[t.template_id] = t
    return out


def check_value(kind: str, value: object) -> bool:
    if not isinstance(value, str):
        return False
    if kind == "link":
        return bool(_LINK.match(value)) and len(value) <= 120
    if kind == "brand":
        return bool(_BRAND.match(value))
    if kind == "short_code":
        return bool(_SHORT.match(value))
    if kind == "slot_time":
        try:
            datetime.fromisoformat(value)
        except ValueError:
            return False
        return True
    return False


# ---- the sandbox sink and the guard ----------------------------------------------------------------------------------------------------------------------------------


@dataclass
class SandboxSink:
    """The demo profile's SmsPort: records that a message WOULD have gone out; nothing leaves the process."""

    sent: list[tuple[str, str]] = field(default_factory=list)  # (template id, a masked recipient)

    async def deliver(self, template_id: str, to: str, variables: Sequence[str], idempotency_key: str) -> SendResult:
        self.sent.append((template_id, "*" * (len(to) - 4) + to[-4:]))
        return SendResult(f"sandbox:{idempotency_key}")


@dataclass(frozen=True)
class SmsLimits:
    """Required settings, no defaults (except the +91 prefix list, which the spec states)."""

    max_per_phone_per_window: int | None
    window_seconds: int | None
    daily_spend_cap: float | None
    price_per_message: float | None
    allowed_prefixes: tuple[str, ...] = DEFAULT_ALLOWED_PREFIXES


class TemplatedSms:
    def __init__(self, templates: Mapping[str, SmsTemplate], limits: SmsLimits, deliver: Callable, *, profile: str, now: Callable[[], datetime]) -> None:
        self._templates, self._limits, self._deliver, self._profile, self._now = templates, limits, deliver, profile, now
        self._per_phone: dict[str, list[datetime]] = {}
        self._spent_day: tuple[str, float] = ("", 0.0)
        self._seen: dict[str, SendResult] = {}

    async def send(self, template_id: str, to: str, variables: Sequence[str], *, idempotency_key: str) -> SendResult:
        if idempotency_key in self._seen:
            return SendResult(self._seen[idempotency_key].provider_id, replayed=True)
        template = self._templates.get(template_id)
        if template is None:
            raise SmsError("unknown template")
        if self._profile != "demo" and template.dlt_status != "registered":
            raise SmsError("template is not registered with DLT")  # the carrier would drop it silently
        if len(variables) != len(template.variable_kinds) or not all(check_value(k, v) for k, v in zip(template.variable_kinds, variables, strict=True)):
            raise SmsError("variables do not match the template")
        if not isinstance(to, str) or not _PHONE.match(to) or not any(to.startswith(p) for p in self._limits.allowed_prefixes):
            raise SmsError("recipient not allowed")
        lim = self._limits
        if None in (lim.max_per_phone_per_window, lim.window_seconds, lim.daily_spend_cap, lim.price_per_message):
            raise SmsError("sms limits are not configured")
        assert lim.max_per_phone_per_window and lim.window_seconds and lim.daily_spend_cap is not None and lim.price_per_message is not None
        now = self._now()
        recent = [t for t in self._per_phone.get(to, []) if (now - t).total_seconds() < lim.window_seconds]
        if len(recent) >= lim.max_per_phone_per_window:
            raise SmsError("per-phone limit reached")
        day = now.date().isoformat()
        spent = self._spent_day[1] if self._spent_day[0] == day else 0.0
        if spent + lim.price_per_message > lim.daily_spend_cap:
            raise SmsError("daily spend cap reached")
        result = await self._deliver(template_id, to, list(variables), idempotency_key)
        self._per_phone[to] = [*recent, now]
        self._spent_day = (day, spent + lim.price_per_message)
        self._seen[idempotency_key] = result
        return result

