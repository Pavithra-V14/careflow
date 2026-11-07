"""A FAKE decision engine for the DEMO profile (BUILD-SPEC.md 5.2), kept out of careflow/demo_front_door.py so that module can promise it imports no gateway. Built only under the demo gate (careflow/demo_front_door.py).
"""

from careflow.ports import AdapterResult, DecisionResult, Usage


class DemoReplyEngine:
    """A FAKE decision engine for the DEMO profile: a reply that starts with the word `yes` is an accept, one that starts with `no` a decline, anything else is unclear. DEMO ONLY - NOT A CLINICAL DECISION:
    it understands two English words and nothing else, its confidence is made up and never calibrated, and it exists so the demo can show the whole backfill flow on synthetic replies."""

    async def __call__(self, request):  # an adapter for the gateway: (request) -> AdapterResult
        words = request.text.strip().lower().split()
        choice = "accept" if words[:1] == ["yes"] else "decline" if words[:1] == ["no"] else "unclear"
        probabilities = {option: (0.9 if option == choice else 0.1 / (len(request.options) - 1)) for option in request.options}
        return AdapterResult(DecisionResult(choice, probabilities, probabilities[choice], "demo-fake", False, "demo-fake"), Usage(1, 1))


def demo_decision_service():
    from decimal import Decimal

    from careflow.decision import DecisionService, InMemoryDecisionRecorder
    from careflow.gateway import Gateway, InMemorySpendLedger, ModelRow, SpendLimits

    zero = Decimal(0)
    row = ModelRow("decision", "demo", "demo-fake", "phi_approved", 1, zero, zero, zero, zero)  # (phi_approved only inside the demo profile with synthetic data: this is built only under the demo gate)
    return DecisionService(Gateway([row], {("demo", "demo-fake"): DemoReplyEngine()}, InMemorySpendLedger(), SpendLimits(clinic_daily_usd=Decimal(1)), origin="test"), InMemoryDecisionRecorder())
