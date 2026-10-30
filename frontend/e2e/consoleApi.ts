// MOCK ONLY. A small stateful fake of the console API routes (F3 and F4), written from openapi.snapshot.json and the route
// docstrings (docs/design/review-task-routes.md, frontend-contract.md, identity-confirmation-revoke.md). It enforces roles so
// "not available to your role" can be exercised in a real browser, and it demands a recent second factor on the merge routes
// so the step-up prompt and retry can be too. It proves this app's behaviour, not the backend's. Every error body carries LEAK.

export const LEAK = 'LEAK-SENTINEL-backend-text';
export const ID = {
  clinic: '3f2b8c1e-6a4d-4e1b-9d0a-1c2d3e4f5a6b',
  doc: '11111111-1111-4111-8111-111111111111',
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

export function createConsoleApi(role: string, options: ConsoleOptions = {}, session: SessionFacts = { steppedUp: () => true }) {
  const taskStatus: Record<string, 'open' | 'acknowledged' | 'resolved'> = { [ID.task]: 'open', [ID.taskRed]: 'open' };
  let holds = [{ clinic_id: ID.clinic, created_at: '2026-10-30T05:00:00Z', expires_at: '2026-10-30T06:00:00Z', hold_id: ID.hold, patient_id: null, slot_id: ID.slot, status: 'active' }];
  let verified = options.patientVerified ?? false;
  let reviewed: string | null = null;
  let mergeStatus: 'awaiting_second_approval' | 'active' | 'unmerged' = 'awaiting_second_approval';

  const canSeeProposal = REVIEWERS.includes(role);
  const referral = () => ({ clinic_id: ID.clinic, content_type: 'application/pdf', created_at: '2026-10-30T05:00:00Z', decided: canSeeProposal ? reviewed !== null : null, document_id: ID.doc, open_holds: 1, proposal_id: canSeeProposal ? ID.candidate : null, proposal_visible: canSeeProposal, registered_by: ID.user, run_state: 'needs_front_desk', size_bytes: 20480 });
  const proposal = { abstained: false, candidates: [{ card_id: 'card-placeholder', version: 1 }], created_at: '2026-10-30T05:00:00Z', mode: 'shadow', patient_id: ID.patient, proposal_id: ID.candidate, review_task_id: ID.task };
  const task = (id: string, kind: string, priority: string, assigned: string) => ({ acknowledged_at: null, acknowledged_by: null, assigned_role: assigned, clinic_id: ID.clinic, created_at: '2026-10-30T05:00:00Z', kind, priority, resolved_at: null, status: taskStatus[id], task_id: id });
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
      if (!['front_desk', 'nurse', 'doctor', 'admin'].includes(role)) return no(403);
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

    if (method === 'GET' && path.startsWith('/v1/staff/scheduling/')) {
      if (!READERS.includes(role)) return no(403);
      const inactive = query.get('include_inactive') === 'true';
      if (path.endsWith('/providers')) return page([{ active: true, clinic_id: ID.clinic, display_name: 'Placeholder Provider', provider_id: ID.doc }, ...(inactive ? [{ active: false, clinic_id: ID.clinic, display_name: 'Retired Provider', provider_id: ID.task }] : [])]);
      if (path.endsWith('/locations')) return page([{ active: true, clinic_id: ID.clinic, location_id: ID.slot, name: 'Room 1' }]);
      if (path.endsWith('/visit-types')) return page([{ active: true, clinic_id: ID.clinic, duration_minutes: 30, name: 'Placeholder Visit', visit_type_id: ID.visitType }]);
      if (path.endsWith('/priority-levels')) return page([{ active: true, clinic_id: ID.clinic, code: 'P1', label: 'Placeholder One', rank: 1 }]);
      if (path.endsWith('/holds')) return page(holds.filter((h) => h.status === (query.get('status') ?? 'active')));
      if (path.endsWith('/offers/draft')) return ok({ status: 'unavailable', reason: `not_configured: ${LEAK}`, data: null });
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
