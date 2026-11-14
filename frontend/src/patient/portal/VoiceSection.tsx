import { useId, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import type { Api, VoiceSessionIn } from '../../shared/api/client';
import { unwrap } from '../../staff/apiState';
import { useIdempotentWrite } from '../../staff/hooks';
import { ConfirmDialog } from '../../staff/ui/ConfirmDialog';
import { Badge } from '../../staff/ui/bits';
import { ProblemText } from '../../staff/ui/states';
import { WriteProblem } from '../../staff/ui/WriteProblem';
import { VOICE_NOTICE_PURPOSE, usePortalQuery } from './portalQuery';
import { useClinicField } from './PortalSections';

// The patient's voice setting and the gate in front of a voice session (BUILD-SPEC 23, PATIENT-WORKFLOWS G1). Voice is opt-in, off until the
// person turns it on, and its scope is deliberately narrow: the three workflows below are the whole list, there is nothing free-form.
// This screen opens the SESSION on the backend. It does not open a microphone or a connection to any speech service: that service is not
// approved for this project, and the route's reply carries nothing a browser could connect to. The consent notice for a session is consent
// and privacy wording: the frontend holds NONE of it. It is shown exactly as the API sends it (the notice whose purpose is
// VOICE_NOTICE_PURPOSE in the consents list), and until the API supplies one the session cannot be opened and the screen says it is not
// set up. Nothing is pre-chosen: not the workflow, not the agreement.
const WORKFLOWS = ['slot_offer', 'intake_question', 'readiness_item'] as const satisfies readonly VoiceSessionIn['workflow'][];

export function VoiceSection({ api }: { api: Api }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [setting, setSetting] = useState<boolean | null>(null); // the value the person is being asked to confirm
  const [note, setNote] = useState<string | null>(null);
  const pref = usePortalQuery(['portal-voice-preference'], () => unwrap(api.getVoicePreference()));

  const save = useIdempotentWrite(
    (v: boolean, key) => unwrap(api.putVoicePreference(v, { idempotencyKey: key })),
    () => {
      setSetting(null);
      setNote(t('portal.voice.saved'));
      void queryClient.invalidateQueries({ queryKey: ['portal-voice-preference'] });
    },
  );

  return (
    <section aria-labelledby="portal-voice">
      <h2 id="portal-voice">{t('portal.voice.title')}</h2>
      <p>{t('portal.voice.intro')}</p>
      {pref.isPending && (
        <p role="status" className="loading">
          {t('common.loading')}
        </p>
      )}
      {pref.isError &&
        (pref.error.kind === 'forbidden' ? (
          <p role="status">{t('portal.notAvailable')}</p>
        ) : (
          <p role="alert">
            <ProblemText kind={pref.error.kind} />
          </p>
        ))}
      {pref.isSuccess && (
        <>
          <p>
            <Badge tone={pref.data.voice_enabled ? 'ok' : 'neutral'}>{t(pref.data.voice_enabled ? 'portal.voice.on' : 'portal.voice.off')}</Badge>
          </p>
          {note && <p role="status">{note}</p>}
          <button
            type="button"
            className="button button-secondary"
            onClick={() => {
              setNote(null);
              setSetting(!pref.data.voice_enabled);
            }}
          >
            {t(pref.data.voice_enabled ? 'portal.voice.turnOff' : 'portal.voice.turnOn')}
          </button>
          {pref.data.voice_enabled && <OpenSession api={api} />}
        </>
      )}
      {setting !== null && (
        <ConfirmDialog
          title={t(setting ? 'portal.voice.confirmOnTitle' : 'portal.voice.confirmOffTitle')}
          confirmLabel={t(setting ? 'portal.voice.turnOn' : 'portal.voice.turnOff')}
          busy={save.isPending}
          onConfirm={() => save.mutate(setting)}
          onCancel={() => {
            save.reset();
            setSetting(null);
          }}
          status={<WriteProblem problem={save.error} />}
        >
          <p>{t('portal.voice.confirmBody')}</p>
        </ConfirmDialog>
      )}
    </section>
  );
}

function OpenSession({ api }: { api: Api }) {
  const { t } = useTranslation();
  const [workflow, setWorkflow] = useState<VoiceSessionIn['workflow'] | null>(null); // nothing chosen for the person
  const [agreed, setAgreed] = useState(false); // unticked
  const [typed, setTyped] = useState('');
  const [opened, setOpened] = useState<{ created: boolean } | null>(null);
  const radio = useId();
  const clinic = useClinicField(api, 'voice', typed, setTyped);
  const notices = usePortalQuery(['portal-consents'], () => unwrap(api.portalConsents()));
  const notice = notices.data?.notices.find((n) => n.purpose === VOICE_NOTICE_PURPOSE) ?? null;

  const open = useIdempotentWrite(
    (_: void, key) => unwrap(api.openVoiceSession({ clinic_id: clinic.clinicId, workflow: workflow as VoiceSessionIn['workflow'], consent: true }, { idempotencyKey: key })),
    (r) => setOpened({ created: r.created }),
  );
  const ready = notice !== null && clinic.ready && clinic.clinicId !== '' && workflow !== null && agreed;
  const changed = () => {
    open.reset(); // a different request is a different key
    setOpened(null);
  };

  // 503: voice is not set up for this hospital (clinic switch, engine or retention setting). 404: not a patient of that hospital.
  const calm = { unavailable: t('portal.voice.unavailable'), not_found: t('portal.voice.notYours'), invalid: t('portal.voice.invalid') };
  return (
    <div>
      <h3>{t('portal.voice.sessionTitle')}</h3>
      {notices.isPending && (
        <p role="status" className="loading">
          {t('common.loading')}
        </p>
      )}
      {notices.isError && (
        <p role="alert">
          <ProblemText kind={notices.error.kind} />
        </p>
      )}
      {notices.isSuccess && notice === null && <p role="status">{t('portal.voice.notSetUp')}</p>}
      {notice !== null && (
        <>
      {clinic.node}
      <fieldset>
        <legend>{t('portal.voice.workflowLegend')}</legend>
        {WORKFLOWS.map((w) => (
          <label key={w} className="choice">
            <input
              type="radio"
              name={radio}
              checked={workflow === w}
              onChange={() => {
                setWorkflow(w);
                changed();
              }}
            />
            <span>{t(`portal.voice.workflow.${w}`)}</span>
          </label>
        ))}
      </fieldset>
      <article className="notice" aria-label={notice.label ?? notice.purpose}>
        <h4>
          {notice.label ?? <code>{notice.purpose}</code>} · {t('portal.consent.version', { version: notice.version })}
        </h4>
        {/* The notice text exactly as sent, as plain text. */}
        <div className="notice-text">{notice.body}</div>
      </article>
      <label className="choice">
        <input
          type="checkbox"
          checked={agreed}
          onChange={(e) => {
            setAgreed(e.target.checked);
            changed();
          }}
        />
        <span>{t('portal.voice.agree')}</span>
      </label>
        </>
      )}
      <p>
        <button type="button" className="button button-primary" disabled={!ready || open.isPending} onClick={() => open.mutate()}>
          {open.isPending ? t('common.working') : t('portal.voice.open')}
        </button>
      </p>
      <WriteProblem problem={open.error} calm={calm} />
      {opened && (
        <div role="status">
          <p>{t(opened.created ? 'portal.voice.opened' : 'portal.voice.repeat')}</p>
          <p className="hint">{t('portal.voice.noAudio')}</p>
        </div>
      )}
    </div>
  );
}
