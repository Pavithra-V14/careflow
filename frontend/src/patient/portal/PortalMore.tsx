import { useState, type FormEvent } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import type { Api } from '../../shared/api/client';
import type { components } from '../../shared/api/schema';
import { unwrap, type ApiProblem } from '../../staff/apiState';
import { useIdempotentWrite } from '../../staff/hooks';
import { ConfirmDialog } from '../../staff/ui/ConfirmDialog';
import { Badge, Time, UUID, useWord } from '../../staff/ui/bits';
import { ProblemText } from '../../staff/ui/states';
import { WriteProblem } from '../../staff/ui/WriteProblem';
import { usePortalQuery } from './portalQuery';
import { useClinicField } from './PortalSections';

type Profile = components['schemas']['ProfileOut'];
type Notification = components['schemas']['NotificationOut'];
type RequestKind = components['schemas']['PatientRequestIn']['kind'];
type RequestField = NonNullable<components['schemas']['PatientRequestIn']['field']>;

const REQUEST_KINDS: readonly RequestKind[] = ['erasure', 'correction', 'nominee'];
const REQUEST_FIELDS: readonly RequestField[] = ['name', 'date_of_birth', 'phone'];
const LANGUAGE_NAMES: Record<string, string> = { en: 'English', ta: 'தமிழ்' }; // each language in its own script, in both display languages

const Loading = () => {
  const { t } = useTranslation();
  return (
    <p role="status" className="loading">
      {t('common.loading')}
    </p>
  );
};

/** A failed read, in the fixed words for its kind. 403 is the calm "not available" line. */
function ReadProblem({ problem }: { problem: ApiProblem }) {
  const { t } = useTranslation();
  return problem.kind === 'forbidden' ? (
    <p role="status">{t('portal.notAvailable')}</p>
  ) : (
    <p role="alert">
      <ProblemText kind={problem.kind} />
    </p>
  );
}

// ---- your details: the name on the record (read-only), the sex word and the preferred language --------------------------------------

export function ProfileSection({ api }: { api: Api }) {
  const { t } = useTranslation();
  const profile = usePortalQuery(['portal-profile'], () => unwrap(api.getProfile()));
  return (
    <section aria-labelledby="portal-profile">
      <h2 id="portal-profile">{t('portal.profile.title')}</h2>
      {profile.isPending && <Loading />}
      {profile.isError && <ReadProblem problem={profile.error} />}
      {profile.isSuccess && <ProfileForm api={api} profile={profile.data} />}
    </section>
  );
}

function ProfileForm({ api, profile }: { api: Api; profile: Profile }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [sex, setSex] = useState(profile.sex ?? '');
  const [language, setLanguage] = useState(profile.preferred_language ?? '');
  const [note, setNote] = useState<string | null>(null);
  const save = useIdempotentWrite(
    (_: void, key) =>
      unwrap(
        api.putProfile(
          // Only what the person changed: an unchanged field is left out, so nothing depends on what null would mean.
          {
            ...(sex !== (profile.sex ?? '') && sex !== '' ? { sex } : {}),
            ...(language !== (profile.preferred_language ?? '') && (language === 'en' || language === 'ta') ? { preferred_language: language } : {}),
          },
          { idempotencyKey: key },
        ),
      ),
    () => {
      setNote(t('portal.profile.saved'));
      void queryClient.invalidateQueries({ queryKey: ['portal-profile'] });
    },
  );
  // Choosing "Choose" again is not a change: clearing a value is not offered, because the route does not say what null does (backend request 32).
  const changed = (sex !== (profile.sex ?? '') && sex !== '') || (language !== (profile.preferred_language ?? '') && language !== '');
  const touch = (set: (v: string) => void) => (v: string) => {
    set(v);
    setNote(null);
    save.reset(); // a different request never reuses the old key
  };
  return (
    <form
      onSubmit={(e: FormEvent) => {
        e.preventDefault();
        if (changed) save.mutate();
      }}
      noValidate
    >
      <dl className="facts">
        <dt>{t('portal.profile.name')}</dt>
        <dd>{profile.display_name ?? t('portal.profile.noName')}</dd>
      </dl>
      <label htmlFor="profile-sex">{t('portal.profile.sex')}</label>
      {profile.sex_words.length === 0 ? (
        <p id="profile-sex" role="status">
          {t('portal.profile.noSexList')}
        </p>
      ) : (
        // Nothing is chosen for the person: the box shows what is saved, or "Choose".
        <select id="profile-sex" value={sex} onChange={(e) => touch(setSex)(e.target.value)}>
          <option value="">{t('portal.profile.choose')}</option>
          {profile.sex_words.map((w) => (
            <option key={w} value={w}>
              {w.replaceAll('_', ' ')}
            </option>
          ))}
          {profile.sex !== null && !profile.sex_words.includes(profile.sex) && <option value={profile.sex}>{profile.sex.replaceAll('_', ' ')}</option>}
        </select>
      )}
      <label htmlFor="profile-language">{t('portal.profile.language')}</label>
      <select id="profile-language" value={language} onChange={(e) => touch(setLanguage)(e.target.value)}>
        <option value="">{t('portal.profile.choose')}</option>
        {profile.languages.map((l) => (
          <option key={l} value={l}>
            {LANGUAGE_NAMES[l] ?? l}
          </option>
        ))}
      </select>
      {note && <p role="status">{note}</p>}
      {save.error && (
        <WriteProblem problem={save.error} forbidden={t('portal.notAvailable')} calm={{ invalid: t('portal.profile.invalid'), unavailable: t('portal.profile.unavailable') }} />
      )}
      <button type="submit" className="button button-primary" disabled={!changed || save.isPending}>
        {save.isPending ? t('common.working') : t('portal.profile.save')}
      </button>
    </form>
  );
}

// ---- in-app notices: a closed kind and an opaque reference; no text comes from the API ------------------------------------------------

export function NotificationsSection({ api }: { api: Api }) {
  const { t } = useTranslation();
  const list = usePortalQuery(['portal-notifications'], () => unwrap(api.listNotifications()));
  const unread = list.data ? list.data.notifications.filter((n) => !n.read).length : 0;
  return (
    <section aria-labelledby="portal-notifications">
      <h2 id="portal-notifications">{t('portal.notifications.title')}</h2>
      {list.isPending && <Loading />}
      {list.isError && <ReadProblem problem={list.error} />}
      {list.isSuccess && list.data.notifications.length === 0 && <p>{t('portal.notifications.none')}</p>}
      {list.isSuccess && list.data.notifications.length > 0 && (
        <>
          <p role="status">{t('portal.notifications.unread', { n: unread })}</p>
          <ul className="plain-list">
            {list.data.notifications.map((n) => (
              <NotificationRow key={n.notification_id} api={api} n={n} />
            ))}
          </ul>
        </>
      )}
    </section>
  );
}

function NotificationRow({ api, n }: { api: Api; n: Notification }) {
  const { t } = useTranslation();
  const word = useWord();
  const queryClient = useQueryClient();
  const read = useIdempotentWrite(
    (_: void, key) => unwrap(api.readNotification(n.notification_id, { idempotencyKey: key })),
    () => void queryClient.invalidateQueries({ queryKey: ['portal-notifications'] }),
  );
  return (
    <li>
      <Badge tone={n.read ? 'neutral' : 'warn'}>{n.read ? t('portal.notifications.read') : t('portal.notifications.new')}</Badge> {word('portal.notifications.kind', n.kind)} ·{' '}
      <Time value={n.created_at} />
      {!n.read && (
        <>
          {' '}
          <button type="button" className="button button-secondary" disabled={read.isPending} aria-label={`${t('portal.notifications.markRead')}: ${word('portal.notifications.kind', n.kind)}`} onClick={() => read.mutate()}>
            {read.isPending ? t('common.working') : t('portal.notifications.markRead')}
          </button>
        </>
      )}
      {read.error && <WriteProblem problem={read.error} forbidden={t('portal.notAvailable')} calm={{ not_found: t('portal.notifications.gone') }} />}
    </li>
  );
}

// ---- requests: erase my record, correct a detail, record a nominee. No free text; a correction names the detail, never the value --------

export function RequestsSection({ api }: { api: Api }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const word = useWord();
  const [clinic, setClinic] = useState('');
  const [kind, setKind] = useState<RequestKind | ''>('');
  const [field, setField] = useState<RequestField | ''>('');
  const [open, setOpen] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const mine = usePortalQuery(['portal-requests'], () => unwrap(api.listMyRequests()));
  const file = useIdempotentWrite(
    (v: { clinicId: string; kind: RequestKind; field: RequestField | null }, key) =>
      unwrap(api.filePatientRequest({ clinic_id: v.clinicId, kind: v.kind, field: v.field }, { idempotencyKey: key })),
    (r) => {
      setOpen(false);
      setNote(r.created ? t('portal.requests.sent') : t('portal.requests.already'));
      void queryClient.invalidateQueries({ queryKey: ['portal-requests'] });
    },
  );
  const clinicField = useClinicField(api, 'rq', clinic, (v) => {
    setClinic(v);
    file.reset();
  });
  const valid = UUID.test(clinicField.clinicId) && kind !== '' && (kind !== 'correction' || field !== '');
  return (
    <section aria-labelledby="portal-requests">
      <h2 id="portal-requests">{t('portal.requests.title')}</h2>
      <p>{t('portal.requests.body')}</p>
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
        <label htmlFor="rq-kind">{t('portal.requests.kind')}</label>
        {/* Nothing is pre-chosen: the person picks what they are asking for. */}
        <select
          id="rq-kind"
          value={kind}
          onChange={(e) => {
            setKind(REQUEST_KINDS.find((k) => k === e.target.value) ?? '');
            setField('');
            setNote(null);
            file.reset();
          }}
        >
          <option value="">{t('portal.requests.chooseKind')}</option>
          {REQUEST_KINDS.map((k) => (
            <option key={k} value={k}>
              {t(`portal.requests.kinds.${k}`)}
            </option>
          ))}
        </select>
        {kind === 'correction' && (
          <>
            <label htmlFor="rq-field">{t('portal.requests.field')}</label>
            <select
              id="rq-field"
              value={field}
              onChange={(e) => {
                setField(REQUEST_FIELDS.find((f) => f === e.target.value) ?? '');
                file.reset();
              }}
            >
              <option value="">{t('portal.requests.chooseField')}</option>
              {REQUEST_FIELDS.map((f) => (
                <option key={f} value={f}>
                  {t(`portal.requests.fields.${f}`)}
                </option>
              ))}
            </select>
          </>
        )}
        <button type="submit" className="button button-primary" disabled={!valid}>
          {t('portal.requests.send')}
        </button>
      </form>
      <h3>{t('portal.requests.mine')}</h3>
      {mine.isPending && <Loading />}
      {mine.isError && <ReadProblem problem={mine.error} />}
      {mine.isSuccess && mine.data.requests.length === 0 && <p>{t('portal.requests.none')}</p>}
      {mine.isSuccess && mine.data.requests.length > 0 && (
        <ul className="plain-list">
          {mine.data.requests.map((r) => (
            <li key={r.request_id}>
              <Badge tone={r.status === 'resolved' ? 'ok' : r.status === 'open' ? 'warn' : 'neutral'}>{word('portal.requests.status', r.status)}</Badge> {t(`portal.requests.kinds.${r.kind}`)}
              {r.field && <> · {t(`portal.requests.fields.${r.field}`, { defaultValue: r.field })}</>} · <Time value={r.created_at} />
            </li>
          ))}
        </ul>
      )}
      {open && (
        <ConfirmDialog
          title={t('portal.requests.confirmTitle')}
          confirmLabel={t('portal.requests.send')}
          busy={file.isPending}
          onConfirm={() => kind !== '' && file.mutate({ clinicId: clinicField.clinicId, kind, field: kind === 'correction' && field !== '' ? field : null })}
          onCancel={() => {
            file.reset();
            setOpen(false);
          }}
          status={<WriteProblem problem={file.error} forbidden={t('portal.requests.refused')} calm={{ invalid: t('portal.requests.invalid') }} />}
        >
          <p>{kind === '' ? '' : t(`portal.requests.kinds.${kind}`)}</p>
          <p>{t('portal.requests.confirmBody')}</p>
        </ConfirmDialog>
      )}
    </section>
  );
}

// ---- a grievance without signing in, to the hospital's published clinic -------------------------------------------------------------

export function PublicGrievanceSection({ api }: { api: Api }) {
  const { t } = useTranslation();
  const [category, setCategory] = useState('');
  const [text, setText] = useState('');
  const [open, setOpen] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  // The words come from the hospital's own list, with no sign-in. Nothing is typed and nothing is pre-chosen.
  const categories = usePortalQuery(['public-grievance-categories'], () => unwrap(api.publicGrievanceCategories()));
  const listed = categories.data?.categories ?? [];
  const send = useIdempotentWrite(
    (_: void, key) => unwrap(api.filePublicGrievance({ category: category.trim(), text }, { idempotencyKey: key })),
    () => {
      setOpen(false);
      setNote(t('portal.publicGrievance.filed'));
      setText(''); // the text is dropped from the page once it is sent
    },
  );
  const valid = listed.includes(category) && text.trim() !== '' && text.length <= 4000;
  const touch = (set: (v: string) => void) => (v: string) => {
    set(v);
    setNote(null);
    send.reset();
  };
  return (
    <section aria-labelledby="portal-public-grievance">
      <h2 id="portal-public-grievance">{t('portal.publicGrievance.title')}</h2>
      <p>{t('portal.publicGrievance.body')}</p>
      {note && <p role="status">{note}</p>}
      <form
        onSubmit={(e: FormEvent) => {
          e.preventDefault();
          if (valid) setOpen(true);
        }}
        autoComplete="off"
        noValidate
      >
        <label htmlFor="pg-category">{t('portal.grievance.category')}</label>
        {categories.isPending ? (
          <Loading />
        ) : categories.isError ? (
          categories.error.kind === 'unavailable' ? (
            <p id="pg-category" role="status">
              {t('portal.publicGrievance.notSetUp')}
            </p>
          ) : (
            <p id="pg-category" role="alert">
              <ProblemText kind={categories.error.kind} />
            </p>
          )
        ) : listed.length === 0 ? (
          <p id="pg-category" role="status">
            {t('portal.publicGrievance.noCategories')}
          </p>
        ) : (
          <select id="pg-category" value={category} onChange={(e) => touch(setCategory)(e.target.value)}>
            <option value="">{t('portal.grievance.chooseCategory')}</option>
            {listed.map((c) => (
              <option key={c} value={c}>
                {c.replaceAll('_', ' ')}
              </option>
            ))}
          </select>
        )}
        <label htmlFor="pg-text">{t('portal.grievance.text')}</label>
        <textarea id="pg-text" rows={5} maxLength={4000} value={text} onChange={(e) => touch(setText)(e.target.value)} />
        <button type="submit" className="button button-primary" disabled={!valid}>
          {t('portal.grievance.file')}
        </button>
      </form>
      {open && (
        <ConfirmDialog
          title={t('portal.publicGrievance.title')}
          confirmLabel={t('portal.grievance.file')}
          busy={send.isPending}
          onConfirm={() => send.mutate()}
          onCancel={() => {
            send.reset();
            setOpen(false);
          }}
          status={
            <WriteProblem
              problem={send.error}
              forbidden={t('portal.publicGrievance.refused')}
              calm={{ not_found: t('portal.publicGrievance.noClinic'), invalid: t('portal.grievance.categoryRefused'), unavailable: t('portal.publicGrievance.unavailable') }}
            />
          }
        >
          <p>{t('portal.publicGrievance.confirmBody')}</p>
        </ConfirmDialog>
      )}
    </section>
  );
}
