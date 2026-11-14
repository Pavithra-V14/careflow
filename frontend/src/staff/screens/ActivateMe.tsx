import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { ApiProblem, toProblem, unwrap } from '../apiState';
import { useAuth } from '../auth/AuthProvider';
import { ConfirmDialog } from '../ui/ConfirmDialog';
import { WriteProblem } from '../ui/WriteProblem';

// A person who was invited to the staff console activates their OWN account here (nobody else can, an administrator included). It is
// offered on the "cannot use the console" page because the API answers that page's 403 the same way for every reason; the activate
// call itself answers 403 when the account is not waiting to be activated, and that is told in fixed words. The route takes no
// idempotency key (it sets a state that is already set on a repeat), so none is sent.
export function ActivateMe() {
  const { t } = useTranslation();
  const { api } = useAuth();
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const activate = useMutation<{ activated: boolean }, ApiProblem, void>({
    retry: false,
    mutationFn: async () => {
      try {
        return await unwrap(api.activateMe());
      } catch (e) {
        throw toProblem(e);
      }
    },
    onSuccess: (r) => {
      setOpen(false);
      if (r.activated) {
        setNote(t('staff.activate.done'));
        void queryClient.invalidateQueries({ queryKey: ['me'] });
      } else setNote(t('staff.activate.notDone'));
    },
  });
  return (
    <section aria-labelledby="activate-title" className="auth-card">
      <h2 id="activate-title">{t('staff.activate.title')}</h2>
      <p>{t('staff.activate.body')}</p>
      {note && <p role="status">{note}</p>}
      <button
        type="button"
        onClick={() => {
          setNote(null);
          setOpen(true);
        }}
      >
        {t('staff.activate.button')}
      </button>
      {open && (
        <ConfirmDialog
          title={t('staff.activate.confirmTitle')}
          confirmLabel={t('staff.activate.button')}
          busy={activate.isPending}
          onConfirm={() => activate.mutate()}
          onCancel={() => {
            activate.reset();
            setOpen(false);
          }}
          status={<WriteProblem problem={activate.error} forbidden={t('staff.activate.refused')} />}
        >
          <p>{t('staff.activate.confirmBody')}</p>
        </ConfirmDialog>
      )}
    </section>
  );
}
