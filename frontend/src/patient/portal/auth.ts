import { createClient } from '@supabase/supabase-js';

/**
 * Patient sign-in with a one-time code sent to the patient's mobile number (Supabase Auth, phone OTP: BUILD-SPEC section 15).
 * Signing in for the first time creates the sign-in account; the portal account is then created by an explicit step (register).
 *
 * supabase-js with its DEFAULT session handling, stated explicitly: the session is kept in the browser's localStorage under
 * `sb-<project>-auth-token` and refreshed automatically. Nothing else about the patient is kept in the browser: no phone number, name,
 * code, consent text or data. Rate limits, lockouts and CAPTCHA on repeated failures are Supabase settings, not this code's.
 */
export interface PortalAuth {
  /** Asks for a code to be sent. False on any failure, with no reason: a reason would say whether a number is known. */
  requestCode(phone: string): Promise<boolean>;
  /** True only if the code opened a session. */
  verifyCode(phone: string, code: string): Promise<boolean>;
  getAccessToken(): Promise<string | null>;
  signOut(): Promise<void>;
  /** Called when the session ends elsewhere (another tab, a failed refresh). */
  onSignedOut(listener: () => void): () => void;
}

export function createPortalAuth(url: string, publishableKey: string): PortalAuth {
  const supabase = createClient(url, publishableKey, {
    auth: { persistSession: true, autoRefreshToken: true, detectSessionInUrl: false },
  });
  const auth = supabase.auth;
  return {
    async requestCode(phone) {
      try {
        const { error } = await auth.signInWithOtp({ phone });
        return !error;
      } catch {
        return false;
      }
    },
    async verifyCode(phone, code) {
      try {
        const { data, error } = await auth.verifyOtp({ phone, token: code, type: 'sms' });
        return !error && data.session !== null;
      } catch {
        return false;
      }
    },
    async getAccessToken() {
      try {
        const { data } = await auth.getSession();
        return data.session?.access_token ?? null;
      } catch {
        return null; // blocked or corrupt storage: behave as signed out
      }
    },
    async signOut() {
      try {
        await auth.signOut();
      } catch {
        /* the local session is dropped either way */
      }
    },
    onSignedOut(listener) {
      const { data } = auth.onAuthStateChange((event) => {
        if (event === 'SIGNED_OUT') listener();
      });
      return () => data.subscription.unsubscribe();
    },
  };
}

/** A mobile number as the sign-in service wants it: a plus sign and digits only. Anything else is not sent. */
export function normalisePhone(raw: string): string | null {
  const compact = raw.replace(/[\s()-]/g, '');
  return /^\+[1-9][0-9]{7,14}$/.test(compact) ? compact : null;
}
