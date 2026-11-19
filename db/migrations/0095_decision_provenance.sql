-- 0095_decision_provenance: BUILD-SPEC.md 9.5 ("every agent decision records provenance: the exact resource ids WITH version numbers it read, the card version, the rule ids").
-- Owner decision 2026-11-18. IDS AND VERSIONS ONLY, NEVER TEXT.
--
-- agents.decision_provenance: one row per fact a triage proposal read. A row is a closed `kind`, an opaque `ref` (an id, or for a rule set the sha256 identity of the rule set in force) and an optional
-- integer `version`:
--   * `document`  the referral document id (it has no version: null);
--   * `patient`   the patient id the proposal is for (no version: null; the record is not versioned);
--   * `card`      a candidate card id and its card version;
--   * `rule_set`  the identity (sha256 of "rule_id:version_id" lines) of the red-flag rule set the screening ran against: it names every rule and version by hash, so "which proposals ran
--                 against this rule set" is answered by joining the hash, and no rule text is stored;
--   * `rule`      a rule id and version when one fired (no rule fires on a clear letter: a clear screening has `rule_set` only).
-- The point (9.5): a CORRECTION to a fact must be able to find the decisions that read it by id and version, not by guessing. `ref` is pattern-checked to ids and hashes, so no sentence, name or value can
-- be put in it. Append-only (the triggers of 0018 are reused); one row per (proposal, kind, ref, version); written by the same roles that may write the proposal (queue worker, nurse, doctor) under the
-- same row-level policy; readable by those roles. No update or delete grant. No new function. No route exposes it (a later correction feature reads it through `careflow/provenance_sql.py`).
-- Engine, model id, confidence and rule that decided are already on the proposal's candidates (0018 and `careflow/decision_info.py`) and are not copied here.

create table agents.decision_provenance (
    id          uuid primary key default gen_random_uuid(),
    clinic_id   uuid not null references iam.clinics (id),
    origin      text not null check (origin in ('live', 'test', 'eval', 'canary')),
    proposal_id uuid not null,
    kind        text not null check (kind in ('document', 'patient', 'card', 'rule_set', 'rule')),
    ref         text not null check (ref ~ '^[A-Za-z0-9][A-Za-z0-9_.:\-]{0,99}$'),
    version     integer check (version is null or version >= 0),
    created_at  timestamptz not null default clock_timestamp(),
    foreign key (proposal_id, clinic_id, origin) references agents.triage_proposals (id, clinic_id, origin)
);

create unique index decision_provenance_one_fact on agents.decision_provenance (proposal_id, kind, ref, coalesce(version, -1));
create index decision_provenance_by_fact on agents.decision_provenance (clinic_id, kind, ref, version);

create trigger decision_provenance_no_update_delete before update or delete on agents.decision_provenance
    for each row execute function agents.append_only();
create trigger decision_provenance_no_truncate before truncate on agents.decision_provenance
    for each statement execute function agents.append_only();

alter table agents.decision_provenance enable row level security;
alter table agents.decision_provenance force row level security;

create policy decision_provenance_insert on agents.decision_provenance for insert to careflow_app
    with check (iam.has_clinic_role(clinic_id, array['queue_worker', 'nurse', 'doctor']));
create policy decision_provenance_select on agents.decision_provenance for select to careflow_app
    using (iam.has_clinic_role(clinic_id, array['queue_worker', 'nurse', 'doctor']));

revoke all on agents.decision_provenance from public;
grant select on agents.decision_provenance to careflow_app;
grant insert (clinic_id, origin, proposal_id, kind, ref, version) on agents.decision_provenance to careflow_app;

comment on table agents.decision_provenance is 'Ids and versions of the facts a triage proposal read (spec 9.5). Never text. Append-only. Found by "which decisions read this id at this version".';

do $$
begin
    if not (select relrowsecurity and relforcerowsecurity from pg_class where oid = 'agents.decision_provenance'::regclass) then
        raise exception '0095: agents.decision_provenance must have forced row-level security';
    end if;
    if (select count(*) from pg_policies where schemaname = 'agents' and tablename = 'decision_provenance') <> 2 then
        raise exception '0095: agents.decision_provenance must have exactly two policies';
    end if;
    if has_table_privilege('careflow_app', 'agents.decision_provenance', 'update') or has_table_privilege('careflow_app', 'agents.decision_provenance', 'delete')
       or has_table_privilege('public', 'agents.decision_provenance', 'select') then
        raise exception '0095: unexpected privilege on agents.decision_provenance';
    end if;
end $$;
