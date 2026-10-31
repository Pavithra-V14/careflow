import { useState } from 'react';
import { Link, useParams } from 'react-router';
import { useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import type { components } from '../../shared/api/schema';
import { unwrap, type ApiProblem } from '../apiState';
import { useAuth } from '../auth/AuthProvider';
import { useApiQuery, useIdempotentWrite } from '../hooks';
import { mergeActionsEnabled } from '../featureFlags';
import { CAN_MERGE_ACTIONS } from '../sections';
import { useSession } from '../session/SessionContext';
import { ConfirmDialog } from '../ui/ConfirmDialog';
import { Badge, ComingSoon, PageHeading, ShortId, Time, UUID } from '../ui/bits';
import { NotAvailableToRole, ProblemText, QueryView } from '../ui/states';
import { NotFoundPage } from './NotFoundPage';

type Merge = components['schemas']['MergeDetailOut'];

export function MergeDetailPage() {
  const { mergeId = '' } = useParams();
  return UUID.test(mergeId) ? <Detail mergeId={mergeId} /> : <NotFoundPage />;
}

function Who({ id, me }: { id: string | null; me: string }) {
  const { t } = useTranslation();
  if (!id) return <>–</>;
  return id === me ? <>{t('tasks.you')}</> : <ShortId id={id} />;
}

function Detail({ mergeId }: { mergeId: string }) {
  const { t } = useTranslation();
  const { api } = useAuth();
  const { account } = useSession();
  const query = useApiQuery(['merge', mergeId], () => unwrap(api.openMerge(mergeId)));
  const me = account.accountId;

  return (
    <section aria-labelledby="merge-title">
      <PageHeading id="merge-title">{t('identity.detail')}</PageHeading>
      <p>
        <Link to="/identity/merges">{t('identity.back')}</Link>
      </p>
      <QueryView query={query}>
        {(m) => (
          <>
            <dl className="facts">
              <dt>{t('identity.col.merge')}</dt>
              <dd>
                <code>{m.merge_id}</code>
              </dd>
              <dt>{t('identity.col.status')}</dt>
              <dd>
                <Badge tone={m.status === 'active' ? 'ok' : m.status === 'unmerged' ? 'neutral' : 'warn'}>{t(`identity.mergeStatus.${m.status}`)}</Badge>
              </dd>
              <dt>{t('identity.col.master')}</dt>
              <dd>
                <Link to={`/patients/${m.master_patient_id}`}>
                  <code>{m.master_patient_id}</code>
                </Link>
              </dd>
              <dt>{t('identity.col.subordinate')}</dt>
              <dd>
                <Link to={`/patients/${m.subordinate_patient_id}`}>
                  <code>{m.subordinate_patient_id}</code>
                </Link>
              </dd>
              <dt>{t('identity.col.candidate')}</dt>
              <dd>
                <ShortId id={m.candidate_id} />
              </dd>
              <dt>{t('identity.col.proposedBy')}</dt>
              <dd>
                <Who id={m.proposed_by} me={me} />
              </dd>
              <dt>{t('identity.col.approvedBy')}</dt>
              <dd>
                <Who id={m.second_approved_by} me={me} />
              </dd>
              <dt>{t('identity.col.created')}</dt>
              <dd>
                <Time value={m.created_at} />
              </dd>
              <dt>{t('identity.col.activated')}</dt>
              <dd>
                <Time value={m.activated_at} />
              </dd>
              <dt>{t('identity.col.unmerged')}</dt>
              <dd>
                <Time value={m.unmerged_at} />
              </dd>
              <dt>{t('identity.col.unmergedBy')}</dt>
              <dd>
                <Who id={m.unmerged_by} me={me} />
              </dd>
            </dl>

            <h2>{t('identity.actions')}</h2>
            {mergeActionsEnabled() ? (
              <MergeActions merge={m} mergeId={mergeId} />
            ) : (
              // The actions exist behind a flag that is OFF until the step-up claim spike is recorded.
              <p>
                {t('identity.actionsSoon')} <ComingSoon />
              </p>
            )}
          </>
        )}
      </QueryView>
    </section>
  );
}

function problemNote(problem: ApiProblem, conflict: string) {
  return problem.kind === 'forbidden' ? <NotAvailableToRole /> : <p role="alert">{problem.kind === 'conflict' ? conflict : <ProblemText kind={problem.kind} />}</p>;
}

/**
 * Approve and unmerge. BEHIND THE mergeActions FLAG (off): both routes need a fresh second factor, and the `aal` / `amr`
 * claim shape the backend checks has not been seen in a real token yet. A 401 with `WWW-Authenticate: step-up` opens the
 * step-up dialog and the request is retried once with the same idempotency key (src/shared/api/client.ts). Nothing is
 * written before a confirmation, and the buttons only offer what this role and this merge could possibly allow; the
 * backend decides.
 */
function MergeActions({ merge, mergeId }: { merge: Merge; mergeId: string }) {
  const { t } = useTranslation();
  const { api } = useAuth();
  const { account, hasRole } = useSession();
  const queryClient = useQueryClient();
  const [dialog, setDialog] = useState<'approve' | 'unmerge' | null>(null);
  const [result, setResult] = useState<string | null>(null);

  const done = (message: string) => {
    setResult(message);
    setDialog(null);
    void queryClient.invalidateQueries();
  };
  const approve = useIdempotentWrite((_: void, key) => unwrap(api.approveMerge(mergeId, { idempotencyKey: key })), () => done(t('identity.approved')));
  const unmerge = useIdempotentWrite(
    (_: void, key) => unwrap(api.unmerge(mergeId, { idempotencyKey: key })),
    // decisions_flagged counts bookings, holds, waitlist entries and match-review rows recorded while the link was active (capped at
    // 500 per kind by the backend). A review task lists them and nothing is undone: the wording says exactly that.
    (r) => done(r.decisions_flagged > 0 ? `${t('identity.unmerged')} ${t('identity.flagged', { n: r.decisions_flagged })}` : t('identity.unmerged')),
  );

  if (!hasRole(CAN_MERGE_ACTIONS)) return <NotAvailableToRole />;

  const canApprove = merge.status === 'awaiting_second_approval';
  const ownProposal = merge.proposed_by === account.accountId;
  const canUnmerge = merge.status !== 'unmerged';
  const close = () => {
    approve.reset();
    unmerge.reset();
    setDialog(null);
  };

  return (
    <>
      {result && (
        <p role="status" className="state state-ok">
          {result}
        </p>
      )}
      {!canApprove && !canUnmerge && <p className="state state-empty">{t('identity.none')}</p>}
      <div className="toolbar">
        {canApprove && !ownProposal && (
          <button type="button" onClick={() => setDialog('approve')}>
            {t('identity.approve')}
          </button>
        )}
        {canUnmerge && (
          <button type="button" onClick={() => setDialog('unmerge')}>
            {t('identity.unmerge')}
          </button>
        )}
      </div>
      {canApprove && ownProposal && <p className="hint">{t('identity.differentPerson')}</p>}

      {dialog === 'approve' && (
        <ConfirmDialog
          title={t('identity.approveTitle')}
          confirmLabel={t('identity.approve')}
          busy={approve.isPending}
          onConfirm={() => approve.mutate()}
          onCancel={close}
          status={approve.error ? problemNote(approve.error, t('identity.approveConflict')) : undefined}
        >
          <p>{t('identity.approveBody')}</p>
        </ConfirmDialog>
      )}
      {dialog === 'unmerge' && (
        <ConfirmDialog
          title={t('identity.unmergeTitle')}
          confirmLabel={t('identity.unmerge')}
          busy={unmerge.isPending}
          onConfirm={() => unmerge.mutate()}
          onCancel={close}
          status={unmerge.error ? problemNote(unmerge.error, t('identity.unmergeConflict')) : undefined}
        >
          <p>{t('identity.unmergeBody')}</p>
        </ConfirmDialog>
      )}
    </>
  );
}
