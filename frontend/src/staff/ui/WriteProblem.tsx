import type { ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import type { ApiProblem, ProblemKind } from '../apiState';
import { NotAvailableToRole, ProblemText } from './states';

/**
 * How a failed write is told inside its dialog. 403 is the shared "not available to your role"; any kind in `calm` is a normal
 * outcome of the system working (something changed, a switch is off, a rule refused) and is a calm status, not an alert; anything
 * else is the fixed message for its kind. The backend's own text is never shown.
 */
export function WriteProblem({ problem, calm = {}, forbidden, details = {} }: { problem: ApiProblem | null; calm?: Partial<Record<ProblemKind, ReactNode>>; forbidden?: ReactNode; details?: Record<string, ReactNode> }) {
  const { t } = useTranslation();
  if (!problem) return null;
  // A backend phrase the screen knows, said in the screen's own plain words (the phrase itself is never shown).
  const known = problem.detail !== null ? details[problem.detail] : undefined;
  if (known) return <p role="status">{known}</p>;
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
