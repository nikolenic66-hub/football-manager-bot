import React from 'react';

type ErrorBoundaryProps = {
  children: React.ReactNode;
};

type ErrorBoundaryState = {
  hasError: boolean;
  retryKey: number;
};

export default class ErrorBoundary extends React.Component<
  ErrorBoundaryProps,
  ErrorBoundaryState
> {
  state: ErrorBoundaryState = {
    hasError: false,
    retryKey: 0,
  };

  static getDerivedStateFromError(): Partial<ErrorBoundaryState> {
    return { hasError: true };
  }

  componentDidCatch(error: Error, info: React.ErrorInfo) {
    console.error('[Mini App] Ошибка рендера:', error);
    console.error('[Mini App] Component stack:', info.componentStack);
    console.error(
      '[Mini App] Полный стек ошибки:',
      error.stack || 'Стек ошибки отсутствует',
    );
  }

  private handleRetry = () => {
    this.setState((state) => ({
      hasError: false,
      retryKey: state.retryKey + 1,
    }));
  };

  render() {
    if (this.state.hasError) {
      return (
        <main className="app-shell error-boundary-screen" role="alert">
          <section className="card error-boundary-card">
            <h1>Что-то пошло не так. Нажмите &quot;Повторить&quot;</h1>
            <button className="btn" type="button" onClick={this.handleRetry}>
              Повторить
            </button>
          </section>
        </main>
      );
    }

    return <React.Fragment key={this.state.retryKey}>{this.props.children}</React.Fragment>;
  }
}
