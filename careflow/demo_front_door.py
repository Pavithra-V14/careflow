"""FAKE Front Door components for the DEMO profile, SYNTHETIC data only (owner request 2026-11-03).

So that the referral `run` and `review` routes answer on seeded data in the dev console, the app wires `FrontDoorComponents` built from FAKE stand-ins, NEVER from the hosted providers, and ONLY when ALL hold:
profile `demo`, `CAREFLOW_DATA_POLICY=synthetic_only`, `CAREFLOW_DEMO_PHASE3` on (the same gate as every other demo placeholder, careflow/demo_phase3.py), and the encryption keys are loaded with a usable active key version. In any
other profile `components_from_environment` returns None and the routes keep answering 503 (tests/test_demo_front_door.py).

What is fake: the TEXT of a letter (a fictional letter is made up from the document's id, whatever file, if any, is stored), the EXTRACTOR (it reads that fictional text with a regular expression) and the
RETRIEVER (it proposes the fictional card ids `demo-synthetic-card-A/B`). What is REAL: the deterministic red-flag rules (`RedFlagEngine`), the identity pipeline, the graph, the checkpointer, the
triage store, the review recorder, the hold and dead-letter task writers: all under the worker's or the reviewer's own row-level security. The task priorities and the reviewing role are the DEMO ONLY
placeholders of config/demo-phase3.yaml, labelled "DEMO ONLY - NOT A CLINICAL DECISION".

HONEST LIMITS.
  * With NO usable red-flag rule at all (in the demo profile a rule is usable when it is APPROVED or a DRAFT; the rules are clinician-owned and nothing here authors one) every letter is UNASSESSABLE: the run ends in a
    hold and a hold task for a person, exactly as it must (unknown is not clear). If any usable rule with criteria exists for the clinic, a letter that matches none of them is CLEAR (in the demo profile), the fake
    extractor runs and the run pauses at review with a proposal.
  * The text that is screened is the FICTIONAL letter below, never the content of an uploaded file: a "clear" here says nothing about what a real file contains. That is acceptable only because these components
    exist only for synthetic data in the demo profile (and the screening holds any "clear" on a `live`-origin document).
  * The fake retriever never abstains and its scores are made up; the review mode and task priorities are the labelled DEMO ONLY values of the pack. A worker must also be running for a run to progress
(`CAREFLOW_RUN_WORKER` on and `CAREFLOW_WORKER_ACCOUNT_ID` naming a queue_worker account); without one a run stays queued.
"""

import re
import uuid
from collections.abc import Mapping
from typing import Final

from careflow import demo_phase3
from careflow.crypto import DemographicVault
from careflow.dead_letters import SqlDeadLetterTaskWriter
from careflow.front_door_runner import FrontDoorComponents
from careflow.mpi import PatientIngestionPipeline
from careflow.referral import ExtractedReferral
from careflow.retrieval import Candidate, Retrieval
from careflow.rules import RedFlagEngine
from careflow.triage import ApprovalMode
from careflow.triage_store import (
    SqlHoldTaskWriter,
    SqlReviewRecorder,
    SqlTriageProposer,
)

# Fictional people (the same five as the first five of scripts/seed_dev_demo_data.py): name, date of birth, 10-digit mobile or None.
DEMO_PEOPLE: Final = (
    ("Demo Alpha Sample", "1958-03-14", "9000000001"),
    ("Demo Bravo Sample", "1971-11-02", "9000000002"),
    ("Demo Charlie Sample", "1984-07-27", "9000000003"),
    ("Demo Delta Sample", "1993-01-09", "9000000004"),
    ("Demo Echo Sample", "2001-09-30", None),
)
DEMO_CARDS: Final = (("demo-synthetic-card-A", 1), ("demo-synthetic-card-B", 1))
_LETTER = re.compile(r"Patient: (?P<name>[^,]+), DOB (?P<dob>\d{4}-\d{2}-\d{2})(?:, Mobile (?P<phone>\d{10}))?")


def demo_letter(document_id: uuid.UUID) -> str:
    name, dob, phone = DEMO_PEOPLE[document_id.int % len(DEMO_PEOPLE)]
    return f"Referral letter (DEMO, fictional). Patient: {name}, DOB {dob}" + (f", Mobile {phone}" if phone else "") + ". Please see this patient."


class DemoTexts:
    """A fictional letter made up from the document's id. It reads no stored file and calls nothing."""

    async def text_for(self, document_id: uuid.UUID) -> str:
        return demo_letter(document_id)


class DemoExtractor:
    """FAKE extractor: reads the fictional letter above with a regular expression. Every claim carries a quote that IS in the letter, so the grounding check can pass honestly."""

    async def extract(self, letter_text: str, clearance, *, clinic_id: uuid.UUID) -> ExtractedReferral:
        match = _LETTER.search(letter_text)
        if match is None:
            return ExtractedReferral()  # nothing claimed: the pipeline routes the letter to a person
        name, dob, phone = match.group("name"), match.group("dob"), match.group("phone")
        return ExtractedReferral(
            name=name, name_quote=f"Patient: {name}", dob=dob, dob_quote=f"DOB {dob}", phone=phone, phone_quote=f"Mobile {phone}" if phone else None,
        )


class DemoRetriever:
    """FAKE retriever: proposes the two fictional card ids. No card text, no embedding, no vector store."""

    async def retrieve(self, query: str, *, scope: dict[str, str]) -> Retrieval:
        return Retrieval(False, None, tuple(Candidate(card, version, 0.03 - i * 0.01, 0.9, 0.5) for i, (card, version) in enumerate(DEMO_CARDS)))


def components_from_environment(environ: Mapping[str, str], profile: str, *, vault: DemographicVault | None, key_version: int | None) -> FrontDoorComponents | None:
    """The fake components, or None (the routes then answer 503). None unless profile=demo AND data_policy=synthetic_only AND the demo switch is on AND the keys are loaded."""
    if vault is None or key_version is None:
        return None
    if not demo_phase3.demo_allowed(profile, demo_phase3.data_policy(environ)) or not demo_phase3.switched_on(environ):
        return None
    pack = demo_phase3.load_demo_phase3(profile, demo_phase3.data_policy(environ))
    return FrontDoorComponents(
        engine=RedFlagEngine(),
        texts=DemoTexts(),
        extractor=DemoExtractor(),
        ingestor=PatientIngestionPipeline(vault, key_version=key_version, registrar="function"),
        retriever=DemoRetriever(),  # type: ignore[arg-type]
        proposer=SqlTriageProposer(mode=ApprovalMode(pack.triage_mode), assigned_role=pack.triage_task_role, task_priority=pack.triage_task_priority),
        recorder=SqlReviewRecorder(),
        holds=SqlHoldTaskWriter(priority=pack.hold_task_priority),
        dead_letters=_dead_letters(pack.dead_letter_task_priority),
        retrieval_scope={"domain": "demo"},
        profile=profile,
    )


def _dead_letters(priority: str) -> SqlDeadLetterTaskWriter:
    return SqlDeadLetterTaskWriter(priority=priority)
