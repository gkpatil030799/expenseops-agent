import { Component, type ErrorInfo, type ReactNode } from "react";
import { AlertTriangle, RefreshCw } from "lucide-react";

import { Button } from "@/components/ui/button";

type ErrorBoundaryProps = {
  children: ReactNode;
};

type ErrorBoundaryState = {
  failed: boolean;
};

class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  state: ErrorBoundaryState = { failed: false };

  static getDerivedStateFromError(): ErrorBoundaryState {
    return { failed: true };
  }

  componentDidCatch(error: Error, errorInfo: ErrorInfo) {
    // Keep diagnostics out of the customer message while still making local
    // development failures inspectable. Production observability can attach to
    // this boundary without changing the recovery experience.
    console.error("ExpenseOps render failure", error, errorInfo);
  }

  render() {
    if (!this.state.failed) return this.props.children;

    return (
      <main className="flex min-h-screen items-center justify-center bg-slate-50 px-5 py-12">
        <section
          aria-labelledby="unexpected-error-title"
          className="w-full max-w-xl rounded-card border border-rose-200 bg-white p-6 shadow-card sm:p-8"
        >
          <span className="mb-5 inline-flex size-12 items-center justify-center rounded-full bg-rose-50 text-rose-700">
            <AlertTriangle aria-hidden="true" className="size-6" />
          </span>
          <h1 id="unexpected-error-title" className="text-2xl font-semibold tracking-tight text-slate-950">
            ExpenseOps hit an unexpected problem
          </h1>
          <p className="mt-2 max-w-lg text-base leading-7 text-slate-600">
            Your saved data was not changed. Reload this view to try again. If the problem continues, share the time it happened with support.
          </p>
          <Button className="mt-6" onClick={() => window.location.reload()}>
            <RefreshCw aria-hidden="true" className="size-4" />
            Reload ExpenseOps
          </Button>
        </section>
      </main>
    );
  }
}

export { ErrorBoundary };
