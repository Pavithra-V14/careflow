import { useEffect, useMemo, useState } from 'react';
import { createApi, type PublicClinicProfile } from '../shared/api/client';

/**
 * What the signed-out home page reads about the hospital, with no sign-in: the name (GET /public/clinic) and the public profile
 * (GET /public/clinic/profile). The page is never worse off for asking: any failure leaves it exactly as it was, with its placeholders and
 * "Coming soon" labels, AND says so in fixed words (never silently, and never the backend's text). An unknown is never read as a value: a
 * feature the profile does not say is on is NOT shown as available.
 *
 * An empty API base means the page's own origin (the development build calls the API through its proxy, and a host may serve both under one
 * origin): it is NOT "not configured". (Found live: treating it so left the page on placeholders although the profile was published.)
 */
type ClinicInfoStatus = 'loading' | 'ok' | 'unpublished' | 'failed';

export interface ClinicInfo {
  /** The hospital's name, or null when it is missing (the placeholder name is then kept). */
  name: string | null;
  /** The public profile, or null when it could not be read (the static page is shown). */
  profile: PublicClinicProfile | null;
  /** loading: not answered yet. ok: the profile was read. unpublished: 404, no clinic is published. failed: anything else (network, 429, 503, 5xx). */
  status: ClinicInfoStatus;
}

const NONE: ClinicInfo = { name: null, profile: null, status: 'loading' };

export function useClinicInfo(apiBaseUrl: string, enabled: boolean): ClinicInfo {
  const [info, setInfo] = useState<ClinicInfo>(NONE);
  const base = apiBaseUrl || (typeof window !== 'undefined' ? window.location.origin : '');
  const api = useMemo(() => (base ? createApi({ baseUrl: base, getAccessToken: () => null }) : null), [base]);
  useEffect(() => {
    if (!enabled || !api) return;
    let live = true;
    void (async () => {
      const [clinic, profile] = await Promise.all([
        api.publicClinic().then((r) => (r.data && typeof r.data.name === 'string' && r.data.name.trim() !== '' ? r.data.name.trim() : null)).catch(() => null),
        api
          .publicClinicProfile()
          .then((r): Pick<ClinicInfo, 'profile' | 'status'> => (r.data ? { profile: r.data, status: 'ok' } : { profile: null, status: r.response.status === 404 ? 'unpublished' : 'failed' }))
          .catch((): Pick<ClinicInfo, 'profile' | 'status'> => ({ profile: null, status: 'failed' })),
      ]);
      if (live) setInfo({ name: clinic, profile: profile.profile, status: profile.status });
    })();
    return () => {
      live = false;
    };
  }, [api, enabled]);
  return info;
}
