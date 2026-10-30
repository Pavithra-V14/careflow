import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useAuth } from '../auth/AuthProvider';
import type { TotpEnrolment } from '../auth/port';
import { CodeForm } from '../components/CodeForm';
import { ScreenHeading } from '../components/ScreenHeading';

// First login: enrol an authenticator app. loading and error are real states: the enrolment call can fail.
export function MfaEnrol() {
  const { t } = useTranslation();
  const { startEnrolment, verifyCode } = useAuth();
  const [state, setState] = useState<{ kind: 'loading' } | { kind: 'error' } | { kind: 'ready'; enrolment: TotpEnrolment }>({ kind: 'loading' });
  const started = useRef(false);

  async function start() {
    setState({ kind: 'loading' });
    const enrolment = await startEnrolment();
    setState(enrolment ? { kind: 'ready', enrolment } : { kind: 'error' });
  }

  useEffect(() => {
    // React StrictMode runs effects twice in development; a second enrol call would invalidate the first QR code.
    if (started.current) return;
    started.current = true;
    void start();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- start once on mount
  }, []);

  return (
    <section aria-labelledby="enrol-title" className="auth-card">
      <ScreenHeading id="enrol-title">{t('staff.mfa.enrol.title')}</ScreenHeading>
      {state.kind === 'loading' && <p role="status">{t('staff.mfa.enrol.preparing')}</p>}
      {state.kind === 'error' && (
        <>
          <p role="alert" className="form-error">
            {t('staff.mfa.enrol.error')}
          </p>
          <button type="button" onClick={() => void start()}>
            {t('staff.mfa.enrol.retry')}
          </button>
        </>
      )}
      {state.kind === 'ready' && (
        <>
          <p>{t('staff.mfa.enrol.intro')}</p>
          <img className="qr" src={state.enrolment.qrCodeSrc} alt={t('staff.mfa.enrol.scanAlt')} width={200} height={200} />
          <p>{t('staff.mfa.enrol.manual')}</p>
          <p>
            <code className="secret">{state.enrolment.secret}</code>
          </p>
          <CodeForm
            submitLabel={t('staff.mfa.enrol.submit')}
            onSubmit={(code) => verifyCode(state.enrolment.factorId, code)}
          />
        </>
      )}
    </section>
  );
}
