import { createContext, useContext, type ReactNode } from 'react';
import type { Account, Grant } from '../me';
import { grantKey } from '../me';

export interface Session {
  account: Account;
  /** The role and clinic the person chose for this session (several grants force an explicit choice). */
  active: Grant;
  /** Part of every query key, so changing role or clinic never shows the previous role's data. */
  scopeKey: string;
  hasRole: (roles: readonly string[]) => boolean;
  changeRole: (() => void) | null;
  signOut: () => void;
}

const SessionContext = createContext<Session | null>(null);

export function SessionProvider({ account, active, changeRole, signOut, children }: { account: Account; active: Grant; changeRole: (() => void) | null; signOut: () => void; children: ReactNode }) {
  const value: Session = {
    account,
    active,
    scopeKey: `scope:${grantKey(active)}`,
    // Convenience only: hides actions this role cannot use. The backend decides.
    hasRole: (roles) => roles.includes(active.role),
    changeRole,
    signOut,
  };
  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export function useSession(): Session {
  const ctx = useContext(SessionContext);
  if (!ctx) throw new Error('useSession must be used inside SessionProvider');
  return ctx;
}
