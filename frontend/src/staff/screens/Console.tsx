import { lazy, Suspense, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { useAuth } from '../auth/AuthProvider';
import { EnvironmentBadge } from '../components/EnvironmentBadge';
import { ScreenHeading } from '../components/ScreenHeading';
import { readStoredGrant, storeGrant } from '../activeRole';
import { parseMe, shortClinicId, type Account, type Grant } from '../me';
import { Loading, Message } from './Message';
import { RolePicker } from './RolePicker';

// The claim spike panel exists only in development builds: the import is behind import.meta.env.DEV so the
// production bundle never contains it (checked by scripts/check-bundle-secrets.mjs).
const DevClaimSpike = import.meta.env.DEV ? lazy(() => import('../components/DevClaimSpike')) : null;

class AccountUnavailable extends Error {}

export function Console() {
  const { t } = useTranslation();
  const { api, signOut } = useAuth();

  const me = useQuery({
    queryKey: ['me'],
    retry: false,
    queryFn: async (): Promise<Account> => {
      const res = await api.getMe();
      if (res.data) return parseMe(res.data);
      // 403 is the API's single answer for "no active account" and "not allowed": say nothing more than it does.
      if (res.response.status === 403) throw new AccountUnavailable();
      throw new Error('me failed');
    },
  });

  // undefined: not chosen in this render yet (fall back to this tab's remembered choice); null: choose again.
  const [picked, setPicked] = useState<Grant | null | undefined>(undefined);

  if (me.isPending) return <Loading label={t('staff.console.loading')} />;

  if (me.error instanceof AccountUnavailable || (me.data && me.data.grants.length === 0)) {
    return (
      <Message id="unavailable-title" title={t('app.staffTitle')} alert>
        <p>{t('staff.account.unavailable')}</p>
        <button type="button" onClick={() => void signOut()}>
          {t('staff.account.signOut')}
        </button>
      </Message>
    );
  }

  if (me.isError || !me.data) {
    return (
      <Message id="me-error-title" title={t('app.staffTitle')} alert>
        <p>{t('staff.console.error')}</p>
        <button type="button" onClick={() => void me.refetch()}>
          {t('staff.console.retry')}
        </button>
      </Message>
    );
  }

  const account = me.data;
  // One grant is active automatically; several require an explicit pick (remembered for this tab only).
  const only = account.grants.length === 1 ? account.grants[0] : undefined;
  const active = only ?? (picked === undefined ? readStoredGrant(account.grants) : picked);

  if (!active) {
    return (
      <RolePicker
        grants={account.grants}
        onPick={(g) => {
          storeGrant(g);
          setPicked(g);
        }}
      />
    );
  }

  return (
    <>
      <div className="session-bar" role="group" aria-label={t('shell.navLabel')}>
        <span className="role-badge">
          <span className="visually-hidden">{t('shell.activeRole')}: </span>
          {t(`roles.${active.role}`, { defaultValue: active.role })}
        </span>
        <span className="clinic">
          <span className="label">{t('shell.clinic')}: </span>
          {active.clinicName ?? <code title={active.clinicId}>{shortClinicId(active.clinicId)}</code>}
        </span>
        <EnvironmentBadge origin={active.clinicOrigin} />
        <span className="profile">
          {account.operatingProfile ? `${t('shell.profile')}: ${t(`profiles.${account.operatingProfile}`)}` : t('shell.profileUnknown')}
        </span>
        {account.grants.length > 1 && (
          <button
            type="button"
            onClick={() => {
              storeGrant(null);
              setPicked(null);
            }}
          >
            {t('shell.changeRole')}
          </button>
        )}
        <button type="button" onClick={() => void signOut()}>
          {t('common.signOut')}
        </button>
      </div>
      <section aria-labelledby="console-title">
        <ScreenHeading id="console-title">{t('app.staffTitle')}</ScreenHeading>
        <p>{t('staff.console.welcome')}</p>
      </section>
      {DevClaimSpike && (
        <Suspense fallback={null}>
          <DevClaimSpike />
        </Suspense>
      )}
    </>
  );
}
