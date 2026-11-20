import { useEffect, useMemo, useState } from 'react';
import { createApi, type PublicClinicProfile } from '../shared/api/client';

/**
 * What the signed-out home page reads about the hospital, with no sign-in: the name (GET /public/clinic) and the public profile
 * (GET /public/clinic/profile). Both are optional: any failure (no clinic published, not configured, rate limited, offline) leaves
 * the page exactly as it was, with its placeholders and "Coming soon" labels. An unknown is never read as a value: a feature the
 * profile does not say is on is NOT shown as available.
 */
export interface ClinicInfo {
  /** The hospital's name, or null when it is missing (the placeholder name is then kept). */
  name: string | null;
  /** The public profile, or null when it could not be read (the static page is shown). */
  profile: PublicClinicProfile | null;
  /** False until both reads have answered (or failed). */
  settled: boolean;
}

const NONE: ClinicInfo = { name: null, profile: null, settled: false };

export function useClinicInfo(apiBaseUrl: string, enabled: boolean): ClinicInfo {
  const [info, setInfo] = useState<ClinicInfo>(NONE);
  const api = useMemo(() => (apiBaseUrl ? createApi({ baseUrl: apiBaseUrl, getAccessToken: () => null }) : null), [apiBaseUrl]);
  useEffect(() => {
    if (!enabled || !api) return;
    let live = true;
    void (async () => {
      const [clinic, profile] = await Promise.all([
        api.publicClinic().then((r) => (r.data && typeof r.data.name === 'string' && r.data.name.trim() !== '' ? r.data.name.trim() : null)).catch(() => null),
        api.publicClinicProfile().then((r) => r.data ?? null).catch(() => null),
      ]);
      if (live) setInfo({ name: clinic, profile, settled: true });
    })();
    return () => {
      live = false;
    };
  }, [api, enabled]);
  return info;
}
