"""The translation port over Sarvam's text translate API (BUILD-SPEC.md §2, §16.2). DEMO PROFILE, SYNTHETIC DATA ONLY (owner decision 2026-10-30).

`SarvamTranslation.translate` is the real `TranslationPort`. Translation is DECORATIVE for triage comprehension (spec §16.2): when it is unavailable the original text and a flag are shown, and the native Tamil
red-flag rules run on the original text regardless. So this adapter is never on the path of a safety rule, and its output is never an input to one.

Endpoint and shape (CONFIRMED only by the opt-in live check, `scripts/provider_live_check.py`; Appendix B): `POST https://api.sarvam.ai/translate`, header `api-subscription-key`, body `input`,
`source_language_code`, `target_language_code`, `model`; the answer holds `translated_text`. The input limit is 1,000 characters for `mayura:v1`: a longer text is REFUSED with fixed words, never silently cut.
Usage is counted in CHARACTERS (the provider bills per character), carried in `Usage.input_tokens`; the row's price is therefore per 1,000 characters.

Its daily spend ceiling is the same in-memory `RoleDailyCeilings` the gateway uses (role `translation`): reaching it raises `SpendCeilingReached`, which the caller turns into "original text + flag".
"""

from decimal import Decimal
from typing import Final

import httpx

from careflow.adapters.provider_http import (
    new_client,
    post_json,
    refuse_redirecting_client,
)
from careflow.gateway import RoleDailyCeilings, SpendCeilingReached
from careflow.ports import (
    AdapterResult,
    DataClass,
    PermanentError,
    TranslationRequest,
    TranslationResult,
    Usage,
)

HOST: Final = "https://api.sarvam.ai"
ENDPOINT: Final = f"{HOST}/translate"
MODEL: Final = "mayura:v1"
MAX_CHARS: Final = 1000  # mayura:v1 (the provider's documented limit)
LANGUAGE_CODES: Final = {"en": "en-IN", "ta": "ta-IN", "hi": "hi-IN"}  # the codes this project translates between; anything else is refused


class SarvamTranslation:
    def __init__(
        self, api_key: str, *, timeout_seconds: float, price_per_1k_chars_usd: Decimal, ceilings: RoleDailyCeilings,
        model_id: str = MODEL, http: httpx.AsyncClient | None = None,
    ) -> None:
        if not api_key or not api_key.strip():
            raise ValueError("a Sarvam API key is required")
        if model_id != MODEL:
            raise ValueError("this adapter is for the mayura:v1 model only")
        if price_per_1k_chars_usd < 0 or not ceilings.has("translation"):
            raise ValueError("a translation price and a required 'translation' spend ceiling are both needed")
        if http is not None:
            refuse_redirecting_client(http)
        self._key, self._seconds, self._model, self._price, self._ceilings = api_key.strip(), timeout_seconds, model_id, price_per_1k_chars_usd, ceilings
        self._http = http or new_client(timeout_seconds)

    def accepts(self, request: TranslationRequest) -> bool:
        return request.data_class is DataClass.SYNTHETIC  # a third party: never patient text

    async def translate(self, request: TranslationRequest) -> AdapterResult[TranslationResult]:
        if request.data_class is not DataClass.SYNTHETIC:
            raise PermanentError("sarvam handles synthetic data only")  # the adapter's own refusal, whatever the caller or the deployment gate says
        source, target = LANGUAGE_CODES.get(request.source), LANGUAGE_CODES.get(request.target)
        if source is None or target is None or source == target:
            raise PermanentError("this language pair is not one the translation route handles")
        if not request.text.strip() or len(request.text) > MAX_CHARS:
            raise PermanentError("the text is empty or longer than the translation route accepts (it is never cut)")
        estimate = Decimal(len(request.text)) / Decimal(1000) * self._price
        if self._price > 0 and self._ceilings.would_exceed("translation", estimate):
            raise SpendCeilingReached("the translation route would pass its daily spend ceiling")
        answer = await post_json(
            self._http, ENDPOINT, headers={"api-subscription-key": self._key},
            body={"input": request.text, "source_language_code": source, "target_language_code": target, "model": self._model}, seconds=self._seconds,
        )
        translated = answer.get("translated_text")
        if not isinstance(translated, str) or not translated.strip():
            raise PermanentError("the provider returned no translation")
        self._ceilings.add("translation", estimate)
        return AdapterResult(TranslationResult(translated, request.source, request.target), Usage(len(request.text), len(translated)))
