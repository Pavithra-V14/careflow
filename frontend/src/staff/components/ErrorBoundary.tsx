import { Component, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';

// A screen that throws while rendering must not take the whole app down or show what went wrong. The message is fixed:
// no error text, no stack, no component name, and nothing from the backend. The error itself is dropped on purpose (a
// message can carry a record id or a name): only getDerivedStateFromError is used, so nothing is logged or kept.
function Fallback() {
  const { t } = useTranslation();
  return (
    <section role="alert" aria-labelledby="boundary-title" className="auth-card">
      <h1 id="boundary-title" tabIndex={-1} ref={(el) => el?.focus()}>
        {t('errorBoundary.title')}
      </h1>
      <p>{t('errorBoundary.body')}</p>
      <p>
        <button type="button" className="primary" onClick={() => window.location.reload()}>
          {t('errorBoundary.reload')}
        </button>{' '}
        <a href="#/" onClick={() => window.setTimeout(() => window.location.reload(), 0)}>
          {t('errorBoundary.home')}
        </a>
      </p>
    </section>
  );
}

interface State {
  failed: boolean;
}

export class ErrorBoundary extends Component<{ children: ReactNode; resetKey?: string }, State> {
  override state: State = { failed: false };

  static getDerivedStateFromError(): State {
    return { failed: true };
  }

  override componentDidUpdate(prev: { resetKey?: string }): void {
    // Moving to another screen gives it a fresh chance.
    if (this.state.failed && prev.resetKey !== this.props.resetKey) this.setState({ failed: false });
  }

  override render(): ReactNode {
    return this.state.failed ? <Fallback /> : this.props.children;
  }
}
