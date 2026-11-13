import { useState } from 'react';
import { NavLink, useParams } from 'react-router';
import { useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import type { AutonomyDecision, StaffRole } from '../../shared/api/client';
import type { components } from '../../shared/api/schema';
import { unwrap } from '../apiState';
import { useAuth } from '../auth/AuthProvider';
import { useApiQuery, useIdempotentWrite } from '../hooks';
import { ADMIN_TABS } from '../sections';
import { useSession } from '../session/SessionContext';
import { ConfirmDialog } from '../ui/ConfirmDialog';
import { Badge, PageHeading, ShortId, Time, useWord } from '../ui/bits';
import { QueryView } from '../ui/states';
import { WriteProblem } from '../ui/WriteProblem';
import { NotFoundPage } from './NotFoundPage';
import { CostCanvas, ReviewerStats, VoicePanel } from './AdminReports';
import { Grievances, Recovery } from './AdminMore';
import { RedFlagRules } from './RedFlagRules';

type Account = components['schemas']['StaffAccountOut'];

/** The roles an admin can invite or grant (the schema's list; queue_worker cannot be granted). None is pre-selected. */
const STAFF_ROLES: readonly StaffRole[] = ['front_desk', 'front_desk_supervisor', 'nurse', 'doctor', 'admin', 'auditor', 'business_owner'];
const isStaffRole = (v: string): v is StaffRole => (STAFF_ROLES as readonly string[]).includes(v);

export function AdminPage() {
  const { t } = useTranslation();
  const { active } = useSession();
  const { tab = '' } = useParams();
  const tabs = ADMIN_TABS.filter((x) => (x.roles as readonly string[]).includes(active.role));
  const current = tab === '' ? tabs[0] : ADMIN_TABS.find((x) => x.id === tab);
  if (!current) return <NotFoundPage />;
  return (
    <section aria-labelledby="admin-title">
      <PageHeading id="admin-title">{t('admin.title')}</PageHeading>
      <nav aria-label={t('admin.tabs')} className="sub-nav">
        <ul>
          {tabs.map((x) => (
            <li key={x.id}>
              <NavLink to={`/admin/${x.id}`}>{t(`admin.tab.${x.id}`)}</NavLink>
            </li>
          ))}
        </ul>
      </nav>
      <h2>{t(`admin.tab.${current.id}`)}</h2>
      {current.id === 'accounts' && <Accounts />}
      {current.id === 'autonomy' && <Autonomy />}
      {current.id === 'rules' && <RedFlagRules />}
      {current.id === 'reviewer' && <ReviewerStats />}
      {current.id === 'cost' && <CostCanvas />}
      {current.id === 'voice' && <VoicePanel />}
      {current.id === 'recovery' && <Recovery />}
      {current.id === 'grievances' && <Grievances />}
    </section>
  );
}

// ---- staff accounts --------------------------------------------------------------------------------------------------------------

type AccountAction = { kind: 'suspend' | 'reinstate' | 'grant' | 'revoke'; account: Account } | { kind: 'invite' };

function Accounts() {
  const { t } = useTranslation();
  const { api } = useAuth();
  const { active } = useSession();
  const word = useWord();
  const [acting, setActing] = useState<AccountAction | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const query = useApiQuery(['accounts'], () => unwrap(api.listAccounts(active.clinicId)));
  return (
    <>
      <p className="hint">{t('admin.accounts.note')}</p>
      <div className="toolbar">
        <button type="button" onClick={() => void query.refetch()} disabled={query.isFetching}>
          {t('common.refresh')}
        </button>
        <button
          type="button"
          onClick={() => {
            setNote(null);
            setActing({ kind: 'invite' });
          }}
        >
          {t('admin.accounts.invite')}
        </button>
      </div>
      {note && <p role="status">{note}</p>}
      <QueryView query={query} isEmpty={(d) => d.items.length === 0} empty={t('admin.accounts.empty')}>
        {(d) => (
          <>
            <table>
              <caption className="visually-hidden">{t('admin.tab.accounts')}</caption>
              <thead>
                <tr>
                  <th scope="col">{t('admin.accounts.col.account')}</th>
                  <th scope="col">{t('admin.accounts.col.status')}</th>
                  <th scope="col">{t('admin.accounts.col.roles')}</th>
                  <th scope="col">{t('admin.accounts.col.action')}</th>
                </tr>
              </thead>
              <tbody>
                {d.items.map((a) => (
                  <tr key={a.account_id}>
                    <th scope="row">
                      <ShortId id={a.account_id} />
                    </th>
                    <td>
                      <Badge tone={a.status === 'active' ? 'ok' : a.status === 'suspended' ? 'warn' : 'neutral'}>{word('admin.accounts.status', a.status)}</Badge>
                    </td>
                    <td>{a.roles.length === 0 ? '–' : a.roles.map((r) => word('roles', r)).join(', ')}</td>
                    <td>
                      <RowButtons account={a} onAct={(kind) => { setNote(null); setActing({ kind, account: a }); }} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {d.has_more && <p className="hint">{t('common.moreExist')}</p>}
          </>
        )}
      </QueryView>
      {acting && (
        <AccountDialog
          action={acting}
          onClose={(done) => {
            setActing(null);
            if (done) setNote(done);
          }}
        />
      )}
    </>
  );
}

function RowButtons({ account, onAct }: { account: Account; onAct: (kind: 'suspend' | 'reinstate' | 'grant' | 'revoke') => void }) {
  const { t } = useTranslation();
  const short = account.account_id.slice(0, 8);
  const btn = (kind: 'suspend' | 'reinstate' | 'grant' | 'revoke') => (
    <button key={kind} type="button" aria-label={`${t(`admin.accounts.${kind}`)} ${short}`} onClick={() => onAct(kind)}>
      {t(`admin.accounts.${kind}`)}
    </button>
  );
  return (
    <>
      {account.status === 'active' && btn('suspend')} {account.status === 'suspended' && btn('reinstate')} {btn('grant')} {account.roles.some(isStaffRole) && btn('revoke')}
    </>
  );
}

function AccountDialog({ action, onClose }: { action: AccountAction; onClose: (done: string | null) => void }) {
  const { t } = useTranslation();
  const { api } = useAuth();
  const { active } = useSession();
  const word = useWord();
  const queryClient = useQueryClient();
  const [email, setEmail] = useState('');
  const [role, setRole] = useState<StaffRole | ''>('');
  const kind = action.kind;
  const roleChoices = kind === 'revoke' ? action.account.roles.filter(isStaffRole) : STAFF_ROLES;
  const needsRole = kind === 'invite' || kind === 'grant' || kind === 'revoke';
  const valid = (kind !== 'invite' || /^\S+@\S+\.\S+$/.test(email.trim())) && (!needsRole || role !== '');

  const write = useIdempotentWrite(
    async (_: void, key) => {
      const o = { idempotencyKey: key };
      if (action.kind === 'invite') await unwrap(api.inviteAccount({ clinic_id: active.clinicId, email: email.trim(), role: role as StaffRole }, o));
      else if (action.kind === 'suspend') await unwrap(api.suspendAccount(action.account.account_id, o));
      else if (action.kind === 'reinstate') await unwrap(api.reinstateAccount(action.account.account_id, o));
      else if (action.kind === 'grant') await unwrap(api.grantRole(action.account.account_id, { clinic_id: active.clinicId, role: role as StaffRole }, o));
      else await unwrap(api.revokeRole(action.account.account_id, { clinic_id: active.clinicId, role: role as StaffRole }, o));
    },
    () => {
      void queryClient.invalidateQueries();
      onClose(t(`admin.accounts.done.${kind}`));
    },
  );
  // 503: no sign-in administration is configured, so nothing was sent. 409: the account exists, or it would leave no active administrator.
  const calm = { unavailable: t('admin.accounts.notConfigured'), conflict: t(kind === 'invite' ? 'admin.accounts.exists' : 'admin.accounts.lastAdmin') };
  return (
    <ConfirmDialog
      title={t(`admin.accounts.${kind}Title`)}
      confirmLabel={t(`admin.accounts.${kind}`)}
      busy={write.isPending}
      confirmDisabled={!valid}
      onConfirm={() => write.mutate()}
      onCancel={() => {
        write.reset();
        onClose(null);
      }}
      status={<WriteProblem problem={write.error} calm={calm} forbidden={kind === 'suspend' || kind === 'reinstate' ? t('admin.accounts.cannotChange') : undefined} />}
    >
      <p>{t(`admin.accounts.${kind}Body`)}</p>
      <p className="hint">{t('admin.stepUp')}</p>
      {kind === 'invite' && (
        <>
          <label htmlFor="inv-email">{t('admin.accounts.email')}</label>
          <input
            id="inv-email"
            type="email"
            autoComplete="off"
            spellCheck={false}
            value={email}
            onChange={(e) => {
              setEmail(e.target.value);
              write.reset();
            }}
          />
        </>
      )}
      {needsRole && (
        <label>
          {t('admin.accounts.role')}
          <select
            value={role}
            onChange={(e) => {
              setRole(e.target.value as StaffRole | '');
              write.reset();
            }}
          >
            <option value="">{t('scheduling.choose')}</option>
            {roleChoices.map((r) => (
              <option key={r} value={r}>
                {word('roles', r)}
              </option>
            ))}
          </select>
        </label>
      )}
    </ConfirmDialog>
  );
}

// ---- autonomy: the clinic's mode, and the two-person switch -------------------------------------------------------------------

function Autonomy() {
  const { t } = useTranslation();
  const { api } = useAuth();
  const { active, hasRole, account } = useSession();
  const word = useWord();
  const queryClient = useQueryClient();
  const [setting, setSetting] = useState(false);
  const [proposing, setProposing] = useState(false);
  const [deciding, setDeciding] = useState<{ id: string; decision: AutonomyDecision } | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const mode = useApiQuery(['autonomy'], () => unwrap(api.getAutonomy(active.clinicId)));
  const proposals = useApiQuery(['autonomy-proposals'], () => unwrap(api.listAutonomyProposals(active.clinicId)));
  const canSwitch = hasRole(['admin', 'doctor']);

  const resolve = useIdempotentWrite(
    (v: { id: string; decision: AutonomyDecision }, key) => unwrap(api.resolveAutonomyProposal(v.id, v.decision, { idempotencyKey: key })),
    (r) => {
      setDeciding(null);
      setNote(t(`admin.autonomy.result.${r.result}`));
      void queryClient.invalidateQueries();
    },
  );

  return (
    <>
      <QueryView query={mode}>
        {(d) => (
          <dl className="facts">
            <dt>{t('admin.autonomy.mode')}</dt>
            <dd>
              <Badge tone={d.mode === 'autonomous' ? 'warn' : 'neutral'}>{word('admin.autonomy.modes', d.mode)}</Badge>
              {!d.configured && <> {t('admin.autonomy.neverSet')}</>}
            </dd>
          </dl>
        )}
      </QueryView>
      <div className="toolbar">
        {hasRole(['admin']) && (
          <button
            type="button"
            onClick={() => {
              setNote(null);
              setSetting(true);
            }}
          >
            {t('admin.autonomy.change')}
          </button>
        )}
        {canSwitch && (
          <button
            type="button"
            onClick={() => {
              setNote(null);
              setProposing(true);
            }}
          >
            {t('admin.autonomy.propose')}
          </button>
        )}
      </div>
      {note && <p role="status">{note}</p>}
      <h3>{t('admin.autonomy.pending')}</h3>
      <QueryView query={proposals} isEmpty={(d) => d.proposals.length === 0} empty={t('admin.autonomy.noPending')}>
        {(d) => (
          <table>
            <caption className="visually-hidden">{t('admin.autonomy.pending')}</caption>
            <thead>
              <tr>
                <th scope="col">{t('admin.autonomy.col.proposal')}</th>
                <th scope="col">{t('admin.autonomy.col.by')}</th>
                <th scope="col">{t('admin.autonomy.col.role')}</th>
                <th scope="col">{t('admin.autonomy.col.created')}</th>
                <th scope="col">{t('admin.autonomy.col.action')}</th>
              </tr>
            </thead>
            <tbody>
              {d.proposals.map((p) => {
                // A convenience: approval has to come from a different person holding the OTHER role. The database decides.
                const mine = p.proposed_by === account.accountId;
                const canApprove = !mine && p.proposer_role !== active.role;
                return (
                  <tr key={p.proposal_id}>
                    <th scope="row">
                      <ShortId id={p.proposal_id} />
                    </th>
                    <td>{mine ? t('admin.autonomy.you') : <ShortId id={p.proposed_by} />}</td>
                    <td>{word('roles', p.proposer_role)}</td>
                    <td>
                      <Time value={p.created_at} />
                    </td>
                    <td>
                      {canApprove && (
                        <button type="button" aria-label={`${t('admin.autonomy.approve')} ${p.proposal_id.slice(0, 8)}`} onClick={() => setDeciding({ id: p.proposal_id, decision: 'approve' })}>
                          {t('admin.autonomy.approve')}
                        </button>
                      )}{' '}
                      <button type="button" aria-label={`${t('admin.autonomy.reject')} ${p.proposal_id.slice(0, 8)}`} onClick={() => setDeciding({ id: p.proposal_id, decision: 'reject' })}>
                        {t('admin.autonomy.reject')}
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </QueryView>
      <h3>{t('admin.autonomy.historyTitle')}</h3>
      <p className="hint">{t('admin.autonomy.historyNote')}</p>
      <History />
      {setting && (
        <SetModeDialog
          onClose={(done) => {
            setSetting(false);
            if (done) setNote(t('admin.autonomy.modeSet'));
          }}
        />
      )}
      {proposing && (
        <ProposeDialog
          onClose={(done) => {
            setProposing(false);
            if (done) setNote(t('admin.autonomy.proposed'));
          }}
        />
      )}
      {deciding && (
        <ConfirmDialog
          title={t(deciding.decision === 'approve' ? 'admin.autonomy.approveTitle' : 'admin.autonomy.rejectTitle')}
          confirmLabel={t(deciding.decision === 'approve' ? 'admin.autonomy.approve' : 'admin.autonomy.reject')}
          busy={resolve.isPending}
          onConfirm={() => resolve.mutate(deciding)}
          onCancel={() => {
            resolve.reset();
            setDeciding(null);
          }}
          status={<WriteProblem problem={resolve.error} calm={{ unavailable: t('admin.autonomy.notEnabled') }} />}
        >
          <p>{t('admin.autonomy.decideBody')}</p>
        </ConfirmDialog>
      )}
    </>
  );
}

function History() {
  const { t } = useTranslation();
  const { api } = useAuth();
  const { active } = useSession();
  const word = useWord();
  const query = useApiQuery(['autonomy-history'], () => unwrap(api.autonomyHistory(active.clinicId)));
  return (
    <QueryView query={query} isEmpty={(d) => d.items.length === 0} empty={t('admin.autonomy.historyEmpty')}>
      {(d) => (
        <table>
          <caption className="visually-hidden">{t('admin.autonomy.historyTitle')}</caption>
          <thead>
            <tr>
              <th scope="col">{t('admin.autonomy.col.when')}</th>
              <th scope="col">{t('admin.autonomy.col.what')}</th>
              <th scope="col">{t('admin.autonomy.col.by')}</th>
              <th scope="col">{t('admin.autonomy.col.mode')}</th>
              <th scope="col">{t('admin.autonomy.col.outcome')}</th>
              <th scope="col">{t('admin.autonomy.col.decidedBy')}</th>
            </tr>
          </thead>
          <tbody>
            {d.items.map((h, i) => (
              <tr key={`${h.at}-${i}`}>
                <th scope="row">
                  <Time value={h.at} />
                </th>
                <td>
                  {t(`admin.autonomy.kinds.${h.kind}`)}
                  {h.proposer_role && <> ({word('roles', h.proposer_role)})</>}
                </td>
                <td>
                  <ShortId id={h.by} />
                </td>
                <td>{h.mode ? word('admin.autonomy.modes', h.mode) : '–'}</td>
                <td>{h.outcome ? t(`admin.autonomy.outcomes.${h.outcome}`) : '–'}</td>
                <td>
                  {h.decided_by ? <ShortId id={h.decided_by} /> : '–'}
                  {h.decided_at && (
                    <>
                      {' '}
                      <Time value={h.decided_at} />
                    </>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </QueryView>
  );
}

function SetModeDialog({ onClose }: { onClose: (done: boolean) => void }) {
  const { t } = useTranslation();
  const { api } = useAuth();
  const { active } = useSession();
  const queryClient = useQueryClient();
  const [mode, setMode] = useState<'shadow' | 'assisted' | ''>('');
  const write = useIdempotentWrite(
    (_: void, key) => unwrap(api.setAutonomy({ clinic_id: active.clinicId, mode: mode as 'shadow' | 'assisted' }, { idempotencyKey: key })),
    () => {
      void queryClient.invalidateQueries();
      onClose(true);
    },
  );
  return (
    <ConfirmDialog
      title={t('admin.autonomy.change')}
      confirmLabel={t('admin.autonomy.set')}
      busy={write.isPending}
      confirmDisabled={mode === ''}
      onConfirm={() => write.mutate()}
      onCancel={() => {
        write.reset();
        onClose(false);
      }}
      status={<WriteProblem problem={write.error} />}
    >
      <p>{t('admin.autonomy.changeBody')}</p>
      <p className="hint">{t('admin.stepUp')}</p>
      <label>
        {t('admin.autonomy.mode')}
        <select
          value={mode}
          onChange={(e) => {
            setMode(e.target.value as 'shadow' | 'assisted' | '');
            write.reset();
          }}
        >
          <option value="">{t('scheduling.choose')}</option>
          <option value="shadow">{t('admin.autonomy.modes.shadow')}</option>
          <option value="assisted">{t('admin.autonomy.modes.assisted')}</option>
        </select>
      </label>
    </ConfirmDialog>
  );
}

function ProposeDialog({ onClose }: { onClose: (done: boolean) => void }) {
  const { t } = useTranslation();
  const { api } = useAuth();
  const { active } = useSession();
  const queryClient = useQueryClient();
  // The proposer's role is the role the person is acting as (admin or doctor): it is not asked for again.
  const as = active.role === 'admin' || active.role === 'doctor' ? active.role : null;
  const write = useIdempotentWrite(
    (_: void, key) => unwrap(api.proposeAutonomy(active.clinicId, as, { idempotencyKey: key })),
    () => {
      void queryClient.invalidateQueries();
      onClose(true);
    },
  );
  return (
    <ConfirmDialog
      title={t('admin.autonomy.propose')}
      confirmLabel={t('admin.autonomy.proposeConfirm')}
      busy={write.isPending}
      onConfirm={() => write.mutate()}
      onCancel={() => {
        write.reset();
        onClose(false);
      }}
      status={<WriteProblem problem={write.error} calm={{ unavailable: t('admin.autonomy.notEnabled'), conflict: t('admin.autonomy.alreadyPending') }} />}
    >
      <p>{t('admin.autonomy.proposeBody', { role: t(`roles.${as ?? 'admin'}`) })}</p>
    </ConfirmDialog>
  );
}
