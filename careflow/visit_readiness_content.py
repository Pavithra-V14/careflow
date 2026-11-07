"""Visit Readiness CONTENT as a REQUIRED SETTING (owner decision of 2026-11-07, item 1; BUILD-SPEC.md 22.2, 22.3, 20.2). Intake questions and readiness checklists are CLINICIAN-AUTHORED; this module only validates their
shape and NEVER supplies a word of them. `CAREFLOW_VISIT_READINESS_CONTENT` is a JSON object:

    {"facts":       {"<fact_name>": "<the clinician's exact question>"},
     "visit_types": {"<visit type name as the clinic spells it>": {"intake": ["<fact_name>", ...], "checklist": [{"id": "<item_id>", "text": "<the clinician's exact wording>"}, ...]}}}

Unset, blank or malformed means NO readiness run can start (the route answers 503). A visit type with no entry cannot start one either (the route says so): an empty list must never read as "nothing to confirm".
The structure checks are the ones visit_readiness already holds (`card_problems`, `checklist_problems`). The demo pack carries clearly labelled placeholder questions.
"""

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

from careflow.visit_readiness import (
    ChecklistItem,
    IntakeCard,
    ReadinessChecklist,
    card_problems,
    checklist_problems,
)

MAX_VISIT_TYPES: Final = 100
MAX_FACTS: Final = 200


class VisitContentError(Exception):
    """The setting is present but not valid. Fixed words; the value is never echoed."""


@dataclass(frozen=True)
class VisitTypeContent:
    card: IntakeCard
    checklist: ReadinessChecklist
    item_text: Mapping[str, str]  # checklist item id -> exact wording


@dataclass(frozen=True)
class VisitContent:
    visit_types: Mapping[str, VisitTypeContent]

    def for_visit_type(self, name: str) -> VisitTypeContent | None:
        return self.visit_types.get(name)


def parse(raw: str | None) -> VisitContent | None:
    if raw is None or not raw.strip():
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        raise VisitContentError("CAREFLOW_VISIT_READINESS_CONTENT is not valid JSON") from None
    if not isinstance(data, dict) or set(data) != {"facts", "visit_types"} or not isinstance(data["facts"], dict) or not isinstance(data["visit_types"], dict):
        raise VisitContentError("CAREFLOW_VISIT_READINESS_CONTENT must have exactly 'facts' and 'visit_types'")
    facts: dict[str, str] = data["facts"]
    if len(facts) > MAX_FACTS or not data["visit_types"] or len(data["visit_types"]) > MAX_VISIT_TYPES:
        raise VisitContentError("too many or no entries")
    if any(not isinstance(k, str) or not re.match(r"^[a-z][a-z0-9_]{0,59}$", k) for k in facts):
        raise VisitContentError("a fact name is malformed")
    out: dict[str, VisitTypeContent] = {}
    for name, body in data["visit_types"].items():
        if not isinstance(name, str) or not name.strip() or len(name) > 120 or not isinstance(body, dict) or set(body) != {"intake", "checklist"}:
            raise VisitContentError("a visit type entry needs exactly 'intake' and 'checklist'")
        intake, checklist = body["intake"], body["checklist"]
        if not isinstance(intake, list) or any(not isinstance(f, str) or f not in facts for f in intake) or not isinstance(checklist, list):
            raise VisitContentError("intake must list declared facts and checklist must be a list")
        if any(not isinstance(i, dict) or set(i) != {"id", "text"} or not isinstance(i["id"], str) or not isinstance(i["text"], str) for i in checklist):
            raise VisitContentError("a checklist item needs exactly 'id' and 'text'")
        if any(not isinstance(q, str) for q in facts.values()):
            raise VisitContentError("a question must be text")
        card = IntakeCard(name, 1, tuple(intake), {f: facts[f] for f in intake})
        items = tuple(ChecklistItem(i["id"], i["text"]) for i in checklist)
        sheet = ReadinessChecklist(name, 1, items)
        if card_problems(card) or checklist_problems(sheet):
            raise VisitContentError("a card or checklist failed its structure check")
        out[name] = VisitTypeContent(card, sheet, {i.item_id: i.text for i in items})
    return VisitContent(out)
