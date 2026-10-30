import type { Me } from '../shared/api/client';

export type ClinicOrigin = 'live' | 'test';

export interface Grant {
  clinicId: string;
  role: string;
  /** Present only once the backend sends clinic_name (docs/frontend-backend-requests.md, item 2). */
  clinicName?: string;
  /** Present only once the backend sends clinic_origin. Absent means UNKNOWN: it is never defaulted. */
  clinicOrigin?: ClinicOrigin;
}

export interface Account {
  accountId: string;
  grants: Grant[];
  /** The operating profile, once the backend exposes it. Absent means unknown. */
  operatingProfile?: 'demo' | 'clinical';
}

const text = (v: unknown): string | undefined => (typeof v === 'string' && v.trim() !== '' ? v : undefined);

/**
 * Reads GET /v1/iam/me defensively. The generated type has only clinic_id and role per grant; the extra
 * fields are requested from the backend and are accepted only when they have exactly an expected value.
 * Anything else stays absent, so "unknown" can never be mistaken for LIVE or TEST.
 */
export function parseMe(me: Me): Account {
  const raw = me as unknown as { operating_profile?: unknown };
  const grants: Grant[] = me.roles.map((g) => {
    const extra = g as unknown as { clinic_name?: unknown; clinic_origin?: unknown };
    const grant: Grant = { clinicId: g.clinic_id, role: g.role };
    const name = text(extra.clinic_name);
    if (name !== undefined) grant.clinicName = name;
    if (extra.clinic_origin === 'live' || extra.clinic_origin === 'test') grant.clinicOrigin = extra.clinic_origin;
    return grant;
  });
  const account: Account = { accountId: me.account_id, grants };
  if (raw.operating_profile === 'demo' || raw.operating_profile === 'clinical') account.operatingProfile = raw.operating_profile;
  return account;
}

export const grantKey = (g: Grant): string => `${g.clinicId}|${g.role}`;

/** First 8 characters of the id, until the backend supplies a clinic name. */
export const shortClinicId = (id: string): string => `${id.slice(0, 8)}…`;

export const KNOWN_ROLES = ['patient', 'guardian', 'front_desk', 'nurse', 'doctor', 'admin', 'business_owner', 'auditor'] as const;
