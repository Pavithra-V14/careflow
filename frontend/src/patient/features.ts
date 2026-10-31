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

// The "how it works" steps and the FAQ answers also describe the portal. Each is marked the same way as the features
// above: anything that describes something not built yet carries the "Coming soon" label next to it, never alone.
// tests/landing-unbuilt.test.tsx fails if a present-tense sentence about an unbuilt feature has no label.
export const HOW_STEPS = [
  { key: 'step1', status: 'soon' },
  { key: 'step2', status: 'soon' },
  { key: 'step3', status: 'soon' },
] as const satisfies readonly ({ key: string } & FeatureStatus)[];

export const FAQ_ITEMS = [
  { q: 'q1', a: 'a1', status: 'soon' },
  { q: 'q2', a: 'a2', status: 'live', route: PATHS.home },
  { q: 'q3', placeholder: 'faqGrievance', status: 'live', route: PATHS.privacy },
  { q: 'q4', placeholder: 'faqPrivacy', status: 'live', route: PATHS.privacy },
] as const;
