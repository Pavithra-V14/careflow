import type { ProblemKind } from '../apiState';
import { unwrap } from '../apiState';
import { useAuth } from '../auth/AuthProvider';
import { useApiQuery } from '../hooks';

export type ErasureReasons =
  | { state: 'loading' }
  | { state: 'not_set_up' }
  | { state: 'failed'; kind: ProblemKind }
  | { state: 'ready'; reasons: readonly string[] };

/**
 * The reasons the clinic allows an erasure for (GET /v1/staff/erasure-requests/reasons): the clinic's closed list, a required setting. The
 * console keeps no list of its own. 503 means the setting is not made yet, which is shown as the calm "not set up" state; an empty list is
 * the same. Read only when `enabled` (the ask form is shown), so a screen that cannot ask does not call it.
 */
export function useErasureReasons(enabled: boolean): ErasureReasons {
  const { api } = useAuth();
  const query = useApiQuery(['erasure-reasons'], () => unwrap(api.erasureReasons()), enabled);
  if (!enabled || query.isPending) return { state: 'loading' };
  if (query.isError) return query.error.kind === 'unavailable' ? { state: 'not_set_up' } : { state: 'failed', kind: query.error.kind };
  return query.data.reasons.length === 0 ? { state: 'not_set_up' } : { state: 'ready', reasons: query.data.reasons };
}
