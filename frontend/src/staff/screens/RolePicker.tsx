import { useId, useState, type FormEvent } from 'react';
import { useTranslation } from 'react-i18next';
import { ScreenHeading } from '../components/ScreenHeading';
import { grantKey, shortClinicId, type Grant } from '../me';

// Least privilege: a person with several role grants picks ONE active role; nothing is selected by default.
export function RolePicker({ grants, onPick }: { grants: readonly Grant[]; onPick: (g: Grant) => void }) {
  const { t } = useTranslation();
  const name = useId();
  const [selected, setSelected] = useState<string | null>(null);

  function submit(e: FormEvent) {
    e.preventDefault();
    const grant = grants.find((g) => grantKey(g) === selected);
    if (grant) onPick(grant);
  }

  return (
    <section aria-labelledby="pick-title" className="auth-card">
      <ScreenHeading id="pick-title">{t('shell.pickTitle')}</ScreenHeading>
      <p>{t('shell.pickHelp')}</p>
      <form onSubmit={submit}>
        <fieldset>
          <legend className="visually-hidden">{t('shell.pickTitle')}</legend>
          {grants.map((g) => (
            <label key={grantKey(g)} className="choice">
              <input type="radio" name={name} value={grantKey(g)} checked={selected === grantKey(g)} onChange={() => setSelected(grantKey(g))} />
              <span>
                {t(`roles.${g.role}`, { defaultValue: g.role })} · {g.clinicName ?? shortClinicId(g.clinicId)}
              </span>
            </label>
          ))}
        </fieldset>
        <button type="submit" className="primary" disabled={selected === null}>
          {t('shell.continue')}
        </button>
      </form>
    </section>
  );
}
