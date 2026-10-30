import type { Language } from './index';

// Intl locales per UI language. Indian conventions for both (lakh grouping, day-first dates).
const LOCALE: Record<Language, string> = { en: 'en-IN', ta: 'ta-IN' };

export function formatNumber(value: number, lng: Language, options?: Intl.NumberFormatOptions): string {
  return new Intl.NumberFormat(LOCALE[lng], options).format(value);
}

export function formatDate(value: Date | string, lng: Language, options: Intl.DateTimeFormatOptions = { dateStyle: 'medium' }): string {
  const date = typeof value === 'string' ? new Date(value) : value;
  if (Number.isNaN(date.getTime())) return '';
  return new Intl.DateTimeFormat(LOCALE[lng], { timeZone: 'Asia/Kolkata', ...options }).format(date);
}

export function formatDateTime(value: Date | string, lng: Language): string {
  return formatDate(value, lng, { dateStyle: 'medium', timeStyle: 'short' });
}
