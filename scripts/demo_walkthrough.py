"""A runnable walkthrough of the synthetic end-to-end flow (docs/demo-walkthrough.md). Usage: python scripts/demo_walkthrough.py

It runs, in memory and with the same fakes the tests use, the three agents that have no HTTP route yet (Care Journey, Visit Readiness, the voice monitor) and PRINTS the HTTP calls for the routed
steps (referral in, review, scheduling, offer, reply), so one page shows the whole story. Nothing here touches a database, a network or a model; every value is made up (DEMO-*)."""

import asyncio
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from careflow import care_journey
from careflow.autonomy import Policy
from careflow.voice import VoiceEvent, run_session
from tests import test_care_journey as cj
from tests import test_visit_readiness as vr
from tests import test_voice as vo

ON = {"FEATURE_CARE_JOURNEY": "1", "FEATURE_VISIT_READINESS": "1"}


def say(step: str, text: str) -> None:
    print(f"\n[{step}] {text}")


def http_steps() -> None:
    say("1 referral in", "POST /v1/staff/documents/uploads then POST /v1/staff/documents/register (front desk). Demo uploads are synthetic only.")
    say("2 run", "POST /v1/staff/referrals/{document_id}/run (nurse/doctor/front desk). The rules read the letter first; a flag raises a nurse task; otherwise a triage proposal waits.")
    say("3 review", "GET /v1/staff/referrals/{document_id}/review-view, then POST .../review with approve | reject (nurse/doctor; edit is Coming soon). A canary looks like a real case; approving one raises a silent admin alert.")
    say("4 scheduling", "GET /v1/staff/scheduling/slots, POST .../slots/{slot_id}/hold, POST .../slots/{slot_id}/book (front desk). Cancel with POST .../bookings/{booking_id}/cancel.")
    say("5 offer", "POST /v1/staff/scheduling/backfills (a freed slot) -> POST .../backfills/{slot_id}/decide (send | skip | stop). The highest clinical priority is offered first, not the longest wait. Offers are delivered by hand: POST .../offers/{hold_id}/mark-delivered.")
    say("6 reply", "POST /v1/staff/scheduling/backfills/{slot_id}/reply with the patient's reply message; the rules screen it first, then POST .../offers/interpret-reply gives a suggestion a person confirms.")
    say("7 alerts and gaps", "GET /v1/staff/console/alerts; GET /v1/staff/care-gaps?clinic_id=...; POST /v1/staff/care-gaps/barriers to record a barrier.")
    say("7b care-gap producers", "POST /v1/staff/care-gaps/episodes (nurse/doctor; the rules come only from CAREFLOW_CARE_GAP_RULES) then POST .../episode-rules/{id}/done; the worker tick raises the overdue alert through a narrow read; POST .../samples/{id}/recheck closes a review sample.")
    say("11 patient portal", "A patient signs in (phone OTP in Supabase Auth), then POST /v1/portal/register, GET /v1/portal/consents, POST /v1/portal/consents (against the CURRENT notice), POST /v1/portal/activate, POST /v1/portal/claims (verified phone + name + date of birth; pending_review goes to the front desk).")
    say("12 care plans and results", "POST /v1/staff/care-plans (doctor; 503 until a model route and the task settings are configured), POST .../{id}/confirm, POST /v1/staff/results (nurse/doctor) wakes the plan whose wait it matches.")
    say("13 admin", "POST /v1/staff/console/autonomy (shadow or assisted; no row means shadow), GET /v1/staff/console/dashboard, GET /v1/staff/voice/sessions and .../transcript (audited), POST /v1/staff/voice/purge (expired chunks only).")


async def care_journey_demo() -> None:
    say("8 care journey", "A doctor's plan becomes a task list; a doctor confirms; a result arrives; a report with no value goes to a doctor, a good report proposes a referral.")
    world = cj.World()
    graph = care_journey.build_care_journey_graph(world.deps(), InMemorySaver(), environ=ON)
    cfg = care_journey.run_config(cj.PLAN)
    await graph.ainvoke(care_journey.initial_state(cj.CLINIC, cj.PLAN, cj.PATIENT, "test"), cfg)
    print("   plan proposed; waiting for the doctor's confirmation:", world.log)
    await graph.ainvoke(Command(resume={"action": "approve"}), cfg)
    missing = cj.add_result(world, {})
    await graph.ainvoke(Command(resume={"kind": "result", "result_id": missing.result_id}), cfg)
    print("   a report lacking the value ->", world.tasks[-1], "(never closed as not met)")
    world2 = cj.World()
    graph2 = care_journey.build_care_journey_graph(world2.deps(), InMemorySaver(), environ=ON)
    cfg2 = care_journey.run_config(uuid.uuid4())
    await graph2.ainvoke(care_journey.initial_state(cj.CLINIC, uuid.uuid4(), cj.PATIENT, "test"), cfg2)
    await graph2.ainvoke(Command(resume={"action": "approve"}), cfg2)
    good = cj.add_result(world2, {"demo_value": 5})
    await graph2.ainvoke(Command(resume={"kind": "result", "result_id": good.result_id}), cfg2)
    print("   a verified value that meets the condition ->", world2.tasks[-1]["reason"], "(a doctor task; nothing leaves the clinic)")


async def readiness_demo() -> None:
    say("9 readiness", "Only the missing facts are asked; a danger phrase volunteered mid-conversation still escalates; any not-done answer reaches a nurse in every mode.")
    world = vr.World(screen=("clear", "red_flag_escalated"))
    graph, cfg, _ = await vr.begin(world)
    await vr.reply(graph, cfg)
    out = await vr.reply(graph, cfg)
    print("   second answer carried a danger phrase ->", out["status"], "(no model read it)")
    for mode in ("shadow", "assisted", "autonomous"):
        w = vr.World(intake=(), readiness=("r1",), readiness_answers=("not_done",))
        g, c, _ = await vr.begin(w, policy=Policy(mode=mode))
        await vr.reply(g, c)
        print(f"   not-done answer in {mode} ->", w.tasks[0]["reason"], "to", w.tasks[0]["role"])


async def voice_demo() -> None:
    say("10 voice", "The fake engine speaks a synthetic red-flag token mid-session; the live monitor forces the fixed safety response and raises the nurse task.")
    engine = vo.FakeEngine([VoiceEvent("transcript", "some ordinary words", 100), VoiceEvent("transcript", " and zzalpha", 250)])
    fakes = vo.Fakes(engine, now=262)
    result = await run_session(fakes.ports(), vo.stream("zzalpha"), session_id=vo.SESSION, workflow="slot_offer", scripted_text="DEMO offer", decline_text="DEMO decline", max_turns=3)
    print("   ", result)


def extras_demo() -> None:
    from careflow.context_assembler import assemble

    say("14 context assembler", "A declared budget; the lowest priority is dropped first and what was dropped is reported by label only.")
    a = assemble("DEMO instruction", budget=12, patient_facts=[("f1", "fact " * 3)], evidence=[("e1", "evidence " * 20)], history=[("h1", "old turn"), ("h2", "new turn")], count_tokens=lambda t: len(t.split()))
    print("   included:", a.included, "dropped:", a.dropped)


async def scanner_demo() -> None:
    from careflow.adapters.fake import FakeScanner
    from careflow.scanner_worker import rescan_batch

    say("15 scanner worker", "Documents last scanned with an older signature are rescanned; a failure is never recorded as clean.")
    done: dict[str, str] = {}

    async def read(doc: str) -> bytes:
        return b"%PDF-1.4 DEMO"

    async def record(doc: str, result) -> None:
        done[doc] = result.verdict

    counts = await rescan_batch(FakeScanner(), [("doc-old", "sig-1"), ("doc-current", "sig-2")], read, record, current_signature="sig-2")
    print("   ", counts, done)


def main() -> int:
    print("CareFlow synthetic walkthrough (demo profile, synthetic data only). Start the app with: CAREFLOW_PROFILE=demo CAREFLOW_DATA_POLICY=synthetic_only CAREFLOW_DEMO_PHASE3=1 CAREFLOW_DEMO_PHASE4=1")
    http_steps()
    asyncio.run(care_journey_demo())
    asyncio.run(readiness_demo())
    asyncio.run(voice_demo())
    extras_demo()
    asyncio.run(scanner_demo())
    print("\nDone. Nothing was stored, sent or called.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
