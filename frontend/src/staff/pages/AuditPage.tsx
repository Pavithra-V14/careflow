import { useId, useState, type FormEvent } from 'react';
import { useMutation } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import type { AuditFilter } from '../../shared/api/client';
import type { components } from '../../shared/api/schema';
import { ApiProblem, toProblem, unwrap } from '../apiState';
import { useAuth } from '../auth/AuthProvider';
import { useSession } from '../session/SessionContext';
import { PageHeading, ShortId, Time, UUID, useWord } from '../ui/bits';
import { EmptyState, NotAvailableToRole, ProblemText } from '../ui/states';
import { CAN_READ_AUDIT } from '../sections';

type Row = components['schemas']['AuditRowOut'];
type Page = components['schemas']['AuditList'];

const PAGE = 50;

/** A value from `details`, as plain text. Objects are shown as JSON; nothing is ever interpreted as markup. */
function plain(v: unknown): string {
  if (v === null || v === undefined) return '–';
  if (typeof v === 'string') return v;
  if (typeof v === 'number' || typeof v === 'boolean') return String(v);
  return JSON.stringify(v);
}

/** A date-time field to an ISO instant, or undefined when blank or unreadable (a blank bound is no bound). */
function instant(v: string): string | undefined {
  if (v === '') return undefined;
  const d = new Date(v);
  return Number.isNaN(d.getTime()) ? undefined : d.toISOString();
}

/**
 * The audit log (auditor). Newest first; every row is shown as the backend sent it (who, role, what, which record, when), and the
 * `details` of a row only on a click. Nothing here is written, and nothing is kept: leaving the page drops the rows. The clinics are
 * the ones the account audits, so each row names its clinic. Read-only, so there is no confirmation and no idempotency key.
 */
export function AuditPage() {
  const { t } = useTranslation();
  const { hasRole } = useSession();
  return (
    <section aria-labelledby="audit-title">
      <PageHeading id="audit-title">{t('audit.title')}</PageHeading>
      <p className="hint">{t('audit.note')}</p>
      {hasRole(CAN_READ_AUDIT) ? <Reader /> : <NotAvailableToRole />}
    </section>
  );
}

function Reader() {
  const { t } = useTranslation();
  const { api } = useAuth();
  const { active } = useSession();
  const id = useId();
  const word = useWord();
  const [action, setAction] = useState('');
  const [resourceType, setResourceType] = useState('');
  const [resourceId, setResourceId] = useState('');
  const [actorId, setActorId] = useState('');
  const [since, setSince] = useState('');
  const [until, setUntil] = useState('');
  const [idError, setIdError] = useState(false);
  // The rows read so far for the current filter. Older pages are appended; a new search starts again.
  const [rows, setRows] = useState<Row[] | null>(null);
  const [more, setMore] = useState<{ has: boolean; before: number | null }>({ has: false, before: null });
  const [filter, setFilter] = useState<AuditFilter>({});

  const read = useMutation<Page, ApiProblem, { f: AuditFilter; append: boolean }>({
    gcTime: 0,
    retry: false,
    mutationFn: async ({ f }) => {
      try {
        return await unwrap(api.readAudit({ ...f, clinicId: active.clinicId, limit: PAGE }));
      } catch (e) {
        throw toProblem(e);
      }
    },
    onSuccess: (page, { append }) => {
      setRows((prev) => (append && prev ? [...prev, ...page.items] : page.items));
      setMore({ has: page.has_more, before: page.next_before_id ?? null });
    },
  });

  function submit(e: FormEvent) {
    e.preventDefault();
    // The actor is always a uuid; a resource id is free (it may be a uuid or another key), so only the actor is checked.
    const actor = actorId.trim().toLowerCase();
    const resource = resourceId.trim();
    if (actor !== '' && !UUID.test(actor)) return setIdError(true);
    setIdError(false);
    const f: AuditFilter = {
      ...(action.trim() ? { action: action.trim() } : {}),
      ...(resourceType.trim() ? { resourceType: resourceType.trim() } : {}),
      ...(resource ? { resourceId: resource } : {}),
      ...(actor ? { actorId: actor } : {}),
      ...(instant(since) ? { since: instant(since) as string } : {}),
      ...(instant(until) ? { until: instant(until) as string } : {}),
    };
    setFilter(f);
    setRows(null);
    read.mutate({ f, append: false });
  }

  return (
    <>
      <form onSubmit={submit} noValidate autoComplete="off">
        <p className="hint">{t('audit.filterHint')}</p>
        <label htmlFor={`${id}-action`}>{t('audit.action')}</label>
        <input id={`${id}-action`} type="text" autoComplete="off" spellCheck={false} value={action} onChange={(e) => setAction(e.target.value)} />
        <label htmlFor={`${id}-rt`}>{t('audit.resourceType')}</label>
        <input id={`${id}-rt`} type="text" autoComplete="off" spellCheck={false} value={resourceType} onChange={(e) => setResourceType(e.target.value)} />
        <label htmlFor={`${id}-rid`}>{t('audit.resourceId')}</label>
        <input id={`${id}-rid`} type="text" autoComplete="off" spellCheck={false} value={resourceId} onChange={(e) => setResourceId(e.target.value)} />
        <label htmlFor={`${id}-actor`}>{t('audit.actor')}</label>
        <input
          id={`${id}-actor`}
          type="text"
          autoComplete="off"
          spellCheck={false}
          value={actorId}
          onChange={(e) => setActorId(e.target.value)}
          aria-invalid={idError || undefined}
          aria-describedby={idError ? `${id}-actor-err` : undefined}
        />
        {idError && (
          <p id={`${id}-actor-err`} role="alert" className="form-error">
            {t('audit.actorInvalid')}
          </p>
        )}
        <label htmlFor={`${id}-since`}>{t('audit.since')}</label>
        <input id={`${id}-since`} type="datetime-local" value={since} onChange={(e) => setSince(e.target.value)} />
        <label htmlFor={`${id}-until`}>{t('audit.until')}</label>
        <input id={`${id}-until`} type="datetime-local" value={until} onChange={(e) => setUntil(e.target.value)} />
        <p className="hint">{t('audit.zoneHint')}</p>
        <button type="submit" className="primary" disabled={read.isPending}>
          {read.isPending && rows === null ? t('common.working') : t('audit.read')}
        </button>
      </form>

      {read.isPending && rows === null && (
        <p role="status" className="loading">
          {t('common.loading')}
        </p>
      )}
      {read.error &&
        (read.error.kind === 'forbidden' ? (
          <NotAvailableToRole />
        ) : (
          <div role="alert" className="state state-error">
            <p>
              <ProblemText kind={read.error.kind} />
            </p>
          </div>
        ))}
      {rows !== null &&
        (rows.length === 0 ? (
          <EmptyState>{t('audit.empty')}</EmptyState>
        ) : (
          <>
            <table>
              <caption className="visually-hidden">{t('audit.title')}</caption>
              <thead>
                <tr>
                  <th scope="col">{t('audit.col.when')}</th>
                  <th scope="col">{t('audit.col.action')}</th>
                  <th scope="col">{t('audit.col.who')}</th>
                  <th scope="col">{t('audit.col.resource')}</th>
                  <th scope="col">{t('audit.col.clinic')}</th>
                  <th scope="col">{t('audit.col.origin')}</th>
                  <th scope="col">{t('audit.col.details')}</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.audit_id}>
                    <th scope="row">
                      <Time value={r.occurred_at} />
                    </th>
                    <td>
                      <code>{r.action}</code>
                    </td>
                    <td>
                      {r.actor_role ? word('roles', r.actor_role) : t('audit.noRole')}
                      {r.actor_id && (
                        <>
                          {' · '}
                          <ShortId id={r.actor_id} />
                        </>
                      )}
                    </td>
                    <td>
                      {r.resource_type ?? '–'}
                      {r.resource_id && (
                        <>
                          {' · '}
                          <code className="short-id">{r.resource_id.length > 12 ? `${r.resource_id.slice(0, 8)}…` : r.resource_id}</code>
                        </>
                      )}
                    </td>
                    <td>{r.clinic_id ? <ShortId id={r.clinic_id} /> : t('audit.noClinic')}</td>
                    <td>{word('audit.origin', r.origin)}</td>
                    <td>
                      <Details row={r} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {more.has && more.before !== null && (
              <p>
                <button type="button" disabled={read.isPending} onClick={() => read.mutate({ f: { ...filter, beforeId: more.before as number }, append: true })}>
                  {read.isPending ? t('common.working') : t('audit.older')}
                </button>
              </p>
            )}
            {!more.has && <p className="hint">{t('audit.end')}</p>}
          </>
        ))}
    </>
  );
}

/** The row's `details`, shown only after a click, as key and value text. */
function Details({ row }: { row: Row }) {
  const { t } = useTranslation();
  const entries = Object.entries(row.details as Record<string, unknown>);
  if (entries.length === 0) return <span>{t('audit.noDetails')}</span>;
  return (
    <details>
      <summary>{t('audit.showDetails')}</summary>
      <dl className="facts">
        {entries.map(([k, v]) => (
          <div key={k} className="fact-row">
            <dt>{k}</dt>
            <dd>{plain(v)}</dd>
          </div>
        ))}
      </dl>
    </details>
  );
}
