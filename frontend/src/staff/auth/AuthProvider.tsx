import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { createApi, type Api } from '../../shared/api/client';
import { StepUpDialog } from '../components/StepUpDialog';
import { SEND_ACTIVE_ROLE_HEADER } from '../featureFlags';
import type { AuthPort, TotpEnrolment } from './port';

// Where the staff user is in sign-in. The server decides the second factor is required (the verified
// factor list and the session's aal), never this code: 'ready' means the session is aal2.
export type Phase = 'loading' | 'signed-out' | 'mfa-enrol' | 'mfa-challenge' | 'ready';

interface AuthContextValue {
  phase: Phase;
  /** True after a session ended or was refused, so the sign-in screen can say so without saying why. */
  sessionEnded: boolean;
  api: Api;
  signIn(email: string, password: string): Promise<boolean>;
  startEnrolment(): Promise<TotpEnrolment | null>;
  /** Completes enrolment (verifying the first code) or answers the sign-in challenge. */
  verifyCode(factorId: string | null, code: string): Promise<boolean>;
  signOut(): Promise<void>;
  port: AuthPort;
  /** Tells the API client which grant is active. Sent as a header only if SEND_ACTIVE_ROLE_HEADER is on (it is off). */
  setActiveRole: (value: string | null) => void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used inside AuthProvider');
  return ctx;
}

interface StepUpState {
  factorId: string;
  resolve: (ok: boolean) => void;
}

function createStepUpGate(port: AuthPort, show: (state: StepUpState) => void): () => Promise<boolean> {
  let pending: Promise<boolean> | null = null;
  return () => {
    if (pending) return pending;
    pending = (async () => {
      const [first] = await port.listVerifiedTotpFactorIds();
      if (!first) return false;
      return new Promise<boolean>((resolve) => show({ factorId: first, resolve }));
    })().finally(() => {
      pending = null;
    });
    return pending;
  };
}

export function AuthProvider({ port, apiBaseUrl, children }: { port: AuthPort; apiBaseUrl: string; children: ReactNode }) {
  const queryClient = useQueryClient();
  const [phase, setPhase] = useState<Phase>('loading');
  const [factorId, setFactorId] = useState<string | null>(null);
  const [sessionEnded, setSessionEnded] = useState(false);
  const [stepUp, setStepUp] = useState<StepUpState | null>(null);

  // Decide the phase from the session's assurance level and the verified factors.
  const settle = useCallback(async () => {
    const { current, next } = await port.getAssurance();
    if (current === null) return setPhase('signed-out');
    if (current === 'aal2') return setPhase('ready');
    if (next === 'aal2') {
      const [first] = await port.listVerifiedTotpFactorIds();
      if (first) {
        setFactorId(first);
        return setPhase('mfa-challenge');
      }
    }
    return setPhase('mfa-enrol');
  }, [port]);

  const endSession = useCallback(
    (notify: boolean) => {
      queryClient.clear();
      setFactorId(null);
      setSessionEnded(notify);
      setPhase('signed-out');
    },
    [queryClient],
  );

  useEffect(() => {
    // Reads the session from an external system (the auth provider) once on mount; setState runs after an await.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void settle();
    return port.onSignedOut(() => endSession(true));
  }, [port, settle, endSession]);

  // One prompt at a time: concurrent step-up 401s share the same dialog and the same answer.
  const requestStepUp = useMemo(() => createStepUpGate(port, setStepUp), [port]);

  // A tiny mutable cell (not React state): the API client reads it at request time. Created once.
  const [activeRole] = useState(() => {
    let current: string | null = null;
    return { get: () => current, set: (v: string | null) => (current = v) };
  });

  const api = useMemo(
    () =>
      createApi({
        activeRole: { enabled: SEND_ACTIVE_ROLE_HEADER, get: activeRole.get },
        baseUrl: apiBaseUrl,
        getAccessToken: () => port.getAccessToken(),
        onStepUp: requestStepUp,
        // Any other 401: drop the local session and go to sign-in. No reason is ever shown.
        onUnauthorized: () => {
          void port.signOut().then(() => endSession(true));
        },
      }),
    [apiBaseUrl, port, requestStepUp, endSession, activeRole],
  );

  const value = useMemo<AuthContextValue>(
    () => ({
      phase,
      sessionEnded,
      api,
      port,
      setActiveRole: (v) => void activeRole.set(v),
      async signIn(email, password) {
        const ok = await port.signInWithPassword(email, password);
        if (!ok) return false;
        setSessionEnded(false);
        await settle();
        return true;
      },
      startEnrolment: () => port.enrollTotp(),
      async verifyCode(id, code) {
        const target = id ?? factorId;
        if (!target) return false;
        const ok = await port.verifyTotp(target, code);
        if (ok) await settle();
        return ok;
      },
      async signOut() {
        await port.signOut();
        endSession(false);
      },
    }),
    [phase, sessionEnded, api, port, factorId, settle, endSession, activeRole],
  );

  return (
    <AuthContext.Provider value={value}>
      {children}
      {stepUp && (
        <StepUpDialog
          onSubmit={(code) => port.verifyTotp(stepUp.factorId, code)}
          onDone={(ok) => {
            stepUp.resolve(ok);
            setStepUp(null);
          }}
        />
      )}
    </AuthContext.Provider>
  );
}
