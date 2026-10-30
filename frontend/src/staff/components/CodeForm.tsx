import { useId, useRef, useState, type FormEvent } from 'react';
import { useTranslation } from 'react-i18next';

// A 6-digit authenticator code form, shared by enrolment, the sign-in challenge and the step-up dialog.
// It validates the shape locally and never shows more than "not accepted": no reason, no attempt count.
export function CodeForm({ submitLabel, onSubmit }: { submitLabel: string; onSubmit: (code: string) => Promise<boolean> }) {
  const { t } = useTranslation();
  const id = useId();
  const [code, setCode] = useState('');
  const [state, setState] = useState<'idle' | 'busy' | 'invalid' | 'failed'>('idle');
  const input = useRef<HTMLInputElement>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    const digits = code.replace(/\s/g, '');
    if (!/^\d{6}$/.test(digits)) {
      setState('invalid');
      input.current?.focus();
      return;
    }
    setState('busy');
    const ok = await onSubmit(digits);
    if (!ok) {
      setCode('');
      setState('failed');
      input.current?.focus();
    }
    // On success the parent moves on (unmounts this form), so there is nothing to reset.
  }

  const message = state === 'invalid' ? t('staff.mfa.invalid') : state === 'failed' ? t('staff.mfa.failed') : null;
  return (
    <form onSubmit={(e) => void submit(e)} noValidate>
      <label htmlFor={`${id}-code`}>{t('staff.mfa.code')}</label>
      <input
        id={`${id}-code`}
        ref={input}
        value={code}
        onChange={(e) => setCode(e.target.value)}
        inputMode="numeric"
        autoComplete="one-time-code"
        pattern="[0-9]*"
        maxLength={7}
        aria-describedby={`${id}-hint${message ? ` ${id}-msg` : ''}`}
        aria-invalid={message ? true : undefined}
        disabled={state === 'busy'}
      />
      <p id={`${id}-hint`} className="hint">
        {t('staff.mfa.codeHint')}
      </p>
      {message && (
        <p id={`${id}-msg`} role="alert" className="form-error">
          {message}
        </p>
      )}
      <button type="submit" className="primary" disabled={state === 'busy'}>
        {state === 'busy' ? t('staff.mfa.verifying') : submitLabel}
      </button>
    </form>
  );
}
