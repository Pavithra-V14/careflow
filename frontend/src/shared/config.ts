import type { ExposedEnv } from '../../env-allowlist';

declare const __CAREFLOW_PUBLIC_CONFIG__: ExposedEnv;

export interface PublicConfig {
  supabaseUrl: string;
  supabasePublishableKey: string;
  /** Empty in development: calls go same-origin through the Vite proxy. */
  apiBaseUrl: string;
  /** False when a required value is missing; screens show an explicit "not configured" state. */
  configured: boolean;
}

export function readConfig(raw: ExposedEnv = __CAREFLOW_PUBLIC_CONFIG__, dev: boolean = import.meta.env.DEV): PublicConfig {
  const supabaseUrl = raw.SUPABASE_URL;
  const supabasePublishableKey = raw.SUPABASE_PUBLISHABLE_KEY;
  const apiBaseUrl = dev ? '' : raw.CAREFLOW_API_BASE_URL;
  return {
    supabaseUrl,
    supabasePublishableKey,
    apiBaseUrl,
    configured: Boolean(supabaseUrl && supabasePublishableKey && (dev || raw.CAREFLOW_API_BASE_URL)),
  };
}
