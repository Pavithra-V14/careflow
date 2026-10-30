import { useRef } from 'react';
import { useMutation, useQuery, type UseMutationResult } from '@tanstack/react-query';
import { ApiProblem, isDefinitive, toProblem } from './apiState';
import { useSession } from './session/SessionContext';

/** A query that is scoped to the active role and clinic, so switching role never shows the previous role's data. */
export function useApiQuery<T>(key: readonly unknown[], fn: () => Promise<T>, enabled = true) {
  const { scopeKey } = useSession();
  return useQuery<T, ApiProblem>({ queryKey: [scopeKey, ...key], queryFn: fn, enabled, retry: false, staleTime: 0 });
}

export type IdempotentWrite<V, R> = Omit<UseMutationResult<R, ApiProblem, V>, 'reset'> & { reset: () => void };

/**
 * A write that keeps ONE idempotency key per intent. The key is made on the first attempt and reused for every retry
 * after a failure that may or may not have reached the server (network, 429, 5xx), so the server replays instead of
 * repeating. It is dropped on success and on an answer that will not change (403, 404, 409, 422): the next attempt is a
 * new intent. Call `reset()` when the user changes what they are asking for, so a different request never reuses a key.
 */
export function useIdempotentWrite<V, R>(run: (vars: V, key: string) => Promise<R>, onSuccess?: (result: R) => void): IdempotentWrite<V, R> {
  const key = useRef<string | null>(null);
  const mutation = useMutation<R, ApiProblem, V>({
    retry: false,
    mutationFn: async (vars) => {
      key.current ??= globalThis.crypto.randomUUID();
      try {
        const result = await run(vars, key.current);
        key.current = null;
        return result;
      } catch (e) {
        const problem = toProblem(e);
        if (isDefinitive(problem.kind)) key.current = null;
        throw problem;
      }
    },
    ...(onSuccess ? { onSuccess } : {}),
  });
  const { reset: resetMutation } = mutation;
  return {
    ...mutation,
    reset: () => {
      key.current = null;
      resetMutation();
    },
  };
}
