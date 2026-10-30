import type { ReactNode } from 'react';
import type { UseQueryResult } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { ApiProblem, toProblem, type ProblemKind } from '../apiState';
import { Loading } from '../screens/Message';

/** "Not available to your role": the API answered 403. Shown as a state, never as an error, and says nothing more. */
export function NotAvailableToRole() {
  const { t } = useTranslation();
  return (
    <div role="status" className="state state-forbidden">
      <p>{t('problem.forbidden')}</p>
    </div>
  );
}

export function EmptyState({ children }: { children: ReactNode }) {
  return (
    <div role="status" className="state state-empty">
      <p>{children}</p>
    </div>
  );
}

/** The one place a problem becomes words. Fixed text per kind; the backend's body is never shown. */
export function ProblemText({ kind }: { kind: ProblemKind }) {
  const { t } = useTranslation();
  return <>{t(`problem.${kind}`)}</>;
}

export function ProblemView({ problem, onRetry }: { problem: ApiProblem; onRetry?: (() => void) | undefined }) {
  const { t } = useTranslation();
  if (problem.kind === 'forbidden') return <NotAvailableToRole />;
  const retryable = problem.kind === 'network' || problem.kind === 'unavailable' || problem.kind === 'rate_limited' || problem.kind === 'unknown';
  return (
    <div role="alert" className="state state-error">
      <p>
        <ProblemText kind={problem.kind} />
      </p>
      {onRetry && retryable && (
        <button type="button" onClick={onRetry}>
          {t('problem.retry')}
        </button>
      )}
    </div>
  );
}

/** Loading, error (with retry), "not available to your role", empty and data: every list and detail uses this. */
export function QueryView<T>({
  query,
  isEmpty,
  empty,
  children,
}: {
  query: UseQueryResult<T, ApiProblem>;
  isEmpty?: (data: T) => boolean;
  empty?: ReactNode;
  children: (data: T) => ReactNode;
}) {
  const { t } = useTranslation();
  if (query.isPending) return <Loading label={t('common.loading')} />;
  if (query.isError) return <ProblemView problem={toProblem(query.error)} onRetry={() => void query.refetch()} />;
  if (isEmpty?.(query.data)) return <EmptyState>{empty}</EmptyState>;
  return <>{children(query.data)}</>;
}
