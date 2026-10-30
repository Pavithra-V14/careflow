// The ONLY environment names the browser bundle may see. Anything else in .env.local or the
// process environment (DATABASE_URL, a Supabase secret key, ...) never reaches the bundle.
// Only the publishable key belongs here; the secret key is never a frontend value.
export const EXPOSED_ENV_NAMES = ['SUPABASE_URL', 'SUPABASE_PUBLISHABLE_KEY', 'CAREFLOW_API_BASE_URL'] as const;

export type ExposedEnvName = (typeof EXPOSED_ENV_NAMES)[number];
export type ExposedEnv = Record<ExposedEnvName, string>;

export const CONFIG_GLOBAL = '__CAREFLOW_PUBLIC_CONFIG__';

export function pickExposedEnv(env: Record<string, string | undefined>): ExposedEnv {
  const out = {} as ExposedEnv;
  for (const name of EXPOSED_ENV_NAMES) out[name] = env[name] ?? '';
  return out;
}

/**
 * Stops the build if an exposed value looks like a secret key pasted into the wrong slot (for example a Supabase
 * secret key in SUPABASE_PUBLISHABLE_KEY). The message names the variable, never the value.
 */
export function assertNoSecretShapedValues(exposed: ExposedEnv): void {
  for (const name of EXPOSED_ENV_NAMES) {
    if (/sb_secret_|service_role|SERVICE_ROLE/.test(exposed[name])) {
      throw new Error(`${name} looks like a secret key. Only the publishable key belongs in the frontend; the build was stopped.`);
    }
  }
}
