// Each patient page is its own HTML entry (multi-page build), so links are plain absolute paths and
// work on any static host without server-side rewrites.
export const PATHS = {
  home: '/patient/',
  signIn: '/patient/sign-in/',
  signUp: '/patient/sign-up/',
  privacy: '/patient/privacy/',
  staff: '/staff/',
} as const;
