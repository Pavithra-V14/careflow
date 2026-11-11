/**
 * Several scheduling lists return the rows of EVERY clinic the account can read, each row carrying its `clinic_id`. The console works in
 * one clinic at a time (the active one), so a row from another clinic is dropped here: it could not be held, booked or joined from this
 * clinic, and the backend would refuse it. The routes that take an optional `clinic_id` (slots, bookings, holds, waitlist) are asked for the active clinic as well; this filter stays as the guard
 * for the ones that do not (offers, reference lists).
 */
export function inClinic<T extends { clinic_id: string }, D extends { items: T[] }>(data: D, clinicId: string): D {
  return { ...data, items: data.items.filter((i) => i.clinic_id === clinicId) };
}
