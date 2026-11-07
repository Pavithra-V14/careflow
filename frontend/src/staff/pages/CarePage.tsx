import { useId, useState, type ReactNode } from 'react';
import { NavLink, useParams } from 'react-router';
import { useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import type { CarePlanAction } from '../../shared/api/client';
import { ApiProblem, unwrap } from '../apiState';
import { useAuth } from '../auth/AuthProvider';
import { useApiQuery, useIdempotentWrite } from '../hooks';
import { CAN_DECIDE_CARE_PLAN, CAN_RECORD_RESULT } from '../sections';
import { useSession } from '../session/SessionContext';
import { ConfirmDialog } from '../ui/ConfirmDialog';
import { Badge, PageHeading, ShortId, Time, UUID, useLang, useWord } from '../ui/bits';
import { NotAvailableToRole, ProblemText, QueryView } from '../ui/states';
import { clinicLocalToIso } from '../../shared/clinicTime';
import { ZoneNote } from '../components/ZoneNote';
import { formatNumber } from '../../shared/i18n/format';
import { NotFoundPage } from './NotFoundPage';
import { GapToolbarActions, RowAction, usePatientHint } from './CareGapActions';

const TABS = ['gaps', 'plans', 'results'] as const;
type Tab = (typeof TABS)[number];
const isTab = (v: string): v is Tab => (TABS as readonly string[]).includes(v);

export function CarePage() {
  const { t } = useTranslation();
  const { tab = 'gaps' } = useParams();
  if (!isTab(tab)) return <NotFoundPage />;
  return (
    <section aria-labelledby="care-title">
      <PageHeading id="care-title">{t('care.title')}</PageHeading>
      <nav aria-label={t('care.tabs')} className="sub-nav">
        <ul>
          {TABS.map((x) => (
            <li key={x}>
              <NavLink to={`/care/${x}`}>{t(`care.tab.${x}`)}</NavLink>
            </li>
          ))}
        </ul>
      </nav>
      <h2>{t(`care.tab.${tab}`)}</h2>
      {tab === 'gaps' && <GapsPanel />}
      {tab === 'plans' && <PlansPanel />}
      {tab === 'results' && <ResultsPanel />}
    </section>
  );
}

const notRead = (t: (k: string) => string) => <span className="not-recorded">{t('care.notReadable')}</span>;

// ---- care gaps (read-only) ---------------------------------------------------------------------------------------------

function GapsPanel() {
  const { t } = useTranslation();
  const { api } = useAuth();
  const { active } = useSession();
  const word = useWord();
  const lang = useLang();
  const query = useApiQuery(['care-gaps'], () => unwrap(api.careGaps(active.clinicId)));
  const [note, setNote] = useState<string | null>(null);
  return (
    <>
      <div className="toolbar">
        <button type="button" onClick={() => void query.refetch()} disabled={query.isFetching}>
          {t('common.refresh')}
        </button>
        <GapToolbarActions onDone={setNote} />
      </div>
      {note && <p role="status">{note}</p>}
      <QueryView query={query}>
        {(d) => (
          <>
            {!d.alert_age_configured && <p role="status">{t('care.gaps.ageNotSet')}</p>}
            {d.unreadable.length > 0 && (
              <p role="status">
                {t('care.gaps.unreadable')} {d.unreadable.map((s) => word('care.gaps.source', s)).join(', ')}
              </p>
            )}
            <h3>{t('care.gaps.summaries')}</h3>
            {d.summaries.length === 0 ? (
              <p>{t('care.gaps.noSummaries')}</p>
            ) : (
              <table>
                <caption className="visually-hidden">{t('care.gaps.summaries')}</caption>
                <thead>
                  <tr>
                    <th scope="col">{t('care.gaps.col.source')}</th>
                    <th scope="col">{t('care.gaps.col.open')}</th>
                    <th scope="col">{t('care.gaps.col.median')}</th>
                    <th scope="col">{t('care.gaps.col.undated')}</th>
                  </tr>
                </thead>
                <tbody>
                  {d.summaries.map((s) => (
                    <tr key={s.source}>
                      <th scope="row">{word('care.gaps.source', s.source)}</th>
                      {/* A source that cannot be read has a null count: never shown as zero. */}
                      <td>{s.open_count === null ? notRead(t) : formatNumber(s.open_count, lang)}</td>
                      <td>{s.median_age_days === null ? notRead(t) : formatNumber(s.median_age_days, lang)}</td>
                      <td>{formatNumber(s.undated, lang)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            <dl className="facts">
              <dt>{t('care.gaps.unknownLastDone')}</dt>
              <dd>{formatNumber(d.recurrence_rules_with_unknown_last_done, lang)}</dd>
              <dt>{t('care.gaps.errorRate')}</dt>
              <dd>{d.after_the_fact_error_rate === null ? <span className="not-recorded">{t('care.gaps.notMeasured')}</span> : formatNumber(d.after_the_fact_error_rate, lang, { maximumFractionDigits: 3 })}</dd>
            </dl>
            <h3>{t('care.gaps.rows')}</h3>
            {d.rows.length === 0 ? (
              <p>{t('care.gaps.noRows')}</p>
            ) : (
              <table>
                <caption className="visually-hidden">{t('care.gaps.rows')}</caption>
                <thead>
                  <tr>
                    <th scope="col">{t('care.gaps.col.source')}</th>
                    <th scope="col">{t('care.gaps.col.item')}</th>
                    <th scope="col">{t('care.gaps.col.age')}</th>
                    <th scope="col">{t('care.gaps.col.flag')}</th>
                    <th scope="col">{t('care.gaps.col.action')}</th>
                  </tr>
                </thead>
                <tbody>
                  {d.rows.map((r) => (
                    <tr key={`${r.source}-${r.item_id}`}>
                      <th scope="row">{word('care.gaps.source', r.source)}</th>
                      <td>
                        <ShortId id={r.item_id} />
                      </td>
                      <td>{r.age_days === null ? <span className="not-recorded">{t('care.gaps.ageUnknown')}</span> : formatNumber(r.age_days, lang)}</td>
                      <td>{r.flagged_overdue ? <Badge tone="warn">{t('care.gaps.overdue')}</Badge> : '–'}</td>
                      <td>
                        <RowAction source={r.source} itemId={r.item_id} onDone={setNote} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </>
        )}
      </QueryView>
    </>
  );
}

// ---- patient id (nurse and doctor cannot search patients, so the id comes from the record they are working from) ------------

function PatientIdField({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  const { t } = useTranslation();
  const id = useId();
  const bad = value.trim() !== '' && !UUID.test(value.trim());
  return (
    <>
      <label htmlFor={id}>{t('care.patientId')}</label>
      <input id={id} type="text" autoComplete="off" spellCheck={false} value={value} aria-invalid={bad || undefined} onChange={(e) => onChange(e.target.value)} />
      {bad && <p className="form-error">{t('care.patientIdBad')}</p>}
    </>
  );
}

function WriteProblem({ problem, notConfigured }: { problem: ApiProblem | null; notConfigured?: string }) {
  const { t } = useTranslation();
  if (!problem) return null;
  if (problem.kind === 'forbidden') return <NotAvailableToRole />;
  if (problem.kind === 'conflict') return <p role="status">{t('care.changed')}</p>;
  if (problem.kind === 'unavailable' && notConfigured) return <p role="status">{notConfigured}</p>;
  return (
    <p role="alert">
      <ProblemText kind={problem.kind} />
    </p>
  );
}

// ---- care plans -----------------------------------------------------------------------------------------------------------

function PlansPanel() {
  const { t } = useTranslation();
  const { api } = useAuth();
  const { active, hasRole } = useSession();
  const queryClient = useQueryClient();
  const [writing, setWriting] = useState(false);
  const [deciding, setDeciding] = useState<{ planId: string; action: CarePlanAction } | null>(null);
  const [outcome, setOutcome] = useState<string | null>(null);
  const query = useApiQuery(['care-plans'], () => unwrap(api.listCarePlans(active.clinicId)));
  const decide = useIdempotentWrite(
    (v: { planId: string; action: CarePlanAction }, key) => unwrap(api.confirmCarePlan(v.planId, v.action, { idempotencyKey: key })),
    (r) => {
      setDeciding(null);
      setOutcome(t('care.plans.decided', { waiting: t(`care.plans.waiting.${r.waiting_for}`) }));
      void queryClient.invalidateQueries();
    },
  );
  const canDecide = hasRole(CAN_DECIDE_CARE_PLAN);
  return (
    <>
      <div className="toolbar">
        <button type="button" onClick={() => void query.refetch()} disabled={query.isFetching}>
          {t('common.refresh')}
        </button>
        {canDecide && (
          <button
            type="button"
            onClick={() => {
              setOutcome(null);
              setWriting(true);
            }}
          >
            {t('care.plans.write')}
          </button>
        )}
      </div>
      <p className="hint">{t('care.plans.note')}</p>
      {outcome && <p role="status">{outcome}</p>}
      <QueryView query={query} isEmpty={(d) => d.plans.length === 0} empty={t('care.plans.empty')}>
        {(d) => (
          <table>
            <caption className="visually-hidden">{t('care.tab.plans')}</caption>
            <thead>
              <tr>
                <th scope="col">{t('care.plans.col.plan')}</th>
                <th scope="col">{t('care.plans.col.patient')}</th>
                <th scope="col">{t('care.plans.col.status')}</th>
                <th scope="col">{t('care.plans.col.created')}</th>
                <th scope="col">{t('care.plans.col.action')}</th>
              </tr>
            </thead>
            <tbody>
              {d.plans.map((p) => (
                <tr key={p.plan_id}>
                  <th scope="row">
                    <ShortId id={p.plan_id} />
                  </th>
                  <td>
                    <ShortId id={p.patient_id} />
                  </td>
                  <td>{t(`care.plans.status.${p.status}`)}</td>
                  <td>
                    <Time value={p.created_at} />
                  </td>
                  <td>
                    {canDecide && p.status === 'awaiting_confirmation' ? (
                      <>
                        <button type="button" aria-label={`${t('care.plans.approve')} ${p.plan_id.slice(0, 8)}`} onClick={() => setDeciding({ planId: p.plan_id, action: 'approve' })}>
                          {t('care.plans.approve')}
                        </button>{' '}
                        <button type="button" aria-label={`${t('care.plans.reject')} ${p.plan_id.slice(0, 8)}`} onClick={() => setDeciding({ planId: p.plan_id, action: 'reject' })}>
                          {t('care.plans.reject')}
                        </button>
                      </>
                    ) : (
                      '–'
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </QueryView>
      {deciding && (
        <ConfirmDialog
          title={t(deciding.action === 'approve' ? 'care.plans.approveTitle' : 'care.plans.rejectTitle')}
          confirmLabel={t(deciding.action === 'approve' ? 'care.plans.approve' : 'care.plans.reject')}
          busy={decide.isPending}
          onConfirm={() => decide.mutate(deciding)}
          onCancel={() => {
            if (decide.error?.kind === 'conflict') void queryClient.invalidateQueries();
            decide.reset();
            setDeciding(null);
          }}
          status={<WriteProblem problem={decide.error} notConfigured={t('care.plans.notConfigured')} />}
        >
          <p>{t('care.plans.decideBody')}</p>
        </ConfirmDialog>
      )}
      {writing && (
        <WritePlanDialog
          onClose={(r) => {
            setWriting(false);
            if (r) {
              setOutcome(t('care.plans.written', { waiting: t(`care.plans.waiting.${r}`) }));
              void queryClient.invalidateQueries();
            }
          }}
        />
      )}
    </>
  );
}

function WritePlanDialog({ onClose }: { onClose: (waiting: 'confirmation' | 'result' | 'none' | null) => void }) {
  const { t } = useTranslation();
  const { api } = useAuth();
  const { active } = useSession();
  const [patient, setPatient] = useState(usePatientHint());
  const [text, setText] = useState('');
  const valid = UUID.test(patient.trim()) && text.trim() !== '';
  const write = useIdempotentWrite(
    (_: void, key) => unwrap(api.createCarePlan({ clinic_id: active.clinicId, patient_id: patient.trim(), plan_text: text }, { idempotencyKey: key })),
    (r) => onClose(r.waiting_for),
  );
  return (
    <ConfirmDialog
      title={t('care.plans.write')}
      confirmLabel={t('care.plans.send')}
      busy={write.isPending}
      confirmDisabled={!valid}
      onConfirm={() => write.mutate()}
      onCancel={() => {
        write.reset();
        onClose(null);
      }}
      status={<WriteProblem problem={write.error} notConfigured={t('care.plans.notConfigured')} />}
    >
      <p>{t('care.plans.writeBody')}</p>
      <PatientIdField
        value={patient}
        onChange={(v) => {
          setPatient(v);
          write.reset();
        }}
      />
      <label htmlFor="plan-text">{t('care.plans.text')}</label>
      <textarea
        id="plan-text"
        rows={6}
        value={text}
        onChange={(e) => {
          setText(e.target.value);
          write.reset();
        }}
      />
    </ConfirmDialog>
  );
}

// ---- results ----------------------------------------------------------------------------------------------------------------

interface MeasureRow {
  id: number;
  name: string;
  value: string;
}
const MAX_MEASURES = 20;
const CODE = /^[A-Za-z0-9][A-Za-z0-9._-]{0,39}$/;

function ResultsPanel() {
  const { t } = useTranslation();
  const { hasRole } = useSession();
  const [open, setOpen] = useState(false);
  const [done, setDone] = useState<{ wake: boolean } | null>(null);
  if (!hasRole(CAN_RECORD_RESULT)) return <NotAvailableToRole />;
  return (
    <>
      <p className="hint">{t('care.results.note')}</p>
      <p>
        <button
          type="button"
          onClick={() => {
            setDone(null);
            setOpen(true);
          }}
        >
          {t('care.results.record')}
        </button>
      </p>
      {done && <p role="status">{done.wake ? t('care.results.doneWake') : t('care.results.done')}</p>}
      {open && (
        <RecordResultDialog
          onClose={(r) => {
            setOpen(false);
            if (r) setDone(r);
          }}
        />
      )}
    </>
  );
}

function RecordResultDialog({ onClose }: { onClose: (r: { wake: boolean } | null) => void }) {
  const { t } = useTranslation();
  const { api } = useAuth();
  const { active } = useSession();
  const [patient, setPatient] = useState(usePatientHint());
  const [code, setCode] = useState('');
  const [rows, setRows] = useState<MeasureRow[]>([{ id: 1, name: '', value: '' }]);
  const [validFrom, setValidFrom] = useState('');
  const [validTo, setValidTo] = useState('');
  const [verified, setVerified] = useState<'yes' | 'no' | null>(null); // nothing chosen for the person
  const [supersedes, setSupersedes] = useState('');
  const [change, setChange] = useState<'amendment' | 'correction' | ''>('');
  const [next, setNext] = useState(2);
  const radio = useId();

  const named = rows.filter((r) => r.name.trim() !== '');
  const numbersOk = named.every((r) => r.value.trim() === '' || Number.isFinite(Number(r.value)));
  const fromIso = clinicLocalToIso(validFrom);
  const toIso = validTo === '' ? null : clinicLocalToIso(validTo);
  const valid =
    UUID.test(patient.trim()) &&
    CODE.test(code.trim()) &&
    named.length > 0 &&
    numbersOk &&
    fromIso !== null &&
    (validTo === '' || toIso !== null) &&
    verified !== null &&
    (supersedes.trim() === '' || UUID.test(supersedes.trim()));

  const record = useIdempotentWrite(
    (_: void, key) => {
      // An empty value is sent as null (unknown), never as zero.
      const measures: Record<string, number | null> = {};
      for (const r of named) measures[r.name.trim()] = r.value.trim() === '' ? null : Number(r.value);
      return unwrap(
        api.recordResult(
          {
            clinic_id: active.clinicId,
            patient_id: patient.trim(),
            code: code.trim(),
            measures,
            valid_from: fromIso as string,
            valid_to: toIso,
            verified: verified === 'yes',
            supersedes_id: supersedes.trim() === '' ? null : supersedes.trim(),
            ...(supersedes.trim() !== '' && change !== '' ? { change } : {}),
          },
          { idempotencyKey: key },
        ),
      );
    },
    (r) => onClose({ wake: r.wake_queued }),
  );
  const touch = <T,>(set: (v: T) => void) => (v: T) => {
    set(v);
    record.reset(); // a different result is a different request: never reuse the old key
  };

  return (
    <ConfirmDialog
      title={t('care.results.record')}
      confirmLabel={t('care.results.save')}
      busy={record.isPending}
      confirmDisabled={!valid}
      onConfirm={() => record.mutate()}
      onCancel={() => {
        record.reset();
        onClose(null);
      }}
      status={<WriteProblem problem={record.error} />}
    >
      <p>{t('care.results.body')}</p>
      <PatientIdField value={patient} onChange={touch(setPatient)} />
      <label htmlFor="res-code">{t('care.results.code')}</label>
      <input id="res-code" type="text" autoComplete="off" value={code} onChange={(e) => touch(setCode)(e.target.value)} />
      <fieldset>
        <legend>{t('care.results.measures')}</legend>
        <p className="hint">{t('care.results.measuresHint')}</p>
        {rows.map((r, i) => (
          <div key={r.id} className="inline-form">
            <label htmlFor={`m-name-${r.id}`}>{t('care.results.measureName', { n: i + 1 })}</label>
            <input
              id={`m-name-${r.id}`}
              type="text"
              autoComplete="off"
              value={r.name}
              onChange={(e) => touch(setRows)(rows.map((x) => (x.id === r.id ? { ...x, name: e.target.value } : x)))}
            />
            <label htmlFor={`m-val-${r.id}`}>{t('care.results.measureValue', { n: i + 1 })}</label>
            <input
              id={`m-val-${r.id}`}
              type="text"
              inputMode="decimal"
              autoComplete="off"
              value={r.value}
              onChange={(e) => touch(setRows)(rows.map((x) => (x.id === r.id ? { ...x, value: e.target.value } : x)))}
            />
          </div>
        ))}
        {!numbersOk && <p className="form-error">{t('care.results.notNumber')}</p>}
        <button
          type="button"
          disabled={rows.length >= MAX_MEASURES}
          onClick={() => {
            touch(setRows)([...rows, { id: next, name: '', value: '' }]);
            setNext(next + 1);
          }}
        >
          {t('care.results.addMeasure')}
        </button>
      </fieldset>
      <ZoneNote />
      <label htmlFor="res-from">{t('care.results.validFrom')}</label>
      <input id="res-from" type="datetime-local" value={validFrom} onChange={(e) => touch(setValidFrom)(e.target.value)} />
      <label htmlFor="res-to">{t('care.results.validTo')}</label>
      <input id="res-to" type="datetime-local" value={validTo} onChange={(e) => touch(setValidTo)(e.target.value)} />
      <fieldset>
        <legend>{t('care.results.verified')}</legend>
        {(['yes', 'no'] as const).map((v) => (
          <label key={v} className="choice">
            <input type="radio" name={radio} checked={verified === v} onChange={() => touch(setVerified)(v)} />
            <span>{t(v === 'yes' ? 'common.yes' : 'common.no')}</span>
          </label>
        ))}
      </fieldset>
      <label htmlFor="res-sup">{t('care.results.supersedes')}</label>
      <input id="res-sup" type="text" autoComplete="off" spellCheck={false} value={supersedes} onChange={(e) => touch(setSupersedes)(e.target.value)} />
      {supersedes.trim() !== '' && (
        <>
          <label htmlFor="res-change">{t('care.results.change')}</label>
          <select id="res-change" value={change} onChange={(e) => touch(setChange)(e.target.value as 'amendment' | 'correction' | '')}>
            <option value="">{t('care.results.changeNone')}</option>
            <option value="amendment">{t('care.results.changes.amendment')}</option>
            <option value="correction">{t('care.results.changes.correction')}</option>
          </select>
          <p className="hint">{t('care.results.changeHint')}</p>
        </>
      )}
    </ConfirmDialog>
  );
}

export type { ReactNode };
