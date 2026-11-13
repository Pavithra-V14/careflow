// The console's sections and which roles see them in the navigation.
//
// CONVENIENCE ONLY: the BACKEND decides what a role may do (row-level security and the route gates). This list only
// hides links a role cannot use so the menu is not full of dead ends. It is never a permission check: a section's
// page still calls the API and shows "not available to your role" when the API answers 403. If this list and the
// backend disagree, the backend wins and this list is the thing to fix. Roles below follow the route docstrings in
// openapi.snapshot.json and docs/design/review-task-routes.md.

type SectionId = 'referrals' | 'tasks' | 'patients' | 'identity' | 'scheduling' | 'alerts' | 'dashboard' | 'care' | 'admin' | 'readiness' | 'claims' | 'access' | 'notes' | 'audit' | 'erasure';

export interface Section {
  id: SectionId;
  path: string;
  /** i18n key of the link text. */
  labelKey: string;
  roles: readonly string[];
}

const SECTIONS: readonly Section[] = [
  { id: 'referrals', path: '/referrals', labelKey: 'nav.referrals', roles: ['front_desk', 'nurse', 'doctor'] },
  { id: 'tasks', path: '/tasks', labelKey: 'nav.tasks', roles: ['front_desk', 'front_desk_supervisor', 'nurse', 'doctor', 'admin'] },
  { id: 'patients', path: '/patients', labelKey: 'nav.patients', roles: ['front_desk', 'nurse', 'doctor'] },
  { id: 'identity', path: '/identity', labelKey: 'nav.identity', roles: ['front_desk', 'nurse', 'doctor'] },
  { id: 'scheduling', path: '/scheduling', labelKey: 'nav.scheduling', roles: ['front_desk', 'nurse', 'doctor', 'admin'] },
  { id: 'alerts', path: '/alerts', labelKey: 'nav.alerts', roles: ['front_desk', 'nurse', 'doctor', 'admin'] },
  { id: 'dashboard', path: '/dashboard', labelKey: 'nav.dashboard', roles: ['admin'] },
  { id: 'care', path: '/care', labelKey: 'nav.care', roles: ['nurse', 'doctor', 'admin'] },
  { id: 'readiness', path: '/readiness', labelKey: 'nav.readiness', roles: ['nurse', 'doctor'] },
  { id: 'notes', path: '/notes', labelKey: 'nav.notes', roles: ['nurse', 'doctor'] },
  { id: 'claims', path: '/claims', labelKey: 'nav.claims', roles: ['front_desk'] },
  { id: 'access', path: '/access', labelKey: 'nav.access', roles: ['front_desk', 'nurse', 'doctor', 'admin'] },
  { id: 'erasure', path: '/erasure', labelKey: 'nav.erasure', roles: ['front_desk'] },
  { id: 'audit', path: '/audit', labelKey: 'nav.audit', roles: ['auditor'] },
  { id: 'admin', path: '/admin', labelKey: 'nav.admin', roles: ['admin', 'doctor'] },
];

/**
 * The admin section's tabs and who sees each. The doctor sees Autonomy (the two-person switch) and Red-flag rules (the two-doctor approval); the rest are admin only.
 * A convenience: the backend decides, and every tab still shows "not available to your role" on a 403.
 */
export const ADMIN_TABS = [
  { id: 'accounts', roles: ['admin'] },
  { id: 'autonomy', roles: ['admin', 'doctor'] },
  { id: 'rules', roles: ['admin', 'doctor'] },
  { id: 'reviewer', roles: ['admin'] },
  { id: 'cost', roles: ['admin'] },
  { id: 'voice', roles: ['admin'] },
  { id: 'recovery', roles: ['admin'] },
  { id: 'grievances', roles: ['admin'] },
] as const;

export const sectionsFor = (role: string): Section[] => SECTIONS.filter((s) => s.roles.includes(role));

// Finer, per-action conveniences (same caveat: the backend decides).
export const CAN_UPLOAD = ['front_desk', 'nurse'] as const;
export const CAN_SEARCH_PATIENTS = ['front_desk'] as const;
export const CAN_CONFIRM_IDENTITY = ['front_desk'] as const;
/** Write a care plan, and approve or reject a proposed one (a doctor, per the route docs). */
export const CAN_DECIDE_CARE_PLAN = ['doctor'] as const;
/** Care-gap writes: record a barrier, start an episode, resolve, mark a rule done, re-check a sample (owner: nurse and doctor only). */
export const CAN_RECORD_CARE_GAP = ['nurse', 'doctor'] as const;
/** Record a result (nurse or doctor). */
export const CAN_RECORD_RESULT = ['nurse', 'doctor'] as const;
/** Approve or reject a draft red-flag rule: doctors only (two different doctors). Admins read the list and decide nothing. */
export const CAN_DECIDE_RULE = ['doctor'] as const;
export const CAN_RELEASE_HOLD = ['front_desk'] as const;
/** Hold, book, cancel and join the waitlist, and mark an offer delivered (the routes say front desk; the backend decides). */
export const CAN_SCHEDULE = ['front_desk'] as const;
/** Add a provider, location, visit type or slot (front desk or admin, per the route docs). */
export const CAN_SET_UP = ['front_desk', 'admin'] as const;
export const CAN_REVIEW_REFERRAL = ['nurse', 'doctor'] as const;
export const CAN_REVOKE_IDENTITY = ['front_desk'] as const;
/** Read the audit log: the route says an auditor of some clinic (not admin). */
export const CAN_READ_AUDIT = ['auditor'] as const;
/** Request, approve and cancel a patient's erasure, and register a patient: front desk, per the route docs. */
export const CAN_ERASE = ['front_desk'] as const;
export const CAN_REGISTER_PATIENT = ['front_desk'] as const;
/** Start a Front Door run for a referral. The route says only "if you may start a run": the roles below are the owner's list; the backend decides. */
export const CAN_RUN_REFERRAL = ['front_desk', 'nurse', 'doctor'] as const;
/** Open the decrypted record: front desk, nurse and doctor of its clinic, per the route docs. */
export const CAN_VIEW_FULL_RECORD = ['front_desk', 'nurse', 'doctor'] as const;
export const CAN_SEE_DUPLICATES = ['front_desk'] as const;
/** Approve and unmerge (behind the mergeActions flag, which is off). */
export const CAN_MERGE_ACTIONS = ['front_desk'] as const;
// Whether a task can be closed here comes from the API (`resolvable` on a task); the console keeps no list of kinds for it.
/** Kinds that must never be treated as routine: shown first and visually distinct. */
export const URGENT_TASK_KINDS = ['red_flag', 'screening_unassessable'] as const;
