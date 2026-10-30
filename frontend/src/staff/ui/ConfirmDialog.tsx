import { useEffect, useRef, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';

/**
 * A confirmation step for an action that changes something. Native <dialog> + showModal(): focus is trapped, Escape
 * cancels, and the page behind is inert. Cancel is the first control, so focus starts on the safe choice, and nothing
 * here is pre-selected for the person. The result of the action (success or a fixed problem message) is shown by the
 * caller through `children`/`status`.
 */
export function ConfirmDialog({
  title,
  confirmLabel,
  busy,
  onConfirm,
  onCancel,
  children,
  status,
}: {
  title: string;
  confirmLabel: string;
  busy: boolean;
  onConfirm: () => void;
  onCancel: () => void;
  children?: ReactNode;
  status?: ReactNode;
}) {
  const { t } = useTranslation();
  const ref = useRef<HTMLDialogElement>(null);
  const opener = useRef<Element | null>(document.activeElement);

  useEffect(() => {
    const dialog = ref.current;
    if (dialog && !dialog.open) dialog.showModal();
    const previous = opener.current;
    return () => {
      if (previous instanceof HTMLElement) previous.focus(); // back to the control that opened the dialog
    };
  }, []);

  return (
    <dialog
      ref={ref}
      aria-labelledby="confirm-title"
      className="confirm"
      onCancel={(e) => {
        e.preventDefault();
        if (!busy) onCancel();
      }}
    >
      <h2 id="confirm-title">{title}</h2>
      {children}
      {status}
      <div className="dialog-actions">
        <button type="button" onClick={onCancel} disabled={busy}>
          {t('common.cancel')}
        </button>
        <button type="button" className="primary" onClick={onConfirm} disabled={busy}>
          {busy ? t('common.working') : confirmLabel}
        </button>
      </div>
    </dialog>
  );
}
