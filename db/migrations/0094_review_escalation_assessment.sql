-- 0094_review_escalation_assessment: BUILD-SPEC.md 14.4 ("escalation accuracy needs two reason codes on every reviewed task"). Owner decision 2026-11-18.
--
-- ONE optional column on the human review decision: `escalation_assessment`, a CLOSED pair of words and nothing else (no free text, no number):
--   * `unnecessary_escalation` the reviewer judges the case did not need to be escalated (over-escalation);
--   * `missed_by_system`       the reviewer judges the system should have escalated and did not (under-escalation).
-- NULL means NOT RECORDED. It is never read as "accurate" and never defaults to a word (unknown is not a value, CLAUDE.md rule 3). The column is written only with the decision, in the same insert, by the
-- same restricted grant and the same row-level policy as every other column of that row (a nurse or doctor of the clinic, as themselves, for a proposal of their clinic); it is append-only like the rest
-- of the table (the update, delete and truncate triggers of 0018 apply), so it cannot be edited afterwards. Nothing about HOW a decision is approved changes: approve, edit and reject, the two-reviewer
-- review task and the reason-code rule are untouched, and a decision without the field is exactly what it was. The decision row is itself the permanent audit record of the human act (decided_by,
-- clinic, time, key); the assessment adds a closed word to it and no personal data.
-- No new table, no new function, and no change of who may read: `agents.review_decisions` keeps its existing select policy. Reviewer statistics (0040) are unchanged.

alter table agents.review_decisions
    add column escalation_assessment text check (escalation_assessment is null or escalation_assessment in ('unnecessary_escalation', 'missed_by_system'));

grant insert (escalation_assessment) on agents.review_decisions to careflow_app;

comment on column agents.review_decisions.escalation_assessment is 'Optional, closed (unnecessary_escalation | missed_by_system). NULL = not recorded, never "accurate". Spec 14.4.';

do $$
begin
    if has_column_privilege('careflow_app', 'agents.review_decisions', 'escalation_assessment', 'update') then
        raise exception '0094: careflow_app must not be able to update escalation_assessment';
    end if;
    if not has_column_privilege('careflow_app', 'agents.review_decisions', 'escalation_assessment', 'insert') then
        raise exception '0094: careflow_app must be able to insert escalation_assessment';
    end if;
    if (select count(*) from pg_trigger t where t.tgrelid = 'agents.review_decisions'::regclass and not t.tgisinternal) < 2 then
        raise exception '0094: the append-only triggers of agents.review_decisions are missing';
    end if;
end $$;
