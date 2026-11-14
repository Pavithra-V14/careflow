-- 0081_consent_purpose_voice_session (owner decision 2026-11-14 on frontend request 31; BUILD-SPEC.md 20.1, 23.4; CLAUDE.md rules 1, 3, 10).
--
-- # LEGAL-PLACEHOLDER: needs counsel sign-off  (the voice notice text and wording)
--
-- `voice_session` becomes a fourth CONSENT NOTICE PURPOSE, next to care_delivery, ai_processing and service_improvement. The notice text is counsel's and is published only by migration or by the owner-run seed script
-- (0053); nothing is written here. With no `voice_session` notice published nothing can be consented to for it (unknown is not a value), and the frontend then shows that voice is not set up.
-- The patient's grant or withdrawal is an ordinary consent event against the CURRENT notice of the purpose (0049): append-only, naming the version. Only the check on the purpose list changes (consent_events already
-- takes its purposes from the notices through the composite key). No grant, policy or function changes.

alter table iam.consent_notices drop constraint consent_notices_purpose_check;
alter table iam.consent_notices add constraint consent_notices_purpose_check check (purpose in ('care_delivery', 'ai_processing', 'service_improvement', 'voice_session'));

do $$
begin
    if not exists (select 1 from pg_constraint where conrelid = 'iam.consent_notices'::regclass and conname = 'consent_notices_purpose_check' and pg_get_constraintdef(oid) like '%voice_session%') then
        raise exception '0081: the purpose check was not replaced';
    end if;
    if has_table_privilege('careflow_app', 'iam.consent_notices', 'insert') or has_table_privilege('careflow_app', 'iam.consent_notices', 'update') or has_table_privilege('careflow_app', 'iam.consent_notices', 'delete') then
        raise exception '0081: notices are published only by migration or the owner-run script';
    end if;
end $$;
