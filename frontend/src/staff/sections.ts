// The console's sections and which roles see them in the navigation.
//
// CONVENIENCE ONLY: the BACKEND decides what a role may do (row-level security and the route gates). This list only
// hides links a role cannot use so the menu is not full of dead ends. It is never a permission check: a section's
// page still calls the API and shows "not available to your role" when the API answers 403. If this list and the
// backend disagree, the backend wins and this list is the thing to fix. Roles below follow the route docstrings in
// openapi.snapshot.json and docs/design/review-task-routes.md.

export type SectionId = 'referrals' | 'tasks' | 'patients' | 'identity' | 'scheduling' | 'alerts' | 'dashboard' | 'care';

export interface Section {
  id: SectionId;
  path: string;
  /** i18n key of the link text. */
  labelKey: string;
  roles: readonly string[];
}

export const SECTIONS: readonly Section[] = [
  { id: 'referrals', path: '/referrals', labelKey: 'nav.referrals', roles: ['front_desk', 'nurse', 'doctor'] },
  { id: 'tasks', path: '/tasks', labelKey: 'nav.tasks', roles: ['front_desk', 'nurse', 'doctor', 'admin'] },
  { id: 'patients', path: '/patients', labelKey: 'nav.patients', roles: ['front_desk', 'nurse', 'doctor'] },
  { id: 'identity', path: '/identity', labelKey: 'nav.identity', roles: ['front_desk', 'nurse', 'doctor'] },
  { id: 'scheduling', path: '/scheduling', labelKey: 'nav.scheduling', roles: ['front_desk', 'nurse', 'doctor', 'admin'] },
  { id: 'alerts', path: '/alerts', labelKey: 'nav.alerts', roles: ['front_desk', 'nurse', 'doctor', 'admin'] },
  { id: 'dashboard', path: '/dashboard', labelKey: 'nav.dashboard', roles: ['admin'] },
  { id: 'care', path: '/care', labelKey: 'nav.care', roles: ['nurse', 'doctor', 'admin'] },
];

export const sectionsFor = (role: string): Section[] => SECTIONS.filter((s) => s.roles.includes(role));

// Finer, per-action conveniences (same caveat: the backend decides).
export const CAN_UPLOAD = ['front_desk', 'nurse'] as const;
export const CAN_SEARCH_PATIENTS = ['front_desk'] as const;
export const CAN_CONFIRM_IDENTITY = ['front_desk'] as const;
/** Write a care plan, and approve or reject a proposed one (a doctor, per the route docs). */
export const CAN_DECIDE_CARE_PLAN = ['doctor'] as const;
/** Record a result (nurse or doctor). */
export const CAN_RECORD_RESULT = ['nurse', 'doctor'] as const;
export const CAN_RELEASE_HOLD = ['front_desk'] as const;
/** Hold, book, cancel and join the waitlist, and mark an offer delivered (the routes say front desk; the backend decides). */
export const CAN_SCHEDULE = ['front_desk'] as const;
/** Add a provider, location, visit type or slot (front desk or admin, per the route docs). */
export const CAN_SET_UP = ['front_desk', 'admin'] as const;
export const CAN_REVIEW_REFERRAL = ['nurse', 'doctor'] as const;
export const CAN_REVOKE_IDENTITY = ['front_desk'] as const;
export const CAN_SEE_DUPLICATES = ['front_desk'] as const;
/** Approve and unmerge (behind the mergeActions flag, which is off). */
export const CAN_MERGE_ACTIONS = ['front_desk'] as const;
/** Kinds the backend's resolve route will close (careflow/review_tasks.py RESOLVABLE_KINDS). Others are a 409. */
export const RESOLVABLE_TASK_KINDS = ['referral_hold', 'unmerge_review', 'referral_dead_letter', 'identity_confirmation_revoked'] as const;
/** Kinds that must never be treated as routine: shown first and visually distinct. */
export const URGENT_TASK_KINDS = ['red_flag', 'screening_unassessable'] as const;
