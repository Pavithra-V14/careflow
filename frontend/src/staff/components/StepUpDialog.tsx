import { useEffect, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { CodeForm } from './CodeForm';

// Shown when the API answers 401 with WWW-Authenticate: step-up. A native <dialog> opened with showModal()
// gives focus trapping, Escape to cancel and an inert background without extra code.
export function StepUpDialog({ onSubmit, onDone }: { onSubmit: (code: string) => Promise<boolean>; onDone: (ok: boolean) => void }) {
  const { t } = useTranslation();
  const ref = useRef<HTMLDialogElement>(null);
  const opener = useRef<Element | null>(document.activeElement);

  useEffect(() => {
    const dialog = ref.current;
    if (dialog && !dialog.open) dialog.showModal();
    const previous = opener.current;
    return () => {
      if (previous instanceof HTMLElement) previous.focus(); // return focus to whatever started the action
    };
  }, []);

  return (
    <dialog ref={ref} aria-labelledby="stepup-title" className="stepup" onCancel={(e) => { e.preventDefault(); onDone(false); }}>
      <h2 id="stepup-title">{t('staff.stepUp.title')}</h2>
      <p>{t('staff.stepUp.help')}</p>
      <CodeForm
        submitLabel={t('staff.stepUp.submit')}
        onSubmit={async (code) => {
          const ok = await onSubmit(code);
          if (ok) onDone(true);
          return ok;
        }}
      />
      <button type="button" onClick={() => onDone(false)}>
        {t('staff.stepUp.cancel')}
      </button>
    </dialog>
  );
}
