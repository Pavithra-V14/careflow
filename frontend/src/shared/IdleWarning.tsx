import { useEffect, useRef } from 'react';
import { useTranslation } from 'react-i18next';

/**
 * The "still there?" warning, shown for the last minute before an idle sign-out. A native modal dialog: focus is trapped and starts on
 * the one button, and the page behind is inert. Escape does nothing (only the button answers), so staying is a deliberate act. The
 * countdown is not announced every second; the title and the first sentence are read when it opens.
 */
export function IdleWarning({ secondsLeft, onStay }: { secondsLeft: number | null; onStay: () => void }) {
  const { t } = useTranslation();
  const ref = useRef<HTMLDialogElement>(null);
  const open = secondsLeft !== null;
  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (open && !dialog.open) dialog.showModal();
    if (!open && dialog.open) dialog.close();
  }, [open]);
  return (
    <dialog ref={ref} aria-labelledby="idle-title" aria-describedby="idle-body" className="confirm" onCancel={(e) => e.preventDefault()}>
      <h2 id="idle-title">{t('idle.title')}</h2>
      <p id="idle-body">{t('idle.body', { n: secondsLeft ?? 0 })}</p>
      <div className="dialog-actions">
        <button type="button" className="primary" onClick={onStay}>
          {t('idle.stay')}
        </button>
      </div>
    </dialog>
  );
}
