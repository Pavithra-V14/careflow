import type { ReactNode } from 'react';
import { HashRouter, Navigate, NavLink, Route, Routes, useLocation } from 'react-router';
import { useTranslation } from 'react-i18next';
import { ErrorBoundary } from '../components/ErrorBoundary';
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
import { AlertsPage } from '../pages/AlertsPage';
import { DashboardPage } from '../pages/DashboardPage';
import { CarePage } from '../pages/CarePage';
import { AdminPage } from '../pages/AdminPage';
import { ReadinessPage } from '../pages/ReadinessPage';
import { ClaimsPage } from '../pages/ClaimsPage';
import { AccessPage } from '../pages/AccessPage';
import { NotesPage } from '../pages/NotesPage';
import { AuditPage } from '../pages/AuditPage';
import { ErasurePage } from '../pages/ErasurePage';

/**
 * The signed-in console: a persistent session bar (role, clinic, LIVE / TEST badge) above every screen, a role-aware
 * navigation, and the screens. Hash routing, so the static /staff/ page needs no server rewrites.
 *
 * The navigation hides sections the active role cannot use. That is a CONVENIENCE: the backend decides, and every
 * page still calls the API and shows "not available to your role" on a 403, so typing a URL by hand gets nowhere.
 */
/** A screen that fails to render is replaced by a fixed message; the session bar and navigation stay, and another screen starts fresh. */
function GuardedRoutes({ children }: { children: ReactNode }) {
  const { pathname } = useLocation();
  return <ErrorBoundary resetKey={pathname}>{children}</ErrorBoundary>;
}

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

      <GuardedRoutes>
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
          <Route path="/scheduling" element={<Navigate to="/scheduling/slots" replace />} />
          <Route path="/scheduling/:list" element={<SchedulingPage />} />
          <Route path="/alerts" element={<AlertsPage />} />
          <Route path="/dashboard" element={<DashboardPage />} />
          <Route path="/care" element={<Navigate to="/care/gaps" replace />} />
          <Route path="/care/:tab" element={<CarePage />} />
          <Route path="/readiness" element={<ReadinessPage />} />
          <Route path="/notes" element={<NotesPage />} />
          <Route path="/claims" element={<ClaimsPage />} />
          <Route path="/access" element={<AccessPage />} />
          <Route path="/erasure" element={<ErasurePage />} />
          <Route path="/audit" element={<AuditPage />} />
          <Route path="/admin" element={<AdminPage />} />
          <Route path="/admin/:tab" element={<AdminPage />} />
          <Route path="*" element={<NotFoundPage />} />
        </Routes>
      </GuardedRoutes>
    </HashRouter>
  );
}
