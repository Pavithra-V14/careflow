import { useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import type { components } from '../../shared/api/schema';
import { unwrap } from '../apiState';
import { useAuth } from '../auth/AuthProvider';
import { useApiQuery, useIdempotentWrite } from '../hooks';
import { useSession } from '../session/SessionContext';
import { ConfirmDialog } from '../ui/ConfirmDialog';
import { Badge, PageHeading, ShortId, Time, useWord } from '../ui/bits';
import { QueryView } from '../ui/states';
import { WriteProblem } from '../ui/WriteProblem';
import { CAN_DECIDE_KNOWLEDGE } from '../sections';

type Draft = components['schemas']['KnowledgeDraftOut'];
type Kind = Draft['kind'];
type Role = 'front_desk' | 'nurse' | 'doctor' | 'admin' | 'queue_worker';
const KINDS: readonly Kind[] = ['card', 'template'];
const AUDIENCE: readonly Role[] = ['front_desk', 'nurse', 'doctor', 'admin', 'queue_worker'];
const CODE = /^[a-z0-9][a-z0-9._-]{0,99}$/;
const REASON = /^[a-z][a-z0-9_]{0,59}$/;
const lines = (s: string): string[] => s.split('\n').map((l) => l.trim()).filter((l) => l !== '');

/**
 * Cards and message templates (nurse writes, doctor approves). EVERYTHING here is typed by a nurse or a doctor: the lists start empty, nothing
 * is seeded and the console holds no card or template text of its own. A draft is written, edited by its author, submitted, and then
 * approved or rejected by a DOCTOR who is not the author (the backend refuses an author deciding their own draft and refuses a nurse; this
 * screen only hides the buttons). Every write carries a key and needs a recent second factor, and nothing is pre-chosen. While the owner's
 * switch is off the answer is 503 and the screen says so and offers nothing.
 */
export function KnowledgePage() {
  const { t } = useTranslation();
  const { api } = useAuth();
  const { active, account, hasRole } = useSession();
  const word = useWord();
  const queryClient = useQueryClient();
  const [form, setForm] = useState<{ draft: Draft | null } | null>(null);
  const [looking, setLooking] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState<Draft | null>(null);
  const [deciding, setDeciding] = useState<{ draft: Draft; decision: 'approve' | 'reject' } | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const drafts = useApiQuery(['knowledge-drafts', active.clinicId], () => unwrap(api.listKnowledgeDrafts(active.clinicId)));
  const items = useApiQuery(['knowledge-items', active.clinicId], () => unwrap(api.listKnowledgeItems(active.clinicId)));
  const off = drafts.isError && drafts.error.status === 503; // 503: the owner's switch is off; a 502 or 504 is an ordinary failure
  const done = (message: string) => {
    setNote(message);
    void queryClient.invalidateQueries();
  };
  return (
    <section aria-labelledby="kn-title">
      <PageHeading id="kn-title">{t('knowledge.title')}</PageHeading>
      <p className="hint">{t('knowledge.note')}</p>
      {off ? (
        <p role="status" className="state state-empty">
          {t('knowledge.off')}
        </p>
      ) : (
        <>
          {note && <p role="status">{note}</p>}
          <div className="toolbar">
            <button type="button" onClick={() => { setNote(null); setForm({ draft: null }); }}>
              {t('knowledge.new')}
            </button>
            <button type="button" onClick={() => { void drafts.refetch(); void items.refetch(); }} disabled={drafts.isFetching}>
              {t('common.refresh')}
            </button>
          </div>
          <h2>{t('knowledge.drafts')}</h2>
          <QueryView query={drafts} isEmpty={(d) => d.drafts.length === 0} empty={t('knowledge.draftsEmpty')}>
            {(d) => (
              <table>
                <caption className="visually-hidden">{t('knowledge.drafts')}</caption>
                <thead>
                  <tr>
                    <th scope="col">{t('knowledge.col.kind')}</th>
                    <th scope="col">{t('knowledge.col.code')}</th>
                    <th scope="col">{t('knowledge.col.titleCol')}</th>
                    <th scope="col">{t('knowledge.col.status')}</th>
                    <th scope="col">{t('knowledge.col.author')}</th>
                    <th scope="col">{t('knowledge.col.decider')}</th>
                    <th scope="col">{t('knowledge.col.updated')}</th>
                    <th scope="col">{t('knowledge.col.action')}</th>
                  </tr>
                </thead>
                <tbody>
                  {d.drafts.map((r) => {
                    const mine = r.author === account.accountId;
                    return (
                      <tr key={r.draft_id}>
                        <td>{word('knowledge.kinds', r.kind)}</td>
                        <th scope="row">
                          <code>{r.code}</code>
                        </th>
                        <td className="pre-wrap">{r.title}</td>
                        <td>
                          <Badge tone={r.status === 'approved' ? 'ok' : r.status === 'submitted' ? 'warn' : 'neutral'}>{word('knowledge.status', r.status)}</Badge>
                          {r.reject_reason && (
                            <>
                              {' '}
                              <code>{r.reject_reason}</code>
                            </>
                          )}
                        </td>
                        <td>
                          <ShortId id={r.author} />
                          {mine && <> ({t('knowledge.you')})</>}
                        </td>
                        <td>{r.decided_by ? <ShortId id={r.decided_by} /> : '–'}</td>
                        <td>
                          <Time value={r.updated_at} />
                        </td>
                        <td>
                          <button type="button" aria-expanded={looking === r.draft_id} aria-label={`${looking === r.draft_id ? t('knowledge.close') : t('knowledge.look')} ${r.code}`} onClick={() => setLooking(looking === r.draft_id ? null : r.draft_id)}>
                            {looking === r.draft_id ? t('knowledge.close') : t('knowledge.look')}
                          </button>
                          {r.status === 'draft' && mine && (
                            <>
                              {' '}
                              <button type="button" aria-label={`${t('knowledge.edit')} ${r.code}`} onClick={() => { setNote(null); setForm({ draft: r }); }}>
                                {t('knowledge.edit')}
                              </button>{' '}
                              <button type="button" aria-label={`${t('knowledge.submit')} ${r.code}`} onClick={() => { setNote(null); setSubmitting(r); }}>
                                {t('knowledge.submit')}
                              </button>
                            </>
                          )}
                          {r.status === 'submitted' && hasRole(CAN_DECIDE_KNOWLEDGE) && !mine && (
                            <>
                              {' '}
                              <button type="button" aria-label={`${t('knowledge.approve')} ${r.code}`} onClick={() => { setNote(null); setDeciding({ draft: r, decision: 'approve' }); }}>
                                {t('knowledge.approve')}
                              </button>{' '}
                              <button type="button" aria-label={`${t('knowledge.reject')} ${r.code}`} onClick={() => { setNote(null); setDeciding({ draft: r, decision: 'reject' }); }}>
                                {t('knowledge.reject')}
                              </button>
                            </>
                          )}
                          {r.status === 'submitted' && hasRole(CAN_DECIDE_KNOWLEDGE) && mine && <p className="hint">{t('knowledge.youWrote')}</p>}
                          {looking === r.draft_id && <DraftContent draft={r} />}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            )}
          </QueryView>
          <h2>{t('knowledge.approved')}</h2>
          <QueryView query={items} isEmpty={(d) => d.items.length === 0} empty={t('knowledge.itemsEmpty')}>
            {(d) => (
              <table>
                <caption className="visually-hidden">{t('knowledge.approved')}</caption>
                <thead>
                  <tr>
                    <th scope="col">{t('knowledge.col.kind')}</th>
                    <th scope="col">{t('knowledge.col.code')}</th>
                    <th scope="col">{t('knowledge.col.version')}</th>
                    <th scope="col">{t('knowledge.col.titleCol')}</th>
                    <th scope="col">{t('knowledge.col.audience')}</th>
                    <th scope="col">{t('knowledge.col.author')}</th>
                    <th scope="col">{t('knowledge.col.approver')}</th>
                    <th scope="col">{t('knowledge.col.approvedAt')}</th>
                  </tr>
                </thead>
                <tbody>
                  {d.items.map((i) => (
                    <tr key={`${i.kind}:${i.code}:${i.version}`}>
                      <td>{word('knowledge.kinds', i.kind)}</td>
                      <th scope="row">
                        <code>{i.code}</code>
                      </th>
                      <td>{i.version}</td>
                      <td className="pre-wrap">{i.title}</td>
                      <td>{i.audience.map((a) => word('knowledge.roles', a)).join(', ') || '–'}</td>
                      <td>
                        <ShortId id={i.author} />
                      </td>
                      <td>
                        <ShortId id={i.approved_by} />
                      </td>
                      <td>
                        <Time value={i.approved_at} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </QueryView>
        </>
      )}
      {form && (
        <DraftForm
          draft={form.draft}
          onClose={(message) => {
            setForm(null);
            if (message) done(message);
          }}
        />
      )}
      {submitting && (
        <SubmitDialog
          draft={submitting}
          onClose={(message) => {
            setSubmitting(null);
            if (message) done(message);
          }}
        />
      )}
      {deciding && (
        <DecisionDialog
          draft={deciding.draft}
          decision={deciding.decision}
          onClose={(message) => {
            setDeciding(null);
            if (message) done(message);
          }}
        />
      )}
    </section>
  );
}

/** The draft exactly as typed, as plain text. */
function DraftContent({ draft }: { draft: Draft }) {
  const { t } = useTranslation();
  const word = useWord();
  return (
    <div>
      <h3>{draft.title}</h3>
      <blockquote className="pre-wrap">{draft.body}</blockquote>
      <dl className="facts">
        <dt>{t('knowledge.synonyms')}</dt>
        <dd>{draft.synonyms.length === 0 ? '–' : draft.synonyms.join(' · ')}</dd>
        <dt>{t('knowledge.phrases')}</dt>
        <dd>{draft.patient_phrases.length === 0 ? '–' : draft.patient_phrases.join(' · ')}</dd>
        <dt>{t('knowledge.audience')}</dt>
        <dd>{draft.audience.length === 0 ? '–' : draft.audience.map((a) => word('knowledge.roles', a)).join(', ')}</dd>
      </dl>
    </div>
  );
}

function DraftForm({ draft, onClose }: { draft: Draft | null; onClose: (message: string | null) => void }) {
  const { t } = useTranslation();
  const { api } = useAuth();
  const { active } = useSession();
  const word = useWord();
  const editing = draft !== null;
  const [kind, setKind] = useState<Kind | ''>(draft?.kind ?? '');
  const [code, setCode] = useState(draft?.code ?? '');
  const [title, setTitle] = useState(draft?.title ?? '');
  const [body, setBody] = useState(draft?.body ?? '');
  const [synonyms, setSynonyms] = useState((draft?.synonyms ?? []).join('\n'));
  const [phrases, setPhrases] = useState((draft?.patient_phrases ?? []).join('\n'));
  const [audience, setAudience] = useState<Role[]>((draft?.audience.filter((a): a is Role => (AUDIENCE as readonly string[]).includes(a)) ?? []) as Role[]);
  const [confirm, setConfirm] = useState(false);
  const syn = lines(synonyms);
  const phr = lines(phrases);
  const tooLong = [...syn, ...phr].some((l) => l.length > 200);
  const valid =
    (editing || (kind !== '' && CODE.test(code))) && title.trim() !== '' && title.length <= 200 && body.trim() !== '' && body.length <= 4000 && syn.length <= 20 && phr.length <= 20 && !tooLong && (kind !== 'card' || audience.length > 0);
  const write = useIdempotentWrite(
    (_: void, key) => {
      const common = { clinic_id: active.clinicId, title: title.trim(), body, synonyms: syn, patient_phrases: phr, audience };
      return editing
        ? unwrap(api.editKnowledgeDraft(draft.draft_id, common, { idempotencyKey: key })).then((r) => (r.result === 'invalid_state' ? 'invalid_state' : 'recorded'))
        : unwrap(api.createKnowledgeDraft({ ...common, kind: kind as Kind, code }, { idempotencyKey: key })).then(() => 'recorded');
    },
    (r) => onClose(r === 'invalid_state' ? t('knowledge.result.invalid_state') : editing ? t('knowledge.edited') : t('knowledge.created')),
  );
  const touch = <T,>(set: (v: T) => void) => (v: T) => {
    set(v);
    write.reset(); // a different request never reuses the old key
  };
  return (
    <section aria-labelledby="kn-form-title">
      <h2 id="kn-form-title">{editing ? t('knowledge.editTitle') : t('knowledge.newTitle')}</h2>
      <p>{t('knowledge.formBody')}</p>
      {!editing && (
        <>
          <label htmlFor="kn-kind">{t('knowledge.kind')}</label>
          {/* Nothing is pre-chosen. */}
          <select id="kn-kind" value={kind} onChange={(e) => touch(setKind)(KINDS.find((k) => k === e.target.value) ?? '')}>
            <option value="">{t('knowledge.chooseKind')}</option>
            {KINDS.map((k) => (
              <option key={k} value={k}>
                {word('knowledge.kinds', k)}
              </option>
            ))}
          </select>
          <label htmlFor="kn-code">{t('knowledge.code')}</label>
          <input id="kn-code" type="text" autoComplete="off" spellCheck={false} value={code} aria-describedby="kn-code-hint" aria-invalid={code !== '' && !CODE.test(code)} onChange={(e) => touch(setCode)(e.target.value)} />
          <p id="kn-code-hint" className="hint">
            {t('knowledge.codeHint')}
          </p>
        </>
      )}
      <label htmlFor="kn-title-in">{t('knowledge.titleLabel')}</label>
      <input id="kn-title-in" type="text" maxLength={200} autoComplete="off" value={title} onChange={(e) => touch(setTitle)(e.target.value)} />
      <label htmlFor="kn-body">{t('knowledge.bodyLabel')}</label>
      <textarea id="kn-body" rows={8} maxLength={4000} value={body} onChange={(e) => touch(setBody)(e.target.value)} />
      <label htmlFor="kn-syn">{t('knowledge.synonymsLabel')}</label>
      <textarea id="kn-syn" rows={3} value={synonyms} onChange={(e) => touch(setSynonyms)(e.target.value)} aria-describedby="kn-lines-hint" />
      <label htmlFor="kn-phr">{t('knowledge.phrasesLabel')}</label>
      <textarea id="kn-phr" rows={3} value={phrases} onChange={(e) => touch(setPhrases)(e.target.value)} aria-describedby="kn-lines-hint" />
      <p id="kn-lines-hint" className="hint">
        {t('knowledge.linesHint')}
      </p>
      <fieldset>
        <legend>{t('knowledge.audienceLabel')}</legend>
        {AUDIENCE.map((a) => (
          <label key={a} className="choice">
            <input
              type="checkbox"
              checked={audience.includes(a)}
              onChange={(e) => touch(setAudience)(e.target.checked ? [...audience, a] : audience.filter((x) => x !== a))}
            />
            <span>{word('knowledge.roles', a)}</span>
          </label>
        ))}
        {kind === 'card' && audience.length === 0 && <p className="hint">{t('knowledge.cardNeedsAudience')}</p>}
      </fieldset>
      <div className="toolbar">
        <button type="button" className="primary" disabled={!valid} onClick={() => setConfirm(true)}>
          {t('knowledge.save')}
        </button>
        <button type="button" onClick={() => onClose(null)}>
          {t('common.cancel')}
        </button>
      </div>
      {confirm && (
        <ConfirmDialog
          title={t('knowledge.saveTitle')}
          confirmLabel={t('knowledge.save')}
          busy={write.isPending}
          onConfirm={() => write.mutate()}
          onCancel={() => {
            write.reset();
            setConfirm(false);
          }}
          status={<WriteProblem problem={write.error} forbidden={t('knowledge.refused')} calm={{ conflict: t('knowledge.conflict'), invalid: t('knowledge.invalid'), unavailable: t('knowledge.off'), not_found: t('knowledge.gone') }} />}
        >
          <p>{t('knowledge.saveBody')}</p>
          <p>
            <code>{editing ? draft.code : code}</code> · {title}
          </p>
        </ConfirmDialog>
      )}
    </section>
  );
}

function SubmitDialog({ draft, onClose }: { draft: Draft; onClose: (message: string | null) => void }) {
  const { t } = useTranslation();
  const { api } = useAuth();
  const { active } = useSession();
  const write = useIdempotentWrite(
    (_: void, key) => unwrap(api.submitKnowledgeDraft(draft.draft_id, active.clinicId, { idempotencyKey: key })),
    (r) => onClose(t(`knowledge.result.${r.result}`)),
  );
  return (
    <ConfirmDialog
      title={t('knowledge.submitTitle')}
      confirmLabel={t('knowledge.submit')}
      busy={write.isPending}
      onConfirm={() => write.mutate()}
      onCancel={() => {
        write.reset();
        onClose(null);
      }}
      status={<WriteProblem problem={write.error} forbidden={t('knowledge.refused')} calm={{ not_found: t('knowledge.gone'), unavailable: t('knowledge.off'), invalid: t('knowledge.invalid') }} />}
    >
      <p>
        <code>{draft.code}</code> · {draft.title}
      </p>
      <p>{t('knowledge.submitBody')}</p>
    </ConfirmDialog>
  );
}

function DecisionDialog({ draft, decision, onClose }: { draft: Draft; decision: 'approve' | 'reject'; onClose: (message: string | null) => void }) {
  const { t } = useTranslation();
  const { api } = useAuth();
  const { active } = useSession();
  const [reason, setReason] = useState('');
  const approve = decision === 'approve';
  const write = useIdempotentWrite(
    (_: void, key) => unwrap(api.decideKnowledgeDraft(draft.draft_id, approve ? { clinic_id: active.clinicId, decision } : { clinic_id: active.clinicId, decision, reason }, { idempotencyKey: key })),
    (r) => onClose(r.result === 'recorded' && approve && r.version !== null ? t('knowledge.approvedAs', { version: r.version }) : t(`knowledge.result.${r.result}`)),
  );
  return (
    <ConfirmDialog
      title={t(approve ? 'knowledge.approveTitle' : 'knowledge.rejectTitle')}
      confirmLabel={t(approve ? 'knowledge.approve' : 'knowledge.reject')}
      busy={write.isPending}
      confirmDisabled={!approve && !REASON.test(reason)}
      onConfirm={() => write.mutate()}
      onCancel={() => {
        write.reset();
        onClose(null);
      }}
      status={<WriteProblem problem={write.error} forbidden={t('knowledge.decideRefused')} calm={{ not_found: t('knowledge.gone'), unavailable: t('knowledge.off'), invalid: t('knowledge.invalid') }} />}
    >
      <p>
        <code>{draft.code}</code> · {draft.title}
      </p>
      <DraftContent draft={draft} />
      <p>{t(approve ? 'knowledge.approveBody' : 'knowledge.rejectBody')}</p>
      {!approve && (
        <>
          <label htmlFor="kn-reason">{t('knowledge.reason')}</label>
          <input
            id="kn-reason"
            type="text"
            autoComplete="off"
            spellCheck={false}
            value={reason}
            aria-describedby="kn-reason-hint"
            onChange={(e) => {
              setReason(e.target.value);
              write.reset();
            }}
          />
          <p id="kn-reason-hint" className="hint">
            {t('knowledge.reasonHint')}
          </p>
        </>
      )}
    </ConfirmDialog>
  );
}
