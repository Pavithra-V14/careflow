import type { ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { ScreenHeading } from '../components/ScreenHeading';
import { formatDateTime } from '../../shared/i18n/format';
import { isLanguage } from '../../shared/i18n';

export function useLang(): 'en' | 'ta' {
  const { i18n } = useTranslation();
  return isLanguage(i18n.language) ? i18n.language : 'en';
}

export function Time({ value }: { value: string | null | undefined }) {
  const lang = useLang();
  const text = value ? formatDateTime(value, lang) : '';
  return value && text ? <time dateTime={value}>{text}</time> : <span aria-hidden="true">–</span>;
}

/** An id shown short in lists. The full id is always on the detail page. */
export function ShortId({ id }: { id: string }) {
  return <code className="short-id">{id.slice(0, 8)}…</code>;
}

export const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export function PageHeading({ id, children }: { id: string; children: ReactNode }) {
  return <ScreenHeading id={id}>{children}</ScreenHeading>;
}

/** A text badge: the words carry the meaning, the colour only reinforces it. */
export function Badge({ tone, children }: { tone: 'neutral' | 'urgent' | 'warn' | 'ok' | 'soon'; children: ReactNode }) {
  return <span className={`pill pill-${tone}`}>{children}</span>;
}

export function ComingSoon() {
  const { t } = useTranslation();
  return <Badge tone="soon">{t('common.comingSoon')}</Badge>;
}

/** Placeholder text the owner, a clinician or counsel must supply. Marked and countable by tests. */
export function PlaceholderNote({ children }: { children: ReactNode }) {
  return (
    <p className="placeholder" data-placeholder>
      {children}
    </p>
  );
}

/** A closed word from the backend, shown humanised ("referral_hold" becomes "referral hold") unless a label exists. */
export function useWord() {
  const { t } = useTranslation();
  return (group: string, word: string | null | undefined): string => {
    if (!word) return '–';
    return t(`${group}.${word}`, { defaultValue: word.replaceAll('_', ' ') });
  };
}
