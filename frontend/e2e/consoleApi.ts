// MOCK ONLY. A small stateful fake of the console API routes (F3 and F4), written from openapi.snapshot.json and the route
// docstrings (docs/design/review-task-routes.md, frontend-contract.md, identity-confirmation-revoke.md). It enforces roles so
// "not available to your role" can be exercised in a real browser, and it demands a recent second factor on the merge routes
// so the step-up prompt and retry can be too. It proves this app's behaviour, not the backend's. Every error body carries LEAK.

export const LEAK = 'LEAK-SENTINEL-backend-text';
export const ID = {
  clinic: '3f2b8c1e-6a4d-4e1b-9d0a-1c2d3e4f5a6b',
  doc: '11111111-1111-4111-8111-111111111111',
  // A referral with no run yet: its reviewer view answers 404, and only then is Run offered.
  docNew: '33333333-3333-4333-8333-333333333333',
  task: '33333333-3333-4333-8333-333333333333',
  taskRed: '44444444-4444-4444-8444-444444444444',
  patient: '66666666-6666-4666-8666-666666666666',
  patient2: '77777777-7777-4777-8777-777777777777',
  hold: '88888888-8888-4888-8888-888888888888',
  slot: '99999999-9999-4999-8999-999999999999',
  visitType: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
  user: 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb',
  merge: 'dddddddd-dddd-4ddd-8ddd-dddddddddddd',
  candidate: 'eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee',
};
/** The signed-in mock account (matches /v1/iam/me in mockBackend). */
export const ME = '9d0a1c2d-3e4f-4a6b-8c1e-3f2b6a4d4e1b';

export interface ConsoleOptions {
  /** What the register step reports for the scan. */
  scan?: string;
  /** Answer uploads with 503 (storage not configured). */
  uploadsOff?: boolean;
  /** Start with the patient record already verified (so identity revoke is offered). */
  patientVerified?: boolean;
  /** The clinic has set its list of review reasons (default: not set). */
  reasonCodes?: boolean;
  /** Who proposed the merge: someone else (default), or the signed-in account. */
  mergeProposedByMe?: boolean;
}

/** What the fake needs to know about the browser session. */
export interface SessionFacts {
  /** True once a SECOND factor verification has happened since sign-in (the step-up). */
  steppedUp: () => boolean;
}

type Reply = [status: number, body: unknown, headers?: Record<string, string>];
const ok = (body: unknown): Reply => [200, body];
const no = (status: number): Reply => [status, { detail: LEAK }];
const page = (items: unknown[]) => ok({ items, limit: 100, has_more: false });
const READERS = ['front_desk', 'nurse', 'doctor'];
const REVIEWERS = ['nurse', 'doctor'];
const SCHEDULE_READERS = [...READERS, 'admin'];
const CLINICAL = ['nurse', 'doctor'];

export function createConsoleApi(role: string, options: ConsoleOptions = {}, session: SessionFacts = { steppedUp: () => true }) {
  const taskStatus: Record<string, 'open' | 'acknowledged' | 'resolved'> = { [ID.task]: 'open', [ID.taskRed]: 'open' };
  let holds = [{ clinic_id: ID.clinic, created_at: '2026-10-30T05:00:00Z', expires_at: '2026-10-30T06:00:00Z', hold_id: ID.hold, patient_id: null, slot_id: ID.slot, status: 'active' }];
  let verified = options.patientVerified ?? false;
  let backfillWaiting: 'send_decision' | 'reply_entry' | 'none' = 'send_decision';
  let reviewed: string | null = null;
  let mergeStatus: 'awaiting_second_approval' | 'active' | 'unmerged' = 'awaiting_second_approval';

  const canSeeProposal = REVIEWERS.includes(role);
  const referral = () => ({ clinic_id: ID.clinic, content_type: 'application/pdf', created_at: '2026-10-30T05:00:00Z', decided: canSeeProposal ? reviewed !== null : null, document_id: ID.doc, open_holds: 1, proposal_id: canSeeProposal ? ID.candidate : null, proposal_visible: canSeeProposal, registered_by: ID.user, run_state: 'needs_front_desk', size_bytes: 20480 });
  const proposal = { abstained: false, candidates: [{ card_id: 'card-placeholder', version: 1 }], created_at: '2026-10-30T05:00:00Z', mode: 'shadow', patient_id: ID.patient, proposal_id: ID.candidate, review_task_id: ID.task };
  const task = (id: string, kind: string, priority: string, assigned: string) => ({ acknowledged_at: null, acknowledged_by: null, assigned_role: assigned, clinic_id: ID.clinic, created_at: '2026-10-30T05:00:00Z', kind, priority, resolvable: ['referral_hold', 'unmerge_review', 'referral_dead_letter', 'identity_confirmation_revoked'].includes(kind), resolved_at: null, status: taskStatus[id], task_id: id });
  const tasks = () => [task(ID.task, 'referral_hold', 'standard', 'front_desk'), ...(role === 'nurse' || role === 'doctor' ? [task(ID.taskRed, 'red_flag', 'immediate', 'nurse')] : [])];
  const merge = () => ({ activated_at: mergeStatus === 'active' ? '2026-10-30T06:00:00Z' : null, candidate_id: ID.candidate, clinic_id: ID.clinic, created_at: '2026-10-30T05:00:00Z', master_patient_id: ID.patient, merge_id: ID.merge, proposed_by: options.mergeProposedByMe ? ME : ID.user, second_approved_by: mergeStatus === 'active' ? ME : null, status: mergeStatus, subordinate_patient_id: ID.patient2, unmerged_at: mergeStatus === 'unmerged' ? '2026-10-30T07:00:00Z' : null, unmerged_by: mergeStatus === 'unmerged' ? ME : null });
  const stepUpRequired = (): Reply => [401, { detail: 'step-up required' }, { 'WWW-Authenticate': 'step-up' }];

  return function handle(method: string, path: string, query: URLSearchParams): Reply | null {
    const m = (re: RegExp) => path.match(re);
    if (!path.startsWith('/v1/staff/')) return null;

    if (method === 'GET' && path === '/v1/staff/referrals') return READERS.includes(role) ? page([referral()]) : no(403);
    if (method === 'GET' && m(/^\/v1\/staff\/referrals\/[^/]+$/)) {
      if (!READERS.includes(role)) return no(403);
      return ok({ ...referral(), holds: [{ assigned_role: 'front_desk', status: taskStatus[ID.task], task_id: ID.task }], proposal: canSeeProposal ? proposal : null });
    }
    if (method === 'POST' && m(/^\/v1\/staff\/referrals\/[^/]+\/review$/)) {
      if (!REVIEWERS.includes(role)) return no(403);
      if (reviewed !== null) return no(409);
      reviewed = 'approve';
      return ok({ decision: 'approve', document_id: ID.doc, replayed: false });
    }
    if (method === 'POST' && path === '/v1/staff/documents/uploads') {
      if (role !== 'front_desk' && role !== 'nurse') return no(403);
      if (options.uploadsOff) return no(503);
      return ok({ document_id: ID.doc, expires_at: '2026-10-30T06:00:00Z', upload_url: 'https://mock-project.supabase.invalid/storage/v1/object/upload/sign/docs/x?token=t' });
    }
    if (method === 'POST' && path === '/v1/staff/documents/register') {
      return ok({ content_type: 'application/pdf', document_id: ID.doc, kind: 'referral', replayed: false, scan: options.scan ?? 'not_scanned', size_bytes: 8 });
    }

    if (method === 'GET' && path === '/v1/staff/review-tasks') {
      if (!['front_desk', 'front_desk_supervisor', 'nurse', 'doctor', 'admin'].includes(role)) return no(403);
      const want = query.get('status') ?? 'open';
      return page(tasks().filter((t) => t.status === want));
    }
    const taskMatch = m(/^\/v1\/staff\/review-tasks\/([^/]+)(?:\/(acknowledge|resolve))?$/);
    if (taskMatch) {
      const id = taskMatch[1] as string;
      const action = taskMatch[2];
      if (!(id in taskStatus)) return no(404);
      if (method === 'GET' && !action) {
        const t = task(id, id === ID.taskRed ? 'red_flag' : 'referral_hold', id === ID.taskRed ? 'immediate' : 'standard', id === ID.taskRed ? 'nurse' : 'front_desk');
        return ok({ ...t, provenance: id === ID.taskRed ? { rule_id: 'rule-placeholder' } : { document_id: ID.doc, reason: 'needs_front_desk' }, source_message_id: ID.doc });
      }
      if (method === 'POST' && action === 'acknowledge') {
        if (taskStatus[id] !== 'open') return no(409);
        taskStatus[id] = 'acknowledged';
        return ok({ replayed: false, status: 'acknowledged', task_id: id });
      }
      if (method === 'POST' && action === 'resolve') {
        if (id === ID.taskRed) return no(409);
        taskStatus[id] = 'resolved';
        return ok({ replayed: false, status: 'resolved', task_id: id });
      }
    }

    if (method === 'POST' && path === '/v1/staff/patients/search') {
      if (role !== 'front_desk') return no(403);
      return ok({ items: [{ matched_kinds: ['name_dob'], patient_id: ID.patient, registered_via: 'referral_worker', verified }] });
    }
    // ---- F9: audit reader, register, erasure, Run, full record (MOCK ONLY) ----
    if (method === 'GET' && path === '/v1/staff/audit') {
      if (role !== 'auditor') return no(403);
      const row = (n: number) => ({ action: n % 2 ? 'patient.view' : 'referral.run', actor_id: ID.user, actor_role: 'nurse', audit_id: n, clinic_id: ID.clinic, details: { count: n, note: 'plain text' }, occurred_at: '2026-11-10T04:00:00Z', origin: 'test', resource_id: ID.patient, resource_type: 'patient' });
      return ok({ items: [row(3), row(2), row(1)], limit: 50, has_more: false, next_before_id: null });
    }
    if (method === 'POST' && path === '/v1/staff/patients') return role === 'front_desk' ? ok({ candidate_ids: [ID.patient2], outcome: 'review', patient_id: ID.patient }) : no(403);
    if (method === 'POST' && m(/^\/v1\/staff\/referrals\/[^/]+\/run$/)) return READERS.includes(role) ? ok({ job_id: ID.candidate, replayed: false }) : no(403);
    if (method === 'GET' && path === '/v1/staff/erasure-requests/reasons') return role === 'front_desk' ? ok({ reasons: ['reason_x', 'reason_y'] }) : no(403);
    if (method === 'GET' && path === '/v1/staff/erasure-requests') {
      if (role !== 'front_desk') return no(403);
      const base = { approved_by: null, created_at: '2026-11-10T04:00:00Z', decided_at: null, patient_id: ID.patient, reason: 'reason_x', status: 'requested' };
      return page([{ ...base, request_id: ID.task, requested_by: ID.user }, { ...base, request_id: ID.taskRed, requested_by: ME }]);
    }
    if (method === 'POST' && m(/^\/v1\/staff\/erasure-requests\/[^/]+\/approve$/)) return role === 'front_desk' ? ok({ erased: true, request_id: ID.task }) : no(403);
    if (method === 'POST' && m(/^\/v1\/staff\/erasure-requests\/[^/]+\/cancel$/)) return role === 'front_desk' ? ok({ cancelled: true, request_id: ID.task }) : no(403);
    if (method === 'GET' && m(/^\/v1\/staff\/patients\/[^/]+\/view$/)) return READERS.includes(role) ? ok({ clinic_id: ID.clinic, date_of_birth: '1990-02-03', name: 'Synthetic Person', patient_id: ID.patient, phone: '9000000000' }) : no(403);
    const patient = m(/^\/v1\/staff\/patients\/([^/]+)(\/confirm-identity|\/revoke-identity)?$/);
    if (patient) {
      if (!READERS.includes(role)) return no(403);
      if (method === 'GET' && !patient[2]) return ok({ clinic_id: ID.clinic, latest_identity_event: verified ? 'confirm' : null, latest_identity_event_at: verified ? '2026-10-30T05:30:00Z' : null, merged_into: null, patient_id: patient[1], registered_via: 'referral_worker', verified });
      if (method === 'POST' && patient[2] === '/confirm-identity') {
        if (role !== 'front_desk') return no(403);
        verified = true;
        return ok({ confirmed: true, patient_id: patient[1], replayed: false });
      }
      if (method === 'POST' && patient[2] === '/revoke-identity') {
        if (role !== 'front_desk') return no(403);
        verified = false;
        return ok({ confirmed: false, patient_id: patient[1], replayed: false, review_task: 'raised' });
      }
    }

    if (method === 'GET' && path === '/v1/staff/identity/possible-duplicates') {
      if (role !== 'front_desk') return no(403);
      return page([{ candidate_id: ID.candidate, candidate_patient_id: ID.patient2, clinic_id: ID.clinic, created_at: '2026-10-30T05:00:00Z', matched_kinds: ['name_dob'], patient_id: ID.patient, reason: 'near_match', resolved_at: null, status: query.get('status') === 'resolved' ? 'resolved' : 'pending' }].filter((d) => d.status === (query.get('status') ?? 'pending')));
    }
    if (method === 'GET' && path === '/v1/staff/identity/merges') {
      if (!READERS.includes(role)) return no(403);
      const want = query.get('status');
      return page([merge()].filter((x) => !want || x.status === want));
    }
    if (method === 'GET' && m(/^\/v1\/staff\/identity\/merges\/[^/]+$/)) return READERS.includes(role) ? ok(merge()) : no(403);
    const mergeAct = m(/^\/v1\/staff\/merges\/[^/]+\/(approve|unmerge)$/);
    if (method === 'POST' && mergeAct) {
      if (role !== 'front_desk') return no(403);
      // Both routes need a second factor verified recently. The first attempt in a session is refused with the step-up challenge.
      if (!session.steppedUp()) return stepUpRequired();
      if (mergeAct[1] === 'approve') {
        if (mergeStatus !== 'awaiting_second_approval' || options.mergeProposedByMe) return no(409);
        mergeStatus = 'active';
        return ok({ master_patient_id: ID.patient, merge_id: ID.merge, replayed: false, status: 'active', subordinate_patient_id: ID.patient2 });
      }
      if (mergeStatus === 'unmerged') return no(409);
      const wasActive = mergeStatus === 'active';
      mergeStatus = 'unmerged';
      return ok({ decisions_flagged: wasActive ? 1 : 0, merge_id: ID.merge, replayed: false, review_task_id: null, status: 'unmerged' });
    }

    // ---- the lists that replace typing, and the offer run for a freed slot (MOCK ONLY) ----
    if (method === 'GET' && path === '/v1/staff/break-glass/reasons') return [...READERS, 'admin'].includes(role) ? ok({ reasons: ['reason_a', 'reason_b'] }) : no(403);
    if (method === 'GET' && path === '/v1/staff/break-glass/mine') return READERS.includes(role) ? ok({ grants: [{ expires_at: '2026-11-10T06:00:00Z', grant_id: ID.merge, opened_at: '2026-11-09T05:00:00Z', patient_id: ID.patient, reason_code: 'reason_a' }] }) : no(403);
    if (method === 'GET' && path === '/v1/staff/visits/upcoming') return CLINICAL.includes(role) ? ok({ visits: [{ ends_at: '2026-11-10T04:30:00Z', patient_id: ID.patient, starts_at: '2026-11-10T04:00:00Z', visit_id: ID.merge, visit_type_id: ID.visitType }] }) : no(403);
    const run = (over: Record<string, unknown> = {}) => ({ candidate_patient_id: ID.patient2, candidate_rank: 1, finished: false, hold_id: null, slot_id: ID.slot, status: 'awaiting_send', task_id: null, waiting_for: 'send_decision', ...over });
    if (method === 'GET' && /^\/v1\/staff\/scheduling\/backfills\/[^/]+$/.test(path)) return SCHEDULE_READERS.includes(role) ? ok(run({ waiting_for: backfillWaiting, status: backfillWaiting === 'reply_entry' ? 'awaiting_reply' : 'awaiting_send', finished: backfillWaiting === 'none', hold_id: backfillWaiting === 'send_decision' ? null : ID.hold })) : no(403);
    if (method === 'POST' && path === '/v1/staff/scheduling/backfills') return role === 'front_desk' ? ok(run()) : no(403);
    if (method === 'POST' && /^\/v1\/staff\/scheduling\/backfills\/[^/]+\/decide$/.test(path)) return role === 'front_desk' ? ((backfillWaiting = 'reply_entry'), ok(run({ waiting_for: 'reply_entry', status: 'awaiting_reply', hold_id: ID.hold }))) : no(403);
    if (method === 'POST' && /^\/v1\/staff\/scheduling\/backfills\/[^/]+\/reply$/.test(path)) return role === 'front_desk' ? ((backfillWaiting = 'none'), ok(run({ waiting_for: 'none', finished: true }))) : no(403);
    if (method === 'POST' && path === '/v1/staff/scheduling/offers/replies') return role === 'front_desk' ? [201, { created: true, message_id: ID.candidate, outcome: 'clear', screening_id: ID.doc, thread_id: ID.hold }] : no(403);
    if (method === 'POST' && path === '/v1/staff/scheduling/offers/interpret-reply') return role === 'front_desk' ? ok({ calibrated: null, confidence: null, engine: null, message_id: ID.candidate, needs_person: false, outcome: 'clear', reason: null, replayed: false, screening_id: ID.doc, suggestion: 'accept' }) : no(403);
    if (method === 'GET' && path === '/v1/staff/messages/threads') return READERS.includes(role) ? ok({ items: [{ clinic_id: ID.clinic, created_at: '2026-11-09T05:00:00Z', last_message_at: null, message_count: 1, patient_id: ID.patient2, status: 'open', thread_id: ID.hold, topic: 'appointments' }], limit: 100, has_more: false }) : no(403);
    if (method === 'GET' && /^\/v1\/staff\/messages\/threads\/[^/]+\/messages$/.test(path)) return READERS.includes(role) ? ok({ items: [{ created_at: '2026-11-09T06:00:00Z', erased: false, message_id: ID.candidate, sender_kind: 'staff', thread_id: ID.hold, topic: 'appointments' }], limit: 100, has_more: false }) : no(403);
    // ---- F7: slots, bookings, waitlist, offers; the reviewer view and reason codes (MOCK ONLY) ----
    if (method === 'GET' && path === '/v1/staff/scheduling/slots') return SCHEDULE_READERS.includes(role) ? page([{ clinic_id: ID.clinic, slot_id: ID.slot, provider_id: ID.doc, location_id: ID.slot, visit_type_id: ID.visitType, starts_at: '2026-11-10T04:00:00Z', ends_at: '2026-11-10T04:30:00Z', status: 'open', hold_expires_at: null }]) : no(403);
    if (method === 'GET' && path === '/v1/staff/scheduling/bookings') return SCHEDULE_READERS.includes(role) ? page([{ booking_id: ID.merge, clinic_id: ID.clinic, created_at: '2026-11-09T04:00:00Z', ends_at: '2026-11-10T04:30:00Z', location_id: ID.slot, patient_id: ID.patient, provider_id: ID.doc, slot_id: ID.slot, starts_at: '2026-11-10T04:00:00Z', visit_type_id: ID.visitType }]) : no(403);
    if (method === 'GET' && path === '/v1/staff/scheduling/waitlist') return SCHEDULE_READERS.includes(role) ? page([{ clinic_id: ID.clinic, clinical_priority: 'P1', joined_at: '2026-11-01T00:00:00Z', patient_id: ID.patient, rank: 1, visit_type_id: ID.visitType, waitlist_id: ID.task }]) : no(403);
    if (method === 'GET' && path === '/v1/staff/scheduling/offers') return SCHEDULE_READERS.includes(role) ? page([{ channel: null, clinic_id: ID.clinic, created_at: '2026-11-09T05:00:00Z', delivered_at: null, expires_at: '2026-11-10T05:00:00Z', hold_id: ID.hold, message_id: ID.candidate, patient_id: ID.patient, slot_id: ID.slot, state: 'awaiting_delivery' }]) : no(403);
    if (method === 'POST' && path.startsWith('/v1/staff/scheduling/')) {
      if (role !== 'front_desk' && !(role === 'admin' && /\/(providers|locations|visit-types|slots)$/.test(path))) return no(403);
      if (/\/slots\/[^/]+\/hold$/.test(path)) return ok({ expires_at: '2026-11-10T05:00:00Z', hold_id: ID.hold, replayed: false, slot_id: ID.slot });
      if (/\/slots\/[^/]+\/book$/.test(path)) return ok({ booking_id: ID.merge, patient_id: ID.patient, replayed: false, slot_id: ID.slot, status: 'confirmed' });
      if (/\/bookings\/[^/]+\/cancel$/.test(path)) return ok({ booking_id: ID.merge, candidates: [{ joined_at: '2026-11-01T00:00:00Z', patient_id: ID.patient2, priority_code: 'P1', rank: 1, waitlist_id: ID.task }], replayed: false, slot_id: ID.slot });
      if (/\/waitlist\/join$/.test(path)) return ok({ clinical_priority: 'P1', patient_id: ID.patient, replayed: false, visit_type_id: ID.visitType, waitlist_id: ID.task });
      if (/\/offers\/[^/]+\/mark-delivered$/.test(path)) return ok({ channel: 'manual', delivered_at: '2026-11-09T06:00:00Z', hold_id: ID.hold, replayed: false });
      if (/\/(providers|locations|visit-types|slots)$/.test(path)) return ok({ clinic_id: ID.clinic, id: ID.candidate, replayed: false });
    }
    if (method === 'POST' && path === '/v1/staff/patients/search' && READERS.includes(role)) return ok({ items: [{ patient_id: ID.patient, verified: true, registered_via: 'staff', matched_kinds: ['name'] }], labels_withheld: false, truncated: false });
    // ---- batch 2: alerts, dashboard, care gaps, care plans, results (MOCK ONLY) ----
    if (method === 'GET' && path === '/v1/staff/console/alerts') return [...READERS, 'admin'].includes(role) ? page([{ acknowledged: false, alert_class: 'task_overdue', alert_id: ID.merge, audience_role: role, clinic_id: ID.clinic, count: 3, created_at: '2026-11-09T05:00:00Z', ref: ID.task }]) : no(403);
    const ackAlert = m(/^\/v1\/staff\/console\/alerts\/([^/]+)\/acknowledge$/);
    if (method === 'POST' && ackAlert) return ok({ acknowledged: true, alert_id: ackAlert[1] });
    if (method === 'GET' && path === '/v1/staff/console/dashboard') return role === 'admin' ? ok({ clinic_id: ID.clinic, metrics: [{ metric_id: 'metric_one', state: 'measured', value: 12 }, { metric_id: 'metric_two', state: 'unreadable', value: null }], not_measured: ['metric_three'] }) : no(403);
    if (method === 'GET' && path === '/v1/staff/care-gaps') return [...CLINICAL, 'admin'].includes(role) ? ok({ after_the_fact_error_rate: null, alert_age_configured: false, clinic_id: ID.clinic, recurrence_rules_with_unknown_last_done: 2, rows: [{ age_days: null, flagged_overdue: false, item_id: ID.task, source: 'barrier' }, { age_days: 40, flagged_overdue: true, item_id: ID.taskRed, source: 'recurrence' }], summaries: [{ median_age_days: null, open_count: null, source: 'barrier', undated: 1 }, { median_age_days: 12, open_count: 4, source: 'recurrence', undated: 0 }], unreadable: ['barrier'] }) : no(403);
    if (method === 'GET' && path === '/v1/staff/care-plans') return CLINICAL.includes(role) ? ok({ plans: [{ created_at: '2026-11-09T05:00:00Z', patient_id: ID.patient, plan_id: ID.merge, status: 'awaiting_confirmation' }] }) : no(403);
    if (method === 'POST' && path === '/v1/staff/care-plans') return role === 'doctor' ? [201, { created: true, plan_id: ID.merge, status: 'proposed', task_id: null, waiting_for: 'confirmation' }] : no(403);
    if (method === 'POST' && /^\/v1\/staff\/care-plans\/[^/]+\/confirm$/.test(path)) return role === 'doctor' ? ok({ created: false, plan_id: ID.merge, status: 'active', task_id: null, waiting_for: 'result' }) : no(403);
    if (method === 'POST' && path === '/v1/staff/results') return CLINICAL.includes(role) ? [201, { created: true, result_id: ID.candidate, wake_queued: false }] : no(403);
    // ---- batch 3: care-gap writes and the admin screens (MOCK ONLY) ----
    if (method === 'POST' && path === '/v1/staff/care-gaps/barriers') return CLINICAL.includes(role) ? [201, { barrier_id: ID.candidate, created: true }] : no(403);
    if (method === 'GET' && path === '/v1/staff/accounts') return role === 'admin' ? page([{ account_id: ID.user, roles: ['nurse'], status: 'active' }, { account_id: ID.candidate, roles: ['admin'], status: 'suspended' }]) : no(403);
    if (method === 'POST' && path === '/v1/staff/accounts/invite') return role === 'admin' ? [503, { detail: LEAK }] : no(403);
    if (method === 'GET' && path === '/v1/staff/medications') {
      if (!['nurse', 'doctor'].includes(role)) return no(403);
      return ok({ medications: [{ code: 'PLACEHOLDER-CODE-1', medication_id: ID.merge, recorded_at: '2026-11-10T05:00:00Z', superseded: false, supersedes_id: null, valid_from: '2026-11-01T05:00:00Z', valid_to: null, verified: true }, { code: 'PLACEHOLDER-CODE-2', medication_id: ID.doc, recorded_at: '2026-11-09T05:00:00Z', superseded: true, supersedes_id: null, valid_from: '2026-10-01T05:00:00Z', valid_to: '2026-10-30T05:00:00Z', verified: false }] });
    }
    if (method === 'POST' && path === '/v1/staff/medications') return ['nurse', 'doctor'].includes(role) ? ok({ created: true, medication_id: ID.patient2 }) : no(403);
    if (method === 'GET' && path === '/v1/staff/console/red-flag-rules') {
      if (!['admin', 'doctor'].includes(role)) return no(403);
      const base = { approved_at: null, has_vitals_criteria: false, origin: 'test', patterns: ['PLACEHOLDER-PATTERN-A'], token_sets: [['placeholder-word-a', 'placeholder-word-b']], phrases: ['PLACEHOLDER-PHRASE-A'], rule_code: 'placeholder_rule', status: 'draft_unreviewed', authored_by_you: false };
      return ok({ rules: [{ ...base, rule_id: ID.merge, version_id: 1, decisions: [{ decided_at: '2026-11-10T05:00:00Z', decided_by: ID.user, decision: 'approve' }] }, { ...base, rule_id: ID.doc, version_id: 2, authored_by_you: true, decisions: [] }, { ...base, rule_id: ID.patient2, version_id: 3, status: 'approved', approved_at: '2026-11-11T05:00:00Z', decisions: [] }] });
    }
    if (method === 'POST' && path === '/v1/staff/console/red-flag-rules') return role === 'doctor' ? ok({ rule_id: ID.patient2, status: 'draft_unreviewed' }) : no(403);
    if (method === 'POST' && path.startsWith('/v1/staff/console/red-flag-rules/') && path.endsWith('/withdraw')) return role === 'doctor' ? ok({ result: 'withdrawn' }) : no(403);
    if (method === 'POST' && path.startsWith('/v1/staff/console/red-flag-rules/') && path.endsWith('/decision')) return role === 'doctor' ? ok({ result: 'recorded' }) : no(403);
    if (method === 'GET' && path === '/v1/staff/console/autonomy') return [...READERS, 'admin'].includes(role) ? ok({ clinic_id: ID.clinic, configured: false, mode: 'shadow' }) : no(403);
    if (method === 'GET' && path === '/v1/staff/console/autonomy/proposals') return ['admin', 'doctor'].includes(role) ? ok({ proposals: [{ created_at: '2026-11-09T05:00:00Z', proposal_id: ID.merge, proposed_by: ID.user, proposer_role: role === 'admin' ? 'doctor' : 'admin' }] }) : no(403);
    if (method === 'GET' && path === '/v1/staff/console/autonomy/history') return ['admin', 'doctor'].includes(role) ? ok({ items: [{ at: '2026-11-08T05:00:00Z', by: ID.user, decided_at: null, decided_by: null, kind: 'proposal', mode: 'autonomous', outcome: 'pending', proposer_role: 'doctor' }, { at: '2026-11-01T05:00:00Z', by: ID.user, decided_at: null, decided_by: null, kind: 'mode_change', mode: 'assisted', outcome: null, proposer_role: null }] }) : no(403);
    if (method === 'GET' && path === '/v1/staff/console/reviewer-stats') return role === 'admin' ? ok({ clinic_id: ID.clinic, days: 30, policy_configured: false, reviewers: [{ approval_rate: null, approvals: 0, canaries_approved: 0, canaries_seen: 2, decisions: 0, edits: 0, median_seconds: null, reasons: [], rejections: 0, reviewer: ID.user, verdict: 'not_configured' }] }) : no(403);
    if (method === 'GET' && path === '/v1/staff/console/cost-canvas') return role === 'admin' ? ok({ actual: { calls_per_workflow: null, failure_tax_per_month: null, platform_floor_per_month: 100, total_per_month: null, usd_per_call: null, workflows_per_day: 12, workload_x_amplification_x_unit_per_month: null }, shadow: null, amplification_over_ceiling: false, clinic_id: ID.clinic, reasons: ['insufficient_data'], state: 'insufficient_data' }) : no(403);
    if (method === 'GET' && path === '/v1/staff/voice/sessions') return role === 'admin' ? ok({ sessions: [{ chunk_count: 3, expired_chunk_count: 1, retention_days: 30, session_id: ID.merge, started_at: '2026-11-09T05:00:00Z', status: 'ended', workflow: 'intake' }] }) : no(403);
    if (method === 'GET' && /^\/v1\/staff\/voice\/sessions\/[^/]+\/transcript$/.test(path)) return role === 'admin' ? ok({ chunks: [{ seq: 1, text: 'First words' }, { seq: 2, text: 'Second words' }], session_id: ID.merge }) : no(403);
    if (method === 'POST' && path === '/v1/staff/voice/purge') return role === 'admin' ? ok({ deleted: 4 }) : no(403);
    // ---- batch 4: readiness, claims, break-glass, recovery, notes, grievances (MOCK ONLY) ----
    if (method === 'GET' && /^\/v1\/staff\/visits\/[^/]+\/readiness$/.test(path)) return CLINICAL.includes(role) ? ok({ current_item: 'item-1', finished: false, status: 'waiting', task_id: ID.task, visit_id: ID.merge, waiting_for: 'reply' }) : no(403);
    if (method === 'POST' && /^\/v1\/staff\/visits\/[^/]+\/readiness\/(start|reply)$/.test(path)) return CLINICAL.includes(role) ? ok({ current_item: 'item-1', finished: false, status: 'waiting', task_id: null, visit_id: ID.merge, waiting_for: 'none' }) : no(403);
    if (method === 'GET' && path === '/v1/staff/patient-requests') return role === 'front_desk' ? ok({ requests: [{ created_at: '2026-11-09T05:00:00Z', erasure_request_id: null, field: null, kind: 'erasure', patient_id: ID.patient, request_id: ID.task, status: 'open' }, { created_at: '2026-11-09T05:00:00Z', erasure_request_id: null, field: 'phone', kind: 'correction', patient_id: ID.patient, request_id: ID.doc, status: 'acknowledged' }] }) : no(403);
    if (method === 'POST' && /^\/v1\/staff\/patient-requests\/[^/]+\/status$/.test(path)) return role === 'front_desk' ? ok({ result: 'recorded' }) : no(403);
    if (method === 'GET' && path === '/v1/staff/portal-claims') return role === 'front_desk' ? ok({ claims: [{ created_at: '2026-11-09T05:00:00Z', request_id: ID.merge }] }) : no(403);
    if (method === 'POST' && /^\/v1\/staff\/portal-claims\/[^/]+\/resolve$/.test(path)) return role === 'front_desk' ? ok({ result: 'approved' }) : no(403);
    if (method === 'GET' && path === '/v1/staff/break-glass') return role === 'admin' ? ok({ grants: [{ expires_at: '2026-11-10T06:00:00Z', grant_id: ID.merge, opened_at: '2026-11-09T05:00:00Z', opened_by: ID.user, patient_id: ID.patient, reason_code: 'reason_a', review_outcome: null, reviewed_at: null, reviewed_by: null }] }) : no(403);
    if (method === 'POST' && path === '/v1/staff/break-glass') return READERS.includes(role) ? [201, { created: true, expires_at: '2026-11-10T06:00:00Z', grant_id: ID.merge }] : no(403);
    if (method === 'GET' && /^\/v1\/staff\/break-glass\/[^/]+\/messages$/.test(path)) return ok({ messages: [{ body: 'Message words', created_at: '2026-11-09T05:00:00Z', erased: false, message_id: ID.candidate, readable: true, sender_kind: 'patient', thread_id: ID.hold, topic: 'care' }] });
    if (method === 'POST' && /^\/v1\/staff\/break-glass\/[^/]+\/review$/.test(path)) return ok({ result: 'reviewed' });
    if (method === 'GET' && path === '/v1/staff/recovery') return role === 'admin' ? ok({ requests: [{ account_id: ID.user, approved_by: null, created_at: '2026-11-09T05:00:00Z', decided_at: null, request_id: ID.merge, requested_by: ID.candidate, status: 'requested' }] }) : no(403);
    if (method === 'GET' && path === '/v1/staff/notes') return CLINICAL.includes(role) ? ok({ notes: [{ author_id: ID.user, created_at: '2026-11-09T05:00:00Z', erased: false, note_id: ID.merge, parent_id: null, text: 'First note words' }, { author_id: ID.user, created_at: '2026-11-09T06:00:00Z', erased: false, note_id: ID.candidate, parent_id: ID.merge, text: 'Addendum words' }] }) : no(403);
    if (method === 'POST' && path === '/v1/staff/notes') return CLINICAL.includes(role) ? [201, { created: true, note_id: ID.hold }] : no(403);
    if (method === 'GET' && path === '/v1/staff/grievances') return role === 'admin' ? ok({ grievances: [{ category: 'service', clinic_id: ID.clinic, created_at: '2026-11-09T05:00:00Z', erased: false, grievance_id: ID.merge, status: 'open', text: 'Patient words' }] }) : no(403);
    if (method === 'GET' && path === '/v1/staff/console/reason-codes') return REVIEWERS.includes(role) ? ok({ configured: options.reasonCodes === true, codes: options.reasonCodes === true ? ['reason_a', 'reason_b'] : [] }) : no(403);
    const reviewView = m(/^\/v1\/staff\/referrals\/([^/]+)\/review-view$/);
    if (method === 'GET' && reviewView) {
      if (!REVIEWERS.includes(role)) return no(403);
      if (path.includes(ID.docNew)) return no(404);
      return ok({ alternatives: [{ label: 'card-placeholder@1', probability: null }, { label: 'card-other@2', probability: 0.25 }], confidence: null, engine: null, evidence: [{ field: 'reason', source: 'letter', crop_ref: null }], kind: 'triage_proposal', on_approve: 'Approve text from the API', on_edit: 'Edit text from the API', on_reject: 'Reject text from the API', proposal: { abstained: false, mode: 'shadow', candidate_count: 1 }, reason_codes: ['reason_a', 'reason_b'], task_id: ID.task, triggering_rule: null });
    }
    if (method === 'GET' && path.startsWith('/v1/staff/scheduling/')) {
      if (!SCHEDULE_READERS.includes(role)) return no(403);
      const inactive = query.get('include_inactive') === 'true';
      if (path.endsWith('/providers')) return page([{ active: true, clinic_id: ID.clinic, display_name: 'Placeholder Provider', provider_id: ID.doc }, ...(inactive ? [{ active: false, clinic_id: ID.clinic, display_name: 'Retired Provider', provider_id: ID.task }] : [])]);
      if (path.endsWith('/locations')) return page([{ active: true, clinic_id: ID.clinic, location_id: ID.slot, name: 'Room 1' }]);
      if (path.endsWith('/visit-types')) return page([{ active: true, clinic_id: ID.clinic, duration_minutes: 30, name: 'Placeholder Visit', visit_type_id: ID.visitType }]);
      if (path.endsWith('/priority-levels')) return page([{ active: true, clinic_id: ID.clinic, code: 'P1', label: 'Placeholder One', rank: 1 }]);
      if (path.endsWith('/holds')) return page(holds.filter((h) => h.status === (query.get('status') ?? 'active')));
      if (path.endsWith('/offers/draft')) return ok({ status: 'ok', reason: null, missing_settings: null, data: { candidates: [{ joined_at: '2026-10-01T05:00:00Z', patient_id: ID.patient, priority_code: 'P1', rank: 1, waitlist_id: ID.hold }], draft: { candidate_patient_id: ID.patient, hold_lifetime_seconds: 900, offer_expiry_seconds: 86400, rule: 'rank_then_joined_at', template: 'PLACEHOLDER template line one.\nPLACEHOLDER line two.', waitlist_id: ID.hold } } });
    }
    const release = m(/^\/v1\/staff\/scheduling\/holds\/([^/]+)\/release$/);
    if (method === 'POST' && release) {
      if (role !== 'front_desk') return no(403);
      const h = holds.find((x) => x.hold_id === release[1]);
      if (!h) return no(404);
      if (h.status !== 'active') return no(409);
      holds = holds.map((x) => (x.hold_id === h.hold_id ? { ...x, status: 'released' } : x));
      return ok({ hold_id: h.hold_id, replayed: false, slot_id: h.slot_id, status: 'released' });
    }
    return no(404);
  };
}
