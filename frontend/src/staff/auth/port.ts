// The only surface of the auth provider the staff app uses. The real implementation wraps supabase-js
// (supabasePort.ts); tests use an in-memory fake. Failures are booleans or nulls on purpose: the UI must
// never show a provider's error text, because it can reveal whether an account exists.
export type Aal = 'aal1' | 'aal2';

export interface TotpEnrolment {
  factorId: string;
  /** A data:image/svg+xml URI, shown in an <img> (the CSP allows data: images for exactly this). */
  qrCodeSrc: string;
  /** The same secret as text, for manual entry. Shown once, never stored or logged. */
  secret: string;
}

export interface AuthPort {
  getAccessToken(): Promise<string | null>;
  signInWithPassword(email: string, password: string): Promise<boolean>;
  /** current: the level of the session now; next: the level it can reach (aal2 once a factor is verified). */
  getAssurance(): Promise<{ current: Aal | null; next: Aal | null }>;
  listVerifiedTotpFactorIds(): Promise<string[]>;
  /** Starts enrolment (removing any abandoned, unverified factor first). Null on failure. */
  enrollTotp(): Promise<TotpEnrolment | null>;
  abandonEnrolment(factorId: string): Promise<void>;
  /** Challenge and verify in one step. A success also replaces the session with a fresh aal2 one. */
  verifyTotp(factorId: string, code: string): Promise<boolean>;
  refreshSession(): Promise<boolean>;
  /** Ends this browser's session only (not the user's other devices). */
  signOut(): Promise<void>;
  /** Fires when the session ends for any reason (sign-out here or in another tab, failed refresh). */
  onSignedOut(listener: () => void): () => void;
}
