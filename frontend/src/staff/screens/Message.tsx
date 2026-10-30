import type { ReactNode } from 'react';
import { ScreenHeading } from '../components/ScreenHeading';

// A neutral full-page message, used for "not configured", "this account cannot use the console" and load
// errors. It gives no reason: the API answers the same way for "no account" and "not allowed".
export function Message({ id, title, children, alert = false }: { id: string; title: string; children: ReactNode; alert?: boolean }) {
  return (
    <section aria-labelledby={id} className="auth-card">
      <ScreenHeading id={id}>{title}</ScreenHeading>
      <div role={alert ? 'alert' : 'status'}>{children}</div>
    </section>
  );
}

export function Loading({ label }: { label: string }) {
  return (
    <p role="status" className="loading">
      {label}
    </p>
  );
}
