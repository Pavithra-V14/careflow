"""The vision role (`vlm`) behind `VisionPort`, routed by the gateway (BUILD-SPEC.md §5.1, §10.4, §17.6; CLAUDE.md rule 7).

A scanned page is patient data. EVERY request is sent as `DataClass.PHI`, so the gateway offers it only to a registry row whose data policy is `phi_approved`; with none, `NoApprovedRoute`
is raised and the pipeline turns that page into a manual task showing the image (clinical profile without an approved route: visible, never silent). No row, client, key or hosted
service is created here: the gateway's registry decides which adapters exist, and the only vision adapter in the tree is the fake (`careflow.adapters.fake.FakeVision`).
Gemini (the §10.4 choice) is NOT approved, so there is no real route; when one is approved it is registered as a gateway adapter returning `AdapterResult[VisionResult]`.
"""

from decimal import Decimal

from careflow.gateway import Gateway, ModelRow
from careflow.ports import DataClass, PermanentError, VisionRequest, VisionResult


class GatewayVision:
    def __init__(self, gateway: Gateway) -> None:
        self._gateway = gateway

    async def read(self, request: VisionRequest) -> VisionResult:
        outcome = await self._gateway.call("vlm", request, data_class=DataClass.PHI, clinic_id=request.clinic_id)
        value = outcome.value
        if not isinstance(value, VisionResult):  # a mis-registered adapter fails the page, it does not produce text
            raise PermanentError("the vlm route did not return a VisionResult")
        return value


def vlm_row(
    *, provider: str, model_id: str, data_policy: str, priority: int, timeout_seconds: float,
    price_in_per_1k: Decimal, price_out_per_1k: Decimal, shadow_in_per_1k: Decimal, shadow_out_per_1k: Decimal,
) -> ModelRow:
    """The ONLY way code should build a `vlm` registry row. `timeout_seconds` (BUILD-SPEC 16.1: a per-page limit for vision, worker only) is REQUIRED with no default: a hung page must not
    hold the screen node, and the limit is a decision for whoever approves the route, not a value we pick. Every price is required as well (a free tier still states its shadow price)."""
    return ModelRow("vlm", provider, model_id, data_policy, priority, price_in_per_1k, price_out_per_1k, shadow_in_per_1k, shadow_out_per_1k, timeout_seconds=timeout_seconds)
