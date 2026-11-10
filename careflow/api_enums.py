"""The closed word lists the API names in its schema, so a client can generate types from them instead of guessing (OpenAPI enums). Each list mirrors a database check or a catalog file, and
tests/test_api_enums.py fails when one drifts: the database check, the catalog and the graph's own words stay the source; this file only names them."""

from __future__ import annotations

from typing import Literal

from careflow import metrics_catalog

# ops.care_plans.status (migrations 0050, 0057)
CarePlanStatus = Literal["screening", "awaiting_confirmation", "active", "completed", "rejected", "held", "erased"]

# the words the Care Journey graph reports as a plan's progress (careflow/care_journey.py): the `status` of a run, not of the stored plan
CarePlanProgress = Literal[
    "started", "proposed", "screened", "held_unassessable", "red_flag_escalated", "plan_invalid", "confirmation_requested", "active", "rejected", "waiting", "result_in", "claimed", "claim_lost",
    "overdue", "condition_met", "condition_not_met", "referral_proposed", "step_closed", "needs_doctor", "no_more_waits", "completed",
]

# the three places a care gap comes from (careflow/care_gaps.py SOURCES)
CareGapSource = Literal["overdue_recurrence", "after_the_fact_review", "unresolved_barrier"]

# ops.alerts.class (migration 0039)
AlertClass = Literal["red_flag_overdue", "task_overdue", "task_escalated", "dead_letters", "queue_age", "canary_approved", "spend_runaway", "delivery_failed", "fallback_spike", "tracing_cap"]

# msg.messages.sender_kind and topic (migrations 0011, 0050)
SenderKind = Literal["patient", "staff", "system"]
MessageTopic = Literal["care", "appointments", "safety", "readiness"]

# msg.voice_sessions.workflow and status (migration 0045)
VoiceWorkflow = Literal["slot_offer", "intake_question", "readiness_item"]
VoiceSessionStatus = Literal["open", "ended"]

# careflow/reviewer_stats.py Verdict
ReviewerVerdict = Literal["not_configured", "too_few", "ok", "review"]

# careflow/cost_canvas.py: the state of the canvas and every reason it can give for being unconfigured or short of data
CostState = Literal["insufficient_data", "unconfigured", "ok"]
CostReason = Literal[
    "fewer than seven days of measured traffic", "no completed workflows", "platform floor (P) is not configured", "reviewer rates are not configured", "a reviewer role has no configured rate",
    "reviewer minutes are not measured yet", "retry cost is not measured yet",
]

# careflow/visit_readiness.py: the words a readiness run reports as its status
ReadinessStatus = Literal[
    "started", "nothing_to_ask", "asked", "reply_in", "screened", "red_flag_escalated", "held_unassessable", "classified", "fact_written", "fact_not_grounded", "next", "readiness_recorded", "nurse_escalated",
    "incomplete_flagged", "brief_stored", "brief_unavailable", "no_reply", "barrier_classified", "barrier_unknown", "rebooking_handed_off",
]

# metrics-catalog.yaml: the ids, read once at import (the catalog is the only list of metrics)
METRIC_IDS: tuple[str, ...] = tuple(m["id"] for m in metrics_catalog.load()["metrics"])
MetricId = Literal[METRIC_IDS]  # type: ignore[valid-type]  # built from the catalog; the OpenAPI document carries the enum
