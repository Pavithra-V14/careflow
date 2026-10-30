import { createClient } from '@supabase/supabase-js';
import type { Aal, AuthPort, TotpEnrolment } from './port';

const asAal = (v: unknown): Aal | null => (v === 'aal1' || v === 'aal2' ? v : null);

/**
 * supabase-js with its DEFAULT session handling, stated explicitly so nobody changes it by accident:
 * the session (access token AND refresh token) is kept in the browser's localStorage under
 * `sb-<project>-auth-token`, refreshed automatically before it expires, and shared between tabs.
 * No custom storage is used. detectSessionInUrl is off because no flow here returns a token in the URL.
 */
export function createSupabasePort(url: string, publishableKey: string): AuthPort {
  const supabase = createClient(url, publishableKey, {
    auth: { persistSession: true, autoRefreshToken: true, detectSessionInUrl: false },
  });
  const auth = supabase.auth;

  return {
    async getAccessToken() {
      try {
        const { data } = await auth.getSession();
        return data.session?.access_token ?? null;
      } catch {
        return null; // blocked or corrupt storage: behave as signed out
      }
    },

    async signInWithPassword(email, password) {
      try {
        const { data, error } = await auth.signInWithPassword({ email, password });
        return !error && data.session !== null;
      } catch {
        return false;
      }
    },

    async getAssurance() {
      try {
        const { data, error } = await auth.mfa.getAuthenticatorAssuranceLevel();
        if (error || !data) return { current: null, next: null };
        return { current: asAal(data.currentLevel), next: asAal(data.nextLevel) };
      } catch {
        return { current: null, next: null };
      }
    },

    async listVerifiedTotpFactorIds() {
      try {
        const { data, error } = await auth.mfa.listFactors();
        if (error || !data) return [];
        return data.all.filter((f) => f.factor_type === 'totp' && f.status === 'verified').map((f) => f.id);
      } catch {
        return [];
      }
    },

    async enrollTotp(): Promise<TotpEnrolment | null> {
      try {
        // An abandoned (never verified) factor would block a new enrolment; remove those first.
        const listed = await auth.mfa.listFactors();
        for (const f of listed.data?.all ?? []) {
          if (f.factor_type === 'totp' && f.status === 'unverified') await auth.mfa.unenroll({ factorId: f.id });
        }
        const { data, error } = await auth.mfa.enroll({ factorType: 'totp' });
        if (error || !data || data.type !== 'totp') return null;
        return { factorId: data.id, qrCodeSrc: data.totp.qr_code, secret: data.totp.secret };
      } catch {
        return null;
      }
    },

    async abandonEnrolment(factorId) {
      try {
        await auth.mfa.unenroll({ factorId });
      } catch {
        /* nothing useful to do; the next enrolment clears unverified factors again */
      }
    },

    async verifyTotp(factorId, code) {
      try {
        const { error } = await auth.mfa.challengeAndVerify({ factorId, code });
        return !error;
      } catch {
        return false;
      }
    },

    async refreshSession() {
      try {
        const { data, error } = await auth.refreshSession();
        return !error && data.session !== null;
      } catch {
        return false;
      }
    },

    async signOut() {
      try {
        await auth.signOut({ scope: 'local' });
      } catch {
        /* the local session is cleared by supabase-js even if the server call fails */
      }
    },

    onSignedOut(listener) {
      // Keep the callback synchronous: awaiting a supabase-js call inside it can deadlock the auth lock.
      const { data } = auth.onAuthStateChange((event) => {
        if (event === 'SIGNED_OUT') listener();
      });
      return () => data.subscription.unsubscribe();
    },
  };
}
