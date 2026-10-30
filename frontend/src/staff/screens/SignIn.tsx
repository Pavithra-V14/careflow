import { useId, useRef, useState, type FormEvent } from 'react';
import { useTranslation } from 'react-i18next';
import { useAuth } from '../auth/AuthProvider';
import { ScreenHeading } from '../components/ScreenHeading';

// Staff are invited, so there is deliberately no registration link or screen here.
export function SignIn() {
  const { t } = useTranslation();
  const { signIn, sessionEnded } = useAuth();
  const id = useId();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [state, setState] = useState<'idle' | 'busy' | 'failed'>('idle');
  const emailInput = useRef<HTMLInputElement>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setState('busy');
    const ok = await signIn(email.trim(), password);
    if (!ok) {
      setPassword('');
      setState('failed');
      emailInput.current?.focus();
    }
  }

  return (
    <section aria-labelledby="signin-title" className="auth-card">
      <ScreenHeading id="signin-title">{t('staff.signIn.title')}</ScreenHeading>
      <p>{t('staff.signIn.intro')}</p>
      {sessionEnded && state === 'idle' && <p role="status">{t('staff.session.ended')}</p>}
      <form onSubmit={(e) => void submit(e)}>
        <label htmlFor={`${id}-email`}>{t('staff.signIn.email')}</label>
        <input id={`${id}-email`} ref={emailInput} type="email" name="email" autoComplete="username" required value={email} onChange={(e) => setEmail(e.target.value)} disabled={state === 'busy'} />
        <label htmlFor={`${id}-password`}>{t('staff.signIn.password')}</label>
        <input id={`${id}-password`} type="password" name="password" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} disabled={state === 'busy'} />
        {state === 'failed' && (
          <p role="alert" className="form-error">
            {t('staff.signIn.failed')}
          </p>
        )}
        <button type="submit" className="primary" disabled={state === 'busy'}>
          {state === 'busy' ? t('staff.signIn.submitting') : t('staff.signIn.submit')}
        </button>
      </form>
      <p className="hint">{t('staff.signIn.invitationOnly')}</p>
    </section>
  );
}
