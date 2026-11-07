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

# metrics-catalog.yaml: the ids, read once at import (the catalog is the only list of metrics)
METRIC_IDS: tuple[str, ...] = tuple(m["id"] for m in metrics_catalog.load()["metrics"])
MetricId = Literal[METRIC_IDS]  # type: ignore[valid-type]  # built from the catalog; the OpenAPI document carries the enum
