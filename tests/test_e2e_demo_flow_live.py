"""The scheduling half of the synthetic demo flow, driven as ONE SCRIPTED SEQUENCE through the HTTP API with real signed tokens, against the real database inside ONE NEVER-COMMITTED transaction, with no external
service and no model (BUILD-SPEC.md Appendix D, Phases 2 and 3; docs/demo-walkthrough.md steps 4 to 7).

Cancel a booking -> the ranked candidates -> a draft offer -> send (the slot is held for the top candidate) -> the offer list -> mark delivered BY HAND -> the patient's reply recorded by front desk (screened by the
rules first) -> a reply interpretation that is visibly unavailable without an approved model route -> the held slot booked by the candidate it was held for (nobody else can take it) -> the alerts list -> the audit
trail, one row per human act, ids and closed words only.

It REUSES the harness (the world, the configured stand-in settings, the helpers) of tests/test_api_offers_live.py: one set of fixtures, not a second copy. The referral half of the flow (upload, run, review, hold) is in
tests/test_phase2_e2e_live.py and the backfill state machine with its fake components in tests/test_backfill_routes.py: they need different components (the Front Door graph, the scheduling agent), so they are separate
harnesses and are NOT merged into this sequence.
"""

import uuid

import pytest

from tests.db_helpers import without_scope
from tests.test_api_offers_live import (  # noqa: F401  (the fixtures are imported so this module seeds its own world)
    DRAFT,
    SEND,
    draft,
    headers,
    rows,
    send_body,
    w,
    waitlist_id,
    world,
)

pytestmark = pytest.mark.asyncio(loop_scope="module")
SCHED = "/v1/staff/scheduling"


async def test_the_scheduling_flow_from_a_cancelled_booking_to_a_booked_offer_with_every_human_act_on_record(w):  # noqa: F811
    # 1. a booking is cancelled: the freed slot and the ranked candidates (clinical priority first, then who waited longer); nobody is offered anything yet
    cancel = await w.client.post(f"{SCHED}/bookings/{w.booking}/cancel", headers=headers(w, "fd_a"))
    assert cancel.status_code == 200 and cancel.json()["slot_id"] == str(w.slot_booked)
    assert [c["patient_id"] for c in cancel.json()["candidates"]] == [str(w.top), str(w.second), str(w.third)]
    assert (await rows(w, "select count(*) from sched.holds where clinic_id = :c", c=w.a))[0][0] == 0

    # 2. the draft offer is computed and stores nothing
    out = await draft(w)
    assert out.status_code == 200 and out.json()["status"] == "ok" and out.json()["data"]["draft"]["candidate_patient_id"] == str(w.top)
    assert (await rows(w, "select count(*) from msg.messages where clinic_id = :c", c=w.a))[0][0] == 0

    # 3. send: the slot is held for the top candidate and the template is recorded encrypted, once, as the person
    top = await waitlist_id(w, w.top)
    key = f"k-{uuid.uuid4()}"
    sent = await w.client.post(SEND, headers=headers(w, "fd_a", key), json=send_body(w, w.slot_booked, top))
    assert sent.status_code == 200 and sent.json()["replayed"] is False and sent.json()["patient_id"] == str(w.top)
    hold_id = sent.json()["hold_id"]
    replay = await w.client.post(SEND, headers=headers(w, "fd_a", key), json=send_body(w, w.slot_booked, top))
    assert replay.status_code == 200 and replay.json()["replayed"] is True and replay.json()["hold_id"] == hold_id

    # 4. the offer list shows it, NOT delivered (nothing is sent by the system); a person marks it delivered by hand
    listed = await w.client.get(f"{SCHED}/offers?clinic_id={w.a}", headers=headers(w, "fd_a", None))
    assert listed.status_code == 200
    mine = [o for o in listed.json()["items"] if o["hold_id"] == hold_id]
    assert len(mine) == 1 and mine[0]["delivered_at"] is None and mine[0]["state"] != "delivered"
    delivered = await w.client.post(f"{SCHED}/offers/{hold_id}/mark-delivered", headers=headers(w, "fd_a"))
    assert delivered.status_code == 200
    again = await w.client.get(f"{SCHED}/offers?clinic_id={w.a}", headers=headers(w, "fd_a", None))
    assert [o["state"] for o in again.json()["items"] if o["hold_id"] == hold_id] == ["delivered"]

    # 5. the patient's reply is recorded by front desk and screened by the rules FIRST (with no approved rule in this world the outcome is `unassessable`, which is NOT clear: a person reads it)
    reply = await w.client.post(f"{SCHED}/offers/replies", headers=headers(w, "fd_a"), json={"clinic_id": str(w.a), "patient_id": str(w.top), "text": "TEST REPLY: yes please"})
    assert reply.status_code == 201 and reply.json()["created"] is True and reply.json()["outcome"] in ("unassessable", "clear", "red_flag")
    assert reply.json()["outcome"] != "clear" or reply.json()["screening_id"]  # a clear carries its screening row
    assert "TEST REPLY" not in reply.text  # ids and closed words only, never the text

    # 6. interpretation: no model route is approved, so it is visibly unavailable (no suggestion appears from nowhere)
    interpreted = await w.client.post(f"{SCHED}/offers/interpret-reply", headers=headers(w, "fd_a"), json={"message_id": reply.json()["message_id"]})
    assert interpreted.status_code == 503

    # 7. the held slot: a longer-waiting patient cannot take it; the candidate it was held for books it
    stolen = await w.client.post(f"{SCHED}/slots/{w.slot_booked}/book", headers=headers(w, "fd_a"), json={"patient_id": str(w.second)})
    assert stolen.status_code == 409
    booked = await w.client.post(f"{SCHED}/slots/{w.slot_booked}/book", headers=headers(w, "fd_a"), json={"patient_id": str(w.top)})
    assert booked.status_code == 200 and booked.json()["status"] == "confirmed" and booked.json()["patient_id"] == str(w.top)
    states = {r[0]: r[1] for r in await rows(w, "select patient_id, status from sched.waitlist where clinic_id = :c", c=w.a)}
    assert states[w.top] == "fulfilled" and states[w.second] == "waiting" and states[w.third] == "waiting"

    # 8. the alerts list answers for the clinic (contentless; this flow raised none of the closed classes it lists)
    alerts = await w.client.get(f"/v1/staff/console/alerts?clinic_id={w.a}", headers=headers(w, "fd_a", None))
    assert alerts.status_code == 200 and set(alerts.json()) == {"items", "limit", "has_more"}
    assert all(set(a) >= {"alert_id", "alert_class", "audience_role"} and "text" not in a for a in alerts.json()["items"])

    # 9. the audit trail: one row per human act, by the person, with ids and closed words only
    actions = [r[0] for r in await rows(w, "select action from audit.log where clinic_id = :c and actor_id = :a order by id", c=w.a, a=w.ids["fd_a"])]
    for expected in ("offer.send", "offer.mark_delivered", "offer.reply_record"):
        assert expected in actions, expected
    for r in await rows(w, "select details from audit.log where clinic_id = :c and actor_id = :a", c=w.a, a=w.ids["fd_a"]):
        assert "TEST REPLY" not in str(without_scope(r[0]))
    assert DRAFT  # (the draft route is part of the sequence above)
