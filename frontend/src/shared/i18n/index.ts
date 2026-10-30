import i18n from 'i18next';
import { initReactI18next } from 'react-i18next';
import en from './en.json';
import ta from './ta.json';

export const LANGUAGES = ['en', 'ta'] as const;
export type Language = (typeof LANGUAGES)[number];

const STORAGE_KEY = 'careflow.language';

export function isLanguage(value: unknown): value is Language {
  return typeof value === 'string' && (LANGUAGES as readonly string[]).includes(value);
}

/** A remembered language is a per-viewer convenience only; every access may throw (blocked storage). */
function readStoredLanguage(): Language {
  try {
    const stored = globalThis.localStorage?.getItem(STORAGE_KEY);
    if (isLanguage(stored)) return stored;
  } catch {
    /* storage unavailable: fall through to the default */
  }
  return 'en';
}

function applyDocumentLanguage(lng: string): void {
  if (typeof document !== 'undefined') document.documentElement.lang = lng;
}

export function setLanguage(lng: Language): Promise<unknown> {
  try {
    globalThis.localStorage?.setItem(STORAGE_KEY, lng);
  } catch {
    /* ignore */
  }
  return i18n.changeLanguage(lng);
}

export function initI18n(initial: Language = readStoredLanguage()): typeof i18n {
  if (!i18n.isInitialized) {
    void i18n.use(initReactI18next).init({
      resources: { en: { translation: en }, ta: { translation: ta } },
      lng: initial,
      fallbackLng: 'en',
      supportedLngs: [...LANGUAGES],
      interpolation: { escapeValue: false },
      returnNull: false,
    });
    i18n.on('languageChanged', applyDocumentLanguage);
  }
  applyDocumentLanguage(i18n.language);
  return i18n;
}

export { i18n };
