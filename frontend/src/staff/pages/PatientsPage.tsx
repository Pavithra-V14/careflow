import { useId, useState, type FormEvent } from 'react';
import { Link, useNavigate } from 'react-router';
import { useMutation } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { ApiProblem, toProblem, unwrap } from '../apiState';
import { useAuth } from '../auth/AuthProvider';
import { CAN_SEARCH_PATIENTS } from '../sections';
import { useSession } from '../session/SessionContext';
import { Badge, PageHeading, ShortId, UUID, useWord } from '../ui/bits';
import { EmptyState, NotAvailableToRole } from '../ui/states';

/**
 * Fields the per-row label read (docs/design/patient-view-fields.md, a proposal) would add. They are NOT in the
 * OpenAPI snapshot yet, so they are read defensively and a missing field means "not provided", never a guess.
 * MOCK-ONLY until the backend adds them.
 */
function readLabels(response: unknown, hit?: unknown): { withheld: boolean; truncated: boolean; label: string | null } {
  const r = response as { labels_withheld?: unknown; truncated?: unknown };
  const h = (hit ?? {}) as { label?: unknown };
  return {
    withheld: r.labels_withheld === true,
    truncated: r.truncated === true,
    label: typeof h.label === 'string' && h.label.trim() !== '' ? h.label : null,
  };
}

function searchProblemText(problem: ApiProblem, t: (k: string) => string): string {
  switch (problem.kind) {
    case 'rate_limited':
      return t('patients.rateLimited');
    case 'unavailable':
      return t('patients.unavailable');
    case 'invalid':
      return t('patients.invalidInput');
    default:
      return t(`problem.${problem.kind}`);
  }
}

export function PatientsPage() {
  const { t } = useTranslation();
  const { api } = useAuth();
  const { active, hasRole } = useSession();
  const word = useWord();
  const navigate = useNavigate();
  const id = useId();
  const [name, setName] = useState('');
  const [dob, setDob] = useState('');
  const [phone, setPhone] = useState('');
  const [openId, setOpenId] = useState('');
  const [openError, setOpenError] = useState(false);

  // A search is a mutation on purpose: nothing about the person is cached, and the result is dropped on leaving.
  const search = useMutation<Awaited<ReturnType<typeof api.searchPatients>>['data'], ApiProblem, void>({
    gcTime: 0,
    retry: false,
    mutationFn: async () => {
      const body = { clinic_id: active.clinicId, name: name.trim(), dob, ...(phone.trim() ? { phone: phone.trim() } : {}) };
      try {
        return await unwrap(api.searchPatients(body));
      } catch (e) {
        throw toProblem(e);
      }
    },
  });

  function submit(e: FormEvent) {
    e.preventDefault();
    if (name.trim() === '' || dob === '') return;
    search.mutate();
  }

  function openById(e: FormEvent) {
    e.preventDefault();
    const trimmed = openId.trim().toLowerCase();
    if (!UUID.test(trimmed)) return setOpenError(true);
    setOpenError(false);
    void navigate(`/patients/${trimmed}`);
  }

  const result = search.data;
  const labels = result ? readLabels(result) : null;

  return (
    <section aria-labelledby="patients-title">
      <PageHeading id="patients-title">{t('patients.title')}</PageHeading>

      <h2>{t('patients.searchTitle')}</h2>
      {!hasRole(CAN_SEARCH_PATIENTS) ? (
        <NotAvailableToRole />
      ) : (
        <form onSubmit={submit} autoComplete="off" noValidate>
          <p className="hint">{t('patients.searchHint')}</p>
          <label htmlFor={`${id}-name`}>{t('patients.name')}</label>
          <input id={`${id}-name`} type="text" autoComplete="off" value={name} onChange={(e) => setName(e.target.value)} required />
          <label htmlFor={`${id}-dob`}>{t('patients.dob')}</label>
          <input id={`${id}-dob`} type="date" autoComplete="off" value={dob} onChange={(e) => setDob(e.target.value)} required />
          <label htmlFor={`${id}-phone`}>{t('patients.phone')}</label>
          <input id={`${id}-phone`} type="tel" autoComplete="off" value={phone} onChange={(e) => setPhone(e.target.value)} />
          <button type="submit" className="primary" disabled={search.isPending || name.trim() === '' || dob === ''}>
            {search.isPending ? t('common.working') : t('patients.search')}
          </button>
        </form>
      )}

      {search.isPending && (
        <p role="status" className="loading">
          {t('common.loading')}
        </p>
      )}
      {search.error &&
        (search.error.kind === 'forbidden' ? (
          <NotAvailableToRole />
        ) : (
          <div role="alert" className="state state-error">
            <p>{searchProblemText(search.error, t)}</p>
          </div>
        ))}
      {result &&
        (result.items.length === 0 ? (
          <EmptyState>{t('patients.noMatch')}</EmptyState>
        ) : (
          <>
            {labels?.withheld && (
              <p role="status" className="state state-forbidden">
                {t('patients.labelsWithheld')}
              </p>
            )}
            {labels?.truncated && <p className="hint">{t('patients.truncated')}</p>}
            <table>
              <caption className="visually-hidden">{t('patients.results')}</caption>
              <thead>
                <tr>
                  <th scope="col">{t('patients.col.record')}</th>
                  {result.items.some((h) => readLabels(result, h).label) && <th scope="col">{t('patients.col.label')}</th>}
                  <th scope="col">{t('patients.col.verified')}</th>
                  <th scope="col">{t('patients.col.via')}</th>
                  <th scope="col">{t('patients.col.matched')}</th>
                </tr>
              </thead>
              <tbody>
                {result.items.map((hit) => {
                  const l = readLabels(result, hit);
                  return (
                    <tr key={hit.patient_id}>
                      <th scope="row">
                        <Link to={`/patients/${hit.patient_id}`} aria-label={`${t('patients.openRecord')} ${hit.patient_id.slice(0, 8)}`}>
                          <ShortId id={hit.patient_id} />
                        </Link>
                      </th>
                      {result.items.some((h) => readLabels(result, h).label) && <td>{l.label ?? (l.withheld ? t('patients.withheld') : '–')}</td>}
                      <td>{hit.verified ? <Badge tone="ok">{t('patients.verified')}</Badge> : <Badge tone="warn">{t('patients.notVerified')}</Badge>}</td>
                      <td>{word('patients.via', hit.registered_via)}</td>
                      <td>{hit.matched_kinds.map((k) => word('patients.matchedKind', k)).join(', ')}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </>
        ))}

      <h2>{t('patients.openTitle')}</h2>
      <form onSubmit={openById} noValidate>
        <label htmlFor={`${id}-open`}>{t('patients.openId')}</label>
        <input id={`${id}-open`} type="text" autoComplete="off" spellCheck={false} value={openId} onChange={(e) => setOpenId(e.target.value)} aria-invalid={openError || undefined} aria-describedby={openError ? `${id}-open-err` : undefined} />
        {openError && (
          <p id={`${id}-open-err`} role="alert" className="form-error">
            {t('patients.openInvalid')}
          </p>
        )}
        <button type="submit">{t('patients.open')}</button>
      </form>
    </section>
  );
}
