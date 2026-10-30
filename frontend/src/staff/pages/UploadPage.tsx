import { useId, useState, type FormEvent } from 'react';
import { Link } from 'react-router';
import { useTranslation } from 'react-i18next';
import type { components } from '../../shared/api/schema';
import { readConfig } from '../../shared/config';
import { ApiProblem, unwrap } from '../apiState';
import { useAuth } from '../auth/AuthProvider';
import { useIdempotentWrite } from '../hooks';
import { CAN_UPLOAD } from '../sections';
import { useSession } from '../session/SessionContext';
import { Badge, PageHeading } from '../ui/bits';
import { NotAvailableToRole, ProblemText } from '../ui/states';
import { TYPE_LABEL } from './ReferralsPage';
import { formatNumber } from '../../shared/i18n/format';
import { useLang } from '../ui/bits';

type Kind = components['schemas']['BeginUploadIn']['kind'];
type ContentType = components['schemas']['BeginUploadIn']['content_type'];
type Registered = components['schemas']['RegisteredOut'];
const KINDS: readonly Kind[] = ['referral', 'report', 'other'];
const isContentType = (v: string): v is ContentType => v in TYPE_LABEL;

/**
 * The file is sent to the URL the backend returned, so that URL is checked first: it must be on the Supabase project
 * origin (the only storage host this build is configured for; the CSP would also block any other). Anything else is
 * refused before a byte is sent.
 */
export function isAllowedUploadUrl(url: string, allowedOrigin: string): boolean {
  try {
    const u = new URL(url);
    return allowedOrigin !== '' && u.protocol === 'https:' && u.origin === allowedOrigin;
  } catch {
    return false;
  }
}

/**
 * Scan status, shown exactly as the server reports it. ONLY the word "clean" is shown as clean: "not scanned" and
 * "not checked" (and anything this build does not know) are shown as not scanned, never as fine.
 */
export function ScanStatus({ scan }: { scan: string }) {
  const { t } = useTranslation();
  if (scan === 'clean') return <Badge tone="ok">{t('upload.scan.clean')}</Badge>;
  return <Badge tone="warn">{scan === 'not_scanned' ? t('upload.scan.not_scanned') : t('upload.scan.not_checked')}</Badge>;
}

export function UploadPage() {
  const { t } = useTranslation();
  const { api } = useAuth();
  const { active, hasRole } = useSession();
  const lang = useLang();
  const id = useId();
  const [file, setFile] = useState<File | null>(null);
  const [kind, setKind] = useState<Kind>('referral');
  const [localError, setLocalError] = useState<'type' | 'empty' | 'none' | null>(null);
  const [phase, setPhase] = useState<'idle' | 'preparing' | 'sending' | 'checking'>('idle');
  const [inputKey, setInputKey] = useState(0);

  const allowedOrigin = (() => {
    try {
      return new URL(readConfig().supabaseUrl).origin;
    } catch {
      return '';
    }
  })();

  const upload = useIdempotentWrite<{ file: File; kind: Kind; contentType: ContentType }, Registered>(async ({ file: f, kind: k, contentType }, key) => {
    try {
      setPhase('preparing');
      // Both steps share ONE key: the server ties the registered row to the ticket by it, and a retry replays.
      const begin = await unwrap(api.beginUpload({ clinic_id: active.clinicId, content_type: contentType, kind: k, size_hint: f.size }, { idempotencyKey: key }));
      if (!isAllowedUploadUrl(begin.upload_url, allowedOrigin)) throw new ApiProblem('unknown');
      setPhase('sending');
      let put: Response;
      try {
        put = await fetch(begin.upload_url, { method: 'PUT', headers: { 'Content-Type': contentType }, body: f, credentials: 'omit', referrerPolicy: 'no-referrer' });
      } catch {
        throw new ApiProblem('network');
      }
      if (!put.ok) throw new ApiProblem(put.status >= 500 ? 'unavailable' : 'unknown');
      setPhase('checking');
      return await unwrap(api.registerDocument({ clinic_id: active.clinicId, kind: k }, { idempotencyKey: key }));
    } finally {
      setPhase('idle');
    }
  });

  if (!hasRole(CAN_UPLOAD)) {
    // Convenience only: the backend would answer 403 as well.
    return (
      <section aria-labelledby="upload-title">
        <PageHeading id="upload-title">{t('upload.title')}</PageHeading>
        <NotAvailableToRole />
      </section>
    );
  }

  function submit(e: FormEvent) {
    e.preventDefault();
    if (!file) return setLocalError('none');
    if (!isContentType(file.type)) return setLocalError('type');
    if (file.size === 0) return setLocalError('empty');
    setLocalError(null);
    upload.mutate({ file, kind, contentType: file.type });
  }

  const busy = upload.isPending;
  const problem = upload.error;
  const result = upload.data;

  return (
    <section aria-labelledby="upload-title">
      <PageHeading id="upload-title">{t('upload.title')}</PageHeading>
      <p>
        <Link to="/referrals">{t('referrals.back')}</Link>
      </p>

      {result ? (
        <div role="status" className="state state-ok">
          <h2>{t('upload.done')}</h2>
          <dl className="facts">
            <dt>{t('referrals.col.document')}</dt>
            <dd>
              <code>{result.document_id}</code>
            </dd>
            <dt>{t('upload.scanStatus')}</dt>
            <dd>
              <ScanStatus scan={result.scan} />
            </dd>
          </dl>
          {result.scan !== 'clean' && <p className="hint">{t(result.scan === 'not_scanned' ? 'upload.notScannedNote' : 'upload.notCheckedNote')}</p>}
          <p>
            <button
              type="button"
              onClick={() => {
                upload.reset();
                setFile(null);
                setInputKey((n) => n + 1);
              }}
            >
              {t('upload.another')}
            </button>{' '}
            <Link to={`/referrals/${result.document_id}`}>{t('upload.openIt')}</Link>
          </p>
        </div>
      ) : (
        <form onSubmit={submit} noValidate>
          <p>
            {t('shell.clinic')}: <strong>{active.clinicName ?? `${active.clinicId.slice(0, 8)}…`}</strong>
          </p>

          <label htmlFor={`${id}-kind`}>{t('upload.kind')}</label>
          <select
            id={`${id}-kind`}
            value={kind}
            disabled={busy}
            onChange={(e) => {
              setKind(e.target.value as Kind);
              upload.reset(); // a different request is a new intent: never reuse the old key
            }}
          >
            {KINDS.map((k) => (
              <option key={k} value={k}>
                {t(`upload.kinds.${k}`)}
              </option>
            ))}
          </select>

          <label htmlFor={`${id}-file`}>{t('upload.file')}</label>
          <input
            key={inputKey}
            id={`${id}-file`}
            type="file"
            accept="application/pdf,image/jpeg,image/png"
            disabled={busy}
            aria-describedby={`${id}-hint`}
            onChange={(e) => {
              setFile(e.target.files?.[0] ?? null);
              setLocalError(null);
              upload.reset();
            }}
          />
          <p id={`${id}-hint`} className="hint">
            {t('upload.fileHint')}
            {file ? ` ${TYPE_LABEL[file.type] ?? ''} · ${formatNumber(Math.max(1, Math.round(file.size / 1024)), lang)} KB` : ''}
          </p>

          {localError && (
            <p role="alert" className="form-error">
              {t(`upload.local.${localError}`)}
            </p>
          )}
          {problem && (
            <div role="alert" className="state state-error">
              {problem.kind === 'forbidden' ? (
                <NotAvailableToRole />
              ) : (
                <p>{problem.kind === 'invalid' ? t('upload.invalid') : problem.kind === 'conflict' ? t('upload.conflict') : <ProblemText kind={problem.kind} />}</p>
              )}
            </div>
          )}
          {busy && (
            <p role="status" className="hint">
              {t(`upload.phase.${phase === 'idle' ? 'preparing' : phase}`)}
            </p>
          )}

          <button type="submit" className="primary" disabled={busy}>
            {problem && !busy && problem.kind !== 'forbidden' && problem.kind !== 'invalid' && problem.kind !== 'conflict' ? t('problem.retry') : t('upload.submit')}
          </button>
        </form>
      )}
    </section>
  );
}
