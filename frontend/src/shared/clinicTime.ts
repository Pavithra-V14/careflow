// The clinic's time zone. Every time the console shows or takes is in THIS zone, never the browser's.
//
// DEMO PLACEHOLDER: the zone is a clinic setting the backend does not send yet (docs/frontend-backend-requests.md item 9). Until it does,
// this placeholder is used and the screens that take a time say so. Replace it by calling setClinicTimeZone with the setting's value.
export const CLINIC_TIME_ZONE_PLACEHOLDER = 'Asia/Calcutta';

let zone = CLINIC_TIME_ZONE_PLACEHOLDER;
export const getClinicTimeZone = (): string => zone;

/** Accepts only a zone name Intl knows; anything else is ignored so a bad setting cannot break every time shown. */
export function setClinicTimeZone(name: string): boolean {
  try {
    new Intl.DateTimeFormat('en', { timeZone: name });
    zone = name;
    return true;
  } catch {
    return false;
  }
}

/** Milliseconds the zone is ahead of UTC at the given instant. */
function offsetMs(instant: number, timeZone: string): number {
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone,
    hourCycle: 'h23',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  }).formatToParts(new Date(instant));
  const n = (type: string) => Number(parts.find((p) => p.type === type)?.value);
  const asUtc = Date.UTC(n('year'), n('month') - 1, n('day'), n('hour'), n('minute'), n('second'));
  return asUtc - Math.floor(instant / 1000) * 1000;
}

/**
 * A wall-clock time typed in a datetime-local box (`YYYY-MM-DDTHH:mm`) read AS the clinic's time, returned as an ISO instant (UTC).
 * Returns null for anything that is not a real wall-clock time, so a half-typed value never becomes a date.
 */
export function clinicLocalToIso(local: string, timeZone: string = zone): string | null {
  const m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})$/.exec(local);
  if (!m) return null;
  const [y, mo, d, h, mi] = m.slice(1).map(Number) as [number, number, number, number, number];
  const wall = Date.UTC(y, mo - 1, d, h, mi);
  const check = new Date(wall);
  // Date.UTC rolls an impossible date over (Feb 31, hour 25); a real wall-clock time reads back exactly as typed.
  if (check.getUTCFullYear() !== y || check.getUTCMonth() !== mo - 1 || check.getUTCDate() !== d || check.getUTCHours() !== h || check.getUTCMinutes() !== mi) return null;
  const first = wall - offsetMs(wall, timeZone);
  const second = wall - offsetMs(first, timeZone); // settles across a daylight-saving change
  return new Date(second).toISOString();
}
