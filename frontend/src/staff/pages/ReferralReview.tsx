import { useId, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import type { ReviewDecision } from '../../shared/api/client';
import { unwrap } from '../apiState';
import { useAuth } from '../auth/AuthProvider';
import { useIdempotentWrite } from '../hooks';
import { ConfirmDialog } from '../ui/ConfirmDialog';
import { PlaceholderNote } from '../ui/bits';
import { NotAvailableToRole, ProblemText } from '../ui/states';

// The three words the review route accepts (ReviewIn.decision). Mirrored here and pinned to the schema by
// tests/f4-schema.test.ts. Shown in the order the API lists them; none is marked or pre-selected.
export const DECISIONS: readonly ReviewDecision[] = ['approve', 'edit', 'reject'];

/**
 * A reviewer's decision on a triage proposal (nurse and doctor). Nothing is chosen for the person, nothing is written
 * until they confirm, and the reasons the clinician will eventually define appear only as a marked placeholder: the
 * route takes a decision and nothing else, so no reason is collected or sent.
 *
 * The result is reported through `onDone`: once recorded the referral reads as decided and the page stops rendering this form,
 * so a message held in here would vanish with it.
 */
export function ReferralReview({ documentId, onDone }: { documentId: string; onDone: () => void }) {
  const { t } = useTranslation();
  const { api } = useAuth();
  const queryClient = useQueryClient();
  const id = useId();
  const [choice, setChoice] = useState<ReviewDecision | null>(null);
  const [confirming, setConfirming] = useState(false);

  const record = useIdempotentWrite(
    (decision: ReviewDecision, key) => unwrap(api.reviewReferral(documentId, decision, { idempotencyKey: key })),
    () => {
      setConfirming(false);
      onDone();
      void queryClient.invalidateQueries();
    },
  );

  const problem = record.error;
  return (
    <>
      <h2>{t('referrals.review.title')}</h2>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          if (choice) setConfirming(true);
        }}
      >
        <p className="hint">{t('referrals.review.help')}</p>
        <fieldset>
          <legend>{t('referrals.review.legend')}</legend>
          {DECISIONS.map((d) => (
            <label key={d} className="choice">
              <input
                type="radio"
                name={id}
                value={d}
                checked={choice === d}
                onChange={() => {
                  setChoice(d);
                  record.reset(); // a different decision is a different request: never reuse the old key
                }}
              />
              <span>{t(`referrals.decision.${d}`)}</span>
            </label>
          ))}
        </fieldset>
        <h3>{t('referrals.review.reasons')}</h3>
        <PlaceholderNote>{t('placeholder.reviewReasons')}</PlaceholderNote>
        <button type="submit" className="primary" disabled={choice === null}>
          {t('referrals.review.record')}
        </button>
      </form>

      {confirming && choice && (
        <ConfirmDialog
          title={t('referrals.review.confirmTitle')}
          confirmLabel={t('referrals.review.record')}
          busy={record.isPending}
          onConfirm={() => record.mutate(choice)}
          onCancel={() => {
            record.reset();
            setConfirming(false);
          }}
          status={
            problem &&
            (problem.kind === 'forbidden' ? (
              <NotAvailableToRole />
            ) : (
              <p role="alert">{problem.kind === 'conflict' ? t('referrals.review.conflict') : <ProblemText kind={problem.kind} />}</p>
            ))
          }
        >
          <p>{t('referrals.review.confirmBody', { decision: t(`referrals.decision.${choice}`) })}</p>
        </ConfirmDialog>
      )}
    </>
  );
}
