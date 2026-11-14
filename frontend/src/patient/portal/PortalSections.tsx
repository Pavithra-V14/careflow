import { useEffect, useState, type FormEvent } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { VOICE_NOTICE_PURPOSE, usePortalQuery } from './portalQuery';
import { useTranslation } from 'react-i18next';
import type { Api } from '../../shared/api/client';
import type { components } from '../../shared/api/schema';
import { ApiProblem, toProblem, unwrap } from '../../staff/apiState';
import { useIdempotentWrite } from '../../staff/hooks';
import { ConfirmDialog } from '../../staff/ui/ConfirmDialog';
import { Badge, Time, UUID, useWord } from '../../staff/ui/bits';
import { ProblemText } from '../../staff/ui/states';
import { WriteProblem } from '../../staff/ui/WriteProblem';

type Stage = components['schemas']['PortalMeOut']['stage'];
type Notice = components['schemas']['NoticeOut'];

const Loading = () => {
  const { t } = useTranslation();
  return (
    <p role="status" className="loading">
      {t('common.loading')}
    </p>
  );
};

// ---- consent and activation ---------------------------------------------------------------------------------------------------------

/**
 * The current notice for each purpose, EXACTLY as the API sends it (text and version are shown as given, in a plain text block: no
 * markup is interpreted, nothing is shortened or reworded), with the person's decision on it. A decision is recorded against the
 * version shown. Nothing is pre-chosen. GRANTING asks for a confirmation first. WITHDRAWING is one click (BUILD-SPEC 20.1: withdrawal is one
 * click): it is recorded at once, the result is shown, and a "Grant again" button is right there.
 */
export function ConsentSection({ api, stage }: { api: Api; stage: Stage }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const word = useWord();
  const [acting, setActing] = useState<{ notice: Notice; action: 'granted' | 'withdrawn' } | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [withdrawn, setWithdrawn] = useState<Notice | null>(null); // the notice just withdrawn, so it can be granted again
  const notices = usePortalQuery(['portal-consents'], () => unwrap(api.portalConsents()));

  const record = useIdempotentWrite(
    (v: { notice: Notice; action: 'granted' | 'withdrawn' }, key) =>
      unwrap(api.recordConsent({ notice_id: v.notice.notice_id, purpose: v.notice.purpose as 'care_delivery', action: v.action }, { idempotencyKey: key })),
    () => {
      setActing(null);
      setNote(t('portal.consent.recorded'));
      void queryClient.invalidateQueries();
    },
  );

  const withdraw = useIdempotentWrite(
    (n: Notice, key) => unwrap(api.recordConsent({ notice_id: n.notice_id, purpose: n.purpose as 'care_delivery', action: 'withdrawn' }, { idempotencyKey: key })),
    () => {
      setNote(t('portal.consent.withdrawnDone'));
      void queryClient.invalidateQueries();
    },
  );
  // The voice session notice is per-session consent shown on the voice screen, not one of the account purposes listed here.
  const shown = (notices.data?.notices ?? []).filter((n) => n.purpose !== VOICE_NOTICE_PURPOSE);
  const withdrawConflict = withdraw.error?.kind === 'conflict';
  useEffect(() => {
    if (withdrawConflict) void queryClient.invalidateQueries(); // the notice changed: read the current one
  }, [withdrawConflict, queryClient]);

  return (
    <section aria-labelledby="portal-consent">
      <h2 id="portal-consent">{t('portal.consent.title')}</h2>
      {note && (
        <p role="status">
          {note}{' '}
          {withdrawn && (
            <button type="button" className="button button-secondary" onClick={() => { setNote(null); setActing({ notice: withdrawn, action: 'granted' }); }}>
              {t('portal.consent.regrant')}
            </button>
          )}
        </p>
      )}
      <WriteProblem problem={withdraw.error} calm={{ conflict: t('portal.consent.changed') }} />
      {notices.isPending && <Loading />}
      {notices.isError &&
        (notices.error.kind === 'forbidden' ? (
          <p role="status">{t('portal.notAvailable')}</p>
        ) : (
          <p role="alert">
            <ProblemText kind={notices.error.kind} />
          </p>
        ))}
      {notices.isSuccess && shown.length === 0 && <p>{t('portal.consent.none')}</p>}
      {notices.isSuccess &&
        shown.map((n) => (
          <article key={n.notice_id} className="notice" aria-label={n.label ?? n.purpose}>
            <h3>
              {n.label ?? <code>{n.purpose}</code>} · {t('portal.consent.version', { version: n.version })}
            </h3>
            {/* The notice text exactly as sent, as plain text. */}
            <div className="notice-text">{n.body}</div>
            <p>
              {t('portal.consent.decision')}:{' '}
              <Badge tone={n.decision === true ? 'ok' : n.decision === false ? 'warn' : 'neutral'}>{n.decision === true ? t('portal.consent.granted') : n.decision === false ? t('portal.consent.withdrawn') : t('portal.consent.noDecision')}</Badge>
            </p>
            <button type="button" className="button button-primary" disabled={n.decision === true} onClick={() => { setNote(null); setActing({ notice: n, action: 'granted' }); }}>
              {t('portal.consent.grant')}
            </button>{' '}
            <button
              type="button"
              className="button button-secondary"
              disabled={n.decision === false || withdraw.isPending}
              onClick={() => {
                setNote(null);
                withdraw.reset(); // a different notice is a different request: never reuse the old key
                setWithdrawn(n);
                withdraw.mutate(n);
              }}
            >
              {t('portal.consent.withdraw')}
            </button>
          </article>
        ))}
      {acting && (
        <ConfirmDialog
          title={t('portal.consent.grantTitle')}
          confirmLabel={t('portal.consent.grant')}
          busy={record.isPending}
          onConfirm={() => record.mutate(acting)}
          onCancel={() => {
            if (record.error?.kind === 'conflict') void queryClient.invalidateQueries();
            record.reset();
            setActing(null);
          }}
          status={<WriteProblem problem={record.error} calm={{ conflict: t('portal.consent.changed') }} />}
        >
          <p>
            {acting.notice.label ?? <code>{acting.notice.purpose}</code>} · {t('portal.consent.version', { version: acting.notice.version })}
          </p>
          <p>{t('portal.consent.confirmBody')}</p>
        </ConfirmDialog>
      )}
      {stage !== 'active' && <Activate api={api} />}
      <span className="visually-hidden">{word('portal.stage', stage)}</span>
    </section>
  );
}

function Activate({ api }: { api: Api }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const activate = useIdempotentWrite(
    (_: void, key) => unwrap(api.portalActivate({ idempotencyKey: key })),
    (r) => {
      setOpen(false);
      setNote(t(`portal.activate.${r.result}`));
      void queryClient.invalidateQueries();
    },
  );
  return (
    <div>
      <h3>{t('portal.activate.title')}</h3>
      <p>{t('portal.activate.body')}</p>
      {note && <p role="status">{note}</p>}
      <button type="button" className="button button-primary" onClick={() => { setNote(null); setOpen(true); }}>
        {t('portal.activate.button')}
      </button>
      {open && (
        <ConfirmDialog
          title={t('portal.activate.title')}
          confirmLabel={t('portal.activate.button')}
          busy={activate.isPending}
          onConfirm={() => activate.mutate()}
          onCancel={() => {
            activate.reset();
            setOpen(false);
          }}
          status={<WriteProblem problem={activate.error} />}
        >
          <p>{t('portal.activate.confirmBody')}</p>
        </ConfirmDialog>
      )}
    </div>
  );
}

/**
 * The hospital the patient is dealing with, from the public clinic route (its id and name), so the patient never types an id. If the owner
 * has marked no clinic public (404) or the read fails, the form falls back to asking for the clinic code the hospital gave the patient.
 */
export function useClinicField(api: Api, idPrefix: string, typed: string, setTyped: (v: string) => void) {
  const { t } = useTranslation();
  const clinic = usePortalQuery(['public-clinic'], () => unwrap(api.publicClinic()));
  const clinicId = clinic.data ? clinic.data.clinic_id : typed.trim();
  const node = clinic.isPending ? (
    <Loading />
  ) : clinic.data ? (
    <p>{t('portal.hospital', { name: clinic.data.name })}</p>
  ) : (
    <>
      <label htmlFor={`${idPrefix}-clinic`}>{t('portal.clinicCode')}</label>
      <input id={`${idPrefix}-clinic`} type="text" autoComplete="off" spellCheck={false} value={typed} onChange={(e) => setTyped(e.target.value)} />
    </>
  );
  return { clinicId, node, ready: !clinic.isPending };
}

// ---- claim a record: your verified phone, plus name and date of birth ---------------------------------------------------------------

export function ClaimSection({ api, bound }: { api: Api; bound: boolean }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [clinic, setClinic] = useState('');
  const [name, setName] = useState('');
  const [dob, setDob] = useState('');
  const [open, setOpen] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const claim = useIdempotentWrite(
    (clinicId: string, key) => unwrap(api.portalClaim({ clinic_id: clinicId, name: name.trim(), dob }, { idempotencyKey: key })),
    (r) => {
      setOpen(false);
      setNote(t(`portal.claim.outcome.${r.outcome}`));
      setName('');
      setDob('');
      void queryClient.invalidateQueries();
    },
  );
  const clinicField = useClinicField(api, 'claim', clinic, (v) => {
    setClinic(v);
    claim.reset();
  });
  const valid = UUID.test(clinicField.clinicId) && name.trim() !== '' && dob !== '';
  const touch = (set: (v: string) => void) => (v: string) => {
    set(v);
    claim.reset(); // a different request never reuses the old key
  };
  return (
    <section aria-labelledby="portal-claim">
      <h2 id="portal-claim">{t('portal.claim.title')}</h2>
      {bound ? (
        <p role="status">{t('portal.claim.alreadyLinked')}</p>
      ) : (
        <form
          onSubmit={(e: FormEvent) => {
            e.preventDefault();
            if (valid) setOpen(true);
          }}
          autoComplete="off"
          noValidate
        >
          <p>{t('portal.claim.body')}</p>
          {note && <p role="status">{note}</p>}
          {clinicField.node}
          <label htmlFor="claim-name">{t('portal.claim.name')}</label>
          <input id="claim-name" type="text" autoComplete="off" value={name} onChange={(e) => touch(setName)(e.target.value)} />
          <label htmlFor="claim-dob">{t('portal.claim.dob')}</label>
          <input id="claim-dob" type="date" autoComplete="off" value={dob} onChange={(e) => touch(setDob)(e.target.value)} />
          <button type="submit" className="button button-primary" disabled={!valid}>
            {t('portal.claim.button')}
          </button>
        </form>
      )}
      {open && (
        <ConfirmDialog
          title={t('portal.claim.title')}
          confirmLabel={t('portal.claim.button')}
          busy={claim.isPending}
          onConfirm={() => claim.mutate(clinicField.clinicId)}
          onCancel={() => {
            claim.reset();
            setOpen(false);
          }}
          status={<WriteProblem problem={claim.error} forbidden={t('portal.claim.refused')} calm={{ unavailable: t('portal.claim.unavailable'), invalid: t('portal.claim.invalid') }} />}
        >
          <p>{t('portal.claim.confirmBody')}</p>
        </ConfirmDialog>
      )}
    </section>
  );
}

// ---- grievances ------------------------------------------------------------------------------------------------------------------------

export function GrievanceSection({ api }: { api: Api }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const word = useWord();
  const [clinic, setClinic] = useState('');
  const [category, setCategory] = useState('');
  const [text, setText] = useState('');
  const [open, setOpen] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const mine = usePortalQuery(['portal-grievances'], () => unwrap(api.listMyGrievances()));
  const file = useIdempotentWrite(
    (clinicId: string, key) => unwrap(api.fileGrievance({ clinic_id: clinicId, category: category.trim(), text }, { idempotencyKey: key })),
    () => {
      setOpen(false);
      setNote(t('portal.grievance.filed'));
      setText(''); // the text is dropped from the page once it is filed
      void queryClient.invalidateQueries();
    },
  );
  const clinicField = useClinicField(api, 'gr', clinic, (v) => {
    setClinic(v);
    file.reset();
  });
  // The category words come from the hospital's own list. The words stay open-ended (a new one may appear), so each is shown humanised.
  // If the list is not set up (503) nothing can be filed; if it cannot be read for another reason, a typed code is the fallback.
  const categories = usePortalQuery(['grievance-categories'], () => unwrap(api.grievanceCategories()));
  const listed = categories.data?.categories ?? null;
  const notSetUp = categories.isError && categories.error.kind === 'unavailable';
  const valid = UUID.test(clinicField.clinicId) && /^[a-z][a-z0-9_]{0,39}$/.test(category.trim()) && !notSetUp && text.trim() !== '' && text.length <= 4000;
  const touch = (set: (v: string) => void) => (v: string) => {
    set(v);
    file.reset();
  };
  return (
    <section aria-labelledby="portal-grievance">
      <h2 id="portal-grievance">{t('portal.grievance.title')}</h2>
      <p>{t('portal.grievance.body')}</p>
      {note && <p role="status">{note}</p>}
      <form
        onSubmit={(e: FormEvent) => {
          e.preventDefault();
          if (valid) setOpen(true);
        }}
        autoComplete="off"
        noValidate
      >
        {clinicField.node}
        <label htmlFor="gr-category">{t('portal.grievance.category')}</label>
        {listed ? (
          // Nothing is pre-chosen: the person picks the category that fits.
          <select id="gr-category" value={category} onChange={(e) => touch(setCategory)(e.target.value)}>
            <option value="">{t('portal.grievance.chooseCategory')}</option>
            {listed.map((c) => (
              <option key={c} value={c}>
                {c.replaceAll('_', ' ')}
              </option>
            ))}
          </select>
        ) : notSetUp ? (
          <p role="status">{t('portal.grievance.unavailable')}</p>
        ) : categories.isPending ? (
          <Loading />
        ) : (
          <>
            <input id="gr-category" type="text" autoComplete="off" spellCheck={false} value={category} onChange={(e) => touch(setCategory)(e.target.value)} aria-describedby="gr-category-hint" />
            <p id="gr-category-hint" className="hint">
              {t('portal.grievance.categoryHint')}
            </p>
          </>
        )}
        <label htmlFor="gr-text">{t('portal.grievance.text')}</label>
        <textarea id="gr-text" rows={6} maxLength={4000} value={text} onChange={(e) => touch(setText)(e.target.value)} />
        <button type="submit" className="button button-primary" disabled={!valid}>
          {t('portal.grievance.file')}
        </button>
      </form>
      <h3>{t('portal.grievance.mine')}</h3>
      {mine.isPending && <Loading />}
      {mine.isError &&
        (mine.error.kind === 'forbidden' ? (
          <p role="status">{t('portal.notAvailable')}</p>
        ) : (
          <p role="alert">
            <ProblemText kind={mine.error.kind} />
          </p>
        ))}
      {mine.isSuccess && mine.data.grievances.length === 0 && <p>{t('portal.grievance.none')}</p>}
      {mine.isSuccess && mine.data.grievances.length > 0 && (
        <ul className="plain-list">
          {mine.data.grievances.map((g) => (
            <li key={g.grievance_id}>
              <Badge tone={g.status === 'resolved' ? 'ok' : g.status === 'open' ? 'warn' : 'neutral'}>{word('portal.grievance.status', g.status)}</Badge> · <Time value={g.created_at} />
              <br />
              {g.erased ? <em>{t('portal.erased')}</em> : g.text === null ? <em>{t('portal.unreadable')}</em> : g.text}
            </li>
          ))}
        </ul>
      )}
      {open && (
        <ConfirmDialog
          title={t('portal.grievance.file')}
          confirmLabel={t('portal.grievance.file')}
          busy={file.isPending}
          onConfirm={() => file.mutate(clinicField.clinicId)}
          onCancel={() => {
            file.reset();
            setOpen(false);
          }}
          status={<WriteProblem problem={file.error} forbidden={t('portal.grievance.refused')} calm={{ invalid: t('portal.grievance.categoryRefused'), unavailable: t('portal.grievance.unavailable') }} />}
        >
          <p>{t('portal.grievance.confirmBody')}</p>
        </ConfirmDialog>
      )}
    </section>
  );
}

// ---- download my data -----------------------------------------------------------------------------------------------------------------

/**
 * Fetches everything the system holds about the person that they may see, offers it as one JSON file exactly as the API sent it, and
 * keeps nothing: the data is held only while the file is made, then dropped. Each download is an audit row on the backend.
 */
export function MyDataSection({ api }: { api: Api }) {
  const { t } = useTranslation();
  const [summary, setSummary] = useState<{ bookings: number; messages: number; consents: number; grievances: number; records: number; truncated: boolean; history: components['schemas']['ExportConsentOut'][] } | null>(null);
  const download = useMutation<components['schemas']['MyDataOut'], ApiProblem, void>({
    gcTime: 0,
    retry: false,
    mutationFn: async () => {
      try {
        return await unwrap(api.myData());
      } catch (e) {
        throw toProblem(e);
      }
    },
    onSuccess: (data) => {
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = 'my-data.json';
      document.body.append(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
      setSummary({ bookings: data.bookings.length, messages: data.messages.length, consents: data.consents.length, grievances: data.grievances.length, records: data.records.length, truncated: data.truncated, history: data.consents });
    },
  });
  return (
    <section aria-labelledby="portal-mydata">
      <h2 id="portal-mydata">{t('portal.myData.title')}</h2>
      <p>{t('portal.myData.body')}</p>
      <button
        type="button"
        className="button button-primary"
        disabled={download.isPending}
        onClick={() => {
          setSummary(null);
          download.mutate();
        }}
      >
        {download.isPending ? t('common.working') : t('portal.myData.button')}
      </button>
      {download.isError &&
        (download.error.kind === 'forbidden' ? (
          <p role="status">{t('portal.notAvailable')}</p>
        ) : (
          <p role="alert">
            <ProblemText kind={download.error.kind} />
          </p>
        ))}
      {summary && (
        <div role="status">
          <p>{t('portal.myData.done')}</p>
          <ul className="plain-list">
            <li>{t('portal.myData.count.records', { count: summary.records })}</li>
            <li>{t('portal.myData.count.bookings', { count: summary.bookings })}</li>
            <li>{t('portal.myData.count.messages', { count: summary.messages })}</li>
            <li>{t('portal.myData.count.consents', { count: summary.consents })}</li>
            <li>{t('portal.myData.count.grievances', { count: summary.grievances })}</li>
          </ul>
          {summary.truncated && <p>{t('portal.myData.truncated')}</p>}
          <ConsentHistory rows={summary.history} />
        </div>
      )}
    </section>
  );
}

// The person's own consent history, taken from the file they just asked for (no extra call, so no extra audit row). Newest first as sent.
function ConsentHistory({ rows }: { rows: components['schemas']['ExportConsentOut'][] }) {
  const { t } = useTranslation();
  const word = useWord();
  if (rows.length === 0) return <p>{t('portal.myData.historyNone')}</p>;
  return (
    <table>
      <caption>{t('portal.myData.history')}</caption>
      <thead>
        <tr>
          <th scope="col">{t('portal.myData.col.purpose')}</th>
          <th scope="col">{t('portal.myData.col.decision')}</th>
          <th scope="col">{t('portal.myData.col.version')}</th>
          <th scope="col">{t('portal.myData.col.when')}</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r, i) => (
          <tr key={`${r.purpose}-${r.occurred_at}-${i}`}>
            <th scope="row">{word('portal.myData.purpose', r.purpose)}</th>
            <td>{word('portal.consent', r.action)}</td>
            <td>{r.notice_version}</td>
            <td>
              <Time value={r.occurred_at} />
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
