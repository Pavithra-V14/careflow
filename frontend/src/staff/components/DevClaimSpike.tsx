import { useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useAuth } from '../auth/AuthProvider';
import { describeClaimShape, type ClaimShape } from '../auth/claimShape';

// CAREFLOW_DEV_CLAIM_SPIKE: development builds only (the production bundle must not contain this marker).
// Shows the STRUCTURE of aal, amr and iat with values blanked, to paste to the backend session. It never
// renders, stores or logs the token.
export default function DevClaimSpike() {
  const { t } = useTranslation();
  const { port } = useAuth();
  const [shape, setShape] = useState<ClaimShape | null>(null);
  const [failed, setFailed] = useState(false);
  const [reveal, setReveal] = useState(false);
  const previous = useRef<string | undefined>(undefined);

  async function capture(refresh: boolean) {
    setFailed(false);
    if (refresh && !(await port.refreshSession())) return setFailed(true);
    const token = await port.getAccessToken();
    const described = token ? describeClaimShape(token, { previousFingerprint: previous.current, revealEnumValues: reveal }) : null;
    if (!described) return setFailed(true);
    previous.current = described.fingerprint;
    setShape(described.shape);
  }

  return (
    <section aria-labelledby="spike-title" className="dev-panel" data-dev-only="CAREFLOW_DEV_CLAIM_SPIKE">
      <h2 id="spike-title">{t('dev.title')}</h2>
      <p>{t('dev.note')}</p>
      <label className="choice">
        <input type="checkbox" checked={reveal} onChange={(e) => setReveal(e.target.checked)} />
        <span>{t('dev.reveal')}</span>
      </label>
      <button type="button" onClick={() => void capture(false)}>
        {t('dev.capture')}
      </button>
      <button type="button" onClick={() => void capture(true)}>
        {t('dev.refresh')}
      </button>
      {failed && <p role="alert">{t('dev.failed')}</p>}
      {shape && <pre aria-label={t('dev.title')}>{JSON.stringify(shape, null, 2)}</pre>}
    </section>
  );
}
