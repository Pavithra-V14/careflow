import type { ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { unwrap } from '../apiState';
import { useAuth } from '../auth/AuthProvider';
import { useApiQuery } from '../hooks';
import { ProblemText } from '../ui/states';
import { useLang, useWord } from '../ui/bits';
import { formatNumber } from '../../shared/i18n/format';

// The reviewer view of a referral's triage proposal (BUILD-SPEC 12.2). The schema declares proposal, evidence and alternatives
// as bare objects, so each is read defensively and only for the keys the backend documents (careflow/review_console.py).
// UNKNOWN IS NOT A VALUE: a missing confidence, probability or rule is shown as "Not recorded" and never as zero, never as
// "low", never as an empty string that could be read as a result.

const isObject = (v: unknown): v is Record<string, unknown> => v !== null && typeof v === 'object' && !Array.isArray(v);
const text = (v: unknown): string | null => (typeof v === 'string' && v.trim() !== '' ? v : null);
const fraction = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) && v >= 0 && v <= 1 ? v : null);

function useFraction() {
  const lang = useLang();
  // Up to one decimal place, so a figure such as 87.6% is not rounded to something the system did not say.
  return (v: number): string => `${formatNumber(Math.round(v * 1000) / 10, lang)}%`;
}

export function ReviewerView({ documentId }: { documentId: string }) {
  const { t } = useTranslation();
  const { api } = useAuth();
  const word = useWord();
  const lang = useLang();
  const percent = useFraction();
  const query = useApiQuery(['review-view', documentId], () => unwrap(api.reviewView(documentId)));
  const notRecorded = <span className="not-recorded">{t('review.notRecorded')}</span>;

  let body: ReactNode;
  if (query.isPending) {
    body = (
      <p role="status" className="loading">
        {t('common.loading')}
      </p>
    );
  } else if (query.isError) {
    body =
      query.error.kind === 'not_found' ? (
        <p role="status">{t('review.viewMissing')}</p>
      ) : query.error.kind === 'forbidden' ? null : (
        <p role="alert">
          <ProblemText kind={query.error.kind} />
        </p>
      );
  } else {
    const v = query.data;
    const proposal = isObject(v.proposal) ? v.proposal : {};
    const evidence = v.evidence.filter(isObject);
    const alternatives = v.alternatives.filter(isObject);
    const confidence = fraction(v.confidence);
    const abstained = typeof proposal.abstained === 'boolean' ? proposal.abstained : null;
    const count = typeof proposal.candidate_count === 'number' ? proposal.candidate_count : null;
    body = (
      <>
        <dl className="facts">
          <dt>{t('review.kind')}</dt>
          <dd>{word('tasks.kind', v.kind)}</dd>
          <dt>{t('review.engine')}</dt>
          <dd>{text(v.engine) ?? notRecorded}</dd>
          <dt>{t('review.confidence')}</dt>
          <dd>{confidence === null ? notRecorded : percent(confidence)}</dd>
          <dt>{t('review.rule')}</dt>
          <dd>{text(v.triggering_rule) ?? notRecorded}</dd>
          <dt>{t('review.mode')}</dt>
          {/* The mode word (for example shadow) exactly as the API sent it. */}
          <dd>{text(proposal.mode) ?? notRecorded}</dd>
          <dt>{t('review.abstained')}</dt>
          <dd>{abstained === null ? notRecorded : abstained ? t('common.yes') : t('common.no')}</dd>
          <dt>{t('review.candidateCount')}</dt>
          <dd>{count === null ? notRecorded : formatNumber(count, lang)}</dd>
        </dl>

        <h3>{t('review.evidence')}</h3>
        {evidence.length === 0 ? (
          <p>{t('review.noEvidence')}</p>
        ) : (
          <table>
            <caption className="visually-hidden">{t('review.evidence')}</caption>
            <thead>
              <tr>
                <th scope="col">{t('review.evidenceField')}</th>
                <th scope="col">{t('review.evidenceSource')}</th>
                <th scope="col">{t('review.evidenceCrop')}</th>
              </tr>
            </thead>
            <tbody>
              {evidence.map((e, i) => (
                <tr key={i}>
                  <th scope="row">{text(e.field) ?? notRecorded}</th>
                  <td>{text(e.source) ?? notRecorded}</td>
                  <td>{text(e.crop_ref) ?? notRecorded}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}

        <h3>{t('review.alternatives')}</h3>
        {alternatives.length === 0 ? (
          <p>{t('review.noAlternatives')}</p>
        ) : (
          <table>
            <caption className="visually-hidden">{t('review.alternatives')}</caption>
            <thead>
              <tr>
                <th scope="col">{t('review.altLabel')}</th>
                <th scope="col">{t('review.altProbability')}</th>
              </tr>
            </thead>
            <tbody>
              {alternatives.map((a, i) => {
                const p = fraction(a.probability);
                return (
                  <tr key={i}>
                    <th scope="row">{text(a.label) ?? notRecorded}</th>
                    <td>{p === null ? notRecorded : percent(p)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}

        <dl className="facts">
          <dt>{t('review.onApprove')}</dt>
          <dd>{text(v.on_approve) ?? notRecorded}</dd>
          <dt>{t('review.onReject')}</dt>
          <dd>{text(v.on_reject) ?? notRecorded}</dd>
          <dt>{t('review.onEdit')}</dt>
          <dd>{text(v.on_edit) ?? notRecorded}</dd>
        </dl>
      </>
    );
  }

  if (body === null) return null;
  return (
    <section aria-labelledby={`rv-${documentId}`}>
      <h2 id={`rv-${documentId}`}>{t('review.viewTitle')}</h2>
      {/* Whatever the system proposed is a suggestion, never a decision: said above the figures, whenever there is something to show. */}
      {query.isSuccess && <p className="hint">{t('review.suggestion')}</p>}
      {body}
    </section>
  );
}
