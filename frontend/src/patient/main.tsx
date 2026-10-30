import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import '../shared/tokens/tokens.css';
import '../shared/tokens/base.css';
import { initI18n } from '../shared/i18n';
import { App } from './App';

initI18n();
document.documentElement.dataset.density = 'patient';

createRoot(document.getElementById('root') as HTMLElement).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
