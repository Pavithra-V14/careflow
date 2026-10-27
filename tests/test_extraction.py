"""The extraction pipeline (careflow/extraction.py, careflow/adapters/pdf.py; BUILD-SPEC.md §10.4, §15.3, §16.2). Offline: synthetic PDFs built in the test, fake vision and layout."""

import asyncio
import uuid

import pytest

from careflow.adapters.fake import FakeLayout, FakeVision
from careflow.adapters.pdf import PypdfExtractor
from careflow.budget import BudgetExceeded
from careflow.extraction import (
    MAX_PAGES,
    R_DOCUMENT_UNREADABLE,
    R_EXTRACTION_FAILED,
    R_IMAGE_UNREADABLE,
    R_NO_TEXT_LAYER,
    R_PAGES_NEED_VISION,
    R_VISION_UNAVAILABLE,
    ExtractionNeedsManual,
    ExtractionPipeline,
    NativePage,
    NotInWorker,
    PipelineTextSource,
    require_worker,
    useful,
    worker_context,
)
from careflow.gateway import NoApprovedRoute
from careflow.ports import DataClass, PermanentError, TransientError, VisionRequest
from tests.pdf_factory import TEXT_LONG, pdf

pytestmark = pytest.mark.asyncio
CLINIC = uuid.uuid4()
PDF = "application/pdf"
SECOND = "The second page records a normal examination and no new medication."


async def run(pipeline, content, content_type=PDF):
    with worker_context():
        return await pipeline.run(content, content_type, clinic_id=CLINIC)


def make(vision=None, layout=None):
    return ExtractionPipeline(PypdfExtractor(), vision=vision, layout=layout)


async def test_a_text_pdf_is_read_natively_with_page_and_element_provenance_and_no_model_is_called():
    vision = FakeVision()
    result = await run(make(vision), pdf([(TEXT_LONG, False), (SECOND, False)]))
    assert result.complete and result.vision_calls == 0 and vision.calls == 0
    assert [(o.page, o.tier) for o in result.pages] == [(1, "native"), (2, "native")]
    assert result.text == f"{TEXT_LONG}\n\n{SECOND}"
    first, second = result.elements
    assert (first.page, first.index, first.tier) == (1, 0, "native") and (second.page, second.index) == (2, 0)
    assert result.text[first.start : first.end] == TEXT_LONG
    assert result.locate(result.text.index("normal examination")).page == 2  # a citation span points back to its physical page
    assert result.locate(len(result.text) + 5) is None


async def test_vision_reads_only_the_page_that_failed_the_native_tier_and_the_result_is_in_document_order():
    vision = FakeVision(script={2: "Handwritten note: patient reports improving breathlessness over the week."})
    result = await run(make(vision), pdf([(TEXT_LONG, True), ("", True), (SECOND, False)]))
    assert vision.pages == [2] and result.vision_calls == 1  # 15.3: page 1 has a text layer even though it holds an image; page 3 has none to fail
    assert [(o.page, o.tier) for o in result.pages] == [(1, "native"), (2, "vision"), (3, "native")]
    assert result.pages[0].skipped_images == 1  # the image on a page whose text was read is not sent, but it is recorded, never silent
    assert result.text.index(TEXT_LONG) < result.text.index("Handwritten note") < result.text.index(SECOND)
    assert [e.kind for e in result.elements if e.page == 2] == ["image_text"] and result.complete


async def test_a_photo_upload_is_one_page_with_no_text_layer_and_goes_to_vision():
    vision = FakeVision()
    result = await run(make(vision), b"\x89PNG fake pixels", "image/png")
    assert vision.pages == [1] and result.complete and result.pages[0].tier == "vision"


async def test_the_layout_tier_goes_before_vision_and_vision_is_not_billed_for_a_page_it_read():
    layout, vision = FakeLayout(script={2: "A table of readings: systolic 120 diastolic 80 recorded at the visit."}), FakeVision()
    result = await run(make(vision, layout), pdf([(TEXT_LONG, False), ("", True)]))
    assert layout.pages == [2] and vision.calls == 0 and result.pages[1].tier == "layout"
    layout_failed = FakeLayout()  # nothing from the layout tier: the page moves on to vision
    again = await run(make(FakeVision(), layout_failed), pdf([("", True)]))
    assert again.pages[0].tier == "vision"


async def test_a_failing_layout_tier_degrades_to_the_next_tier():
    layout = FakeLayout()
    layout.fail_next(1, TransientError("layout down"))
    result = await run(make(FakeVision(), layout), pdf([("", True)]))
    assert result.pages[0].tier == "vision"


@pytest.mark.parametrize("error", [NoApprovedRoute("vlm", DataClass.PHI), TransientError("down"), PermanentError("no")])
async def test_without_a_working_vision_route_the_page_is_manual_and_the_text_is_never_returned_as_complete(error):
    vision = FakeVision()
    vision.fail_next(2, error)  # once for the direct run, once for the text source below
    source_pdf = pdf([(TEXT_LONG, False), ("", True)])
    result = await run(make(vision), source_pdf)
    assert not result.complete and result.manual_pages == (2,) and result.pages[1].reason == R_VISION_UNAVAILABLE
    assert R_PAGES_NEED_VISION in result.reasons and result.text == TEXT_LONG
    source = PipelineTextSource(make(vision), _loader(source_pdf))
    with worker_context(), pytest.raises(ExtractionNeedsManual) as raised:
        await source.text_for(uuid.uuid4())
    assert raised.value.pages == (2,) and R_VISION_UNAVAILABLE in raised.value.reasons


async def test_no_vision_adapter_at_all_is_the_same_visible_manual_outcome():
    result = await run(make(None), pdf([("", True)]))
    assert not result.complete and result.reasons == (R_VISION_UNAVAILABLE, R_PAGES_NEED_VISION)


async def test_a_blank_page_with_no_text_and_no_image_is_manual_with_its_own_reason():
    result = await run(make(FakeVision()), pdf([("", False)]))
    assert not result.complete and result.reasons == (R_NO_TEXT_LAYER,)


async def test_an_image_the_reader_cannot_hand_to_vision_makes_the_page_manual_not_silent():
    class Native:
        def read(self, content):
            return [NativePage(1, "", (), 1)]

    vision = FakeVision()
    result = await run(ExtractionPipeline(Native(), vision=vision), b"%PDF")
    assert vision.calls == 0 and result.reasons == (R_IMAGE_UNREADABLE, R_PAGES_NEED_VISION)


async def test_an_empty_vision_answer_is_a_failed_page_not_empty_text():
    result = await run(make(FakeVision(script={1: "  "})), pdf([("", True)]))
    assert not result.complete and result.reasons == (R_EXTRACTION_FAILED,)


async def test_a_spend_stop_on_vision_becomes_a_visible_manual_outcome_not_an_unheld_failure():
    # 15.2: the safety path is never budget-limited. A re-raise would leave the letter unscreened and unheld until the job dead-letters.
    vision = FakeVision()
    vision.fail_next(1, BudgetExceeded("daily", 1.0))
    result = await run(make(vision), pdf([("", True)]))
    assert not result.complete and result.reasons == (R_VISION_UNAVAILABLE, R_PAGES_NEED_VISION)


@pytest.mark.parametrize("content", [b"", b"not a pdf", b"%PDF-1.4\ntruncated", pdf([(TEXT_LONG, False)])[:60]])
async def test_a_corrupt_or_truncated_document_is_unreadable_never_an_exception(content):
    result = await run(make(FakeVision()), content)
    assert not result.complete and result.reasons == (R_DOCUMENT_UNREADABLE,) and result.text == ""


async def test_an_unsupported_content_type_and_an_oversized_document_are_unreadable():
    assert (await run(make(), b"x", "text/html")).reasons == (R_DOCUMENT_UNREADABLE,)

    class Many:
        def read(self, content):
            return [NativePage(n, TEXT_LONG) for n in range(1, MAX_PAGES + 2)]

    assert (await run(ExtractionPipeline(Many()), b"%PDF")).reasons == (R_DOCUMENT_UNREADABLE,)


async def test_extraction_refuses_to_run_outside_the_worker():
    with pytest.raises(NotInWorker):
        require_worker()
    with pytest.raises(NotInWorker):
        await make().run(pdf([(TEXT_LONG, False)]), PDF, clinic_id=CLINIC)
    with worker_context():
        require_worker()
    with pytest.raises(NotInWorker):  # the context is left behind it
        require_worker()


async def test_the_worker_context_does_not_leak_into_a_concurrent_request_task():
    async def request_path():
        with pytest.raises(NotInWorker):
            await make().run(pdf([(TEXT_LONG, False)]), PDF, clinic_id=CLINIC)

    with worker_context():
        await asyncio.create_task(asyncio.sleep(0))
    await asyncio.gather(asyncio.create_task(request_path()))


def _loader(content, content_type=PDF):
    calls = []

    async def load(document_id):
        calls.append(document_id)
        return content, content_type, CLINIC

    load.calls = calls  # type: ignore[attr-defined]
    return load


async def test_the_text_source_reads_and_bills_once_per_document_however_often_the_graph_asks():
    vision, loader = FakeVision(), _loader(pdf([("", True), (SECOND, False)]))
    source, doc = PipelineTextSource(make(vision), loader), uuid.uuid4()
    with worker_context():
        texts = await asyncio.gather(source.text_for(doc), source.text_for(doc), source.text_for(doc))
    assert len(set(texts)) == 1 and vision.calls == 1 and len(loader.calls) == 1


async def test_useful_is_a_technical_test_for_a_real_text_layer():
    assert useful(TEXT_LONG) and not useful("") and not useful("   \n ") and not useful("ab cd") and not useful("#" * 80 + " " + "!" * 40)


async def test_results_and_requests_do_not_print_page_text_or_image_bytes():
    result = await run(make(FakeVision()), pdf([(TEXT_LONG, False)]))
    assert TEXT_LONG not in repr(result)
    from careflow.ports import VisionRequest

    assert "fake pixels" not in repr(VisionRequest(b"fake pixels", "image/png", 1, CLINIC))


async def test_every_reason_the_pipeline_can_give_is_in_the_closed_vocabulary_a_hold_task_accepts():
    from careflow.extraction import REASONS
    from careflow.referral import is_fixed_reason

    assert all(is_fixed_reason(word) for word in REASONS)


# ---- the gateway-backed vision adapter -----------------------------------------------------------------------------------------------------


def _gateway(*rows, adapters=None):
    from datetime import date
    from decimal import Decimal as D

    from careflow.gateway import Gateway, InMemorySpendLedger, SpendLimits

    return Gateway(list(rows), adapters or {}, InMemorySpendLedger(today=lambda: date(2030, 1, 1)), SpendLimits(D("10.00"), D("1.00")), origin="test")


def _vlm_row(policy):
    from decimal import Decimal as D

    from careflow.gateway import ModelRow

    return ModelRow("vlm", "fake-vision-provider", "fake-vlm-1", policy, 1, D(0), D(0), D(0), D(0))


async def test_a_scanned_page_is_phi_so_a_synthetic_only_route_is_never_offered_it_and_the_page_becomes_manual():
    from careflow.adapters.vision import GatewayVision

    seen = []

    async def adapter(request):
        seen.append(request.page)
        raise AssertionError("a synthetic_only route must never receive a scanned page")

    gateway = _gateway(_vlm_row("synthetic_only"), adapters={("fake-vision-provider", "fake-vlm-1"): adapter})
    with pytest.raises(NoApprovedRoute):
        await GatewayVision(gateway).read(VisionRequest(b"px", "image/png", 1, CLINIC))
    result = await run(ExtractionPipeline(PypdfExtractor(), vision=GatewayVision(gateway)), pdf([(TEXT_LONG, False), ("", True)]))
    assert seen == [] and not result.complete and result.manual_pages == (2,) and result.pages[1].reason == R_VISION_UNAVAILABLE


async def test_with_no_registry_row_at_all_the_pipeline_still_degrades_visibly():
    from careflow.adapters.vision import GatewayVision

    result = await run(ExtractionPipeline(PypdfExtractor(), vision=GatewayVision(_gateway())), pdf([("", True)]))
    assert not result.complete and result.reasons == (R_VISION_UNAVAILABLE, R_PAGES_NEED_VISION)


async def test_a_phi_approved_route_reads_the_page_and_a_wrongly_typed_answer_fails_the_page():
    from careflow.adapters.vision import GatewayVision
    from careflow.ports import AdapterResult, Usage, VisionResult

    async def good(request):
        return AdapterResult(VisionResult("Scanned letter text: referral for review of a persistent cough."), Usage(10, 10))

    async def wrong(request):
        return AdapterResult("just a string", Usage(1, 1))

    key = ("fake-vision-provider", "fake-vlm-1")
    ok = await run(ExtractionPipeline(PypdfExtractor(), vision=GatewayVision(_gateway(_vlm_row("phi_approved"), adapters={key: good}))), pdf([("", True)]))
    assert ok.complete and ok.pages[0].tier == "vision"
    with pytest.raises(PermanentError):
        await GatewayVision(_gateway(_vlm_row("phi_approved"), adapters={key: wrong})).read(
            VisionRequest(b"px", "image/png", 1, CLINIC)
        )


async def test_the_memo_is_bounded_so_patient_text_is_not_kept_for_the_life_of_the_worker():
    from careflow.extraction import MEMO_DOCUMENTS

    source = PipelineTextSource(make(), _loader(pdf([(TEXT_LONG, False)])))
    with worker_context():
        for _ in range(MEMO_DOCUMENTS + 5):
            await source.text_for(uuid.uuid4())
    assert len(source._memo) == MEMO_DOCUMENTS and len(source._locks) <= MEMO_DOCUMENTS + 1


# ---- a text layer is not the whole page when a large image sits beside it (owner decision 2026-10-27; threshold pending confirmation) --------------

HEADER = "Memorial Clinic Referral Letter Header"
BODY_SCAN = "Handwritten body of the referral: the patient reports a persistent cough and night sweats."


async def test_a_scanned_page_with_a_typed_header_sends_the_large_image_to_vision_and_keeps_the_header_first():
    vision = FakeVision(script={1: BODY_SCAN})
    result = await run(make(vision), pdf([(HEADER, 600)]))
    assert vision.pages == [1] and result.complete and result.pages[0].vision_images == 1 and result.pages[0].skipped_images == 0
    assert result.text.startswith(HEADER) and BODY_SCAN in result.text and result.text.index(HEADER) < result.text.index(BODY_SCAN)
    assert [(e.tier, e.kind) for e in result.elements] == [("native", "text"), ("vision", "image_text")]
    assert result.locate(result.text.index("night sweats")).page == 1


async def test_a_small_logo_or_stamp_is_not_sent_but_is_recorded():
    vision = FakeVision()
    result = await run(make(vision), pdf([(TEXT_LONG, 60)]))
    assert vision.calls == 0 and result.complete and result.pages[0].skipped_images == 1 and result.pages[0].vision_images == 0


async def test_the_threshold_is_twenty_percent_of_the_page_area():
    from careflow.extraction import IMAGE_COVERAGE_THRESHOLD

    assert IMAGE_COVERAGE_THRESHOLD == 0.20
    just_under, just_over = FakeVision(), FakeVision()
    await run(make(just_under), pdf([(TEXT_LONG, 310)]))  # 310*310 / (612*792) = 19.8%
    await run(make(just_over), pdf([(TEXT_LONG, 312)]))  # 20.08%
    assert just_under.calls == 0 and just_over.calls == 1


async def test_an_image_whose_size_cannot_be_determined_counts_as_large_and_is_sent():
    vision = FakeVision(script={1: BODY_SCAN})
    result = await run(make(vision), pdf([(TEXT_LONG, "unmeasured")]))
    assert vision.pages == [1] and result.complete and result.pages[0].vision_images == 1


async def test_a_large_image_with_no_usable_vision_route_is_a_nurse_hold_not_a_clear_page():
    for vision in (None, FakeVision()):
        if vision is not None:
            vision.fail_next(1, NoApprovedRoute("vlm", DataClass.PHI))
        result = await run(make(vision), pdf([(HEADER, 600)]))
        assert not result.complete and result.manual_pages == (1,) and result.reasons == (R_VISION_UNAVAILABLE, R_PAGES_NEED_VISION)
    source = PipelineTextSource(make(None), _loader(pdf([(HEADER, 600)])))
    with worker_context(), pytest.raises(ExtractionNeedsManual):
        await source.text_for(uuid.uuid4())


async def test_a_large_image_the_reader_cannot_hand_to_vision_holds_the_page():
    class Native:
        def read(self, content):
            return [NativePage(1, TEXT_LONG, (), 1)]  # text read, plus one image of unknown size that cannot be sent

    result = await run(ExtractionPipeline(Native(), vision=FakeVision()), b"%PDF")
    assert not result.complete and R_IMAGE_UNREADABLE in result.reasons


async def test_a_vision_registry_row_cannot_be_built_without_an_explicit_timeout():
    from decimal import Decimal as D

    from careflow.adapters.vision import vlm_row

    prices = {"price_in_per_1k": D(0), "price_out_per_1k": D(0), "shadow_in_per_1k": D(0), "shadow_out_per_1k": D(0)}
    base = {"provider": "p", "model_id": "m-1", "data_policy": "phi_approved", "priority": 1, **prices}
    with pytest.raises(TypeError):
        vlm_row(**base)  # type: ignore[call-arg]
    row = vlm_row(**base, timeout_seconds=45.0)  # a test input, not a recommendation
    assert row.role == "vlm" and row.timeout_seconds == 45.0
