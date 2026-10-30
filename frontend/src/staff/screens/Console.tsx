import { useEffect, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { formatActiveRole } from '../../shared/api/client';
import { useAuth } from '../auth/AuthProvider';
import { readStoredGrant, storeGrant } from '../activeRole';
import { parseMe, type Account, type Grant } from '../me';
import { SessionProvider } from '../session/SessionContext';
import { DevSpikeSlot } from '../components/DevSpikeSlot';
import { Shell } from './Shell';
import { Loading, Message } from './Message';
import { RolePicker } from './RolePicker';

class AccountUnavailable extends Error {}

/** Keeps the API client's "active role" cell in step with the chosen grant. The header it feeds is off by default. */
function ActiveRoleSync({ grant }: { grant: Grant }) {
  const { setActiveRole } = useAuth();
  useEffect(() => {
    setActiveRole(formatActiveRole(grant.clinicId, grant.role));
    return () => setActiveRole(null);
  }, [grant, setActiveRole]);
  return null;
}

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
      <>
        <Message id="me-error-title" title={t('app.staffTitle')} alert>
          <p>{t('staff.console.error')}</p>
          <button type="button" onClick={() => void me.refetch()}>
            {t('staff.console.retry')}
          </button>
        </Message>
        {/* Development builds only: the claim spike needs the session, not the backend. Outside the alert on purpose. */}
        <DevSpikeSlot />
      </>
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
    <SessionProvider
      account={account}
      active={active}
      signOut={() => void signOut()}
      changeRole={
        account.grants.length > 1
          ? () => {
              storeGrant(null);
              setPicked(null);
            }
          : null
      }
    >
      <ActiveRoleSync grant={active} />
      <Shell />
    </SessionProvider>
  );
}
