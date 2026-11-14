import { useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import type { RuleDecision } from '../../shared/api/client';
import type { components } from '../../shared/api/schema';
import { unwrap } from '../apiState';
import { useAuth } from '../auth/AuthProvider';
import { useApiQuery, useIdempotentWrite } from '../hooks';
import { CAN_DECIDE_RULE } from '../sections';
import { useSession } from '../session/SessionContext';
import { ConfirmDialog } from '../ui/ConfirmDialog';
import { Badge, ShortId, Time, useWord } from '../ui/bits';
import { QueryView } from '../ui/states';
import { WriteProblem } from '../ui/WriteProblem';
import { RuleAuthoring } from './RuleAuthoring';

type Rule = components['schemas']['RuleVersionOut'];

// The red-flag rule versions of this clinic, and the two-doctor approval of a draft one (BUILD-SPEC 12.5 and 20.2).
// MECHANISM ONLY: every phrase, pattern and word on this screen is shown exactly as the API sends it. The console does not write,
// edit, suggest or translate rule content, and it never decides anything by itself: a version becomes approved only when a SECOND,
// different doctor approves it (the database enforces that, with a verified second factor). Admins read this list; they decide nothing.
// The API says whether the signed-in person wrote a version (`authored_by_you`): Approve and Reject are not offered to the author, and
// not to anyone who already decided. The database still decides everything, and its refusal (`author_cannot_decide`) is told plainly.
// Two more doctor actions, each with its own confirmation showing the content: Withdraw (a DRAFT; closed for good, never the same as
// retired) and Retire (an APPROVED version; nothing is retired until two different doctors have each recorded it).

const RESULT_WORDS = ['recorded', 'approved', 'rejected', 'retired', 'withdrawn', 'already_decided', 'not_draft', 'not_approved', 'origin_mismatch', 'author_cannot_decide', 'empty_rule', 'rejected_earlier', 'platform_rule'] as const;
type RuleAction = RuleDecision | 'withdraw';
const isResult = (v: unknown): v is (typeof RESULT_WORDS)[number] => (RESULT_WORDS as readonly string[]).includes(v as string);

export function RedFlagRules() {
  const { t } = useTranslation();
  const { api } = useAuth();
  const { active, hasRole, account } = useSession();
  const queryClient = useQueryClient();
  const [deciding, setDeciding] = useState<{ rule: Rule; decision: RuleAction } | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const rules = useApiQuery(['red-flag-rules'], () => unwrap(api.listRedFlagRules(active.clinicId)));
  const canDecide = hasRole(CAN_DECIDE_RULE);

  const decide = useIdempotentWrite(
    (v: { rule: Rule; decision: RuleAction }, key) =>
      unwrap(v.decision === 'withdraw' ? api.withdrawRedFlagDraft(v.rule.rule_id, { idempotencyKey: key }) : api.decideRedFlagRule(v.rule.rule_id, v.decision, { idempotencyKey: key })),
    (r) => {
      setDeciding(null);
      // An unknown word is never read as "approved": it gets the neutral line.
      setNote(isResult(r.result) ? t(`admin.rules.result.${r.result}`) : t('admin.rules.result.unknown'));
      void queryClient.invalidateQueries();
    },
  );

  return (
    <>
      <p>{t('admin.rules.intro')}</p>
      <p className="hint">{canDecide ? t('admin.rules.twoDoctors') : t('admin.rules.readOnly')}</p>
      {canDecide && <RuleAuthoring onSaved={() => setNote(t('admin.rules.author.saved'))} />}
      {note && <p role="status">{note}</p>}
      <QueryView query={rules} isEmpty={(d) => d.rules.length === 0} empty={t('admin.rules.empty')}>
        {(d) => (
          <ul className="rule-list" aria-label={t('admin.rules.listLabel')}>
            {d.rules.map((rule) => {
              const mine = rule.decisions.some((x) => x.decided_by === account.accountId && x.decision !== 'retire');
              const rejected = rule.decisions.some((x) => x.decision === 'reject');
              const author = rule.authored_by_you === true; // an older answer without the flag is not read as "not the author": the database still refuses
              const draft = rule.status === 'draft_unreviewed';
              // Approve and Reject: an open draft this person did not write and has not decided on. The database decides everything else.
              const open = draft && !mine && !rejected && !author;
              const retires = rule.decisions.filter((x) => x.decision === 'retire');
              const retiredByMe = retires.some((x) => x.decided_by === account.accountId);
              const label = `${rule.rule_code} v${rule.version_id}`;
              return (
                <li key={`${rule.rule_id}`}>
                  <RuleCard rule={rule} accountId={account.accountId} />
                  {canDecide && open && (
                    <div className="toolbar">
                      <button type="button" aria-label={`${t('admin.rules.approve')} ${label}`} onClick={() => { setNote(null); setDeciding({ rule, decision: 'approve' }); }}>
                        {t('admin.rules.approve')}
                      </button>
                      <button type="button" aria-label={`${t('admin.rules.reject')} ${label}`} onClick={() => { setNote(null); setDeciding({ rule, decision: 'reject' }); }}>
                        {t('admin.rules.reject')}
                      </button>
                    </div>
                  )}
                  {canDecide && draft && author && <p className="hint">{t('admin.rules.youWrote')}</p>}
                  {canDecide && draft && mine && <p className="hint">{t('admin.rules.youDecided')}</p>}
                  {canDecide && draft && (
                    <div className="toolbar">
                      <button type="button" aria-label={`${t('admin.rules.withdraw')} ${label}`} onClick={() => { setNote(null); setDeciding({ rule, decision: 'withdraw' }); }}>
                        {t('admin.rules.withdraw')}
                      </button>
                    </div>
                  )}
                  {canDecide && rule.status === 'approved' && (
                    <>
                      {retires.length > 0 && <p className="hint">{t('admin.rules.retireCount', { count: retires.length })}</p>}
                      {retiredByMe ? (
                        <p className="hint">{t('admin.rules.youRetired')}</p>
                      ) : (
                        <div className="toolbar">
                          <button type="button" aria-label={`${t('admin.rules.retire')} ${label}`} onClick={() => { setNote(null); setDeciding({ rule, decision: 'retire' }); }}>
                            {t('admin.rules.retire')}
                          </button>
                        </div>
                      )}
                    </>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </QueryView>
      {deciding && (
        <ConfirmDialog
          title={t(`admin.rules.${deciding.decision}Title`)}
          confirmLabel={t(`admin.rules.${deciding.decision}`)}
          busy={decide.isPending}
          onConfirm={() => decide.mutate(deciding)}
          onCancel={() => {
            decide.reset();
            setDeciding(null);
          }}
          status={<WriteProblem problem={decide.error} />}
        >
          <p>
            <strong>
              {deciding.rule.rule_code} v{deciding.rule.version_id}
            </strong>
          </p>
          <p>{t(`admin.rules.${deciding.decision}Body`)}</p>
          <p className="hint">{t('admin.stepUp')}</p>
          <RuleContent rule={deciding.rule} />
        </ConfirmDialog>
      )}
    </>
  );
}

function RuleContent({ rule }: { rule: Rule }) {
  const { t } = useTranslation();
  const tokenSets = rule.token_sets ?? []; // older answers had none
  return (
    <dl className="facts">
      <dt>{t('admin.rules.phrases')}</dt>
      <dd>
        {rule.phrases.length === 0 ? (
          t('admin.rules.none')
        ) : (
          <ul>
            {rule.phrases.map((p, i) => (
              <li key={`${i}-${p}`}>{p}</li>
            ))}
          </ul>
        )}
      </dd>
      <dt>{t('admin.rules.patterns')}</dt>
      <dd>
        {rule.patterns.length === 0 ? (
          t('admin.rules.none')
        ) : (
          <ul>
            {rule.patterns.map((p, i) => (
              <li key={`${i}-${p}`}>
                <code>{p}</code>
              </li>
            ))}
          </ul>
        )}
      </dd>
      <dt>{t('admin.rules.tokenSets')}</dt>
      <dd>
        {tokenSets.length === 0 ? (
          t('admin.rules.none')
        ) : (
          <ul>
            {tokenSets.map((s, i) => (
              <li key={`${i}-${s.join(' ')}`}>{s.join(' + ')}</li>
            ))}
          </ul>
        )}
      </dd>
      <dt>{t('admin.rules.vitals')}</dt>
      <dd>{rule.has_vitals_criteria ? t('admin.rules.vitalsYes') : t('admin.rules.vitalsNo')}</dd>
    </dl>
  );
}

function RuleCard({ rule, accountId }: { rule: Rule; accountId: string }) {
  const { t } = useTranslation();
  const word = useWord();
  return (
    <article aria-label={`${rule.rule_code} v${rule.version_id}`}>
      <h3>
        {rule.rule_code} <span className="hint">v{rule.version_id}</span>
      </h3>
      <dl className="facts">
        <dt>{t('admin.rules.status')}</dt>
        <dd>
          <Badge tone={rule.status === 'approved' ? 'ok' : rule.status === 'draft_unreviewed' ? 'warn' : 'neutral'}>{word('admin.rules.statuses', rule.status)}</Badge>
        </dd>
        <dt>{t('admin.rules.origin')}</dt>
        <dd>{word('admin.rules.origins', rule.origin)}</dd>
        <dt>{t('admin.rules.approvedAt')}</dt>
        <dd>
          <Time value={rule.approved_at} />
        </dd>
      </dl>
      {rule.status === 'draft_unreviewed' && <p className="hint">{t('admin.rules.draftNote')}</p>}
      <RuleContent rule={rule} />
      <h4>{t('admin.rules.decisions')}</h4>
      {rule.decisions.length === 0 ? (
        <p>{t('admin.rules.noDecisions')}</p>
      ) : (
        <table>
          <caption className="visually-hidden">{`${t('admin.rules.decisions')}: ${rule.rule_code} v${rule.version_id}`}</caption>
          <thead>
            <tr>
              <th scope="col">{t('admin.rules.col.by')}</th>
              <th scope="col">{t('admin.rules.col.decision')}</th>
              <th scope="col">{t('admin.rules.col.at')}</th>
            </tr>
          </thead>
          <tbody>
            {rule.decisions.map((x) => (
              <tr key={`${x.decided_by}-${x.decided_at}`}>
                <th scope="row">{x.decided_by === accountId ? t('admin.autonomy.you') : <ShortId id={x.decided_by} />}</th>
                <td>{word('admin.rules.decisionWords', x.decision)}</td>
                <td>
                  <Time value={x.decided_at} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </article>
  );
}
