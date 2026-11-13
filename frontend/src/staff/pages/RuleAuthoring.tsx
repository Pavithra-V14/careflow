import { useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { unwrap } from '../apiState';
import { useAuth } from '../auth/AuthProvider';
import { useIdempotentWrite } from '../hooks';
import { useSession } from '../session/SessionContext';
import { ConfirmDialog } from '../ui/ConfirmDialog';
import { WriteProblem } from '../ui/WriteProblem';

// A doctor writes a DRAFT red-flag rule version in the RESTRICTED language: literal phrases and token sets, nothing else (there is no regular
// expression field, on purpose). MECHANISM ONLY: this form has no default text, no example, no suggestion and no template. The limits below
// are the route's own (RuleDraftIn in openapi.snapshot.json and the checks in migration 0076); the database checks them again and answers
// 422 for anything outside them. A draft is never active: it is shown as a draft, and it is approved only by two different doctors.

const RULE_CODE = /^[a-z][a-z0-9_]{0,62}$/;
const MAX_PHRASES = 100;
const MAX_PHRASE_LENGTH = 200;
const MAX_SETS = 50;
const MIN_SET = 2;
const MAX_SET = 10;
const MAX_TOKEN = 40;
// Control characters, and the invisible or look-alike space and direction characters the database refuses (the same list as migration 0076).
const isControl = (s: string) => Array.from(s).some((ch) => { const c = ch.charCodeAt(0); return c <= 0x1f || (c >= 0x7f && c <= 0x9f); });
const ODD = /[\u00a0\u00ad\u0600-\u0605\u061c\u180e\u2000-\u200f\u2028-\u202f\u205f-\u206f\u3000\ufeff\ufff9-\ufffb]/;

type DraftProblem =
  | { kind: 'code' }
  | { kind: 'noCriterion' }
  | { kind: 'tooManyPhrases' }
  | { kind: 'tooManySets' }
  | { kind: 'phraseLong'; line: number }
  | { kind: 'phraseChars'; line: number }
  | { kind: 'setSize'; line: number }
  | { kind: 'tokenLong'; line: number }
  | { kind: 'tokenChars'; line: number }
  | { kind: 'sameWords'; line: number };

interface Draft {
  ruleCode: string;
  phrases: string[];
  tokenSets: string[][];
  problems: DraftProblem[];
}

/** Reads the three fields. Phrases: one per line. Token sets: one per line, words separated by spaces. Empty lines are ignored; nothing else is changed. */
function parseDraft(ruleCode: string, phraseText: string, setText: string): Draft {
  const problems: DraftProblem[] = [];
  const code = ruleCode.trim();
  if (!RULE_CODE.test(code)) problems.push({ kind: 'code' });
  const phrases: string[] = [];
  phraseText.split(/\r?\n/).forEach((raw, i) => {
    const p = raw.trim();
    if (p === '') return;
    if (p.length > MAX_PHRASE_LENGTH) problems.push({ kind: 'phraseLong', line: i + 1 });
    else if (isControl(p) || ODD.test(p)) problems.push({ kind: 'phraseChars', line: i + 1 });
    phrases.push(p);
  });
  const tokenSets: string[][] = [];
  setText.split(/\r?\n/).forEach((raw, i) => {
    if (raw.trim() === '') return;
    const words = raw.trim().split(/[ \t]+/);
    if (words.length < MIN_SET || words.length > MAX_SET) problems.push({ kind: 'setSize', line: i + 1 });
    else if (words.some((w) => w.length > MAX_TOKEN)) problems.push({ kind: 'tokenLong', line: i + 1 });
    else if (words.some((w) => isControl(w) || ODD.test(w))) problems.push({ kind: 'tokenChars', line: i + 1 });
    else if (new Set(words.map((w) => w.toLowerCase())).size < 2) problems.push({ kind: 'sameWords', line: i + 1 });
    tokenSets.push(words);
  });
  if (phrases.length > MAX_PHRASES) problems.push({ kind: 'tooManyPhrases' });
  if (tokenSets.length > MAX_SETS) problems.push({ kind: 'tooManySets' });
  if (phrases.length === 0 && tokenSets.length === 0) problems.push({ kind: 'noCriterion' });
  return { ruleCode: code, phrases, tokenSets, problems };
}

export function RuleAuthoring({ onSaved }: { onSaved: () => void }) {
  const { t } = useTranslation();
  const { api } = useAuth();
  const { active } = useSession();
  const queryClient = useQueryClient();
  const [ruleCode, setRuleCode] = useState('');
  const [phraseText, setPhraseText] = useState('');
  const [setText, setSetText] = useState('');
  const [step, setStep] = useState<'edit' | 'confirm'>('edit');
  const [open, setOpen] = useState(false);

  const draft = parseDraft(ruleCode, phraseText, setText);
  const untouched = ruleCode === '' && phraseText.trim() === '' && setText.trim() === '';
  const valid = draft.problems.length === 0;

  const write = useIdempotentWrite(
    (_: void, key) => unwrap(api.authorRedFlagRule({ clinic_id: active.clinicId, rule_code: draft.ruleCode, phrases: draft.phrases, token_sets: draft.tokenSets }, { idempotencyKey: key })),
    () => {
      void queryClient.invalidateQueries();
      setOpen(false);
      onSaved();
    },
  );
  const edit = (set: (v: string) => void) => (v: string) => {
    set(v);
    write.reset(); // a different draft is a different request: never reuse the old key
  };
  const close = () => {
    write.reset();
    setOpen(false);
  };

  return (
    <>
      <div className="toolbar">
        <button
          type="button"
          onClick={() => {
            setRuleCode('');
            setPhraseText('');
            setSetText('');
            setStep('edit');
            write.reset();
            setOpen(true);
          }}
        >
          {t('admin.rules.author.open')}
        </button>
      </div>
      {open && (
        <ConfirmDialog
          title={t(step === 'edit' ? 'admin.rules.author.title' : 'admin.rules.author.confirmTitle')}
          confirmLabel={t(step === 'edit' ? 'admin.rules.author.review' : 'admin.rules.author.save')}
          busy={write.isPending}
          confirmDisabled={step === 'edit' && (untouched || !valid)}
          onConfirm={() => (step === 'edit' ? setStep('confirm') : write.mutate())}
          onCancel={close}
          status={
            <WriteProblem
              problem={write.error}
              calm={{ conflict: t('admin.rules.author.conflict'), invalid: t('admin.rules.author.invalid') }}
              details={{}}
            />
          }
        >
          {step === 'edit' ? (
            <>
              <p>{t('admin.rules.author.body')}</p>
              <label htmlFor="rule-code">{t('admin.rules.author.code')}</label>
              <input id="rule-code" type="text" autoComplete="off" spellCheck={false} value={ruleCode} onChange={(e) => edit(setRuleCode)(e.target.value)} aria-describedby="rule-code-hint" />
              <p id="rule-code-hint" className="hint">
                {t('admin.rules.author.codeHint')}
              </p>
              <label htmlFor="rule-phrases">{t('admin.rules.author.phrases')}</label>
              <textarea id="rule-phrases" rows={5} spellCheck={false} value={phraseText} onChange={(e) => edit(setPhraseText)(e.target.value)} aria-describedby="rule-phrases-hint" />
              <p id="rule-phrases-hint" className="hint">
                {t('admin.rules.author.phrasesHint', { max: MAX_PHRASES, length: MAX_PHRASE_LENGTH })}
              </p>
              <label htmlFor="rule-sets">{t('admin.rules.author.sets')}</label>
              <textarea id="rule-sets" rows={5} spellCheck={false} value={setText} onChange={(e) => edit(setSetText)(e.target.value)} aria-describedby="rule-sets-hint" />
              <p id="rule-sets-hint" className="hint">
                {t('admin.rules.author.setsHint', { min: MIN_SET, max: MAX_SET, token: MAX_TOKEN, sets: MAX_SETS })}
              </p>
              {!untouched && draft.problems.length > 0 && (
                <ul className="form-error" aria-label={t('admin.rules.author.problems')}>
                  {draft.problems.map((p, i) => (
                    <li key={`${p.kind}-${'line' in p ? p.line : i}`}>{t(`admin.rules.author.err.${p.kind}`, { line: 'line' in p ? p.line : 0, max: MAX_PHRASES, sets: MAX_SETS })}</li>
                  ))}
                </ul>
              )}
            </>
          ) : (
            <>
              <p>{t('admin.rules.author.confirmBody')}</p>
              <dl className="facts">
                <dt>{t('admin.rules.author.code')}</dt>
                <dd>{draft.ruleCode}</dd>
                <dt>{t('admin.rules.phrases')}</dt>
                <dd>
                  {draft.phrases.length === 0 ? (
                    t('admin.rules.none')
                  ) : (
                    <ul>
                      {draft.phrases.map((p, i) => (
                        <li key={`${i}-${p}`}>{p}</li>
                      ))}
                    </ul>
                  )}
                </dd>
                <dt>{t('admin.rules.tokenSets')}</dt>
                <dd>
                  {draft.tokenSets.length === 0 ? (
                    t('admin.rules.none')
                  ) : (
                    <ul>
                      {draft.tokenSets.map((s, i) => (
                        <li key={`${i}-${s.join(' ')}`}>{s.join(' + ')}</li>
                      ))}
                    </ul>
                  )}
                </dd>
              </dl>
              <button type="button" onClick={() => setStep('edit')}>
                {t('admin.rules.author.back')}
              </button>
            </>
          )}
        </ConfirmDialog>
      )}
    </>
  );
}
