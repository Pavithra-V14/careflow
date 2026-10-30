import { PATHS } from './paths';

// The single source of truth for what the landing page may present as available. A feature is `live`
// only if a built page exists for it (`route`); everything else is `soon` and is shown with the
// "Coming soon" label. tests/features.test.ts fails if a live feature has no built route or a
// soon feature loses its label.
export type FeatureStatus = { status: 'live'; route: string } | { status: 'soon' };

export const FEATURES = [
  { key: 'book', status: 'soon' },
  { key: 'offers', status: 'soon' },
  { key: 'messages', status: 'soon' },
  { key: 'records', status: 'soon' },
  { key: 'family', status: 'soon' },
  // The language switcher is on every patient page, so the home page is its route.
  { key: 'language', status: 'live', route: PATHS.home },
] as const satisfies readonly ({ key: string } & FeatureStatus)[];
