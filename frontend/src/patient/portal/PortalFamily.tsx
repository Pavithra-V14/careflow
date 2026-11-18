import { useState, type FormEvent } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import type { Api } from '../../shared/api/client';
import type { components } from '../../shared/api/schema';
import { unwrap } from '../../staff/apiState';
import { useIdempotentWrite } from '../../staff/hooks';
import { ConfirmDialog } from '../../staff/ui/ConfirmDialog';
import { Badge, Time, UUID, useWord } from '../../staff/ui/bits';
import { ProblemText } from '../../staff/ui/states';
import { WriteProblem } from '../../staff/ui/WriteProblem';
import { usePortalQuery } from './portalQuery';
import { useClinicField } from './PortalSections';

type LinkType = components['schemas']['FamilyLinkRequestIn']['link_type'];
type Link = components['schemas']['MyFamilyLinkOut'];
const LINK_TYPES: readonly LinkType[] = ['guardian_of_minor', 'carer_of_adult'];
/** The backend's own sentence for "the clinic has not entered its rules"; it is matched here and said in this screen's fixed words. */
export const FAMILY_NO_RULES = 'family links are not set up: the clinic has not entered its rules';

/**
 * Family links (the patient's own requests): ask to be linked as the guardian of a child or the carer of an adult at a clinic, see the
 * status of each request (a closed word; no name and no date of birth), and withdraw one. A staff member verifies a request afterwards.
 * A verified link grants NOTHING in this build: no account can read or act for another through it, and the screen says so. The whole
 * section is hidden while the owner's switch is off (the list answers 503), and a clinic that has not entered counsel's rules gets a
 * fixed-wording message instead of a working request.
 */
export function FamilySection({ api }: { api: Api }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const word = useWord();
  const [clinic, setClinic] = useState('');
  const [type, setType] = useState<LinkType | ''>('');
  const [open, setOpen] = useState(false);
  const [revoking, setRevoking] = useState<Link | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const links = usePortalQuery(['portal-family'], () => unwrap(api.listMyFamilyLinks()));
  const request = useIdempotentWrite(
    (v: { clinicId: string; type: LinkType }, key) => unwrap(api.requestFamilyLink({ clinic_id: v.clinicId, link_type: v.type }, { idempotencyKey: key })),
    (r) => {
      setOpen(false);
      setNote(t(`portal.family.result.${r.result}`));
      void queryClient.invalidateQueries({ queryKey: ['portal-family'] });
    },
  );
  const revoke = useIdempotentWrite(
    (l: Link, key) => unwrap(api.revokeFamilyLink(l.link_id, { idempotencyKey: key })),
    (r) => {
      setRevoking(null);
      setNote(t(`portal.family.revokeResult.${r.result}`));
      void queryClient.invalidateQueries({ queryKey: ['portal-family'] });
    },
  );
  const clinicField = useClinicField(api, 'fam', clinic, (v) => {
    setClinic(v);
    request.reset();
  });

  // Hidden while the switch is off, and for an account that may not use it: the section is simply not there.
  if (links.isPending || (links.isError && (links.error.status === 503 || links.error.kind === 'forbidden'))) return null;
  const valid = UUID.test(clinicField.clinicId) && type !== '';
  const rulesMissing = request.error?.status === 503 && request.error.detail === FAMILY_NO_RULES;
  return (
    <section aria-labelledby="portal-family">
      <h2 id="portal-family">{t('portal.family.title')}</h2>
      <p>{t('portal.family.body')}</p>
      <p className="hint">{t('portal.family.grantsNothing')}</p>
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
        <label htmlFor="fam-type">{t('portal.family.type')}</label>
        {/* Nothing is pre-chosen: the person says which kind of link they are asking for. */}
        <select
          id="fam-type"
          value={type}
          onChange={(e) => {
            setType(LINK_TYPES.find((x) => x === e.target.value) ?? '');
            setNote(null);
            request.reset();
          }}
        >
          <option value="">{t('portal.family.chooseType')}</option>
          {LINK_TYPES.map((x) => (
            <option key={x} value={x}>
              {t(`portal.family.types.${x}`)}
            </option>
          ))}
        </select>
        <button type="submit" className="button button-primary" disabled={!valid}>
          {t('portal.family.ask')}
        </button>
      </form>
      <h3>{t('portal.family.mine')}</h3>
      {links.isError && (
        <p role="alert">
          <ProblemText kind={links.error.kind} />
        </p>
      )}
      {links.isSuccess && links.data.links.length === 0 && <p>{t('portal.family.none')}</p>}
      {links.isSuccess && links.data.links.length > 0 && (
        <ul className="plain-list">
          {links.data.links.map((l) => (
            <li key={l.link_id}>
              {t(`portal.family.types.${l.link_type}`)} · <Badge tone={l.status === 'verified' ? 'ok' : l.status === 'requested' ? 'warn' : 'neutral'}>{word('portal.family.status', l.status)}</Badge> · <Time value={l.created_at} />
              {(l.status === 'requested' || l.status === 'verified') && (
                <>
                  {' '}
                  <button type="button" className="button button-secondary" aria-label={`${t('portal.family.revoke')}: ${t(`portal.family.types.${l.link_type}`)}`} onClick={() => setRevoking(l)}>
                    {t('portal.family.revoke')}
                  </button>
                </>
              )}
            </li>
          ))}
        </ul>
      )}
      {open && (
        <ConfirmDialog
          title={t('portal.family.confirmTitle')}
          confirmLabel={t('portal.family.ask')}
          busy={request.isPending}
          onConfirm={() => type !== '' && request.mutate({ clinicId: clinicField.clinicId, type })}
          onCancel={() => {
            request.reset();
            setOpen(false);
          }}
          status={
            rulesMissing ? (
              <p role="status">{t('portal.family.noRules')}</p>
            ) : (
              <WriteProblem problem={request.error} forbidden={t('portal.family.refused')} calm={{ unavailable: t('portal.family.off'), invalid: t('portal.family.invalid') }} />
            )
          }
        >
          <p>{type === '' ? '' : t(`portal.family.types.${type}`)}</p>
          <p>{t('portal.family.confirmBody')}</p>
        </ConfirmDialog>
      )}
      {revoking && (
        <ConfirmDialog
          title={t('portal.family.revokeTitle')}
          confirmLabel={t('portal.family.revoke')}
          busy={revoke.isPending}
          onConfirm={() => revoke.mutate(revoking)}
          onCancel={() => {
            revoke.reset();
            setRevoking(null);
          }}
          status={<WriteProblem problem={revoke.error} forbidden={t('portal.family.refused')} calm={{ not_found: t('portal.family.gone'), unavailable: t('portal.family.off') }} />}
        >
          <p>{t('portal.family.revokeBody')}</p>
        </ConfirmDialog>
      )}
    </section>
  );
}
