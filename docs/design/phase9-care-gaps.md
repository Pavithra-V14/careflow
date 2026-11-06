# Phase 9: Care gap review, mechanism level, 2026-11-06

BUILD-SPEC.md 24 (with 9.3, 12.6, 22.4). Code: `careflow/care_gaps.py`. No new agent and no new automated action: the panel reads, and the only thing it can do is raise the existing overdue alert.

## What is built

- **Three sources, one panel**: overdue recurrence (a care episode's rule), after-the-fact review (the auto-approved sample), unresolved social/resource barriers. Each source is a port returning open items and resolving an id back to its record.
- **Recurrence arithmetic** for clinician-authored rules (`RecurrenceRule`: an interval in days or an annual month, plus `overdue_after_days`, no default). A malformed rule can never make anything overdue. A never-done patient is *unknown*, not overdue and not fine.
- **Unknown is not zero**: an unreadable or unwired source is reported with a count of `None`, never 0, and does not hide the others. An undated item is counted, listed as undated, and has no age.
- **Rows carry only a source, an opaque id and an age**; the reader opens the record with their own role. A source cannot leak another source's items in, and closed items are not shown.
- **Metrics**: `metric_values` produces `care_gap_open_count` and `care_gap_median_age` by source, using the ids defined once in `metrics-catalog.yaml` (a test checks they exist there).
- **The overdue alert** reuses the existing `task_overdue` class (audience nurse) with a per-item key so a second pass raises nothing new. The age limit is a required setting; unset means the check does not run and says `alert_unconfigured`.
- **The reachability gate** (`verify_reachable`): every row resolves to a real, still-open item of its own source, and no real open item is missing from the panel. Tested both ways on seeded data.

## Not built, needs a decision or a package

1. **The data**: care episodes and recurrence rules, barrier records, and the after-the-fact sample have no tables yet (they arrive with the storage packages of Phases 6 and 7, and the sampling step of 12.6). The sources are ports with fakes, so the gate is proven on seed data in the test, not on the database.
2. **The route and the panel**: a read-only `GET` for the Live dashboard panel needs a role-matrix case and a decision on who may see it (the catalog owner is the clinical lead). The after-the-fact error rate comes from the Phase 4 reviewer statistics.
3. **The alert age** and who is the audience (today the existing class goes to the nurse role) are owner/clinician settings.
4. **Episode content** (for example an antenatal schedule) is clinician-authored data to be loaded like protocol cards.
