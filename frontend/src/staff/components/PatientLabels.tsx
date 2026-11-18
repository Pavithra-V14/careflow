import { useState, type ReactNode } from 'react';
import { useMutation } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import type { components } from '../../shared/api/schema';
import { ApiProblem, toProblem, unwrap } from '../apiState';
import { useAuth } from '../auth/AuthProvider';
import { useSession } from '../session/SessionContext';
import { ConfirmDialog } from '../ui/ConfirmDialog';
import { ShortId } from '../ui/bits';
import { WriteProblem } from '../ui/WriteProblem';
import { formatNumber } from '../../shared/i18n/format';
import { useLang } from '../ui/bits';

type Label = components['schemas']['PatientLabelOut'];
const PAGE_MAX = 25;

/**
 * Patient labels (name, year of birth, last four digits of the phone) for the patients shown on a list, on request. Staff who may see a
 * patient's record (front desk, nurse, doctor) get up to 25 at a time; the reading is one audit row with the COUNT only. Nothing is read
 * until the person confirms; the labels live in this screen's own state and go when the screen closes. Outside the demo profile with
 * synthetic data the backend refuses (503) and the screen says so in fixed words; ids stay as ids. The route takes no idempotency key.
 */
export function usePatientLabels() {
  const { t } = useTranslation();
  const lang = useLang();
  const { api } = useAuth();
  const { active } = useSession();
  const [labels, setLabels] = useState<Map<string, Label>>(new Map());
  const [pending, setPending] = useState<string[] | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const read = useMutation<Label[], ApiProblem, string[]>({
    gcTime: 0,
    retry: false,
    mutationFn: async (ids) => {
      try {
        return (await unwrap(api.patientLabels({ clinic_id: active.clinicId, patient_ids: ids }))).items;
      } catch (e) {
        throw toProblem(e);
      }
    },
    onSuccess: (items) => {
      setLabels((old) => new Map([...old, ...items.map((l): [string, Label] => [l.patient_id, l])]));
      setPending(null);
      setNote(t('labels.shown', { n: formatNumber(items.length, lang) }));
    },
  });

  return {
    /** The patient as an id, and as a label when it has been read. */
    cell(id: string): ReactNode {
      const l = labels.get(id);
      if (!l) return <ShortId id={id} />;
      return (
        <>
          <strong>{l.name}</strong> · {t('labels.born', { year: l.birth_year })} · {l.phone_last4 ? t('labels.phone', { last4: l.phone_last4 }) : t('labels.noPhone')} <ShortId id={id} />
        </>
      );
    },
    /** The "Show names" button, its note and its confirmation, for the ids on the list now. */
    panel(ids: readonly string[]): ReactNode {
      const unique = [...new Set(ids)];
      const first = unique.slice(0, PAGE_MAX);
      return (
        <>
          <div className="toolbar">
            <button
              type="button"
              disabled={unique.length === 0}
              onClick={() => {
                setNote(null);
                read.reset();
                setPending(first);
              }}
            >
              {t('labels.show')}
            </button>
          </div>
          {note && <p role="status">{note}</p>}
          {pending && (
            <ConfirmDialog
              title={t('labels.confirmTitle')}
              confirmLabel={t('labels.show')}
              busy={read.isPending}
              onConfirm={() => read.mutate(pending)}
              onCancel={() => {
                read.reset();
                setPending(null);
              }}
              status={
                <WriteProblem
                  problem={read.error}
                  forbidden={t('labels.refused')}
                  calm={{ not_found: t('labels.gone'), unavailable: t('labels.unavailable'), invalid: t('labels.tooMany') }}
                />
              }
            >
              <p>{t('labels.confirmBody', { n: formatNumber(pending.length, lang) })}</p>
              {unique.length > PAGE_MAX && <p>{t('labels.firstOnly', { max: formatNumber(PAGE_MAX, lang) })}</p>}
            </ConfirmDialog>
          )}
        </>
      );
    },
  };
}
