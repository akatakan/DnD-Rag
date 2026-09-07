import { Component, type ErrorInfo, type ReactNode } from "react";

type Props = {
  /** Names the failed area in the message, e.g. "3B zar tepsisi". */
  area: string;
  children: ReactNode;
  /** Rendered instead of the default panel, for non-critical subtrees. */
  fallback?: ReactNode;
};

type State = { error: Error | null };

/**
 * Keeps one thrown render error from blanking the whole table.
 *
 * React unmounts the entire tree when a render throws and nothing catches it,
 * so before this a single component fault dropped players out of a live
 * session with an empty screen. Retry re-mounts just the wrapped subtree.
 */
export default class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error(`[${this.props.area}] render hatasi`, error, info.componentStack);
  }

  private retry = () => this.setState({ error: null });

  render() {
    if (!this.state.error) return this.props.children;
    if (this.props.fallback !== undefined) return this.props.fallback;
    return (
      <div className="error-boundary" role="alert">
        <h2>{this.props.area} yüklenemedi</h2>
        <p>
          Beklenmeyen bir hata oluştu. Oturum sunucuda duruyor; yeniden
          denemek verinizi etkilemez.
        </p>
        <pre>{this.state.error.message}</pre>
        <div className="error-boundary-actions">
          <button type="button" onClick={this.retry}>
            Yeniden dene
          </button>
          <button type="button" onClick={() => window.location.reload()}>
            Sayfayı yenile
          </button>
        </div>
      </div>
    );
  }
}
