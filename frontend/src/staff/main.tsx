import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import '../shared/tokens/tokens.css';
import '../shared/tokens/base.css';
import './staff.css';
import './console.css';
import { readConfig } from '../shared/config';
import { initI18n, i18n } from '../shared/i18n';
import { App } from './App';
import { createSupabasePort } from './auth/supabasePort';

initI18n();
document.documentElement.dataset.density = 'staff';

const setTitle = () => {
  document.title = i18n.t('app.staffTitle');
};
setTitle();
i18n.on('languageChanged', setTitle);

const config = readConfig();
const port = config.configured ? createSupabasePort(config.supabaseUrl, config.supabasePublishableKey) : null;

createRoot(document.getElementById('root') as HTMLElement).render(
  <StrictMode>
    <App port={port} apiBaseUrl={config.apiBaseUrl} />
  </StrictMode>,
);
