// Turns an API result into data or ONE of a small set of problem kinds. The UI shows a fixed message per kind
// (src/shared/i18n: problem.*), never the backend's body: a body can name a record, a rule or a reason.

export type ProblemKind = 'unauthorized' | 'forbidden' | 'not_found' | 'conflict' | 'invalid' | 'rate_limited' | 'unavailable' | 'network' | 'unknown';

export class ApiProblem extends Error {
  readonly kind: ProblemKind;
  constructor(kind: ProblemKind) {
    super(kind); // the kind word only; never a status line or a body
    this.kind = kind;
  }
}

export function problemFromStatus(status: number): ProblemKind {
  if (status === 401) return 'unauthorized';
  if (status === 403) return 'forbidden';
  if (status === 404) return 'not_found';
  if (status === 409) return 'conflict';
  if (status === 422) return 'invalid';
  if (status === 429) return 'rate_limited';
  if (status === 502 || status === 503 || status === 504) return 'unavailable';
  return 'unknown';
}

export const toProblem = (e: unknown): ApiProblem => (e instanceof ApiProblem ? e : new ApiProblem('unknown'));

interface Result<T> {
  data?: T | undefined;
  response: Response;
}

/** The data of a successful result, or an ApiProblem. A rejected fetch (offline, blocked) is a 'network' problem. */
export async function unwrap<T>(pending: Promise<Result<T>>): Promise<T> {
  let result: Result<T>;
  try {
    result = await pending;
  } catch {
    throw new ApiProblem('network');
  }
  if (result.response.ok && result.data !== undefined) return result.data;
  throw new ApiProblem(result.response.ok ? 'unknown' : problemFromStatus(result.response.status));
}

/** Answers that will not change on a retry: the idempotency key is dropped, so the next attempt is a new intent. */
export const isDefinitive = (kind: ProblemKind): boolean => kind === 'forbidden' || kind === 'not_found' || kind === 'conflict' || kind === 'invalid';
