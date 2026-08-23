import { Component, type ErrorInfo, type ReactNode } from "react";
import { useLocation } from "react-router-dom";
import { ErrorState } from "./states";

type RouteErrorBoundaryProps = {
  children: ReactNode;
  resetKey?: string;
};

type RouteErrorBoundaryState = {
  error: Error | null;
};

export class RouteErrorBoundary extends Component<RouteErrorBoundaryProps, RouteErrorBoundaryState> {
  state: RouteErrorBoundaryState = { error: null };

  static getDerivedStateFromError(error: Error): RouteErrorBoundaryState {
    return { error };
  }

  componentDidUpdate(prevProps: RouteErrorBoundaryProps) {
    if (prevProps.resetKey !== this.props.resetKey && this.state.error) {
      this.setState({ error: null });
    }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("Route render failure", error, info.componentStack);
  }

  render() {
    if (this.state.error) {
      return (
        <div className="shell-content">
          <ErrorState
            label="This section failed to render"
            detail={this.state.error.message || "An unexpected error occurred in this route."}
            onRetry={() => this.setState({ error: null })}
            retryLabel="Try again"
          />
        </div>
      );
    }
    return this.props.children;
  }
}

export function RoutedOutletBoundary({ children }: { children: ReactNode }) {
  const location = useLocation();
  return <RouteErrorBoundary resetKey={location.pathname}>{children}</RouteErrorBoundary>;
}
