import i18n from 'i18next';
import { initReactI18next } from 'react-i18next';
import en from './en.json';
import ta from './ta.json';
import consoleEn from './console.en.json';
import consoleTa from './console.ta.json';
import f4En from './f4.en.json';
import f4Ta from './f4.ta.json';
import f6En from './f6.en.json';
import f6Ta from './f6.ta.json';
import f7En from './f7.en.json';
import f7Ta from './f7.ta.json';
import f8En from './f8.en.json';
import f8Ta from './f8.ta.json';
import f9En from './f9.en.json';
import f9Ta from './f9.ta.json';
import f10En from './f10.en.json';
import f10Ta from './f10.ta.json';
import f11En from './f11.en.json';
import f11Ta from './f11.ta.json';
import f12En from './f12.en.json';
import f13En from './f13.en.json';
import f14En from './f14.en.json';
import f15En from './f15.en.json';
import f16En from './f16.en.json';
import f17En from './f17.en.json';
import f18En from './f18.en.json';
import f19En from './f19.en.json';
import f19Ta from './f19.ta.json';
import f20En from './f20.en.json';
import f20Ta from './f20.ta.json';
import f21En from './f21.en.json';
import f21Ta from './f21.ta.json';
import f22En from './f22.en.json';
import f22Ta from './f22.ta.json';
import f23En from './f23.en.json';
import f23Ta from './f23.ta.json';
import f24En from './f24.en.json';
import f24Ta from './f24.ta.json';
import f25En from './f25.en.json';
import f25Ta from './f25.ta.json';
import f26En from './f26.en.json';
import f26Ta from './f26.ta.json';
import f18Ta from './f18.ta.json';
import f17Ta from './f17.ta.json';
import f16Ta from './f16.ta.json';
import f15Ta from './f15.ta.json';
import f14Ta from './f14.ta.json';
import f13Ta from './f13.ta.json';
import f12Ta from './f12.ta.json';

type Tree = { [key: string]: string | Tree };

/** Merges nested string trees; a key in `extra` extends (never replaces) a group already in `base`. */
function mergeTrees(base: Tree, extra: Tree): Tree {
  const out: Tree = { ...base };
  for (const [k, v] of Object.entries(extra)) {
    const existing = out[k];
    out[k] = typeof v === 'object' && existing !== undefined && typeof existing === 'object' ? mergeTrees(existing, v) : v;
  }
  return out;
}

export const RESOURCES = {
  en: mergeTrees(mergeTrees(mergeTrees(mergeTrees(mergeTrees(mergeTrees(mergeTrees(mergeTrees(mergeTrees(mergeTrees(mergeTrees(mergeTrees(mergeTrees(mergeTrees(mergeTrees(mergeTrees(mergeTrees(mergeTrees(mergeTrees(mergeTrees(mergeTrees(mergeTrees(mergeTrees(en as Tree, consoleEn as Tree), f4En as Tree), f6En as Tree), f7En as Tree), f8En as Tree), f9En as Tree), f10En as Tree), f12En as Tree), f11En as Tree), f13En as Tree), f14En as Tree), f15En as Tree), f16En as Tree), f17En as Tree), f18En as Tree), f19En as Tree), f20En as Tree), f21En as Tree), f22En as Tree), f23En as Tree), f24En as Tree), f25En as Tree), f26En as Tree),
  ta: mergeTrees(mergeTrees(mergeTrees(mergeTrees(mergeTrees(mergeTrees(mergeTrees(mergeTrees(mergeTrees(mergeTrees(mergeTrees(mergeTrees(mergeTrees(mergeTrees(mergeTrees(mergeTrees(mergeTrees(mergeTrees(mergeTrees(mergeTrees(mergeTrees(mergeTrees(mergeTrees(ta as Tree, consoleTa as Tree), f4Ta as Tree), f6Ta as Tree), f7Ta as Tree), f8Ta as Tree), f9Ta as Tree), f10Ta as Tree), f12Ta as Tree), f11Ta as Tree), f13Ta as Tree), f14Ta as Tree), f15Ta as Tree), f16Ta as Tree), f17Ta as Tree), f18Ta as Tree), f19Ta as Tree), f20Ta as Tree), f21Ta as Tree), f22Ta as Tree), f23Ta as Tree), f24Ta as Tree), f25Ta as Tree), f26Ta as Tree),
};

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
