import { HashRouter, Navigate, NavLink, Route, Routes } from 'react-router';
import { useTranslation } from 'react-i18next';
import { EnvironmentBadge } from '../components/EnvironmentBadge';
import { shortClinicId } from '../me';
import { sectionsFor } from '../sections';
import { useSession } from '../session/SessionContext';
import { HomePage } from '../pages/HomePage';
import { NotFoundPage } from '../pages/NotFoundPage';
import { ReferralsPage } from '../pages/ReferralsPage';
import { ReferralDetailPage } from '../pages/ReferralDetailPage';
import { UploadPage } from '../pages/UploadPage';
import { TasksPage } from '../pages/TasksPage';
import { TaskDetailPage } from '../pages/TaskDetailPage';
import { PatientsPage } from '../pages/PatientsPage';
import { PatientRecordPage } from '../pages/PatientRecordPage';
import { SchedulingPage } from '../pages/SchedulingPage';
import { IdentityPage } from '../pages/IdentityPage';
import { MergeDetailPage } from '../pages/MergeDetailPage';

/**
 * The signed-in console: a persistent session bar (role, clinic, LIVE / TEST badge) above every screen, a role-aware
 * navigation, and the screens. Hash routing, so the static /staff/ page needs no server rewrites.
 *
 * The navigation hides sections the active role cannot use. That is a CONVENIENCE: the backend decides, and every
 * page still calls the API and shows "not available to your role" on a 403, so typing a URL by hand gets nowhere.
 */
export function Shell() {
  const { t } = useTranslation();
  const { account, active, changeRole, signOut } = useSession();
  const sections = sectionsFor(active.role);

  return (
    <HashRouter>
      <div className="session-bar" role="group" aria-label={t('shell.navLabel')}>
        <span className="role-badge">
          <span className="visually-hidden">{t('shell.activeRole')}: </span>
          {t(`roles.${active.role}`, { defaultValue: active.role })}
        </span>
        <span className="clinic">
          <span className="label">{t('shell.clinic')}: </span>
          {active.clinicName ?? <code title={active.clinicId}>{shortClinicId(active.clinicId)}</code>}
        </span>
        <EnvironmentBadge origin={active.clinicOrigin} />
        <span className="profile">
          {account.operatingProfile ? `${t('shell.profile')}: ${t(`profiles.${account.operatingProfile}`)}` : t('shell.profileUnknown')}
        </span>
        {changeRole && (
          <button type="button" onClick={changeRole}>
            {t('shell.changeRole')}
          </button>
        )}
        <button type="button" onClick={signOut}>
          {t('common.signOut')}
        </button>
      </div>

      <nav aria-label={t('nav.label')} className="main-nav">
        <ul>
          <li>
            <NavLink to="/" end>
              {t('nav.home')}
            </NavLink>
          </li>
          {sections.map((s) => (
            <li key={s.id}>
              <NavLink to={s.path}>{t(s.labelKey)}</NavLink>
            </li>
          ))}
        </ul>
      </nav>

      <Routes>
        <Route path="/" element={<HomePage />} />
        <Route path="/referrals" element={<ReferralsPage />} />
        <Route path="/referrals/upload" element={<UploadPage />} />
        <Route path="/referrals/:documentId" element={<ReferralDetailPage />} />
        <Route path="/tasks" element={<TasksPage />} />
        <Route path="/tasks/:taskId" element={<TaskDetailPage />} />
        <Route path="/patients" element={<PatientsPage />} />
        <Route path="/patients/:patientId" element={<PatientRecordPage />} />
        <Route path="/identity" element={<Navigate to="/identity/merges" replace />} />
        <Route path="/identity/merges/:mergeId" element={<MergeDetailPage />} />
        <Route path="/identity/:list" element={<IdentityPage />} />
        {/* The bare section opens its first list, so the sub-navigation always shows where you are. */}
        <Route path="/scheduling" element={<Navigate to="/scheduling/providers" replace />} />
        <Route path="/scheduling/:list" element={<SchedulingPage />} />
        <Route path="*" element={<NotFoundPage />} />
      </Routes>
    </HashRouter>
  );
}
