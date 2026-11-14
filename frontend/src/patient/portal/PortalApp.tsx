import { useCallback, useEffect, useMemo, useState, type FormEvent } from 'react';
import { QueryClient, QueryClientProvider, useQueryClient } from '@tanstack/react-query';
import { usePortalQuery } from './portalQuery';
import { useTranslation } from 'react-i18next';
import { createApi } from '../../shared/api/client';
import { IDLE_SIGN_OUT_MINUTES, useIdleSignOut } from '../../shared/idle';
import { ApiProblem, toProblem, unwrap } from '../../staff/apiState';
import { ProblemText } from '../../staff/ui/states';
import { PATHS } from '../paths';
import { normalisePhone, type PortalAuth } from './auth';
import { VoiceSection } from './VoiceSection';
import { BookingSection, MessagesSection } from './PortalVisits';
import { NotificationsSection, ProfileSection, PublicGrievanceSection, RequestsSection } from './PortalMore';
import { ClaimSection, ConsentSection, GrievanceSection, MyDataSection } from './PortalSections';

// The patient portal (BUILD-SPEC sections 15 and 19). Mobile first, one column. Patient session checks only: nothing about the patient is
// kept in the browser except the Supabase session (see auth.ts); no analytics, no third-party scripts. Every message is a fixed word per
// problem kind, never the backend's text. Consent notices are shown exactly as the API sends them.

type Phase = 'loading' | 'signed-out' | 'signed-in';

export function PortalApp({ auth, apiBaseUrl }: { auth: PortalAuth | null; apiBaseUrl: string }) {
  const { t } = useTranslation();
  const [phase, setPhase] = useState<Phase>('loading');
  const [idleEnded, setIdleEnded] = useState(false);
  const [queryClient] = useState(() => new QueryClient({ defaultOptions: { queries: { refetchOnWindowFocus: false, retry: false, gcTime: 0 }, mutations: { gcTime: 0 } } }));

  useEffect(() => {
    if (!auth) return;
    let cancelled = false;
    void auth.getAccessToken().then((token) => !cancelled && setPhase(token ? 'signed-in' : 'signed-out'));
    const stop = auth.onSignedOut(() => {
      queryClient.clear(); // nothing of the patient stays on screen or in memory after the session ends
      setPhase('signed-out');
    });
    return () => {
      cancelled = true;
      stop();
    };
  }, [auth, queryClient]);

  const publicApi = useMemo(() => createApi({ baseUrl: apiBaseUrl, getAccessToken: () => null }), [apiBaseUrl]); // no token: these routes need none

  const signOut = useCallback(async () => {
    await auth?.signOut();
    queryClient.clear();
    setPhase('signed-out');
  }, [auth, queryClient]);

  // Off until the owner supplies an approved number of minutes (src/shared/idle.ts). When on, the person is signed out and told so.
  useIdleSignOut(IDLE_SIGN_OUT_MINUTES.patient, phase === 'signed-in', () => {
    setIdleEnded(true);
    void signOut();
  });

  if (!auth) {
    return (
      <section aria-labelledby="portal-title">
        <h1 id="portal-title">{t('portal.title')}</h1>
        <p role="status">{t('common.notConfigured')}</p>
        <p>
          <a href={PATHS.home}>{t('pages.backHome')}</a>
        </p>
      </section>
    );
  }
  return (
    <section aria-labelledby="portal-title" className="portal">
      <h1 id="portal-title">{t('portal.title')}</h1>
      {phase === 'loading' && (
        <p role="status" className="loading">
          {t('common.loading')}
        </p>
      )}
      {phase === 'signed-out' && (
        <>
          {idleEnded && <p role="status">{t('portal.idleEnded')}</p>}
          <SignIn
            auth={auth}
            onDone={() => {
              setIdleEnded(false);
              setPhase('signed-in');
            }}
          />
          <QueryClientProvider client={queryClient}>
            <PublicGrievanceSection api={publicApi} />
          </QueryClientProvider>
        </>
      )}
      {phase === 'signed-in' && (
        <QueryClientProvider client={queryClient}>
          <Account auth={auth} apiBaseUrl={apiBaseUrl} onSignedOut={() => void signOut()} />
        </QueryClientProvider>
      )}
      <p>
        <a href={PATHS.home}>{t('pages.backHome')}</a>
      </p>
    </section>
  );
}

// ---- sign in with a code sent to the patient's mobile number -------------------------------------------------------------------

function SignIn({ auth, onDone }: { auth: PortalAuth; onDone: () => void }) {
  const { t } = useTranslation();
  const [step, setStep] = useState<'phone' | 'code'>('phone');
  const [phone, setPhone] = useState('');
  const [code, setCode] = useState('');
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState<'phone' | 'code' | null>(null);
  const number = normalisePhone(phone);

  async function send(e: FormEvent) {
    e.preventDefault();
    if (!number) return;
    setBusy(true);
    setFailed(null);
    const ok = await auth.requestCode(number);
    setBusy(false);
    if (ok) setStep('code');
    else setFailed('phone');
  }

  async function verify(e: FormEvent) {
    e.preventDefault();
    if (!number || !/^\d{6}$/.test(code)) return;
    setBusy(true);
    setFailed(null);
    const ok = await auth.verifyCode(number, code);
    setBusy(false);
    setCode('');
    if (ok) onDone();
    else setFailed('code');
  }

  return (
    <div>
      <h2>{t('portal.signIn.title')}</h2>
      <p>{t('portal.signIn.intro')}</p>
      {step === 'phone' ? (
        <form onSubmit={(e) => void send(e)} noValidate>
          <label htmlFor="portal-phone">{t('portal.signIn.phone')}</label>
          <input id="portal-phone" type="tel" inputMode="tel" autoComplete="tel" value={phone} onChange={(e) => setPhone(e.target.value)} aria-describedby="portal-phone-hint" />
          <p id="portal-phone-hint" className="hint">
            {t('portal.signIn.phoneHint')}
          </p>
          {phone !== '' && !number && <p className="form-error">{t('portal.signIn.phoneBad')}</p>}
          {failed === 'phone' && <p role="alert">{t('portal.signIn.sendFailed')}</p>}
          <button type="submit" className="button button-primary" disabled={busy || !number}>
            {busy ? t('common.working') : t('portal.signIn.send')}
          </button>
        </form>
      ) : (
        <form onSubmit={(e) => void verify(e)} noValidate>
          <p role="status">{t('portal.signIn.sent')}</p>
          <label htmlFor="portal-code">{t('portal.signIn.code')}</label>
          <input id="portal-code" type="text" inputMode="numeric" autoComplete="one-time-code" maxLength={6} value={code} onChange={(e) => setCode(e.target.value.replace(/\D/g, ''))} />
          {failed === 'code' && <p role="alert">{t('portal.signIn.codeFailed')}</p>}
          <button type="submit" className="button button-primary" disabled={busy || !/^\d{6}$/.test(code)}>
            {busy ? t('common.working') : t('portal.signIn.verify')}
          </button>{' '}
          <button
            type="button"
            className="button button-secondary"
            onClick={() => {
              setStep('phone');
              setCode('');
              setFailed(null);
            }}
          >
            {t('portal.signIn.change')}
          </button>
        </form>
      )}
    </div>
  );
}

// ---- the signed-in account --------------------------------------------------------------------------------------------------------

function Account({ auth, apiBaseUrl, onSignedOut }: { auth: PortalAuth; apiBaseUrl: string; onSignedOut: () => void }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const api = useMemo(() => createApi({ baseUrl: apiBaseUrl, getAccessToken: () => auth.getAccessToken(), onUnauthorized: onSignedOut }), [auth, apiBaseUrl, onSignedOut]);
  const me = usePortalQuery(['portal-me'], () => unwrap(api.portalMe()));
  const [registering, setRegistering] = useState(false);
  const [registerFailed, setRegisterFailed] = useState<ApiProblem | null>(null);

  async function register() {
    setRegistering(true);
    setRegisterFailed(null);
    try {
      await unwrap(api.portalRegister());
      await queryClient.invalidateQueries({ queryKey: ['portal-me'] });
    } catch (e) {
      setRegisterFailed(toProblem(e));
    } finally {
      setRegistering(false);
    }
  }

  const header = (
    <p>
      <button type="button" className="button button-secondary" onClick={onSignedOut}>
        {t('common.signOut')}
      </button>
    </p>
  );

  if (me.isPending)
    return (
      <p role="status" className="loading">
        {t('common.loading')}
      </p>
    );

  // 403 is the API's one answer for "you have no patient account yet": offer to create it, as an explicit step.
  if (me.isError && me.error.kind === 'forbidden') {
    return (
      <div>
        {header}
        <h2>{t('portal.register.title')}</h2>
        <p>{t('portal.register.body')}</p>
        {registerFailed && (
          <p role="alert">
            {registerFailed.kind === 'forbidden' ? t('portal.register.refused') : <ProblemText kind={registerFailed.kind} />}
          </p>
        )}
        <button type="button" className="button button-primary" disabled={registering} onClick={() => void register()}>
          {registering ? t('common.working') : t('portal.register.create')}
        </button>
      </div>
    );
  }
  if (me.isError) {
    return (
      <div>
        {header}
        <p role="alert">
          <ProblemText kind={me.error.kind} />
        </p>
        <button type="button" className="button button-secondary" onClick={() => void me.refetch()}>
          {t('common.retry')}
        </button>
      </div>
    );
  }

  const account = me.data;
  const active = account.stage === 'active';
  return (
    <div>
      {header}
      <dl className="facts">
        <dt>{t('portal.stage.label')}</dt>
        <dd>{t(`portal.stage.${account.stage}`)}</dd>
        <dt>{t('portal.linked.label')}</dt>
        <dd>{account.bound_to_a_patient_record ? t('common.yes') : t('common.no')}</dd>
      </dl>
      <ConsentSection api={api} stage={account.stage} />
      {active ? (
        <>
          <ClaimSection api={api} bound={account.bound_to_a_patient_record} />
          <ProfileSection api={api} />
          <NotificationsSection api={api} />
          <RequestsSection api={api} />
          <BookingSection api={api} />
          <MessagesSection api={api} />
          <GrievanceSection api={api} />
          <VoiceSection api={api} />
          <MyDataSection api={api} />
        </>
      ) : (
        <p className="hint">{t('portal.afterActivation')}</p>
      )}
    </div>
  );
}
