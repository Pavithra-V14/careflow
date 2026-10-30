import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import '../shared/tokens/tokens.css';
import '../shared/tokens/base.css';
import './patient.css';
import { initI18n, i18n } from '../shared/i18n';
import { App, type PatientPage } from './App';

const PAGES: readonly PatientPage[] = ['landing', 'sign-in', 'sign-up', 'privacy'];
const requested = document.body.dataset.page;
const page: PatientPage = PAGES.find((p) => p === requested) ?? 'landing';

initI18n();
document.documentElement.dataset.density = 'patient';

const TITLE_KEY: Record<PatientPage, string> = {
  landing: 'app.hospitalName',
  'sign-in': 'pages.signInTitle',
  'sign-up': 'pages.signUpTitle',
  privacy: 'pages.privacyTitle',
};
const setTitle = () => {
  document.title = i18n.t(TITLE_KEY[page]);
};
setTitle();
i18n.on('languageChanged', setTitle);

createRoot(document.getElementById('root') as HTMLElement).render(
  <StrictMode>
    <App page={page} />
  </StrictMode>,
);
