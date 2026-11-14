"""The SYNTHETIC PLACEHOLDER cards for the retrieval mechanism's tests and demo (config/demo-cards.yaml; owner decision 2026-11-14).

Every word in the file is invented and means nothing: no medicine, condition, symptom, dose, threshold or instruction. Protocol cards are clinician-owned and `kb/cards` stays empty (CLAUDE.md rule 1; a test
proves it). This module only reads the file, refuses anything that is not labelled DEMO ONLY, and hands back `CardFields` (what retrieval is allowed to read, spec 10.2) with the roles allowed to retrieve each
card. It is safe to load in any profile (it is placeholder text, not a decision); INDEXING it into a clinic is gated elsewhere (scripts/index_demo_cards.py: dev, demo, synthetic_only, a test clinic).
"""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import yaml

from careflow.retrieval import CardFields

DEMO_LABEL: Final = "DEMO ONLY - NOT A CLINICAL DECISION"
FILE: Final = Path(__file__).resolve().parent.parent / "config" / "demo-cards.yaml"
_CARD_KEYS: Final = {"card_id", "version", "audience", "title", "description", "synonyms", "patient_phrases", "label"}
_ROLES: Final = frozenset({"front_desk", "nurse", "doctor", "admin", "queue_worker"})
_ID: Final = re.compile(r"\A[a-z0-9][a-z0-9._-]{0,99}\Z")


class DemoCardsRefused(Exception):
    """The card file breaks a rule. Fixed words; never a value."""


@dataclass(frozen=True)
class DemoCard:
    fields: CardFields
    audience: tuple[str, ...]


@dataclass(frozen=True)
class DemoQuery:
    text: str
    expect: str


def _words(value: Any, what: str, *, limit: int) -> tuple[str, ...]:
    if not isinstance(value, list) or not value or len(value) > limit or not all(isinstance(v, str) and v.strip() and len(v) <= 200 for v in value):
        raise DemoCardsRefused(f"a card's {what} is not a short list of short strings")
    return tuple(v.strip() for v in value)


def load(path: Path = FILE) -> tuple[tuple[DemoCard, ...], tuple[DemoQuery, ...]]:
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(doc, dict) or doc.get("label") != DEMO_LABEL or set(doc) != {"label", "cards", "queries"}:
        raise DemoCardsRefused("the demo card file is not the expected shape or is not labelled DEMO ONLY")
    cards: list[DemoCard] = []
    for raw in doc["cards"]:
        if not isinstance(raw, dict) or set(raw) != _CARD_KEYS or raw["label"] != DEMO_LABEL:
            raise DemoCardsRefused("a demo card has missing or unknown keys, or is not labelled DEMO ONLY")
        if not isinstance(raw["card_id"], str) or not _ID.fullmatch(raw["card_id"]) or not raw["card_id"].startswith("demo-card-"):
            raise DemoCardsRefused("a demo card id must start with demo-card-")
        if not isinstance(raw["version"], int) or isinstance(raw["version"], bool) or raw["version"] < 1:
            raise DemoCardsRefused("a card version is a whole number from 1")
        audience = _words(raw["audience"], "audience", limit=5)
        if not set(audience) <= _ROLES:
            raise DemoCardsRefused("a card audience names a role that does not exist")
        for name in ("title", "description"):
            if not isinstance(raw[name], str) or not raw[name].strip() or len(raw[name]) > 400:
                raise DemoCardsRefused(f"a card's {name} is empty or too long")
        cards.append(
            DemoCard(
                CardFields(raw["card_id"], raw["version"], raw["title"].strip(), raw["description"].strip(), _words(raw["synonyms"], "synonyms", limit=20), _words(raw["patient_phrases"], "patient phrases", limit=20)),
                audience,
            )
        )
    if len({c.fields.card_id for c in cards}) != len(cards) or not cards:
        raise DemoCardsRefused("the demo cards need distinct ids")
    ids = {c.fields.card_id for c in cards}
    queries = []
    for raw in doc["queries"]:
        if not isinstance(raw, dict) or set(raw) != {"text", "expect"} or not isinstance(raw["text"], str) or not raw["text"].strip() or raw["expect"] not in ids:
            raise DemoCardsRefused("a demo query needs text and the id of a demo card")
        queries.append(DemoQuery(raw["text"].strip(), raw["expect"]))
    return tuple(cards), tuple(queries)
