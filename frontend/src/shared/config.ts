import type { ExposedEnv } from '../../env-allowlist';

declare const __CAREFLOW_PUBLIC_CONFIG__: ExposedEnv;

interface PublicConfig {
  supabaseUrl: string;
  supabasePublishableKey: string;
  /** Empty in development: calls go same-origin through the Vite proxy. */
  apiBaseUrl: string;
  /** False when a required value is missing; screens show an explicit "not configured" state. */
  configured: boolean;
  /** Idle sign-out minutes per audience; null (off) unless a positive number was set at build time. There is no default. */
  idleMinutes: { staff: number | null; patient: number | null };
}

function minutes(value: string): number | null {
  const n = Number(value.trim());
  return value.trim() !== '' && Number.isFinite(n) && n > 0 ? n : null;
}

export function readConfig(raw: ExposedEnv = __CAREFLOW_PUBLIC_CONFIG__, dev: boolean = import.meta.env.DEV): PublicConfig {
  const supabaseUrl = raw.SUPABASE_URL;
  const supabasePublishableKey = raw.SUPABASE_PUBLISHABLE_KEY;
  const apiBaseUrl = dev ? '' : raw.CAREFLOW_API_BASE_URL;
  return {
    supabaseUrl,
    supabasePublishableKey,
    apiBaseUrl,
    idleMinutes: { staff: minutes(raw.CAREFLOW_IDLE_MINUTES_STAFF), patient: minutes(raw.CAREFLOW_IDLE_MINUTES_PATIENT) },
    configured: Boolean(supabaseUrl && supabasePublishableKey && (dev || raw.CAREFLOW_API_BASE_URL)),
  };
}
