import { useTranslation } from 'react-i18next';
import { LANGUAGES, isLanguage, setLanguage } from './index';

export function LanguageSwitcher() {
  const { t, i18n } = useTranslation();
  const current = isLanguage(i18n.language) ? i18n.language : 'en';
  return (
    <label>
      <span className="visually-hidden">{t('common.language')}</span>
      <select
        value={current}
        onChange={(e) => {
          if (isLanguage(e.target.value)) void setLanguage(e.target.value);
        }}
      >
        {LANGUAGES.map((lng) => (
          // Each language is named in its own script, with its lang attribute, so screen readers pronounce it.
          <option key={lng} value={lng} lang={lng}>
            {t(`languages.${lng}`, { lng })}
          </option>
        ))}
      </select>
    </label>
  );
}
