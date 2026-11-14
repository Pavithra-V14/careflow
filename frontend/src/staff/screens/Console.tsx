import { useEffect, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { formatActiveRole } from '../../shared/api/client';
import { useAuth } from '../auth/AuthProvider';
import { readStoredGrant, storeGrant } from '../activeRole';
import { resetClinicTimeZone, setClinicTimeZone } from '../../shared/clinicTime';
import { parseMe, type Account, type Grant } from '../me';
import { SessionProvider } from '../session/SessionContext';
import { DevSpikeSlot } from '../components/DevSpikeSlot';
import { Shell } from './Shell';
import { Loading, Message } from './Message';
import { RolePicker } from './RolePicker';
import { ActivateMe } from './ActivateMe';

class AccountUnavailable extends Error {}

/** Keeps the API client's "active role" cell in step with the chosen grant. The header it feeds is off by default. */
function ActiveRoleSync({ grant, narrow }: { grant: Grant; narrow: boolean }) {
  const { setActiveRole } = useAuth();
  useEffect(() => {
    // A person with ONE grant has nothing to narrow, so no header is ever sent for them.
    setActiveRole(narrow ? formatActiveRole(grant.clinicId, grant.role) : null);
    return () => setActiveRole(null);
  }, [grant, narrow, setActiveRole]);
  return null;
}

export function Console() {
  const { t } = useTranslation();
  const { api, signOut, activeRoleRefused, acknowledgeRoleRefusal } = useAuth();
  const queryClient = useQueryClient();

  // The backend refused the header (the role was revoked, or is not held here): forget the remembered choice and re-read the
  // grants so the picker offers what is true now. The person stays signed in.
  useEffect(() => {
    if (!activeRoleRefused) return;
    storeGrant(null);
    void queryClient.invalidateQueries({ queryKey: ['me'] });
  }, [activeRoleRefused, queryClient]);

  const me = useQuery({
    queryKey: ['me'],
    retry: false,
    queryFn: async (): Promise<Account> => {
      const res = await api.getMe();
      if (res.data) {
        // The clinic's own time zone, when the backend sends one; otherwise the labelled demo placeholder stays.
        const zone = res.data.clinic_time_zone;
        if (typeof zone !== 'string' || !setClinicTimeZone(zone)) resetClinicTimeZone();
        return parseMe(res.data);
      }
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
      <>
        <Message id="unavailable-title" title={t('app.staffTitle')} alert>
          <p>{t('staff.account.unavailable')}</p>
          <button type="button" onClick={() => void signOut()}>
            {t('staff.account.signOut')}
          </button>
        </Message>
        <ActivateMe />
      </>
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
  const refused = activeRoleRefused && account.grants.length > 1;
  const active = refused ? null : (only ?? (picked === undefined ? readStoredGrant(account.grants) : picked));

  if (!active) {
    return (
      <RolePicker
        grants={account.grants}
        notice={refused ? t('shell.activeRoleUnavailable') : null}
        onPick={(g) => {
          storeGrant(g);
          setPicked(g);
          acknowledgeRoleRefusal();
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
      <ActiveRoleSync grant={active} narrow={account.grants.length > 1} />
      <Shell />
    </SessionProvider>
  );
}
