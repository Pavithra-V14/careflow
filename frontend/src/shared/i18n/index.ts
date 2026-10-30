import i18n from 'i18next';
import { initReactI18next } from 'react-i18next';
import en from './en.json';
import ta from './ta.json';
import consoleEn from './console.en.json';
import consoleTa from './console.ta.json';

type Tree = { [key: string]: string | Tree };

/** Merges nested string trees; a key in `extra` extends (never replaces) a group already in `base`. */
export function mergeTrees(base: Tree, extra: Tree): Tree {
  const out: Tree = { ...base };
  for (const [k, v] of Object.entries(extra)) {
    const existing = out[k];
    out[k] = typeof v === 'object' && existing !== undefined && typeof existing === 'object' ? mergeTrees(existing, v) : v;
  }
  return out;
}

export const RESOURCES = { en: mergeTrees(en as Tree, consoleEn as Tree), ta: mergeTrees(ta as Tree, consoleTa as Tree) };

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
      resources: { en: { translation: RESOURCES.en }, ta: { translation: RESOURCES.ta } },
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
