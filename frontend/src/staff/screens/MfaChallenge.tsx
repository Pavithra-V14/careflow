import { useTranslation } from 'react-i18next';
import { useAuth } from '../auth/AuthProvider';
import { CodeForm } from '../components/CodeForm';
import { ScreenHeading } from '../components/ScreenHeading';

export function MfaChallenge() {
  const { t } = useTranslation();
  const { verifyCode } = useAuth();
  return (
    <section aria-labelledby="challenge-title" className="auth-card">
      <ScreenHeading id="challenge-title">{t('staff.mfa.challenge.title')}</ScreenHeading>
      <p>{t('staff.mfa.challenge.help')}</p>
      <CodeForm submitLabel={t('staff.mfa.challenge.submit')} onSubmit={(code) => verifyCode(null, code)} />
    </section>
  );
}
