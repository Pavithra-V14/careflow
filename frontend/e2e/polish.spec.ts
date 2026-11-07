import { expect, test } from '@playwright/test';
import { ID } from './consoleApi';
import { LANGS, VIEWPORTS, check, go, openConsole } from './helpers';

// The whole console, once: EVERY route a role can reach, at 320 and 1280 px, in English and Tamil, with axe (WCAG 2.2 AA is the goal we test
// toward), no horizontal scroll, no CSP violation, no backend text, and the LIVE / TEST badge present. Earlier specs go deeper into
// dialogs and keyboard paths; this one is the guarantee that no screen was left out. MOCK ONLY (e2e/consoleApi.ts).

const PATIENT = ID.patient;
const ROUTES: [role: string, name: string, hash: string][] = [
  ['front_desk', 'home', '#/'],
  ['front_desk', 'referrals', '#/referrals'],
  ['front_desk', 'referral-detail', `#/referrals/${ID.doc}`],
  ['front_desk', 'upload', '#/referrals/upload'],
  ['front_desk', 'tasks', '#/tasks'],
  ['front_desk', 'task-detail', `#/tasks/${ID.task}`],
  ['front_desk', 'patients', '#/patients'],
  ['front_desk', 'patient-record', `#/patients/${PATIENT}`],
  ['front_desk', 'identity-merges', '#/identity/merges'],
  ['front_desk', 'identity-duplicates', '#/identity/duplicates'],
  ['front_desk', 'merge-detail', `#/identity/merges/${ID.merge}`],
  ['front_desk', 'scheduling-slots', '#/scheduling/slots'],
  ['front_desk', 'scheduling-bookings', '#/scheduling/bookings'],
  ['front_desk', 'scheduling-waitlist', '#/scheduling/waitlist'],
  ['front_desk', 'scheduling-holds', '#/scheduling/holds'],
  ['front_desk', 'scheduling-offers', '#/scheduling/offers'],
  ['front_desk', 'scheduling-providers', '#/scheduling/providers'],
  ['front_desk', 'scheduling-locations', '#/scheduling/locations'],
  ['front_desk', 'scheduling-visit-types', '#/scheduling/visit-types'],
  ['front_desk', 'scheduling-priority-levels', '#/scheduling/priority-levels'],
  ['front_desk', 'alerts', '#/alerts'],
  ['front_desk', 'claims', '#/claims'],
  ['front_desk', 'access', '#/access'],
  ['front_desk', 'not-found', '#/nowhere'],
  ['nurse', 'referral-review', `#/referrals/${ID.doc}`],
  ['nurse', 'care-gaps', '#/care/gaps'],
  ['nurse', 'care-plans', '#/care/plans'],
  ['nurse', 'care-results', '#/care/results'],
  ['nurse', 'readiness', '#/readiness'],
  ['nurse', 'notes', `#/notes?patient=${PATIENT}`],
  ['nurse', 'access-nurse', '#/access'],
  ['doctor', 'autonomy-doctor', '#/admin/autonomy'],
  ['admin', 'dashboard', '#/dashboard'],
  ['admin', 'admin-accounts', '#/admin/accounts'],
  ['admin', 'admin-autonomy', '#/admin/autonomy'],
  ['admin', 'admin-reviewer', '#/admin/reviewer'],
  ['admin', 'admin-cost', '#/admin/cost'],
  ['admin', 'admin-voice', '#/admin/voice'],
  ['admin', 'admin-recovery', '#/admin/recovery'],
  ['admin', 'admin-grievances', '#/admin/grievances'],
  ['admin', 'access-review', '#/access'],
];

for (const lang of LANGS) {
  for (const [role, name, hash] of ROUTES) {
    test(`${role}: ${name} [${lang}]`, async ({ page }) => {
      await openConsole(page, role, lang);
      await go(page, hash, /\S/);
      // Wait for the screen to settle: no loading text left.
      await expect(page.locator('main')).not.toContainText(/Loading…|ஏற்றுகிறது…/);
      for (const vp of [VIEWPORTS[0], VIEWPORTS[2]]) await check(page, `polish-${role}-${name}`, lang, vp);
    });
  }
}
