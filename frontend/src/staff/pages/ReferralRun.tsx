import { useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { unwrap } from '../apiState';
import { useAuth } from '../auth/AuthProvider';
import { useIdempotentWrite } from '../hooks';
import { ConfirmDialog } from '../ui/ConfirmDialog';
import { ShortId } from '../ui/bits';
import { WriteProblem } from '../ui/WriteProblem';

/**
 * Start the Front Door run for a referral document (front desk, nurse, doctor; the backend decides). It queues a run and nothing else:
 * the console shows the job that was queued, or that the same request had already queued one (a replay). The run's own result appears
 * on this page as the referral's state, and any red flag it finds arrives as a task, never from here.
 */
export function ReferralRun({ documentId }: { documentId: string }) {
  const { t } = useTranslation();
  const { api } = useAuth();
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [queued, setQueued] = useState<{ jobId: string; replayed: boolean } | null>(null);
  const run = useIdempotentWrite(
    (_: void, key) => unwrap(api.startReferralRun(documentId, { idempotencyKey: key })),
    (r) => {
      setQueued({ jobId: r.job_id, replayed: r.replayed });
      setOpen(false);
      void queryClient.invalidateQueries();
    },
  );
  return (
    <>
      <div className="toolbar">
        <button
          type="button"
          onClick={() => {
            setQueued(null);
            setOpen(true);
          }}
        >
          {t('referrals.run.button')}
        </button>
      </div>
      {queued && (
        <p role="status" className="state state-ok">
          {t(queued.replayed ? 'referrals.run.replayed' : 'referrals.run.queued')} <ShortId id={queued.jobId} />
        </p>
      )}
      {open && (
        <ConfirmDialog
          title={t('referrals.run.title')}
          confirmLabel={t('referrals.run.button')}
          busy={run.isPending}
          onConfirm={() => run.mutate()}
          onCancel={() => {
            run.reset();
            setOpen(false);
          }}
          status={<WriteProblem problem={run.error} calm={{ unavailable: t('referrals.run.unavailable'), not_found: t('referrals.run.notFound') }} />}
        >
          <p>{t('referrals.run.body')}</p>
        </ConfirmDialog>
      )}
    </>
  );
}
