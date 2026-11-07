"""The consent notice texts as a REQUIRED SETTING (owner decision of 2026-11-07, item 6): counsel owns the real wording; this module only validates the shape and never supplies a text.

`CAREFLOW_CONSENT_NOTICES` is a JSON object: {"care_delivery": {"version": 1, "body": "..."}, "ai_processing": {...}, "service_improvement": {...}}; any subset of the three purposes. Unset, blank or malformed means NO
notice can be published and nothing can be consented to (unknown is not a value). The demo pack carries a placeholder that says in its own first words that it is not a legal notice.
"""

import json
from dataclasses import dataclass
from typing import Final

PURPOSES: Final = ("care_delivery", "ai_processing", "service_improvement")


class ConsentSettingsError(Exception):
    """The setting is present but not valid. Fixed words; the value is never echoed."""


@dataclass(frozen=True)
class NoticeDef:
    purpose: str
    version: int
    body: str


def parse(raw: str | None) -> dict[str, NoticeDef] | None:
    if raw is None or not raw.strip():
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        raise ConsentSettingsError("CAREFLOW_CONSENT_NOTICES is not valid JSON") from None
    if not isinstance(data, dict) or not data or not set(data) <= set(PURPOSES):
        raise ConsentSettingsError("CAREFLOW_CONSENT_NOTICES must be an object keyed by purpose")
    out: dict[str, NoticeDef] = {}
    for purpose, body in data.items():
        if not isinstance(body, dict) or set(body) != {"version", "body"}:
            raise ConsentSettingsError("a notice needs exactly a version and a body")
        version, text_ = body["version"], body["body"]
        if isinstance(version, bool) or not isinstance(version, int) or not 1 <= version <= 1000 or not isinstance(text_, str) or not 1 <= len(text_) <= 20000:
            raise ConsentSettingsError("a notice version or body is out of range")
        out[purpose] = NoticeDef(purpose, version, text_)
    return out
