import { useId, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import type { components } from '../../shared/api/schema';
import { unwrap } from '../apiState';
import { useAuth } from '../auth/AuthProvider';
import { useApiQuery, useIdempotentWrite } from '../hooks';
import { useSession } from '../session/SessionContext';
import { ConfirmDialog } from '../ui/ConfirmDialog';
import { PageHeading, ShortId, Time, UUID } from '../ui/bits';
import { QueryView } from '../ui/states';
import { WriteProblem } from '../ui/WriteProblem';
import { usePatientHint } from './CareGapActions';

type Note = components['schemas']['NoteRowOut'];

/**
 * A patient's clinical notes (nurse and doctor). Notes are APPEND-ONLY: there is no edit and no delete. A correction is an addendum,
 * a new note that names the one it adds to. The text is the clinician's own, shown as written while the keys are loaded and the note
 * has not been erased; an erased or unreadable note says so and never shows a blank that could be read as an empty note.
 */
export function NotesPage() {
  const { t } = useTranslation();
  const id = useId();
  const hint = usePatientHint();
  const [text, setText] = useState(hint);
  const [patientId, setPatientId] = useState<string | null>(hint || null);
  const bad = text.trim() !== '' && !UUID.test(text.trim());
  return (
    <section aria-labelledby="notes-title">
      <PageHeading id="notes-title">{t('notes.title')}</PageHeading>
      <p className="hint">{t('notes.note')}</p>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          if (UUID.test(text.trim())) setPatientId(text.trim().toLowerCase());
        }}
      >
        <label htmlFor={id}>{t('care.patientId')}</label>
        <input id={id} type="text" autoComplete="off" spellCheck={false} value={text} aria-invalid={bad || undefined} onChange={(e) => setText(e.target.value)} />
        {bad && <p className="form-error">{t('care.patientIdBad')}</p>}
        <button type="submit" className="primary" disabled={!UUID.test(text.trim())}>
          {t('notes.open')}
        </button>
      </form>
      {patientId && <PatientNotes key={patientId} patientId={patientId} />}
    </section>
  );
}

function PatientNotes({ patientId }: { patientId: string }) {
  const { t } = useTranslation();
  const { api } = useAuth();
  const { active } = useSession();
  const queryClient = useQueryClient();
  const [writing, setWriting] = useState<{ parent: Note | null } | null>(null);
  const [done, setDone] = useState(false);
  const query = useApiQuery(['notes', patientId], () => unwrap(api.listNotes(active.clinicId, patientId)));
  return (
    <>
      <div className="toolbar">
        <button
          type="button"
          onClick={() => {
            setDone(false);
            setWriting({ parent: null });
          }}
        >
          {t('notes.write')}
        </button>
        <button type="button" onClick={() => void query.refetch()} disabled={query.isFetching}>
          {t('common.refresh')}
        </button>
      </div>
      {done && <p role="status">{t('notes.saved')}</p>}
      <QueryView query={query} isEmpty={(d) => d.notes.length === 0} empty={t('notes.empty')}>
        {(d) => (
          <ol className="notes" aria-label={t('notes.title')}>
            {d.notes.map((n) => (
              <li key={n.note_id} className={n.parent_id ? 'addendum' : undefined}>
                <p className="hint">
                  <ShortId id={n.note_id} /> · <Time value={n.created_at} /> · {t('notes.by')} <ShortId id={n.author_id} />
                  {n.parent_id && (
                    <>
                      {' '}
                      · {t('notes.addendumTo')} <ShortId id={n.parent_id} />
                    </>
                  )}
                </p>
                <p className="note-text">{n.erased ? <em>{t('notes.erased')}</em> : n.text === null ? <em>{t('notes.unreadable')}</em> : n.text}</p>
                <button type="button" aria-label={`${t('notes.addend')} ${n.note_id.slice(0, 8)}`} onClick={() => { setDone(false); setWriting({ parent: n }); }}>
                  {t('notes.addend')}
                </button>
              </li>
            ))}
          </ol>
        )}
      </QueryView>
      {writing && (
        <WriteDialog
          patientId={patientId}
          parent={writing.parent}
          onClose={(ok) => {
            setWriting(null);
            if (ok) {
              setDone(true);
              void queryClient.invalidateQueries();
            }
          }}
        />
      )}
    </>
  );
}

function WriteDialog({ patientId, parent, onClose }: { patientId: string; parent: Note | null; onClose: (ok: boolean) => void }) {
  const { t } = useTranslation();
  const { api } = useAuth();
  const { active } = useSession();
  const [text, setText] = useState('');
  const write = useIdempotentWrite(
    (_: void, key) => unwrap(api.writeNote({ clinic_id: active.clinicId, patient_id: patientId, parent_id: parent ? parent.note_id : null, text }, { idempotencyKey: key })),
    () => onClose(true),
  );
  return (
    <ConfirmDialog
      title={t(parent ? 'notes.addend' : 'notes.write')}
      confirmLabel={t('notes.save')}
      busy={write.isPending}
      confirmDisabled={text.trim() === ''}
      onConfirm={() => write.mutate()}
      onCancel={() => {
        write.reset(); // the typed text is dropped with the dialog
        onClose(false);
      }}
      status={<WriteProblem problem={write.error} calm={{ unavailable: t('notes.keysMissing'), not_found: t('notes.notFound') }} />}
    >
      <p>{t(parent ? 'notes.addendBody' : 'notes.writeBody')}</p>
      <p className="hint">{t('admin.stepUp')}</p>
      <label htmlFor="note-text">{t('notes.text')}</label>
      <textarea
        id="note-text"
        rows={8}
        value={text}
        onChange={(e) => {
          setText(e.target.value);
          write.reset();
        }}
      />
    </ConfirmDialog>
  );
}
