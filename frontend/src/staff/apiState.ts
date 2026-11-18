// Turns an API result into data or ONE of a small set of problem kinds. The UI shows a fixed message per kind
// (src/shared/i18n: problem.*), never the backend's body: a body can name a record, a rule or a reason.
import { isStepUpChallenge, OfflineWriteError } from '../shared/api/client';

export type ProblemKind = 'unauthorized' | 'step_up' | 'forbidden' | 'not_found' | 'conflict' | 'invalid' | 'rate_limited' | 'unavailable' | 'network' | 'offline' | 'unknown';

export class ApiProblem extends Error {
  readonly kind: ProblemKind;
  /**
   * The backend's `detail` phrase, kept ONLY so the console can recognise a phrase it knows (for example "unknown patient for this clinic")
   * and say it in its own plain words. It is never rendered as it came: an unknown phrase is shown as the fixed message for the kind.
   */
  readonly detail: string | null;
  /** The HTTP status when there was an answer (null for a network problem). Only used to tell 503, a switch that is off, from 502 and 504, a failing gateway. */
  readonly status: number | null;
  constructor(kind: ProblemKind, detail: string | null = null, status: number | null = null) {
    super(kind); // the kind word only; never a status line or a body
    this.kind = kind;
    this.detail = detail;
    this.status = status;
  }
}

function problemFromStatus(status: number): ProblemKind {
  if (status === 401) return 'unauthorized';
  if (status === 403) return 'forbidden';
  if (status === 404 || status === 410) return 'not_found'; // 410: the resource is gone (a message whose body was erased)
  if (status === 409) return 'conflict';
  if (status === 422) return 'invalid';
  if (status === 429) return 'rate_limited';
  if (status === 502 || status === 503 || status === 504) return 'unavailable';
  return 'unknown';
}

export const toProblem = (e: unknown): ApiProblem => (e instanceof ApiProblem ? e : new ApiProblem('unknown'));

interface Result<T> {
  data?: T | undefined;
  error?: unknown;
  response: Response;
}

/** The data of a successful result, or an ApiProblem. A rejected fetch (offline, blocked) is a 'network' problem. */
export async function unwrap<T>(pending: Promise<Result<T>>): Promise<T> {
  let result: Result<T>;
  try {
    result = await pending;
  } catch (e) {
    // A write that was held back because the browser is offline: nothing was sent, so the message can say nothing was saved.
    throw new ApiProblem(e instanceof OfflineWriteError ? 'offline' : 'network');
  }
  if (result.response.ok && result.data !== undefined) return result.data;
  // A step-up challenge that was cancelled or not satisfied is not a sign-out: nothing was changed and a fresh code is needed.
  if (isStepUpChallenge(result.response)) throw new ApiProblem('step_up');
  const detail = (result.error as { detail?: unknown } | undefined)?.detail;
  throw new ApiProblem(result.response.ok ? 'unknown' : problemFromStatus(result.response.status), typeof detail === 'string' ? detail : null, result.response.status);
}

/** Answers that will not change on a retry: the idempotency key is dropped, so the next attempt is a new intent. */
export const isDefinitive = (kind: ProblemKind): boolean => kind === 'forbidden' || kind === 'not_found' || kind === 'conflict' || kind === 'invalid';
