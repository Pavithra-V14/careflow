import { useTranslation } from 'react-i18next';
import { PATHS } from '../paths';
import { Placeholder } from '../components/Placeholder';

export type NoticeKind = 'sign-in' | 'sign-up' | 'privacy';

// Pages whose real content does not exist yet. Each one says so plainly (the "empty" state) and never
// pretends to work. The privacy notice itself is hospital/counsel wording, so it is a placeholder; the
// signed-in consent flow fetches the current notice from the server rather than showing this page.
export function NoticePage({ kind }: { kind: NoticeKind }) {
  const { t } = useTranslation();
  const title = { 'sign-in': 'pages.signInTitle', 'sign-up': 'pages.signUpTitle', privacy: 'pages.privacyTitle' }[kind];
  return (
    <section aria-labelledby="page-heading">
      <h1 id="page-heading">{t(title)}</h1>
      {kind === 'privacy' ? (
        <Placeholder>{t('placeholder.privacyNotice')}</Placeholder>
      ) : (
        <p role="status">{t(kind === 'sign-in' ? 'pages.notAvailableSignIn' : 'pages.notAvailableSignUp')}</p>
      )}
      <p>
        <a href={PATHS.home}>{t('pages.backHome')}</a>
      </p>
    </section>
  );
}
