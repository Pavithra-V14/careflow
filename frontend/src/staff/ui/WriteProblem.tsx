import type { ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import type { ApiProblem, ProblemKind } from '../apiState';
import { NotAvailableToRole, ProblemText } from './states';

/**
 * How a failed write is told inside its dialog. 403 is the shared "not available to your role"; any kind in `calm` is a normal
 * outcome of the system working (something changed, a switch is off, a rule refused) and is a calm status, not an alert; anything
 * else is the fixed message for its kind. The backend's own text is never shown.
 */
export function WriteProblem({ problem, calm = {}, forbidden }: { problem: ApiProblem | null; calm?: Partial<Record<ProblemKind, ReactNode>>; forbidden?: ReactNode }) {
  const { t } = useTranslation();
  if (!problem) return null;
  const message = calm[problem.kind];
  if (message) return <p role="status">{message}</p>;
  if (problem.kind === 'forbidden') return forbidden ? <p role="status">{forbidden}</p> : <NotAvailableToRole />;
  if (problem.kind === 'conflict') return <p role="status">{t('scheduling.conflict')}</p>;
  return (
    <p role="alert">
      <ProblemText kind={problem.kind} />
    </p>
  );
}
