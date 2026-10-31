import { useId, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import type { RevokeReason } from '../../shared/api/client';
import type { components } from '../../shared/api/schema';
import { unwrap } from '../apiState';
import { useAuth } from '../auth/AuthProvider';
import { useIdempotentWrite } from '../hooks';
import { ConfirmDialog } from '../ui/ConfirmDialog';
import { PlaceholderNote } from '../ui/bits';
import { NotAvailableToRole, ProblemText } from '../ui/states';

// The reasons the route accepts (RevokeIdentityIn.reason), in the API's order. Pinned to the schema by tests/f4-schema.test.ts.
// None is pre-selected. Their wording is not final (docs/design/identity-confirmation-revoke.md leaves the list open), so a
// placeholder note says so. No step-up: the route's doc says a revoke only moves a record towards "a person decides".
export const REVOKE_REASONS: readonly RevokeReason[] = ['wrong_person', 'planted_or_suspect', 'mistaken_click'];

type TaskOutcome = components['schemas']['RevokeIdentityOut']['review_task'];
const isOutcome = (v: unknown): v is TaskOutcome => v === 'raised' || v === 'not_configured' || v === 'on_record';

export type RevokeOutcome = TaskOutcome | 'unknown';

/**
 * The result is reported through `onDone`, not shown here: after a revoke the record is no longer verified, so the page stops
 * rendering this control, and a message held inside it would vanish with it.
 */
export function IdentityRevoke({ patientId, onDone }: { patientId: string; onDone: (outcome: RevokeOutcome) => void }) {
  const { t } = useTranslation();
  const { api } = useAuth();
  const queryClient = useQueryClient();
  const id = useId();
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState<RevokeReason | null>(null);

  const revoke = useIdempotentWrite(
    (r: RevokeReason, key) => unwrap(api.revokeIdentity(patientId, r, { idempotencyKey: key })),
    (result) => {
      onDone(isOutcome(result.review_task) ? result.review_task : 'unknown'); // an unknown word is never read as "raised"
      setOpen(false);
      void queryClient.invalidateQueries();
    },
  );

  const problem = revoke.error;
  return (
    <>
      <div className="toolbar">
        <button
          type="button"
          onClick={() => {
            setReason(null);
            setOpen(true);
          }}
        >
          {t('patients.revoke.button')}
        </button>
      </div>

      {open && (
        <ConfirmDialog
          title={t('patients.revoke.title')}
          confirmLabel={t('patients.revoke.button')}
          busy={revoke.isPending}
          confirmDisabled={reason === null}
          onConfirm={() => reason && revoke.mutate(reason)}
          onCancel={() => {
            revoke.reset();
            setOpen(false);
          }}
          status={
            problem &&
            (problem.kind === 'forbidden' ? (
              <NotAvailableToRole />
            ) : (
              <p role="alert">{problem.kind === 'conflict' ? t('patients.revoke.conflict') : <ProblemText kind={problem.kind} />}</p>
            ))
          }
        >
          <p>{t('patients.revoke.body')}</p>
          <fieldset>
            <legend>{t('patients.revoke.reason')}</legend>
            {REVOKE_REASONS.map((r) => (
              <label key={r} className="choice">
                <input
                  type="radio"
                  name={id}
                  value={r}
                  checked={reason === r}
                  onChange={() => {
                    setReason(r);
                    revoke.reset(); // another reason is another request: a new key
                  }}
                />
                <span>{t(`patients.revoke.reasons.${r}`)}</span>
              </label>
            ))}
          </fieldset>
          {reason === null && <p className="hint">{t('patients.revoke.chooseFirst')}</p>}
          <PlaceholderNote>{t('placeholder.revokeReasons')}</PlaceholderNote>
        </ConfirmDialog>
      )}
    </>
  );
}
