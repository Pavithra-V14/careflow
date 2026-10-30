import createClient from 'openapi-fetch';
import type { components, paths } from './schema';

export type Me = components['schemas']['Me'];
export type ProposeMergeIn = components['schemas']['ProposeMergeIn'];
export type BeginUploadIn = components['schemas']['BeginUploadIn'];
export type RegisterIn = components['schemas']['RegisterIn'];
export type PatientSearchIn = components['schemas']['PatientSearchIn'];
type TaskQuery = NonNullable<paths['/v1/staff/review-tasks']['get']['parameters']['query']>;
export type ReviewTaskStatus = TaskQuery['status'];
export type TaskRole = NonNullable<TaskQuery['assigned_role']>;
export type HoldStatus = NonNullable<paths['/v1/staff/scheduling/holds']['get']['parameters']['query']>['status'];

/** The request header that names the active role. See docs/design/active-role.md (a proposal: the backend does not read it yet). */
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
} as const satisfies Record<string, { method: 'get' | 'post'; path: keyof paths }>;

export interface ApiOptions {
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
}

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
      await authorize(request);
      const first = await transport(request);
      if (first.status !== 401) return first;

      if (isStepUpChallenge(first) && options.onStepUp && retry) {
        if (!(await options.onStepUp())) return first;
        await authorize(retry); // the token minted by the fresh verification
        const second = await transport(retry);
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
  };
}

export type Api = ReturnType<typeof createApi>;
