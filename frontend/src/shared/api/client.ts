import createClient from 'openapi-fetch';
import type { components, paths } from './schema';

export type Me = components['schemas']['Me'];
export type ProposeMergeIn = components['schemas']['ProposeMergeIn'];
export type BeginUploadIn = components['schemas']['BeginUploadIn'];
export type RegisterIn = components['schemas']['RegisterIn'];
export type PatientSearchIn = components['schemas']['PatientSearchIn'];
export type ReviewDecision = components['schemas']['ReviewIn']['decision'];
export type RevokeReason = components['schemas']['RevokeIdentityIn']['reason'];
type MergeQuery = NonNullable<paths['/v1/staff/identity/merges']['get']['parameters']['query']>;
type DuplicateQuery = NonNullable<paths['/v1/staff/identity/possible-duplicates']['get']['parameters']['query']>;
export type MergeStatus = NonNullable<MergeQuery['status']>;
export type DuplicateStatus = NonNullable<DuplicateQuery['status']>;
type TaskQuery = NonNullable<paths['/v1/staff/review-tasks']['get']['parameters']['query']>;
type ReviewTaskStatus = TaskQuery['status'];
type TaskRole = NonNullable<TaskQuery['assigned_role']>;
export type RecordReplyIn = components['schemas']['RecordReplyIn'];
export type BackfillAction = components['schemas']['BackfillDecideIn']['action'];
type BackfillReplyIn = components['schemas']['BackfillReplyIn'];
type ThreadQuery = NonNullable<paths['/v1/staff/messages/threads']['get']['parameters']['query']>;
type ThreadTopic = NonNullable<ThreadQuery['topic']>;
export type ConsentIn = components['schemas']['ConsentIn'];
export type ClaimIn = components['schemas']['ClaimIn'];
export type GrievanceIn = components['schemas']['GrievanceIn'];
export type NoteIn = components['schemas']['NoteIn'];
export type BreakGlassOpenIn = components['schemas']['BreakGlassOpenIn'];
export type BreakGlassOutcome = components['schemas']['BreakGlassReviewIn']['outcome'];
export type ClaimDecision = components['schemas']['ClaimResolveIn']['decision'];
export type GrievanceStatus = components['schemas']['GrievanceStatusIn']['status'];
type ReadinessReplyIn = components['schemas']['ReadinessReplyIn'];
export type RecoveryRequestIn = components['schemas']['RecoveryRequestIn'];
export type BarrierIn = components['schemas']['BarrierIn'];
export type BarrierKind = BarrierIn['barrier'];
export type EpisodeIn = components['schemas']['EpisodeIn'];
export type InviteIn = components['schemas']['InviteIn'];
export type StaffRole = InviteIn['role'];
export type RoleChangeIn = components['schemas']['RoleChangeIn'];
export type AutonomySetIn = components['schemas']['AutonomySetIn'];
export type AutonomyDecision = components['schemas']['AutonomyResolveIn']['decision'];
export type VoicePurgeIn = components['schemas']['VoicePurgeIn'];
export type CarePlanIn = components['schemas']['CarePlanIn'];
export type ResultIn = components['schemas']['ResultIn'];
export type CarePlanAction = components['schemas']['CarePlanConfirmIn']['action'];
export type SlotIn = components['schemas']['SlotIn'];
export type HoldIn = components['schemas']['HoldIn'];
export type WaitlistJoinIn = components['schemas']['WaitlistJoinIn'];
export type LocationIn = components['schemas']['LocationIn'];
export type ProviderIn = components['schemas']['ProviderIn'];
export type VisitTypeIn = components['schemas']['VisitTypeIn'];
type OfferQuery = NonNullable<paths['/v1/staff/scheduling/offers']['get']['parameters']['query']>;
export type OfferState = NonNullable<OfferQuery['state']>;
export type HoldStatus = NonNullable<paths['/v1/staff/scheduling/holds']['get']['parameters']['query']>['status'];

/** The request header that names the active role (docs/design/active-role.md; the backend reads it since migration 0028). */
export const ACTIVE_ROLE_HEADER = 'X-CareFlow-Active-Role';
const ACTIVE_ROLE_VALUE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}:[a-z][a-z_]*$/;
export const formatActiveRole = (clinicId: string, role: string): string => `${clinicId}:${role}`;

/**
 * Every operation this client may call, keyed by method name. The contract test checks that each entry
 * exists in openapi.snapshot.json and that the request the client really sends matches it. Add a client
 * method here AND to the contract test table, or the test fails.
 */
export const OPERATIONS = {
  getMe: { method: 'get', path: '/v1/iam/me' },
  getReadyz: { method: 'get', path: '/readyz' },
  proposeMerge: { method: 'post', path: '/v1/staff/merges' },
  approveMerge: { method: 'post', path: '/v1/staff/merges/{merge_id}/approve' },
  unmerge: { method: 'post', path: '/v1/staff/merges/{merge_id}/unmerge' },
  listReferrals: { method: 'get', path: '/v1/staff/referrals' },
  openReferral: { method: 'get', path: '/v1/staff/referrals/{document_id}' },
  beginUpload: { method: 'post', path: '/v1/staff/documents/uploads' },
  registerDocument: { method: 'post', path: '/v1/staff/documents/register' },
  listReviewTasks: { method: 'get', path: '/v1/staff/review-tasks' },
  openReviewTask: { method: 'get', path: '/v1/staff/review-tasks/{task_id}' },
  acknowledgeTask: { method: 'post', path: '/v1/staff/review-tasks/{task_id}/acknowledge' },
  resolveTask: { method: 'post', path: '/v1/staff/review-tasks/{task_id}/resolve' },
  searchPatients: { method: 'post', path: '/v1/staff/patients/search' },
  openPatient: { method: 'get', path: '/v1/staff/patients/{patient_id}' },
  confirmIdentity: { method: 'post', path: '/v1/staff/patients/{patient_id}/confirm-identity' },
  listProviders: { method: 'get', path: '/v1/staff/scheduling/providers' },
  listLocations: { method: 'get', path: '/v1/staff/scheduling/locations' },
  listVisitTypes: { method: 'get', path: '/v1/staff/scheduling/visit-types' },
  listPriorityLevels: { method: 'get', path: '/v1/staff/scheduling/priority-levels' },
  listHolds: { method: 'get', path: '/v1/staff/scheduling/holds' },
  releaseHold: { method: 'post', path: '/v1/staff/scheduling/holds/{hold_id}/release' },
  draftOffer: { method: 'get', path: '/v1/staff/scheduling/offers/draft' },
  reviewReferral: { method: 'post', path: '/v1/staff/referrals/{document_id}/review' },
  revokeIdentity: { method: 'post', path: '/v1/staff/patients/{patient_id}/revoke-identity' },
  listMerges: { method: 'get', path: '/v1/staff/identity/merges' },
  openMerge: { method: 'get', path: '/v1/staff/identity/merges/{merge_id}' },
  listDuplicates: { method: 'get', path: '/v1/staff/identity/possible-duplicates' },
  // ---- F7: scheduling ----
  listSlots: { method: 'get', path: '/v1/staff/scheduling/slots' },
  createSlot: { method: 'post', path: '/v1/staff/scheduling/slots' },
  holdSlot: { method: 'post', path: '/v1/staff/scheduling/slots/{slot_id}/hold' },
  bookSlot: { method: 'post', path: '/v1/staff/scheduling/slots/{slot_id}/book' },
  listBookings: { method: 'get', path: '/v1/staff/scheduling/bookings' },
  cancelBooking: { method: 'post', path: '/v1/staff/scheduling/bookings/{booking_id}/cancel' },
  listWaitlist: { method: 'get', path: '/v1/staff/scheduling/waitlist' },
  joinWaitlist: { method: 'post', path: '/v1/staff/scheduling/waitlist/join' },
  listOffers: { method: 'get', path: '/v1/staff/scheduling/offers' },
  markOfferDelivered: { method: 'post', path: '/v1/staff/scheduling/offers/{hold_id}/mark-delivered' },
  createLocation: { method: 'post', path: '/v1/staff/scheduling/locations' },
  createProvider: { method: 'post', path: '/v1/staff/scheduling/providers' },
  createVisitType: { method: 'post', path: '/v1/staff/scheduling/visit-types' },
  readMessage: { method: 'get', path: '/v1/staff/messages/{message_id}' },
  // ---- alerts, dashboard, care gaps, care plans, results ----
  listAlerts: { method: 'get', path: '/v1/staff/console/alerts' },
  acknowledgeAlert: { method: 'post', path: '/v1/staff/console/alerts/{alert_id}/acknowledge' },
  dashboard: { method: 'get', path: '/v1/staff/console/dashboard' },
  careGaps: { method: 'get', path: '/v1/staff/care-gaps' },
  listCarePlans: { method: 'get', path: '/v1/staff/care-plans' },
  createCarePlan: { method: 'post', path: '/v1/staff/care-plans' },
  confirmCarePlan: { method: 'post', path: '/v1/staff/care-plans/{plan_id}/confirm' },
  recordResult: { method: 'post', path: '/v1/staff/results' },
  // ---- backfill: an offer run for a freed slot (nothing is sent to a patient) ----
  startBackfill: { method: 'post', path: '/v1/staff/scheduling/backfills' },
  backfillState: { method: 'get', path: '/v1/staff/scheduling/backfills/{slot_id}' },
  decideBackfill: { method: 'post', path: '/v1/staff/scheduling/backfills/{slot_id}/decide' },
  replyBackfill: { method: 'post', path: '/v1/staff/scheduling/backfills/{slot_id}/reply' },
  interpretReply: { method: 'post', path: '/v1/staff/scheduling/offers/interpret-reply' },
  recordReply: { method: 'post', path: '/v1/staff/scheduling/offers/replies' },
  listThreads: { method: 'get', path: '/v1/staff/messages/threads' },
  listThreadMessages: { method: 'get', path: '/v1/staff/messages/threads/{thread_id}/messages' },
  // ---- lists that replace typing: clinic, grievance categories, break-glass reasons and grants, upcoming visits ----
  publicClinic: { method: 'get', path: '/public/clinic' },
  grievanceCategories: { method: 'get', path: '/v1/portal/grievance-categories' },
  breakGlassReasons: { method: 'get', path: '/v1/staff/break-glass/reasons' },
  breakGlassMine: { method: 'get', path: '/v1/staff/break-glass/mine' },
  upcomingVisits: { method: 'get', path: '/v1/staff/visits/upcoming' },
  // ---- the patient portal (patient session only) ----
  portalRegister: { method: 'post', path: '/v1/portal/register' },
  portalMe: { method: 'get', path: '/v1/portal/me' },
  portalConsents: { method: 'get', path: '/v1/portal/consents' },
  recordConsent: { method: 'post', path: '/v1/portal/consents' },
  portalActivate: { method: 'post', path: '/v1/portal/activate' },
  portalClaim: { method: 'post', path: '/v1/portal/claims' },
  listMyGrievances: { method: 'get', path: '/v1/portal/grievances' },
  fileGrievance: { method: 'post', path: '/v1/portal/grievances' },
  myData: { method: 'get', path: '/v1/portal/my-data' },
  // ---- visit readiness, portal claims, break-glass, recovery, notes, grievances ----
  readinessView: { method: 'get', path: '/v1/staff/visits/{visit_id}/readiness' },
  startReadiness: { method: 'post', path: '/v1/staff/visits/{visit_id}/readiness/start' },
  replyReadiness: { method: 'post', path: '/v1/staff/visits/{visit_id}/readiness/reply' },
  listPortalClaims: { method: 'get', path: '/v1/staff/portal-claims' },
  resolvePortalClaim: { method: 'post', path: '/v1/staff/portal-claims/{request_id}/resolve' },
  listBreakGlass: { method: 'get', path: '/v1/staff/break-glass' },
  openBreakGlass: { method: 'post', path: '/v1/staff/break-glass' },
  readBreakGlass: { method: 'get', path: '/v1/staff/break-glass/{grant_id}/messages' },
  reviewBreakGlass: { method: 'post', path: '/v1/staff/break-glass/{grant_id}/review' },
  listRecovery: { method: 'get', path: '/v1/staff/recovery' },
  requestRecovery: { method: 'post', path: '/v1/staff/recovery' },
  approveRecovery: { method: 'post', path: '/v1/staff/recovery/{request_id}/approve' },
  cancelRecovery: { method: 'post', path: '/v1/staff/recovery/{request_id}/cancel' },
  completeRecovery: { method: 'post', path: '/v1/staff/recovery/{request_id}/complete' },
  listNotes: { method: 'get', path: '/v1/staff/notes' },
  writeNote: { method: 'post', path: '/v1/staff/notes' },
  listGrievances: { method: 'get', path: '/v1/staff/grievances' },
  setGrievanceStatus: { method: 'post', path: '/v1/staff/grievances/{grievance_id}/status' },
  // ---- care-gap writes ----
  recordBarrier: { method: 'post', path: '/v1/staff/care-gaps/barriers' },
  resolveBarrier: { method: 'post', path: '/v1/staff/care-gaps/barriers/{barrier_id}/resolve' },
  startEpisode: { method: 'post', path: '/v1/staff/care-gaps/episodes' },
  markRuleDone: { method: 'post', path: '/v1/staff/care-gaps/episode-rules/{episode_rule_id}/done' },
  recheckSample: { method: 'post', path: '/v1/staff/care-gaps/samples/{sample_id}/recheck' },
  // ---- admin ----
  listAccounts: { method: 'get', path: '/v1/staff/accounts' },
  inviteAccount: { method: 'post', path: '/v1/staff/accounts/invite' },
  suspendAccount: { method: 'post', path: '/v1/staff/accounts/{account_id}/suspend' },
  reinstateAccount: { method: 'post', path: '/v1/staff/accounts/{account_id}/reinstate' },
  grantRole: { method: 'post', path: '/v1/staff/accounts/{account_id}/roles' },
  revokeRole: { method: 'post', path: '/v1/staff/accounts/{account_id}/roles/revoke' },
  getAutonomy: { method: 'get', path: '/v1/staff/console/autonomy' },
  setAutonomy: { method: 'post', path: '/v1/staff/console/autonomy' },
  listAutonomyProposals: { method: 'get', path: '/v1/staff/console/autonomy/proposals' },
  autonomyHistory: { method: 'get', path: '/v1/staff/console/autonomy/history' },
  proposeAutonomy: { method: 'post', path: '/v1/staff/console/autonomy/proposals' },
  resolveAutonomyProposal: { method: 'post', path: '/v1/staff/console/autonomy/proposals/{proposal_id}/resolve' },
  reviewerStats: { method: 'get', path: '/v1/staff/console/reviewer-stats' },
  costCanvas: { method: 'get', path: '/v1/staff/console/cost-canvas' },
  listVoiceSessions: { method: 'get', path: '/v1/staff/voice/sessions' },
  readTranscript: { method: 'get', path: '/v1/staff/voice/sessions/{session_id}/transcript' },
  purgeVoice: { method: 'post', path: '/v1/staff/voice/purge' },
  // ---- review console ----
  reviewView: { method: 'get', path: '/v1/staff/referrals/{document_id}/review-view' },
  reasonCodes: { method: 'get', path: '/v1/staff/console/reason-codes' },
} as const satisfies Record<string, { method: 'get' | 'post'; path: keyof paths }>;

interface ApiOptions {
  baseUrl: string;
  /** Supplies the current access token, or null when signed out. Never logged. */
  getAccessToken: () => Promise<string | null> | string | null;
  /** Transport. Defaults to the global fetch; tests wrap it. */
  fetch?: typeof fetch;
  /**
   * Called when the API answers 401 with `WWW-Authenticate: step-up`. Resolves true only if the user just
   * completed a fresh second-factor verification; the request is then retried ONCE with the new token and the
   * same idempotency key. Resolving false (cancelled, failed) returns the original 401 to the caller.
   */
  onStepUp?: () => Promise<boolean>;
  /** Called for every other 401. It receives no reason on purpose: the UI must not reveal one. */
  onUnauthorized?: () => void;
  /**
   * The active-role header (docs/design/active-role.md). OFF unless `enabled` is true: the backend does not read it
   * yet, and an unknown request header forces a CORS preflight that production does not allow yet. When on, the
   * value is sent on the first request AND on a step-up retry, and only if it is exactly `<clinic uuid>:<role>`.
   */
  activeRole?: { enabled: boolean; get: () => string | null };
  /**
   * Called when the backend answers the ONE fixed refusal for the active-role header (403, detail "active role not available")
   * to a request that carried the header. The caller falls back to choosing a role again; the user stays signed in.
   */
  onActiveRoleRefused?: () => void;
  /** True while the browser reports no connection. A write is then not sent at all (it fails as a network error, key kept). */
  isOffline?: () => boolean;
}

/** A write that was not sent because the browser reports no connection. Nothing reached the server. */
export class OfflineWriteError extends TypeError {
  constructor() {
    super('offline');
  }
}

/** The body the backend sends for a refused active-role header. Matched exactly: any other 403 is an ordinary refusal. */
export const ACTIVE_ROLE_REFUSAL_DETAIL = 'active role not available';

/** True only for the exact step-up challenge the backend sends. Anything else is an ordinary 401. */
export function isStepUpChallenge(res: Response): boolean {
  if (res.status !== 401) return false;
  return (res.headers.get('www-authenticate') ?? '').trim().split(/[\s,]/)[0]?.toLowerCase() === 'step-up';
}

export interface WriteOptions {
  /** Reuse the same key when retrying a write so the server replays it instead of repeating it. */
  idempotencyKey?: string;
}

const newKey = (): string => globalThis.crypto.randomUUID();

export function createApi(options: ApiOptions) {
  const transport = options.fetch ?? ((input: Request) => globalThis.fetch(input));
  // A search is a POST only so personal details stay out of the URL; it changes nothing, so it is not held back here (offline it fails by itself).
  const isSearch = (r: Request) => new URL(r.url).pathname === '/v1/staff/patients/search';
  async function noteActiveRoleRefusal(res: Response, req: Request): Promise<void> {
    if (!options.onActiveRoleRefused || !req.headers.has(ACTIVE_ROLE_HEADER)) return;
    const body: unknown = await res.clone().json().catch(() => null);
    if ((body as { detail?: unknown } | null)?.detail === ACTIVE_ROLE_REFUSAL_DETAIL) options.onActiveRoleRefused();
  }
  const client = createClient<paths>({
    baseUrl: options.baseUrl,
    fetch: async (request: Request) => {
      // Cloned before the first send: a request body can be read only once, and the retry needs it.
      const retry = options.onStepUp ? request.clone() : null;
      const authorize = async (r: Request) => {
        const token = await options.getAccessToken();
        if (token) r.headers.set('Authorization', `Bearer ${token}`);
        const role = options.activeRole?.enabled ? options.activeRole.get() : null;
        if (role !== null && ACTIVE_ROLE_VALUE.test(role)) r.headers.set(ACTIVE_ROLE_HEADER, role);
      };
      // Offline: nothing is sent for a write, so it can never half-happen. It fails like any lost connection (the caller keeps its key).
      if (options.isOffline?.() && request.method !== 'GET' && request.method !== 'HEAD' && !isSearch(request)) throw new OfflineWriteError();
      await authorize(request);
      const first = await transport(request);
      if (first.status === 403) await noteActiveRoleRefusal(first, request);
      if (first.status !== 401) return first;

      if (isStepUpChallenge(first) && options.onStepUp && retry) {
        if (!(await options.onStepUp())) return first;
        await authorize(retry); // the token minted by the fresh verification
        const second = await transport(retry);
        if (second.status === 403) await noteActiveRoleRefusal(second, retry);
        // One retry only. A second step-up challenge is returned to the caller, never looped on.
        if (second.status === 401 && !isStepUpChallenge(second)) options.onUnauthorized?.();
        return second;
      }
      options.onUnauthorized?.();
      return first;
    },
  });
  const key = (o?: WriteOptions): string => o?.idempotencyKey ?? newKey();
  const inactive = (include: boolean) => (include ? { include_inactive: true } : {});

  return {
    getMe: () => client.GET('/v1/iam/me'),
    getReadyz: () => client.GET('/readyz'),
    proposeMerge: (body: ProposeMergeIn, o?: WriteOptions) =>
      client.POST('/v1/staff/merges', { params: { header: { 'X-Idempotency-Key': key(o) } }, body }),
    approveMerge: (mergeId: string, o?: WriteOptions) =>
      client.POST('/v1/staff/merges/{merge_id}/approve', {
        params: { path: { merge_id: mergeId }, header: { 'X-Idempotency-Key': key(o) } },
      }),
    unmerge: (mergeId: string, o?: WriteOptions) =>
      client.POST('/v1/staff/merges/{merge_id}/unmerge', {
        params: { path: { merge_id: mergeId }, header: { 'X-Idempotency-Key': key(o) } },
      }),

    // ---- F3: staff console screens ---------------------------------------------------------------------------
    listReferrals: (limit?: number) => client.GET('/v1/staff/referrals', { params: { query: limit === undefined ? {} : { limit } } }),
    openReferral: (documentId: string) => client.GET('/v1/staff/referrals/{document_id}', { params: { path: { document_id: documentId } } }),
    // Upload is two writes that share ONE key: the server ties the registered row to the ticket by it.
    beginUpload: (body: BeginUploadIn, o?: WriteOptions) =>
      client.POST('/v1/staff/documents/uploads', { params: { header: { 'X-Idempotency-Key': key(o) } }, body }),
    registerDocument: (body: RegisterIn, o?: WriteOptions) =>
      client.POST('/v1/staff/documents/register', { params: { header: { 'X-Idempotency-Key': key(o) } }, body }),
    listReviewTasks: (f: { status?: ReviewTaskStatus; kind?: string | null; assignedRole?: TaskRole | null; limit?: number } = {}) =>
      client.GET('/v1/staff/review-tasks', {
        params: {
          query: {
            ...(f.status ? { status: f.status } : {}),
            ...(f.kind ? { kind: f.kind } : {}),
            ...(f.assignedRole ? { assigned_role: f.assignedRole } : {}),
            ...(f.limit ? { limit: f.limit } : {}),
          },
        },
      }),
    openReviewTask: (taskId: string) => client.GET('/v1/staff/review-tasks/{task_id}', { params: { path: { task_id: taskId } } }),
    acknowledgeTask: (taskId: string, o?: WriteOptions) =>
      client.POST('/v1/staff/review-tasks/{task_id}/acknowledge', { params: { path: { task_id: taskId }, header: { 'X-Idempotency-Key': key(o) } } }),
    resolveTask: (taskId: string, o?: WriteOptions) =>
      client.POST('/v1/staff/review-tasks/{task_id}/resolve', { params: { path: { task_id: taskId }, header: { 'X-Idempotency-Key': key(o) } } }),
    // A search is a read (no key): the route is a POST only so the person's details are not put in a URL.
    searchPatients: (body: PatientSearchIn) => client.POST('/v1/staff/patients/search', { body }),
    openPatient: (patientId: string) => client.GET('/v1/staff/patients/{patient_id}', { params: { path: { patient_id: patientId } } }),
    confirmIdentity: (patientId: string, o?: WriteOptions) =>
      client.POST('/v1/staff/patients/{patient_id}/confirm-identity', { params: { path: { patient_id: patientId }, header: { 'X-Idempotency-Key': key(o) } } }),
    listProviders: (includeInactive = false) => client.GET('/v1/staff/scheduling/providers', { params: { query: inactive(includeInactive) } }),
    listLocations: (includeInactive = false) => client.GET('/v1/staff/scheduling/locations', { params: { query: inactive(includeInactive) } }),
    listVisitTypes: (includeInactive = false) => client.GET('/v1/staff/scheduling/visit-types', { params: { query: inactive(includeInactive) } }),
    listPriorityLevels: (includeInactive = false) => client.GET('/v1/staff/scheduling/priority-levels', { params: { query: inactive(includeInactive) } }),
    listHolds: (status?: HoldStatus) => client.GET('/v1/staff/scheduling/holds', { params: { query: status ? { status } : {} } }),
    releaseHold: (holdId: string, o?: WriteOptions) =>
      client.POST('/v1/staff/scheduling/holds/{hold_id}/release', { params: { path: { hold_id: holdId }, header: { 'X-Idempotency-Key': key(o) } } }),
    draftOffer: (clinicId: string, visitTypeId: string) =>
      client.GET('/v1/staff/scheduling/offers/draft', { params: { query: { clinic_id: clinicId, visit_type_id: visitTypeId } } }),

    // ---- F4: referral decisions, identity revoke, merge and duplicate lists ---------------------------------------
    reviewReferral: (documentId: string, decision: ReviewDecision, o?: WriteOptions, reasonCode?: string | null) =>
      client.POST('/v1/staff/referrals/{document_id}/review', {
        params: { path: { document_id: documentId }, header: { 'X-Idempotency-Key': key(o) } },
        body: reasonCode ? { decision, reason_code: reasonCode } : { decision },
      }),
    revokeIdentity: (patientId: string, reason: RevokeReason, o?: WriteOptions) =>
      client.POST('/v1/staff/patients/{patient_id}/revoke-identity', {
        params: { path: { patient_id: patientId }, header: { 'X-Idempotency-Key': key(o) } },
        body: { reason },
      }),
    listMerges: (status?: MergeStatus) => client.GET('/v1/staff/identity/merges', { params: { query: status ? { status } : {} } }),
    openMerge: (mergeId: string) => client.GET('/v1/staff/identity/merges/{merge_id}', { params: { path: { merge_id: mergeId } } }),
    listDuplicates: (status?: DuplicateStatus) => client.GET('/v1/staff/identity/possible-duplicates', { params: { query: status ? { status } : {} } }),

    // ---- F7: scheduling ---------------------------------------------------------------------------------------------
    listSlots: (f: { providerId?: string; locationId?: string; limit?: number } = {}) =>
      client.GET('/v1/staff/scheduling/slots', {
        params: { query: { ...(f.providerId ? { provider_id: f.providerId } : {}), ...(f.locationId ? { location_id: f.locationId } : {}), ...(f.limit ? { limit: f.limit } : {}) } },
      }),
    createSlot: (body: SlotIn, o?: WriteOptions) => client.POST('/v1/staff/scheduling/slots', { params: { header: { 'X-Idempotency-Key': key(o) } }, body }),
    holdSlot: (slotId: string, body: HoldIn, o?: WriteOptions) =>
      client.POST('/v1/staff/scheduling/slots/{slot_id}/hold', { params: { path: { slot_id: slotId }, header: { 'X-Idempotency-Key': key(o) } }, body }),
    bookSlot: (slotId: string, patientId: string, o?: WriteOptions) =>
      client.POST('/v1/staff/scheduling/slots/{slot_id}/book', { params: { path: { slot_id: slotId }, header: { 'X-Idempotency-Key': key(o) } }, body: { patient_id: patientId } }),
    listBookings: (limit?: number) => client.GET('/v1/staff/scheduling/bookings', { params: { query: limit ? { limit } : {} } }),
    cancelBooking: (bookingId: string, o?: WriteOptions) =>
      client.POST('/v1/staff/scheduling/bookings/{booking_id}/cancel', { params: { path: { booking_id: bookingId }, header: { 'X-Idempotency-Key': key(o) } } }),
    listWaitlist: (visitTypeId?: string) => client.GET('/v1/staff/scheduling/waitlist', { params: { query: visitTypeId ? { visit_type_id: visitTypeId } : {} } }),
    joinWaitlist: (body: WaitlistJoinIn, o?: WriteOptions) => client.POST('/v1/staff/scheduling/waitlist/join', { params: { header: { 'X-Idempotency-Key': key(o) } }, body }),
    listOffers: (state?: OfferState) => client.GET('/v1/staff/scheduling/offers', { params: { query: state ? { state } : {} } }),
    markOfferDelivered: (holdId: string, o?: WriteOptions) =>
      client.POST('/v1/staff/scheduling/offers/{hold_id}/mark-delivered', { params: { path: { hold_id: holdId }, header: { 'X-Idempotency-Key': key(o) } } }),
    createLocation: (body: LocationIn, o?: WriteOptions) => client.POST('/v1/staff/scheduling/locations', { params: { header: { 'X-Idempotency-Key': key(o) } }, body }),
    createProvider: (body: ProviderIn, o?: WriteOptions) => client.POST('/v1/staff/scheduling/providers', { params: { header: { 'X-Idempotency-Key': key(o) } }, body }),
    createVisitType: (body: VisitTypeIn, o?: WriteOptions) => client.POST('/v1/staff/scheduling/visit-types', { params: { header: { 'X-Idempotency-Key': key(o) } }, body }),
    // Reads one message AND audits the read: only on a person's click, never in the background, and the body is never cached.
    readMessage: (messageId: string) => client.GET('/v1/staff/messages/{message_id}', { params: { path: { message_id: messageId } } }),

    // ---- alerts, dashboard, care gaps, care plans, results ---------------------------------------------------------------
    listAlerts: (clinicId: string, includeAcknowledged = false) =>
      client.GET('/v1/staff/console/alerts', { params: { query: { clinic_id: clinicId, ...(includeAcknowledged ? { include_acknowledged: true } : {}) } } }),
    acknowledgeAlert: (alertId: string, o?: WriteOptions) =>
      client.POST('/v1/staff/console/alerts/{alert_id}/acknowledge', { params: { path: { alert_id: alertId }, header: { 'X-Idempotency-Key': key(o) } } }),
    dashboard: (clinicId: string) => client.GET('/v1/staff/console/dashboard', { params: { query: { clinic_id: clinicId } } }),
    careGaps: (clinicId: string) => client.GET('/v1/staff/care-gaps', { params: { query: { clinic_id: clinicId } } }),
    listCarePlans: (clinicId: string) => client.GET('/v1/staff/care-plans', { params: { query: { clinic_id: clinicId } } }),
    createCarePlan: (body: CarePlanIn, o?: WriteOptions) => client.POST('/v1/staff/care-plans', { params: { header: { 'X-Idempotency-Key': key(o) } }, body }),
    confirmCarePlan: (planId: string, action: CarePlanAction, o?: WriteOptions) =>
      client.POST('/v1/staff/care-plans/{plan_id}/confirm', { params: { path: { plan_id: planId }, header: { 'X-Idempotency-Key': key(o) } }, body: { action } }),
    recordResult: (body: ResultIn, o?: WriteOptions) => client.POST('/v1/staff/results', { params: { header: { 'X-Idempotency-Key': key(o) } }, body }),

    // ---- backfill: an offer run for a freed slot. Nothing here sends anything to a patient. -----------------------------------
    startBackfill: (body: { clinic_id: string; slot_id: string; visit_type_id: string }, o?: WriteOptions) =>
      client.POST('/v1/staff/scheduling/backfills', { params: { header: { 'X-Idempotency-Key': key(o) } }, body }),
    backfillState: (slotId: string) => client.GET('/v1/staff/scheduling/backfills/{slot_id}', { params: { path: { slot_id: slotId } } }),
    decideBackfill: (slotId: string, action: BackfillAction, o?: WriteOptions) =>
      client.POST('/v1/staff/scheduling/backfills/{slot_id}/decide', { params: { path: { slot_id: slotId }, header: { 'X-Idempotency-Key': key(o) } }, body: { action } }),
    replyBackfill: (slotId: string, body: BackfillReplyIn, o?: WriteOptions) =>
      client.POST('/v1/staff/scheduling/backfills/{slot_id}/reply', { params: { path: { slot_id: slotId }, header: { 'X-Idempotency-Key': key(o) } }, body }),
    // Stores what a patient told the front desk (encrypted, on the appointments thread; the red-flag rules read it first). Nothing is sent to the patient.
    recordReply: (body: RecordReplyIn, o?: WriteOptions) => client.POST('/v1/staff/scheduling/offers/replies', { params: { header: { 'X-Idempotency-Key': key(o) } }, body }),
    interpretReply: (messageId: string, o?: WriteOptions) =>
      client.POST('/v1/staff/scheduling/offers/interpret-reply', { params: { header: { 'X-Idempotency-Key': key(o) } }, body: { message_id: messageId } }),
    listThreads: (patientId: string, topic: ThreadTopic) => client.GET('/v1/staff/messages/threads', { params: { query: { patient_id: patientId, topic } } }),
    listThreadMessages: (threadId: string) => client.GET('/v1/staff/messages/threads/{thread_id}/messages', { params: { path: { thread_id: threadId } } }),

    // ---- lists that replace typing ------------------------------------------------------------------------------------------------
    publicClinic: () => client.GET('/public/clinic'),
    grievanceCategories: () => client.GET('/v1/portal/grievance-categories'),
    breakGlassReasons: (clinicId: string) => client.GET('/v1/staff/break-glass/reasons', { params: { query: { clinic_id: clinicId } } }),
    breakGlassMine: (clinicId: string) => client.GET('/v1/staff/break-glass/mine', { params: { query: { clinic_id: clinicId } } }),
    upcomingVisits: (clinicId: string) => client.GET('/v1/staff/visits/upcoming', { params: { query: { clinic_id: clinicId } } }),

    // ---- the patient portal ---------------------------------------------------------------------------------------------------
    portalRegister: (o?: WriteOptions) => client.POST('/v1/portal/register', { params: { header: { 'X-Idempotency-Key': key(o) } } }),
    portalMe: () => client.GET('/v1/portal/me'),
    portalConsents: () => client.GET('/v1/portal/consents'),
    recordConsent: (body: ConsentIn, o?: WriteOptions) => client.POST('/v1/portal/consents', { params: { header: { 'X-Idempotency-Key': key(o) } }, body }),
    portalActivate: (o?: WriteOptions) => client.POST('/v1/portal/activate', { params: { header: { 'X-Idempotency-Key': key(o) } } }),
    portalClaim: (body: ClaimIn, o?: WriteOptions) => client.POST('/v1/portal/claims', { params: { header: { 'X-Idempotency-Key': key(o) } }, body }),
    listMyGrievances: () => client.GET('/v1/portal/grievances'),
    fileGrievance: (body: GrievanceIn, o?: WriteOptions) => client.POST('/v1/portal/grievances', { params: { header: { 'X-Idempotency-Key': key(o) } }, body }),
    // Everything held about the person: only on their click, saved as one file, never cached.
    myData: () => client.GET('/v1/portal/my-data'),

    // ---- visit readiness, portal claims, break-glass, recovery, notes, grievances ---------------------------------------------
    readinessView: (visitId: string) => client.GET('/v1/staff/visits/{visit_id}/readiness', { params: { path: { visit_id: visitId } } }),
    startReadiness: (visitId: string, o?: WriteOptions) =>
      client.POST('/v1/staff/visits/{visit_id}/readiness/start', { params: { path: { visit_id: visitId }, header: { 'X-Idempotency-Key': key(o) } } }),
    replyReadiness: (visitId: string, body: ReadinessReplyIn, o?: WriteOptions) =>
      client.POST('/v1/staff/visits/{visit_id}/readiness/reply', { params: { path: { visit_id: visitId }, header: { 'X-Idempotency-Key': key(o) } }, body }),
    listPortalClaims: (clinicId: string) => client.GET('/v1/staff/portal-claims', { params: { query: { clinic_id: clinicId } } }),
    resolvePortalClaim: (requestId: string, decision: ClaimDecision, patientId: string | null, o?: WriteOptions) =>
      client.POST('/v1/staff/portal-claims/{request_id}/resolve', { params: { path: { request_id: requestId }, header: { 'X-Idempotency-Key': key(o) } }, body: { decision, patient_id: patientId } }),
    listBreakGlass: (clinicId: string, unreviewedOnly = true) =>
      client.GET('/v1/staff/break-glass', { params: { query: { clinic_id: clinicId, unreviewed_only: unreviewedOnly } } }),
    openBreakGlass: (body: BreakGlassOpenIn, o?: WriteOptions) => client.POST('/v1/staff/break-glass', { params: { header: { 'X-Idempotency-Key': key(o) } }, body }),
    // Each read is an audit row: only on a person's click, and the text is never cached.
    readBreakGlass: (grantId: string) => client.GET('/v1/staff/break-glass/{grant_id}/messages', { params: { path: { grant_id: grantId } } }),
    reviewBreakGlass: (grantId: string, outcome: BreakGlassOutcome, o?: WriteOptions) =>
      client.POST('/v1/staff/break-glass/{grant_id}/review', { params: { path: { grant_id: grantId }, header: { 'X-Idempotency-Key': key(o) } }, body: { outcome } }),
    listRecovery: (clinicId: string, openOnly = true) => client.GET('/v1/staff/recovery', { params: { query: { clinic_id: clinicId, open_only: openOnly } } }),
    requestRecovery: (body: RecoveryRequestIn, o?: WriteOptions) => client.POST('/v1/staff/recovery', { params: { header: { 'X-Idempotency-Key': key(o) } }, body }),
    approveRecovery: (requestId: string, o?: WriteOptions) =>
      client.POST('/v1/staff/recovery/{request_id}/approve', { params: { path: { request_id: requestId }, header: { 'X-Idempotency-Key': key(o) } } }),
    cancelRecovery: (requestId: string, o?: WriteOptions) =>
      client.POST('/v1/staff/recovery/{request_id}/cancel', { params: { path: { request_id: requestId }, header: { 'X-Idempotency-Key': key(o) } } }),
    completeRecovery: (requestId: string, o?: WriteOptions) =>
      client.POST('/v1/staff/recovery/{request_id}/complete', { params: { path: { request_id: requestId }, header: { 'X-Idempotency-Key': key(o) } } }),
    listNotes: (clinicId: string, patientId: string) => client.GET('/v1/staff/notes', { params: { query: { clinic_id: clinicId, patient_id: patientId } } }),
    writeNote: (body: NoteIn, o?: WriteOptions) => client.POST('/v1/staff/notes', { params: { header: { 'X-Idempotency-Key': key(o) } }, body }),
    listGrievances: (clinicId: string) => client.GET('/v1/staff/grievances', { params: { query: { clinic_id: clinicId } } }),
    setGrievanceStatus: (grievanceId: string, status: GrievanceStatus, o?: WriteOptions) =>
      client.POST('/v1/staff/grievances/{grievance_id}/status', { params: { path: { grievance_id: grievanceId }, header: { 'X-Idempotency-Key': key(o) } }, body: { status } }),

    // ---- care-gap writes (nurse and doctor) -----------------------------------------------------------------------------------
    recordBarrier: (body: BarrierIn, o?: WriteOptions) => client.POST('/v1/staff/care-gaps/barriers', { params: { header: { 'X-Idempotency-Key': key(o) } }, body }),
    resolveBarrier: (barrierId: string, o?: WriteOptions) =>
      client.POST('/v1/staff/care-gaps/barriers/{barrier_id}/resolve', { params: { path: { barrier_id: barrierId }, header: { 'X-Idempotency-Key': key(o) } } }),
    startEpisode: (body: EpisodeIn, o?: WriteOptions) => client.POST('/v1/staff/care-gaps/episodes', { params: { header: { 'X-Idempotency-Key': key(o) } }, body }),
    markRuleDone: (ruleId: string, doneOn: string, o?: WriteOptions) =>
      client.POST('/v1/staff/care-gaps/episode-rules/{episode_rule_id}/done', { params: { path: { episode_rule_id: ruleId }, header: { 'X-Idempotency-Key': key(o) } }, body: { done_on: doneOn } }),
    recheckSample: (sampleId: string, o?: WriteOptions) =>
      client.POST('/v1/staff/care-gaps/samples/{sample_id}/recheck', { params: { path: { sample_id: sampleId }, header: { 'X-Idempotency-Key': key(o) } } }),

    // ---- admin ------------------------------------------------------------------------------------------------------------------
    listAccounts: (clinicId: string) => client.GET('/v1/staff/accounts', { params: { query: { clinic_id: clinicId } } }),
    inviteAccount: (body: InviteIn, o?: WriteOptions) => client.POST('/v1/staff/accounts/invite', { params: { header: { 'X-Idempotency-Key': key(o) } }, body }),
    suspendAccount: (accountId: string, o?: WriteOptions) =>
      client.POST('/v1/staff/accounts/{account_id}/suspend', { params: { path: { account_id: accountId }, header: { 'X-Idempotency-Key': key(o) } } }),
    reinstateAccount: (accountId: string, o?: WriteOptions) =>
      client.POST('/v1/staff/accounts/{account_id}/reinstate', { params: { path: { account_id: accountId }, header: { 'X-Idempotency-Key': key(o) } } }),
    grantRole: (accountId: string, body: RoleChangeIn, o?: WriteOptions) =>
      client.POST('/v1/staff/accounts/{account_id}/roles', { params: { path: { account_id: accountId }, header: { 'X-Idempotency-Key': key(o) } }, body }),
    revokeRole: (accountId: string, body: RoleChangeIn, o?: WriteOptions) =>
      client.POST('/v1/staff/accounts/{account_id}/roles/revoke', { params: { path: { account_id: accountId }, header: { 'X-Idempotency-Key': key(o) } }, body }),
    getAutonomy: (clinicId: string) => client.GET('/v1/staff/console/autonomy', { params: { query: { clinic_id: clinicId } } }),
    setAutonomy: (body: AutonomySetIn, o?: WriteOptions) => client.POST('/v1/staff/console/autonomy', { params: { header: { 'X-Idempotency-Key': key(o) } }, body }),
    autonomyHistory: (clinicId: string) => client.GET('/v1/staff/console/autonomy/history', { params: { query: { clinic_id: clinicId } } }),
    listAutonomyProposals: (clinicId: string) => client.GET('/v1/staff/console/autonomy/proposals', { params: { query: { clinic_id: clinicId } } }),
    proposeAutonomy: (clinicId: string, proposerRole: 'admin' | 'doctor' | null, o?: WriteOptions) =>
      client.POST('/v1/staff/console/autonomy/proposals', { params: { header: { 'X-Idempotency-Key': key(o) } }, body: { clinic_id: clinicId, proposer_role: proposerRole } }),
    resolveAutonomyProposal: (proposalId: string, decision: AutonomyDecision, o?: WriteOptions) =>
      client.POST('/v1/staff/console/autonomy/proposals/{proposal_id}/resolve', { params: { path: { proposal_id: proposalId }, header: { 'X-Idempotency-Key': key(o) } }, body: { decision } }),
    reviewerStats: (clinicId: string, days?: number) => client.GET('/v1/staff/console/reviewer-stats', { params: { query: { clinic_id: clinicId, ...(days ? { days } : {}) } } }),
    costCanvas: (clinicId: string, days?: number) => client.GET('/v1/staff/console/cost-canvas', { params: { query: { clinic_id: clinicId, ...(days ? { days } : {}) } } }),
    listVoiceSessions: (clinicId: string) => client.GET('/v1/staff/voice/sessions', { params: { query: { clinic_id: clinicId } } }),
    // Reads a transcript AND the database audits the read: only on a person's click, and the text is never cached.
    readTranscript: (sessionId: string, clinicId: string) =>
      client.GET('/v1/staff/voice/sessions/{session_id}/transcript', { params: { path: { session_id: sessionId }, query: { clinic_id: clinicId } } }),
    // `limit` is optional on the wire: the schema's own default (500, at most 1000) applies when it is left out.
    purgeVoice: (body: { clinic_id: string; limit?: number }, o?: WriteOptions) => client.POST('/v1/staff/voice/purge', { params: { header: { 'X-Idempotency-Key': key(o) } }, body: body as VoicePurgeIn }),

    // ---- review console -----------------------------------------------------------------------------------------------
    reviewView: (documentId: string) => client.GET('/v1/staff/referrals/{document_id}/review-view', { params: { path: { document_id: documentId } } }),
    reasonCodes: () => client.GET('/v1/staff/console/reason-codes'),
  };
}

export type Api = ReturnType<typeof createApi>;
