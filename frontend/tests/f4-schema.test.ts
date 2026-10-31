import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';
import { DECISIONS, OFFERED_DECISIONS } from '../src/staff/pages/ReferralReview';
import { REVOKE_REASONS } from '../src/staff/pages/IdentityRevoke';
import { DUPLICATE_STATUSES, MERGE_STATUSES } from '../src/staff/pages/IdentityPage';

// The word lists the F4 screens show are copied from the backend's enums. This pins each copy to openapi.snapshot.json, so
// a backend change to a list fails here instead of silently leaving the console offering a word the API no longer accepts.

type Json = Record<string, unknown>;
const spec = JSON.parse(readFileSync(resolve(import.meta.dirname, '../../openapi.snapshot.json'), 'utf8')) as Json;
const schemas = (spec.components as Json).schemas as Record<string, { properties: Record<string, { enum?: string[]; anyOf?: { enum?: string[] }[] }> }>;
const paths = spec.paths as Record<string, Record<string, { parameters?: { name: string; schema: { anyOf?: { enum?: string[] }[] } }[] }>>;

const prop = (schema: string, name: string) => schemas[schema]?.properties[name]?.enum ?? [];
const queryEnum = (path: string, name: string) => paths[path]?.get?.parameters?.find((p) => p.name === name)?.schema.anyOf?.find((a) => a.enum)?.enum ?? [];

describe('word lists mirrored from the schema', () => {
  it('referral decisions are exactly the words ReviewIn accepts, in order', () => {
    expect([...DECISIONS]).toEqual(prop('ReviewIn', 'decision'));
    expect(DECISIONS.length).toBeGreaterThan(0);
  });
  it('offers only decisions the API accepts, and edit is not offered (clinician item 26)', () => {
    for (const d of OFFERED_DECISIONS) expect(DECISIONS).toContain(d);
    expect([...OFFERED_DECISIONS]).toEqual(['approve', 'reject']);
  });
  it('identity-revoke reasons are exactly the words RevokeIdentityIn accepts, in order', () => {
    expect([...REVOKE_REASONS]).toEqual(prop('RevokeIdentityIn', 'reason'));
    expect(REVOKE_REASONS.length).toBeGreaterThan(0);
  });
  it('merge and duplicate status filters match the list routes', () => {
    expect([...MERGE_STATUSES]).toEqual(queryEnum('/v1/staff/identity/merges', 'status'));
    expect([...DUPLICATE_STATUSES]).toEqual(queryEnum('/v1/staff/identity/possible-duplicates', 'status'));
  });
  it('the review and revoke routes take exactly one body field each, so no reason or free text can be sent by mistake', () => {
    expect(Object.keys(schemas.ReviewIn?.properties ?? {})).toEqual(['decision']);
    expect(Object.keys(schemas.RevokeIdentityIn?.properties ?? {})).toEqual(['reason']);
  });
});
