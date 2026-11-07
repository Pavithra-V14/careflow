import { useState, type ReactNode } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import type { components } from '../../shared/api/schema';
import { ApiProblem, toProblem, unwrap } from '../apiState';
import { useAuth } from '../auth/AuthProvider';
import { useApiQuery, useIdempotentWrite } from '../hooks';
import { useSession } from '../session/SessionContext';
import { ConfirmDialog } from '../ui/ConfirmDialog';
import { Badge, ShortId, Time, useLang, useWord } from '../ui/bits';
import { NotAvailableToRole, ProblemText, QueryView } from '../ui/states';
import { WriteProblem } from '../ui/WriteProblem';
import { formatNumber } from '../../shared/i18n/format';

const notRecorded = (t: (k: string) => string): ReactNode => <span className="not-recorded">{t('admin.notRecorded')}</span>;

// ---- reviewer statistics (admin) -----------------------------------------------------------------------------------------------

export function ReviewerStats() {
  const { t } = useTranslation();
  const { api } = useAuth();
  const { active } = useSession();
  const lang = useLang();
  const word = useWord();
  const query = useApiQuery(['reviewer-stats'], () => unwrap(api.reviewerStats(active.clinicId)));
  return (
    <>
      <div className="toolbar">
        <button type="button" onClick={() => void query.refetch()} disabled={query.isFetching}>
          {t('common.refresh')}
        </button>
      </div>
      <QueryView query={query}>
        {(d) => (
          <>
            <p className="hint">{t('admin.reviewer.window', { days: formatNumber(d.days, lang) })}</p>
            {!d.policy_configured && <p role="status">{t('admin.reviewer.noPolicy')}</p>}
            {d.reviewers.length === 0 ? (
              <p>{t('admin.reviewer.empty')}</p>
            ) : (
              <table>
                <caption className="visually-hidden">{t('admin.tab.reviewer')}</caption>
                <thead>
                  <tr>
                    <th scope="col">{t('admin.reviewer.col.reviewer')}</th>
                    <th scope="col">{t('admin.reviewer.col.decisions')}</th>
                    <th scope="col">{t('admin.reviewer.col.approvals')}</th>
                    <th scope="col">{t('admin.reviewer.col.rejections')}</th>
                    <th scope="col">{t('admin.reviewer.col.edits')}</th>
                    <th scope="col">{t('admin.reviewer.col.rate')}</th>
                    <th scope="col">{t('admin.reviewer.col.median')}</th>
                    <th scope="col">{t('admin.reviewer.col.canaries')}</th>
                    <th scope="col">{t('admin.reviewer.col.verdict')}</th>
                    <th scope="col">{t('admin.reviewer.col.reasons')}</th>
                  </tr>
                </thead>
                <tbody>
                  {d.reviewers.map((r) => (
                    <tr key={r.reviewer}>
                      <th scope="row">
                        <ShortId id={r.reviewer} />
                      </th>
                      <td>{formatNumber(r.decisions, lang)}</td>
                      <td>{formatNumber(r.approvals, lang)}</td>
                      <td>{formatNumber(r.rejections, lang)}</td>
                      <td>{formatNumber(r.edits, lang)}</td>
                      {/* null is "not recorded": a missing rate or time is never shown as zero. */}
                      <td>{r.approval_rate === null ? notRecorded(t) : formatNumber(r.approval_rate, lang, { style: 'percent', maximumFractionDigits: 0 })}</td>
                      <td>{r.median_seconds === null ? notRecorded(t) : formatNumber(r.median_seconds, lang, { maximumFractionDigits: 1 })}</td>
                      <td>{t('admin.reviewer.canaryCount', { approved: formatNumber(r.canaries_approved, lang), seen: formatNumber(r.canaries_seen, lang) })}</td>
                      <td>{word('admin.reviewer.verdicts', r.verdict)}</td>
                      <td>{r.reasons.length === 0 ? '–' : r.reasons.join(', ')}</td>
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

// ---- cost canvas (admin) --------------------------------------------------------------------------------------------------------

type CostColumn = components['schemas']['CostColumnOut'];
const COST_ROWS: { key: keyof CostColumn; label: string }[] = [
  { key: 'workflows_per_day', label: 'workflows' },
  { key: 'calls_per_workflow', label: 'calls' },
  { key: 'usd_per_call', label: 'perCall' },
  { key: 'workload_x_amplification_x_unit_per_month', label: 'variable' },
  { key: 'platform_floor_per_month', label: 'floor' },
  { key: 'failure_tax_per_month', label: 'failure' },
  { key: 'total_per_month', label: 'total' },
];

export function CostCanvas() {
  const { t } = useTranslation();
  const { api } = useAuth();
  const { active } = useSession();
  const lang = useLang();
  const word = useWord();
  const query = useApiQuery(['cost-canvas'], () => unwrap(api.costCanvas(active.clinicId)));
  // A null term is "not configured or not measured" and stays null on screen: never zero, never an estimate.
  const cell = (c: CostColumn | null, key: keyof CostColumn): ReactNode => {
    if (c === null) return <span className="not-recorded">{t('admin.cost.noColumn')}</span>;
    const v = c[key];
    return v === null ? <span className="not-recorded">{t('admin.cost.notMeasured')}</span> : formatNumber(v, lang, { maximumFractionDigits: 4 });
  };
  return (
    <>
      <p className="hint">{t('admin.cost.note')}</p>
      <div className="toolbar">
        <button type="button" onClick={() => void query.refetch()} disabled={query.isFetching}>
          {t('common.refresh')}
        </button>
      </div>
      <QueryView query={query}>
        {(d) => (
          <>
            <dl className="facts">
              <dt>{t('admin.cost.state')}</dt>
              <dd>
                <Badge tone={d.state === 'ok' ? 'ok' : 'warn'}>{word('admin.cost.states', d.state)}</Badge>
              </dd>
              <dt>{t('admin.cost.amplification')}</dt>
              <dd>{d.amplification_over_ceiling ? <Badge tone="warn">{t('common.yes')}</Badge> : t('common.no')}</dd>
            </dl>
            {d.reasons.length > 0 && (
              <>
                <h3>{t('admin.cost.reasons')}</h3>
                <ul>
                  {d.reasons.map((r) => (
                    <li key={r}>{word('admin.cost.reason', r)}</li>
                  ))}
                </ul>
              </>
            )}
            <table>
              <caption className="visually-hidden">{t('admin.tab.cost')}</caption>
              <thead>
                <tr>
                  <th scope="col">{t('admin.cost.col.term')}</th>
                  <th scope="col">{t('admin.cost.col.actual')}</th>
                  <th scope="col">{t('admin.cost.col.shadow')}</th>
                </tr>
              </thead>
              <tbody>
                {COST_ROWS.map((row) => (
                  <tr key={row.key}>
                    <th scope="row">{t(`admin.cost.term.${row.label}`)}</th>
                    <td>{cell(d.actual, row.key)}</td>
                    <td>{cell(d.shadow, row.key)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </>
        )}
      </QueryView>
    </>
  );
}

// ---- voice transcripts (admin; every read and purge is audited by the database) -----------------------------------------------

type Session = components['schemas']['VoiceSessionRowOut'];

export function VoicePanel() {
  const { t } = useTranslation();
  const { api } = useAuth();
  const { active } = useSession();
  const lang = useLang();
  const word = useWord();
  const queryClient = useQueryClient();
  const [purging, setPurging] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [reading, setReading] = useState<Session | null>(null);
  const query = useApiQuery(['voice-sessions'], () => unwrap(api.listVoiceSessions(active.clinicId)));
  return (
    <>
      <p className="hint">{t('admin.voice.note')}</p>
      <div className="toolbar">
        <button type="button" onClick={() => void query.refetch()} disabled={query.isFetching}>
          {t('common.refresh')}
        </button>
        <button
          type="button"
          onClick={() => {
            setNote(null);
            setPurging(true);
          }}
        >
          {t('admin.voice.purge')}
        </button>
      </div>
      {note && <p role="status">{note}</p>}
      <QueryView query={query} isEmpty={(d) => d.sessions.length === 0} empty={t('admin.voice.empty')}>
        {(d) => (
          <table>
            <caption className="visually-hidden">{t('admin.tab.voice')}</caption>
            <thead>
              <tr>
                <th scope="col">{t('admin.voice.col.session')}</th>
                <th scope="col">{t('admin.voice.col.workflow')}</th>
                <th scope="col">{t('admin.voice.col.status')}</th>
                <th scope="col">{t('admin.voice.col.started')}</th>
                <th scope="col">{t('admin.voice.col.chunks')}</th>
                <th scope="col">{t('admin.voice.col.expired')}</th>
                <th scope="col">{t('admin.voice.col.retention')}</th>
                <th scope="col">{t('admin.voice.col.action')}</th>
              </tr>
            </thead>
            <tbody>
              {d.sessions.map((s) => (
                <tr key={s.session_id}>
                  <th scope="row">
                    <ShortId id={s.session_id} />
                  </th>
                  <td>{word('admin.voice.workflows', s.workflow)}</td>
                  <td>{word('admin.voice.statuses', s.status)}</td>
                  <td>
                    <Time value={s.started_at} />
                  </td>
                  <td>{formatNumber(s.chunk_count, lang)}</td>
                  <td>{formatNumber(s.expired_chunk_count, lang)}</td>
                  <td>{formatNumber(s.retention_days, lang)}</td>
                  <td>
                    <button type="button" aria-label={`${t('admin.voice.read')} ${s.session_id.slice(0, 8)}`} onClick={() => setReading(s)}>
                      {t('admin.voice.read')}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </QueryView>
      {reading && <TranscriptDialog session={reading} onClose={() => setReading(null)} />}
      {purging && (
        <PurgeDialog
          onClose={(deleted) => {
            setPurging(false);
            if (deleted !== null) {
              setNote(t('admin.voice.purged', { count: formatNumber(deleted, lang) }));
              void queryClient.invalidateQueries();
            }
          }}
        />
      )}
    </>
  );
}

/** Reads one transcript on a click. The read is audited by the database; the text is held only while this dialog is open and is never cached. */
function TranscriptDialog({ session, onClose }: { session: Session; onClose: () => void }) {
  const { t } = useTranslation();
  const { api } = useAuth();
  const { active } = useSession();
  const read = useMutation<components['schemas']['VoiceTranscriptOut'], ApiProblem, void>({
    gcTime: 0,
    retry: false,
    mutationFn: async () => {
      try {
        return await unwrap(api.readTranscript(session.session_id, active.clinicId));
      } catch (e) {
        throw toProblem(e);
      }
    },
  });
  return (
    <ConfirmDialog
      title={t('admin.voice.readTitle')}
      confirmLabel={t('admin.voice.read')}
      busy={read.isPending}
      confirmDisabled={read.isSuccess}
      onConfirm={() => read.mutate()}
      onCancel={() => {
        read.reset(); // the text is dropped with the dialog
        onClose();
      }}
      status={
        read.isError ? (
          read.error.kind === 'forbidden' ? (
            <NotAvailableToRole />
          ) : read.error.kind === 'unavailable' ? (
            <p role="status">{t('admin.voice.keysMissing')}</p>
          ) : (
            <p role="alert">
              <ProblemText kind={read.error.kind} />
            </p>
          )
        ) : read.isSuccess ? (
          read.data.chunks.length === 0 ? (
            <p role="status">{t('admin.voice.noChunks')}</p>
          ) : (
            <ol className="transcript" aria-label={t('admin.voice.transcript')}>
              {[...read.data.chunks]
                .sort((a, b) => a.seq - b.seq)
                .map((c) => (
                  <li key={c.seq}>{c.text}</li>
                ))}
            </ol>
          )
        ) : null
      }
    >
      <p>{t('admin.voice.readBody')}</p>
      <p className="hint">
        <ShortId id={session.session_id} />
      </p>
    </ConfirmDialog>
  );
}

function PurgeDialog({ onClose }: { onClose: (deleted: number | null) => void }) {
  const { t } = useTranslation();
  const { api } = useAuth();
  const { active } = useSession();
  const [limit, setLimit] = useState('');
  const n = Number(limit);
  const valid = limit === '' || (Number.isInteger(n) && n >= 1 && n <= 1000);
  const purge = useIdempotentWrite(
    (_: void, key) => unwrap(api.purgeVoice({ clinic_id: active.clinicId, ...(limit === '' ? {} : { limit: n }) }, { idempotencyKey: key })),
    (r) => onClose(r.deleted),
  );
  return (
    <ConfirmDialog
      title={t('admin.voice.purge')}
      confirmLabel={t('admin.voice.purgeConfirm')}
      busy={purge.isPending}
      confirmDisabled={!valid}
      onConfirm={() => purge.mutate()}
      onCancel={() => {
        purge.reset();
        onClose(null);
      }}
      status={<WriteProblem problem={purge.error} />}
    >
      <p>{t('admin.voice.purgeBody')}</p>
      <label htmlFor="purge-limit">{t('admin.voice.purgeLimit')}</label>
      <input
        id="purge-limit"
        type="number"
        min={1}
        max={1000}
        step={1}
        inputMode="numeric"
        value={limit}
        onChange={(e) => {
          setLimit(e.target.value);
          purge.reset();
        }}
      />
    </ConfirmDialog>
  );
}
