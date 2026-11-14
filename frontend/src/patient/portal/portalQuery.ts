import { useQuery } from '@tanstack/react-query';
import type { ApiProblem } from '../../staff/apiState';

/** A read in the patient portal. The error type is the fixed problem kind, never the backend's text. */
export function usePortalQuery<T>(key: readonly unknown[], fn: () => Promise<T>) {
  return useQuery<T, ApiProblem>({ queryKey: key, queryFn: fn });
}

/** The purpose word of the notice the API supplies for a voice session (assumed; backend request 31). The frontend holds no notice text. */
export const VOICE_NOTICE_PURPOSE = 'voice_session';
