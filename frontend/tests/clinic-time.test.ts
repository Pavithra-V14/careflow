import { afterEach, describe, expect, it } from 'vitest';
import { CLINIC_TIME_ZONE_PLACEHOLDER, clinicLocalToIso, getClinicTimeZone, setClinicTimeZone } from '../src/shared/clinicTime';
import { formatDateTime } from '../src/shared/i18n/format';

afterEach(() => setClinicTimeZone(CLINIC_TIME_ZONE_PLACEHOLDER));

describe('the clinic time zone (owner decision 2026-11-07; the zone is a labelled demo placeholder until the backend sends it)', () => {
  it('defaults to the demo placeholder Asia/Calcutta', () => {
    expect(CLINIC_TIME_ZONE_PLACEHOLDER).toBe('Asia/Calcutta');
    expect(getClinicTimeZone()).toBe('Asia/Calcutta');
  });

  it('reads a typed wall-clock time AS the clinic time, whatever the browser zone is', () => {
    expect(clinicLocalToIso('2026-11-10T09:30')).toBe('2026-11-10T04:00:00.000Z');
    expect(clinicLocalToIso('2026-11-10T00:00')).toBe('2026-11-09T18:30:00.000Z');
  });

  it('follows the clinic setting, including a daylight-saving zone', () => {
    expect(clinicLocalToIso('2026-07-01T12:00', 'America/New_York')).toBe('2026-07-01T16:00:00.000Z');
    expect(clinicLocalToIso('2026-12-01T12:00', 'America/New_York')).toBe('2026-12-01T17:00:00.000Z');
    expect(setClinicTimeZone('UTC')).toBe(true);
    expect(clinicLocalToIso('2026-11-10T09:30')).toBe('2026-11-10T09:30:00.000Z');
  });

  it('returns null for anything that is not a real wall-clock time, so a half-typed value never becomes a date', () => {
    for (const bad of ['', '2026-11-10', '2026-02-31T10:00', '2026-13-01T10:00', 'tomorrow', '2026-11-10T25:00']) expect(clinicLocalToIso(bad), bad).toBeNull();
  });

  it('ignores a zone name Intl does not know, and keeps the previous one', () => {
    expect(setClinicTimeZone('Mars/Olympus')).toBe(false);
    expect(getClinicTimeZone()).toBe('Asia/Calcutta');
  });

  it('shows every time in the clinic zone, not the machine zone', () => {
    expect(formatDateTime('2026-11-10T04:00:00Z', 'en')).toMatch(/9:30/);
    setClinicTimeZone('UTC');
    expect(formatDateTime('2026-11-10T04:00:00Z', 'en')).toMatch(/4:00/);
  });
});
