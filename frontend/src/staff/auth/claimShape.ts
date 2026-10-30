// Step-up claim spike (BUILD-SPEC Appendix B; docs/design/step-up-spike.md). The backend's step-up check
// depends on the shape of the `aal` and `amr` claims, which has never been checked against a real token.
// This module decodes an access token's payload IN CODE and describes only the STRUCTURE, with every value
// blanked, so the result can be pasted to the backend session. It returns no part of the token, no key and
// no user data, and nothing here logs.

type Json = Record<string, unknown>;

export function decodeJwtPayload(token: string): Json | null {
  const part = token.split('.')[1];
  if (!part) return null;
  try {
    const b64 = part.replace(/-/g, '+').replace(/_/g, '/').padEnd(Math.ceil(part.length / 4) * 4, '=');
    const bytes = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
    const parsed: unknown = JSON.parse(new TextDecoder().decode(bytes));
    return parsed !== null && typeof parsed === 'object' && !Array.isArray(parsed) ? (parsed as Json) : null;
  } catch {
    return null;
  }
}

/** A value reduced to its type: "<string>", "<integer, 10 digits>", "<boolean>", "<null>", "<array>" ... */
function blank(value: unknown): string {
  if (value === null) return '<null>';
  if (typeof value === 'number') {
    return Number.isInteger(value) ? `<integer, ${String(Math.abs(value)).length} digits>` : '<non-integer number>';
  }
  if (Array.isArray(value)) return '<array>';
  return `<${typeof value}>`;
}

export type AmrShape = string | Record<string, string> | '<missing>';

export interface ClaimShape {
  aal: string;
  amr: '<missing>' | '<not an array>' | AmrShape[];
  iat: string;
  /** Per amr entry: where its `timestamp` sits relative to `iat`. Null when either is not a number. */
  amrTimestampVsIat: ('before' | 'equal' | 'after' | null)[];
  /** Whether the amr timestamps are identical to the previous capture. Answers "does a refresh move them?". */
  amrTimestampsSinceLastCapture: 'first capture' | 'unchanged' | 'changed';
  /** Only when explicitly requested: the enum-like values (the aal level and each amr method name). */
  enumValues?: { aal: unknown; amrMethods: unknown[] };
}

export interface DescribeOptions {
  /** The (hashed) fingerprint returned by an earlier capture, to compare amr timestamps across a refresh. */
  previousFingerprint?: string | undefined;
  /** Off by default: the instruction was values blanked. Method names such as the TOTP one are enum-like, not secret. */
  revealEnumValues?: boolean;
}

/** A short hash, so the comparison value carries none of the timestamps it was made from. */
function hash(text: string): string {
  let h = 0x811c9dc5;
  for (let i = 0; i < text.length; i++) h = Math.imul(h ^ text.charCodeAt(i), 0x01000193) >>> 0;
  return h.toString(16).padStart(8, '0');
}

export function describeClaimShape(token: string, options: DescribeOptions = {}): { shape: ClaimShape; fingerprint: string } | null {
  const payload = decodeJwtPayload(token);
  if (!payload) return null;

  const amrRaw = payload.amr;
  const entries: unknown[] | null = Array.isArray(amrRaw) ? amrRaw : null;
  const amr: ClaimShape['amr'] = amrRaw === undefined ? '<missing>' : entries === null ? '<not an array>' : entries.map(
    (e): AmrShape => {
      if (e !== null && typeof e === 'object' && !Array.isArray(e)) {
        return Object.fromEntries(Object.entries(e as Json).map(([k, v]) => [k, blank(v)]));
      }
      return blank(e);
    },
  );

  const iat = payload.iat;
  const timestamps = (entries ?? []).map((e) => (e !== null && typeof e === 'object' ? (e as Json).timestamp : undefined));
  const fingerprint = hash(JSON.stringify(timestamps));

  const shape: ClaimShape = {
    aal: payload.aal === undefined ? '<missing>' : blank(payload.aal),
    amr,
    iat: iat === undefined ? '<missing>' : blank(iat),
    amrTimestampVsIat: timestamps.map((ts) => {
      if (typeof ts !== 'number' || typeof iat !== 'number') return null;
      return ts < iat ? 'before' : ts > iat ? 'after' : 'equal';
    }),
    amrTimestampsSinceLastCapture:
      options.previousFingerprint === undefined ? 'first capture' : options.previousFingerprint === fingerprint ? 'unchanged' : 'changed',
  };
  if (options.revealEnumValues) {
    shape.enumValues = {
      aal: payload.aal,
      amrMethods: (entries ?? []).map((e) => (e !== null && typeof e === 'object' ? (e as Json).method : e)),
    };
  }
  return { shape, fingerprint };
}
