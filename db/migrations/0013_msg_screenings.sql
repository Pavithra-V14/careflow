-- 0013_msg_screenings: the record that a stored message WAS screened for red flags, and the one function allowed to write it
-- (BUILD-SPEC.md §5.2, §11.6, §12.1, §20.2; CLAUDE.md rules 1, 2, 3, 5, 7, 8, 9, 10). Design: docs/design/msg-screening-binding-design.md
-- (amendments v3 .. v3.6; this file is the migration called "0012 screenings" there).
--
-- * msg.screenings: one row per screening of a message. A message with NO row has never been screened: that is not "clean"
--   (rule 3). Rows are written ONLY by msg.record_screening: the application role has SELECT and nothing else.
-- * msg.record_screening: a single SECURITY DEFINER function, callable by nurse, doctor, queue_worker of the message's clinic and
--   by front_desk only for appointments / safety messages. It REFUSES a red_flag or unassessable ONLY for authorisation, an
--   unknown or erased message, a wrong transaction isolation level, or an outcome that is not one of the three values
--   (a caller bug, not a red flag). It never refuses them for a count, hash, provenance, key, profile, uniqueness or task-linking
--   reason (rule 2: a safety escalation must always be recordable). Only `clear` is fail-closed: any doubt refuses it with the
--   fixed message CLEAR_REFUSED, and the caller then records `unassessable`, which raises the nurse task.
-- * Every key the function writes (the review task, the audit row, the screening) is generated inside it from gen_random_uuid(); the
--   caller's key is stored only as sha256 in a NON-unique column for replay detection, so no predictable or planted key can make a
--   red flag fail.
-- * Serialisation per thread: the function and the table trigger take `select 1 from msg.threads ... for no key update`. The
--   application role cannot take that lock (no UPDATE privilege on msg.threads or msg.messages, 0011), no advisory lock is used
--   anywhere (any session could hold one), and key rotation never touches thread rows, so maintenance cannot delay an escalation.
-- * The message INSERT policy is widened to sender_kind 'patient' (a message RELAYED by staff on a patient's behalf; recorded_by
--   names who typed it), together with a DEFERRED constraint: a patient-kind message cannot commit without a screening.
-- * agents.review_tasks: front desk and admin lose access to MESSAGE-sourced tasks (red_flag, screening_unassessable): rule codes and
--   the existence of a flag can reveal a condition, and admin has no case-level access (18.4). They keep `unmerge_review`. Admin
--   escalation (12.3) will use alerts with class, count and opaque id only (Phase 4 watchdog).
--
-- One deliberate exception to the "existence of a flag can reveal a condition" rationale above: front desk can read the
-- OUTCOME and the task uuid of a screening for appointments and safety messages (it can read those bodies already, by decision),
-- but never the task, and never anything about a care message.
--
-- RESIDUAL RISKS, stated rather than hidden (BUILD-SPEC.md §18.1): a database cannot prove the Python rule engine ran, nor that the
-- text it screened is the text stored. Anyone who can run arbitrary SQL as careflow_app can set the request claims and call
-- msg.record_screening with a dishonest outcome; the function narrows who may call it and rejects every inconsistent combination, it
-- does not authenticate the engine. A staff member can mislabel a relayed patient message as sender_kind 'staff', which avoids the
-- deferred screening requirement; msg.message_screening_status lists every unscreened message regardless of label. The migrator can
-- disable triggers / set session_replication_role, as for audit.log. The rule-set hash covers rule ids and versions, not text
-- (kb.red_flag_rules rows are never edited in place by convention; an immutability trigger is on the deferred list).
--
-- Screening rows are never training labels (CLAUDE.md rule 9).

-- ---- prerequisite: lets screenings prove the task shares the message's clinic AND origin -------------------------------------------

alter table agents.review_tasks add constraint review_tasks_id_clinic_origin_key unique (id, clinic_id, origin);

-- ---- small helpers (not granted to any role) -----------------------------------------------------------------------------------------

-- The ONE error used for every authorisation refusal: identical to a real RLS denial, so it is no existence oracle.
create function msg.deny_screening() returns void
    language plpgsql set search_path = ''
as $$
begin
    raise exception using errcode = '42501', message = 'new row violates row-level security policy for table "screenings"';
end
$$;

-- The rules IN FORCE for (clinic or platform-wide, origin, profile): the same walk as careflow/rules.py select_active_versions.
-- Per (clinic scope, rule_code) the NEWEST version whose status is `retired` or usable by the profile decides: if it is retired the rule
-- is withdrawn, otherwise it is in force. clinical uses `approved` only; demo also uses `draft_unreviewed`. Any other profile is
-- treated as clinical (unknown is not permissive). Reads kb.red_flag_rules as the owner (which bypasses RLS, as has_clinic_role relies on).
create function kb.active_red_flag_rules(p_clinic uuid, p_origin text, p_profile text)
    returns table (id uuid, rule_code text, version_id integer, status text, has_criteria boolean)
    language sql stable security definer set search_path = ''
as $$
    select d.id, d.rule_code, d.version_id, d.status, (cardinality(d.phrases) > 0 or cardinality(d.patterns) > 0) as has_criteria
    from (
        select distinct on (coalesce(r.clinic_id, '00000000-0000-0000-0000-000000000000'::uuid), r.rule_code)
               r.id, r.rule_code, r.version_id, r.status, r.phrases, r.patterns
        from kb.red_flag_rules r
        where (r.clinic_id = p_clinic or r.clinic_id is null)
          and r.origin = p_origin
          and (r.status = 'retired'
               or r.status = 'approved'
               or (r.status = 'draft_unreviewed' and p_profile is not distinct from 'demo'))
        order by coalesce(r.clinic_id, '00000000-0000-0000-0000-000000000000'::uuid), r.rule_code, r.version_id desc
    ) d
    where d.status <> 'retired'
$$;

-- Keeps at most 50 well-formed matches from a caller-supplied provenance and counts the rest as dropped. It can never raise: every
-- cast is guarded by a regular expression or a type test first. Returns {matches, dropped, truncated}. Patient text cannot be carried:
-- only these keys, with these exact value shapes, survive.
create function msg.sanitise_matches(p jsonb) returns jsonb
    language plpgsql immutable set search_path = ''
as $$
declare
    v_arr jsonb;
    v_elem jsonb;
    v_kept jsonb := '[]'::jsonb;
    v_n integer := 0;
    v_dropped integer := 0;
    v_truncated boolean := false;
    v_uuid constant text := '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$';
begin
    if p is null then
        return jsonb_build_object('matches', '[]'::jsonb, 'dropped', 0, 'truncated', false);
    end if;
    if jsonb_typeof(p) <> 'object' then
        return jsonb_build_object('matches', '[]'::jsonb, 'dropped', 1, 'truncated', false);
    end if;
    v_arr := p -> 'matches';
    if v_arr is null then
        return jsonb_build_object('matches', '[]'::jsonb, 'dropped', 0, 'truncated', false);
    end if;
    if jsonb_typeof(v_arr) <> 'array' then
        return jsonb_build_object('matches', '[]'::jsonb, 'dropped', 1, 'truncated', false);
    end if;
    for v_elem in select e.value from jsonb_array_elements(v_arr) as e(value) loop
        if jsonb_typeof(v_elem) <> 'object'
           or exists (select 1 from jsonb_object_keys(v_elem) k
                      where k not in ('rule_id', 'rule_code', 'version_id', 'rule_status', 'kind', 'index', 'start', 'end'))
           or coalesce(jsonb_typeof(v_elem -> 'rule_id'), '') <> 'string' or (v_elem ->> 'rule_id') !~ v_uuid
           or coalesce(jsonb_typeof(v_elem -> 'rule_code'), '') <> 'string' or (v_elem ->> 'rule_code') !~ '^[a-z][a-z0-9_]{0,63}$'
           or coalesce(jsonb_typeof(v_elem -> 'rule_status'), '') <> 'string'
           or (v_elem ->> 'rule_status') not in ('draft_unreviewed', 'approved', 'retired')
           or coalesce(jsonb_typeof(v_elem -> 'kind'), '') <> 'string' or (v_elem ->> 'kind') not in ('phrase', 'pattern')
           or coalesce(jsonb_typeof(v_elem -> 'version_id'), '') <> 'number' or (v_elem ->> 'version_id') !~ '^[0-9]{1,7}$'
           or coalesce(jsonb_typeof(v_elem -> 'index'), '') <> 'number' or (v_elem ->> 'index') !~ '^[0-9]{1,3}$'
           or coalesce(jsonb_typeof(v_elem -> 'start'), '') <> 'number' or (v_elem ->> 'start') !~ '^[0-9]{1,7}$'
           or coalesce(jsonb_typeof(v_elem -> 'end'), '') <> 'number' or (v_elem ->> 'end') !~ '^[0-9]{1,7}$'
        then
            v_dropped := v_dropped + 1;
            continue;
        end if;
        if (v_elem ->> 'version_id')::integer not between 1 and 1000000
           or (v_elem ->> 'index')::integer > 499
           or (v_elem ->> 'start')::integer > 1000000 or (v_elem ->> 'end')::integer > 1000000 then
            v_dropped := v_dropped + 1;
            continue;
        end if;
        if v_n >= 50 then
            v_truncated := true;
            continue;
        end if;
        v_kept := v_kept || jsonb_build_array(v_elem);
        v_n := v_n + 1;
    end loop;
    return jsonb_build_object('matches', v_kept, 'dropped', v_dropped, 'truncated', v_truncated);
end
$$;

-- ---- msg.screenings --------------------------------------------------------------------------------------------------------------

create table msg.screenings (
    id                      uuid primary key default gen_random_uuid(),
    clinic_id               uuid not null,
    origin                  text not null check (origin in ('live', 'test', 'eval', 'canary')),
    topic                   text not null constraint screenings_topic_check check (topic in ('care', 'appointments', 'safety')),
    message_id              uuid not null,
    outcome                 text not null check (outcome in ('red_flag', 'clear', 'unassessable')),
    profile                 text not null check (profile in ('demo', 'clinical')),
    -- The values the DATABASE computed from kb.red_flag_rules for this message's clinic, origin and profile.
    rules_evaluated         integer not null check (rules_evaluated >= 0),
    rule_set_hash           text not null check (rule_set_hash ~ '^[0-9a-f]{64}$'),
    -- true when the in-force rule set could not be computed (zero hash, zero count). Such a row is NEVER a clear.
    rule_set_unverified     boolean not null default false,
    -- What the caller claimed, kept for audit. NULL when the claim was missing or malformed (never refused for a red flag).
    claimed_rules_evaluated integer check (claimed_rules_evaluated between 0 and 100000),
    claimed_rule_set_hash   text check (claimed_rule_set_hash ~ '^[0-9a-f]{64}$'),
    claimed_values_invalid  boolean not null default false,
    -- sha256 of the caller's key and of the caller's provenance as received: replay detection only. NOT unique.
    key_sha256              text not null check (key_sha256 ~ '^[0-9a-f]{64}$'),
    provenance_sha256       text not null check (provenance_sha256 ~ '^[0-9a-f]{64}$'),
    review_task_id          uuid,
    -- # CLINICAL-PLACEHOLDER: needs clinician sign-off
    -- The emergency reply to the patient is NOT sent by anything yet: this column only records that it is pending a clinician-approved
    -- template. No emergency wording is stored or written anywhere. A later migration adds 'sent' once such a template exists.
    patient_reply           text not null check (patient_reply in ('not_required', 'pending_clinician_template')),
    screened_by             uuid not null references iam.accounts (id),
    created_at              timestamptz not null default clock_timestamp(),
    -- Rule 5. Generated inside record_screening from gen_random_uuid(); never derived from anything a caller controls.
    idempotency_key         text not null unique check (length(idempotency_key) > 0),
    foreign key (message_id, clinic_id, origin, topic) references msg.messages (id, clinic_id, origin, topic),
    foreign key (review_task_id, clinic_id, origin) references agents.review_tasks (id, clinic_id, origin),
    -- A later red_flag under an unchanged rule set (a model escalation, a normalisation fix) must stay recordable, so the outcome is part of the key.
    unique (message_id, profile, rule_set_hash, outcome),
    -- A red flag or unassessable ALWAYS has its nurse task; a clear never has one.
    constraint screenings_task_iff_not_clear check ((outcome = 'clear') = (review_task_id is null)),
    -- A demo-profile clear can never stand for a live message.
    constraint screenings_no_demo_clear_on_live check (outcome <> 'clear' or profile = 'clinical' or origin <> 'live'),
    -- A clear needs a real, verified, non-empty rule set and honest claims.
    constraint screenings_clear_is_verified check (
        outcome <> 'clear' or (rules_evaluated > 0 and not rule_set_unverified and not claimed_values_invalid)
    ),
    constraint screenings_reply_iff_red_flag check ((outcome = 'red_flag') = (patient_reply = 'pending_clinician_template'))
);

comment on table msg.screenings is
    'Written ONLY by msg.record_screening. A screening is not bound to the text that was screened (BUILD-SPEC 18.1). Never a training label (CLAUDE.md rule 9).';
comment on column msg.screenings.patient_reply is
    '# CLINICAL-PLACEHOLDER: needs clinician sign-off. The emergency reply is NOT sent; pending_clinician_template only records that a clinician-approved template does not exist yet.';
comment on column msg.screenings.review_task_id is
    'For unassessable the task priority is standard: # CLINICAL-PLACEHOLDER: needs clinician sign-off (pending clinical lead confirmation).';

create index screenings_message_idx on msg.screenings (message_id, created_at);
create index screenings_key_idx on msg.screenings (message_id, key_sha256);

-- Applies to EVERY writer, including the function and the owner. Takes the same thread lock as record_screening.
create function msg.screenings_guard() returns trigger
    language plpgsql security definer set search_path = ''
as $$
declare
    v_thread uuid;
    v_task record;
begin
    if current_setting('transaction_isolation') <> 'read committed' then
        raise exception 'screening needs READ COMMITTED' using errcode = '25000';
    end if;
    select m.thread_id into v_thread from msg.messages m where m.id = new.message_id;
    if v_thread is null then
        perform msg.deny_screening();
    end if;
    perform 1 from msg.threads t where t.id = v_thread for no key update;
    -- A clear can never stand next to a red flag (read side: msg.message_screening_status is worst-of in any case).
    if new.outcome = 'clear' and exists (select 1 from msg.screenings s where s.message_id = new.message_id and s.outcome = 'red_flag') then
        raise exception 'CLEAR_REFUSED' using errcode = 'P0001';
    end if;
    if new.review_task_id is not null then
        select t.kind, t.assigned_role, t.status, t.source_message_id into v_task
          from agents.review_tasks t where t.id = new.review_task_id;
        if v_task.kind is distinct from (case new.outcome when 'red_flag' then 'red_flag' when 'unassessable' then 'screening_unassessable' end)
           or v_task.assigned_role is distinct from 'nurse'
           or v_task.status is distinct from 'open'
           or v_task.source_message_id is distinct from new.message_id::text then
            raise exception 'screening link refused' using errcode = '23514';
        end if;
    end if;
    return new;
end
$$;

create trigger screenings_guard before insert on msg.screenings
    for each row execute function msg.screenings_guard();

-- Immutable for every role: a screening is never edited or removed.
create function msg.screenings_immutable() returns trigger
    language plpgsql set search_path = ''
as $$
begin
    raise exception 'msg.screenings is append-only: % is not permitted', tg_op using errcode = 'restrict_violation';
end
$$;
create trigger screenings_no_update_delete before update or delete on msg.screenings
    for each row execute function msg.screenings_immutable();
create trigger screenings_no_truncate before truncate on msg.screenings
    for each statement execute function msg.screenings_immutable();

-- ---- msg.record_screening ----------------------------------------------------------------------------------------------------------

create function msg.record_screening(
    p_message_id uuid,
    p_outcome text,
    p_profile text,
    p_rules_evaluated integer,
    p_rule_set_hash text,
    p_match_provenance jsonb,
    p_idempotency_key text
) returns table (screening_id uuid, replayed boolean, recorded_outcome text)
    language plpgsql security definer set search_path = ''
as $$
declare
    v_msg record;
    v_erased timestamptz;
    v_profile text;
    v_key_sha text;
    v_prov_sha text;
    v_count integer;
    v_hash text;
    v_unverified boolean := false;
    v_claim_count integer;
    v_claim_hash text;
    v_claims_invalid boolean;
    v_sanitised jsonb;
    v_matches jsonb := '[]'::jsonb;
    v_truncated boolean := false;
    v_dropped integer := 0;
    v_unverified_matches boolean := false;
    v_caller_truncated boolean;
    v_prov jsonb;
    v_kind text;
    v_priority text;
    v_task uuid;
    v_sid uuid;
    v_existing record;
begin
    -- An outcome that is not one of the three is a caller bug, not a red flag: refuse loudly. NULL-safe.
    if p_outcome is null or p_outcome not in ('red_flag', 'clear', 'unassessable') then
        raise exception 'invalid screening outcome' using errcode = '22023';
    end if;
    if current_setting('transaction_isolation') <> 'read committed' then
        raise exception 'screening needs READ COMMITTED' using errcode = '25000';
    end if;

    -- 1. Authorisation. A missing message, another clinic's, a wrong role and a wrong topic all give the SAME error.
    select m.id, m.clinic_id, m.origin, m.topic, m.thread_id into v_msg from msg.messages m where m.id = p_message_id;
    if not found
       or not (
            iam.has_clinic_role(v_msg.clinic_id, array['nurse', 'doctor', 'queue_worker'])
            or (iam.has_clinic_role(v_msg.clinic_id, array['front_desk']) and v_msg.topic in ('appointments', 'safety'))
       ) then
        perform msg.deny_screening();
    end if;

    -- 2. Serialise per thread. A row lock on a table the application role cannot lock (it has no UPDATE privilege there).
    --    Each statement below is a separate statement, so READ COMMITTED gives it a fresh snapshot after the lock.
    perform 1 from msg.threads t where t.id = v_msg.thread_id for no key update;
    select m.erased_at into v_erased from msg.messages m where m.id = p_message_id;
    if not found or v_erased is not null then
        perform msg.deny_screening();
    end if;

    -- 3. Normalise the caller's inputs. Nothing below may refuse a red_flag or unassessable.
    v_profile := case when p_profile is not distinct from 'demo' then 'demo' else 'clinical' end;
    v_key_sha := encode(sha256(convert_to(coalesce(p_idempotency_key, ''), 'UTF8')), 'hex');
    v_prov_sha := encode(sha256(convert_to(coalesce(p_match_provenance::text, 'null'), 'UTF8')), 'hex');
    v_claim_count := case when coalesce(p_rules_evaluated between 0 and 100000, false) then p_rules_evaluated end;
    v_claim_hash := case when coalesce(p_rule_set_hash ~ '^[0-9a-f]{64}$', false) then p_rule_set_hash end;
    v_claims_invalid := v_claim_count is null or v_claim_hash is null;

    -- 4. The rule set in force, computed by the database. The empty set hashes to sha256('') (what Python's hashlib gives for no
    --    rules). If the computation itself fails the screening is still recorded, flagged, with a zero hash and zero count.
    begin
        select (count(*) filter (where f.has_criteria))::integer,
               encode(sha256(convert_to(coalesce(string_agg(f.id::text || ':' || f.version_id::text, E'\n'
                                                            order by (f.id::text || ':' || f.version_id::text) collate "C"), ''), 'UTF8')), 'hex')
          into v_count, v_hash
          from kb.active_red_flag_rules(v_msg.clinic_id, v_msg.origin, v_profile) f;
    exception when others then
        v_count := 0;
        v_hash := repeat('0', 64);
        v_unverified := true;
    end;

    -- 5. `clear` is the only fail-closed outcome. Every comparison is NULL-safe (IS DISTINCT FROM).
    if p_outcome = 'clear' then
        if v_unverified
           or v_count <= 0
           or v_claim_count is distinct from v_count
           or v_claim_hash is distinct from v_hash
           or coalesce(btrim(p_idempotency_key), '') = ''
           or (v_profile = 'demo' and v_msg.origin = 'live')
           or exists (select 1 from msg.screenings s where s.message_id = p_message_id and s.outcome = 'red_flag')
           or exists (select 1 from msg.screenings s where s.message_id = p_message_id and s.key_sha256 = v_key_sha
                      and not (s.outcome = 'clear' and s.profile = v_profile and s.provenance_sha256 = v_prov_sha
                               and s.claimed_rules_evaluated is not distinct from v_claim_count
                               and s.claimed_rule_set_hash is not distinct from v_claim_hash)) then
            raise exception 'CLEAR_REFUSED' using errcode = 'P0001';
        end if;
    end if;

    -- 6. Replay: the same key with ALL the same parameters (compared against EVERY row with this key) returns that row.
    for v_existing in
        select s.id, s.outcome, s.profile, s.provenance_sha256, s.claimed_rules_evaluated, s.claimed_rule_set_hash
          from msg.screenings s where s.message_id = p_message_id and s.key_sha256 = v_key_sha order by s.created_at
    loop
        if v_existing.outcome = p_outcome and v_existing.profile = v_profile and v_existing.provenance_sha256 = v_prov_sha
           and v_existing.claimed_rules_evaluated is not distinct from v_claim_count
           and v_existing.claimed_rule_set_hash is not distinct from v_claim_hash then
            return query select v_existing.id, true, v_existing.outcome;
            return;
        end if;
    end loop;

    -- 7. A repeat of the same logical screening under another key is a no-op, never an error and never a second task. An
    --    unassessable on a message that is already red-flagged is satisfied by the red flag (the nurse already has the task).
    if p_outcome = 'unassessable' then
        select s.id, s.outcome into v_existing from msg.screenings s where s.message_id = p_message_id and s.outcome = 'red_flag' order by s.created_at limit 1;
        if found then
            return query select v_existing.id, true, v_existing.outcome;
            return;
        end if;
    end if;
    select s.id, s.outcome into v_existing from msg.screenings s
     where s.message_id = p_message_id and s.profile = v_profile and s.rule_set_hash = v_hash and s.outcome = p_outcome;
    if found then
        return query select v_existing.id, true, v_existing.outcome;
        return;
    end if;

    -- 8. The nurse task, for red_flag and unassessable. The function builds the stored provenance itself.
    if p_outcome <> 'clear' then
        begin
            v_sanitised := msg.sanitise_matches(p_match_provenance);
            select coalesce(jsonb_agg(
                       case when f.id is not null
                            then jsonb_build_object('rule_id', f.id, 'rule_code', f.rule_code, 'version_id', f.version_id,
                                                    'rule_status', f.status, 'kind', i.item ->> 'kind',
                                                    'index', (i.item ->> 'index')::integer, 'start', (i.item ->> 'start')::integer,
                                                    'end', (i.item ->> 'end')::integer)
                            else jsonb_build_object('rule_id', i.item ->> 'rule_id') end
                       order by i.ord), '[]'::jsonb),
                   coalesce(bool_or(f.id is null), false)
              into v_matches, v_unverified_matches
              from jsonb_array_elements(v_sanitised -> 'matches') with ordinality as i(item, ord)
              left join kb.active_red_flag_rules(v_msg.clinic_id, v_msg.origin, v_profile) f
                on f.id = (i.item ->> 'rule_id')::uuid and f.rule_code = i.item ->> 'rule_code'
               and f.version_id = (i.item ->> 'version_id')::integer and f.status = i.item ->> 'rule_status';
            v_dropped := (v_sanitised ->> 'dropped')::integer;
            v_caller_truncated := case when jsonb_typeof(p_match_provenance) = 'object'
                                            and jsonb_typeof(p_match_provenance -> 'matches_truncated') = 'boolean'
                                       then (p_match_provenance ->> 'matches_truncated')::boolean else false end;
            v_truncated := coalesce((v_sanitised ->> 'truncated')::boolean, false) or v_caller_truncated;
        exception when others then
            v_matches := '[]'::jsonb;
            v_unverified_matches := true;
            v_truncated := false;
            v_dropped := -1;
        end;
        v_prov := jsonb_build_object('outcome', p_outcome, 'profile', v_profile, 'matches', v_matches,
                                     'matches_truncated', v_truncated, 'unverified_matches', v_unverified_matches,
                                     'dropped_items', v_dropped);
        if p_outcome = 'unassessable' then
            v_kind := 'screening_unassessable';
            v_priority := 'standard';
            v_prov := v_prov || jsonb_build_object('placeholder', '# CLINICAL-PLACEHOLDER: needs clinician sign-off',
                                                   'priority_note', 'pending clinical lead confirmation');
        else
            v_kind := 'red_flag';
            v_priority := 'immediate';
        end if;
        insert into agents.review_tasks (clinic_id, origin, kind, priority, assigned_role, source_message_id, provenance, idempotency_key)
        values (v_msg.clinic_id, v_msg.origin, v_kind, v_priority, 'nurse', p_message_id::text, v_prov,
                'screen-task:' || p_message_id::text || ':' || gen_random_uuid()::text)
        returning id into v_task;
    end if;

    -- 9. The screening row. A conflict on the logical tuple re-selects the existing row; it never returns nothing.
    insert into msg.screenings (clinic_id, origin, topic, message_id, outcome, profile, rules_evaluated, rule_set_hash, rule_set_unverified,
                                claimed_rules_evaluated, claimed_rule_set_hash, claimed_values_invalid, key_sha256, provenance_sha256,
                                review_task_id, patient_reply, screened_by, idempotency_key)
    values (v_msg.clinic_id, v_msg.origin, v_msg.topic, p_message_id, p_outcome, v_profile, v_count, v_hash, v_unverified,
            v_claim_count, v_claim_hash, v_claims_invalid, v_key_sha, v_prov_sha,
            v_task, case when p_outcome = 'red_flag' then 'pending_clinician_template' else 'not_required' end,
            auth.uid(), 'screen:' || p_message_id::text || ':' || gen_random_uuid()::text)
    on conflict (message_id, profile, rule_set_hash, outcome) do nothing
    returning id into v_sid;
    if v_sid is null then
        select s.id into v_sid from msg.screenings s
         where s.message_id = p_message_id and s.profile = v_profile and s.rule_set_hash = v_hash and s.outcome = p_outcome;
        if v_sid is null then
            raise exception 'screening could not be recorded' using errcode = 'XX000';  -- never a silent drop
        end if;
        return query select v_sid, true, p_outcome;
        return;
    end if;

    -- 10. The audit trail: ids and enums only, never patient text. Its key is random, so a planted audit key cannot block it.
    insert into audit.log (origin, clinic_id, actor_id, action, resource_type, resource_id, details, idempotency_key)
    values (v_msg.origin, v_msg.clinic_id, auth.uid(), 'msg.screening.recorded', 'msg.screenings', v_sid::text,
            jsonb_build_object('message_id', p_message_id, 'outcome', p_outcome, 'profile', v_profile),
            'screening-audit:' || v_sid::text || ':' || gen_random_uuid()::text);

    return query select v_sid, false, p_outcome;
end
$$;

-- ---- a patient-kind message cannot commit without a screening ------------------------------------------------------------------

create function msg.require_screening() returns trigger
    language plpgsql security definer set search_path = ''
as $$
begin
    -- Read-only and independent of the committing role's SELECT rights. A clinical-profile row of any outcome, a red_flag or an
    -- unassessable of any profile satisfies it; a demo `clear` only for a non-live message.
    if not exists (
        select 1 from msg.screenings s
         where s.message_id = new.id
           and (s.profile = 'clinical' or s.outcome in ('red_flag', 'unassessable') or s.origin <> 'live')
    ) then
        raise exception 'a patient message must be screened before it commits' using errcode = '23514';
    end if;
    return null;
end
$$;

create constraint trigger messages_require_screening after insert on msg.messages
    deferrable initially deferred for each row when (new.sender_kind = 'patient')
    execute function msg.require_screening();

-- ---- the worst-of view ---------------------------------------------------------------------------------------------------------------

-- One row per message. effective_outcome is the WORST of every screening row (red_flag > unassessable > clear), never "the latest"
-- or "the clinical one"; 'demo_clear' when the only clears are demo-profile; 'unscreened' when there is no row. clinical_clear is
-- true ONLY with a verified clinical-profile clear and no red_flag or unassessable row in any profile: anything that needs "safe to
-- treat as clear" in the clinical profile reads clinical_clear. A clinical clear stays a clear for the rule set recorded in
-- clinical_clear_rule_set_hash; a consumer that needs currency compares it with the current set's hash. security_invoker means the
-- caller's row-level security applies to both tables, so no role sees a row it could not read directly.
create view msg.message_screening_status with (security_invoker = true) as
select m.id as message_id, m.clinic_id, m.origin, m.topic, m.thread_id,
       case
           when coalesce(bool_or(s.outcome = 'red_flag'), false) then 'red_flag'
           when coalesce(bool_or(s.outcome = 'unassessable'), false) then 'unassessable'
           when coalesce(bool_or(s.outcome = 'clear' and s.profile = 'clinical' and not s.rule_set_unverified), false) then 'clear'
           when coalesce(bool_or(s.outcome = 'clear'), false) then 'demo_clear'
           else 'unscreened'
       end as effective_outcome,
       (coalesce(bool_or(s.outcome = 'clear' and s.profile = 'clinical' and not s.rule_set_unverified), false)
        and not coalesce(bool_or(s.outcome in ('red_flag', 'unassessable')), false)) as clinical_clear,
       (case when coalesce(bool_or(s.outcome = 'clear' and s.profile = 'clinical' and not s.rule_set_unverified), false)
                  and not coalesce(bool_or(s.outcome in ('red_flag', 'unassessable')), false)
             then (array_agg(s.rule_set_hash order by s.created_at desc)
                   filter (where s.outcome = 'clear' and s.profile = 'clinical' and not s.rule_set_unverified))[1]
        end) as clinical_clear_rule_set_hash
  from msg.messages m
  left join msg.screenings s on s.message_id = m.id
 group by m.id, m.clinic_id, m.origin, m.topic, m.thread_id;

-- ---- Row-level security --------------------------------------------------------------------------------------------------------------

alter table msg.screenings enable row level security;
alter table msg.screenings force row level security;

-- Row-local, identical to msg.messages: nurse and doctor see every topic; front desk only appointments and safety. queue_worker and
-- admin read nothing here (a worker calls the function; the function reads as the owner).
create policy screenings_select on msg.screenings for select to careflow_app
    using (
        iam.has_clinic_role(clinic_id, array['nurse', 'doctor'])
        or (iam.has_clinic_role(clinic_id, array['front_desk']) and topic in ('appointments', 'safety'))
    );
-- No INSERT / UPDATE / DELETE policy and no such privilege: only msg.record_screening writes this table.

-- Staff may now also RELAY a patient's message (sender_kind 'patient'); 'system' stays denied. Replaces 0011's policy.
drop policy messages_insert on msg.messages;
create policy messages_insert on msg.messages for insert to careflow_app
    with check (
        recorded_by = auth.uid()
        and sender_kind in ('staff', 'patient')
        and (
            iam.has_clinic_role(clinic_id, array['nurse', 'doctor'])
            or (iam.has_clinic_role(clinic_id, array['front_desk']) and topic = 'appointments')
        )
    );

-- Message-sourced review tasks are hidden from front desk and admin. Policies are permissive and OR together, so 0007's two policies
-- are DROPPED first, not supplemented. review_tasks_update (nurse, doctor) is untouched.
drop policy review_tasks_select on agents.review_tasks;
drop policy review_tasks_insert on agents.review_tasks;
create policy review_tasks_select on agents.review_tasks for select to careflow_app
    using (
        iam.has_clinic_role(clinic_id, array['nurse', 'doctor', 'queue_worker'])
        or (iam.has_clinic_role(clinic_id, array['front_desk', 'admin']) and kind = 'unmerge_review')
    );
create policy review_tasks_insert on agents.review_tasks for insert to careflow_app
    with check (
        iam.has_clinic_role(clinic_id, array['nurse', 'doctor', 'queue_worker'])
        or (iam.has_clinic_role(clinic_id, array['front_desk', 'admin']) and kind = 'unmerge_review')
    );

-- ---- Privileges ----------------------------------------------------------------------------------------------------------------------

revoke all on msg.screenings from public;
grant select on msg.screenings to careflow_app;
grant select on msg.message_screening_status to careflow_app;

revoke all on function msg.deny_screening() from public, careflow_app;
revoke all on function kb.active_red_flag_rules(uuid, text, text) from public, careflow_app;
revoke all on function msg.sanitise_matches(jsonb) from public, careflow_app;
revoke all on function msg.screenings_guard() from public, careflow_app;
revoke all on function msg.screenings_immutable() from public, careflow_app;
revoke all on function msg.require_screening() from public, careflow_app;
revoke all on function msg.record_screening(uuid, text, text, integer, text, jsonb, text) from public;
grant execute on function msg.record_screening(uuid, text, text, integer, text, jsonb, text) to careflow_app;

do $$
declare r text;
begin
    foreach r in array array['anon', 'authenticated', 'service_role'] loop
        if exists (select 1 from pg_roles where rolname = r) then
            execute format('revoke all on msg.screenings from %I', r);
            execute format('revoke all on msg.message_screening_status from %I', r);
            execute format('revoke all on all functions in schema msg from %I', r);
            execute format('revoke all on function kb.active_red_flag_rules(uuid, text, text) from %I', r);
        end if;
    end loop;
end $$;
