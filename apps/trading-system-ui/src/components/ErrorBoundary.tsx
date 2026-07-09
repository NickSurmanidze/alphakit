import { Component, type ErrorInfo, type ReactNode } from 'react';

import { Button } from '@/components/ui/button';

interface Props {
  children: ReactNode;
}

interface State {
  error: Error | null;
}

// React error boundaries have no hook equivalent (as of React 19) -- must be a class component.
// Without this, an uncaught render error anywhere in `children` unmounts the whole tree, which is
// exactly what turned a chart-data bug into a blank white page with nothing else on it (no nav,
// no way to navigate away) rather than a contained, recoverable failure.
export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error('Uncaught error in route content:', error, info.componentStack);
  }

  render() {
    if (this.state.error) {
      return (
        <div className="flex flex-col items-start gap-3 rounded-md border border-destructive/30 bg-destructive/5 p-6">
          <p className="text-sm font-medium text-destructive">Something went wrong rendering this page.</p>
          <p className="max-w-xl text-sm text-muted-foreground">{this.state.error.message}</p>
          <Button variant="outline" size="sm" onClick={() => this.setState({ error: null })}>
            Try again
          </Button>
        </div>
      );
    }

    return this.props.children;
  }
}
