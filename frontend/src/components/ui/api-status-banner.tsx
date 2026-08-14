import { useEffect, useState } from "react";
import { AlertCircle, CloudOff, LoaderCircle, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import type { ApiStatusDetail } from "@/lib/api";

type GlobalStatus = "offline" | "provider-outage" | "session-expired" | "slow" | "stale" | null;

function ApiStatusBanner() {
  const [offline, setOffline] = useState(() => typeof navigator !== "undefined" && navigator.onLine === false);
  const [sessionExpired, setSessionExpired] = useState(false);
  const [providerOutage, setProviderOutage] = useState(false);
  const [slowRequests, setSlowRequests] = useState<Set<string>>(() => new Set());
  const [stalePaths, setStalePaths] = useState<Set<string>>(() => new Set());

  useEffect(() => {
    const handleOnline = () => setOffline(false);
    const handleOffline = () => setOffline(true);
    const handleApiStatus = (event: Event) => {
      const detail = (event as CustomEvent<ApiStatusDetail>).detail;
      if (!detail) return;
      if (detail.state === "offline") setOffline(true);
      if (detail.state === "session-expired") setSessionExpired(true);
      if (detail.state === "provider-outage") setProviderOutage(true);
      if (detail.state === "stale") setStalePaths((current) => new Set(current).add(detail.path));
      if (detail.state === "fresh") {
        setStalePaths((current) => {
          const next = new Set(current);
          next.delete(detail.path);
          return next;
        });
      }
      if (detail.state === "slow") {
        setSlowRequests((current) => new Set(current).add(detail.requestId));
      }
      if (detail.state === "settled") {
        setSlowRequests((current) => {
          const next = new Set(current);
          next.delete(detail.requestId);
          return next;
        });
      }
    };

    window.addEventListener("online", handleOnline);
    window.addEventListener("offline", handleOffline);
    window.addEventListener("expenseops:api-status", handleApiStatus);
    return () => {
      window.removeEventListener("online", handleOnline);
      window.removeEventListener("offline", handleOffline);
      window.removeEventListener("expenseops:api-status", handleApiStatus);
    };
  }, []);

  const status: GlobalStatus = offline ? "offline" : sessionExpired ? "session-expired" : providerOutage ? "provider-outage" : slowRequests.size ? "slow" : stalePaths.size ? "stale" : null;
  if (!status) return <div className="sr-only" aria-live="polite" aria-atomic="true" />;

  const content = {
    offline: {
      icon: CloudOff,
      title: "You’re offline",
      message: "ExpenseOps is showing the last information it loaded. Changes will be available after you reconnect.",
    },
    "session-expired": {
      icon: AlertCircle,
      title: "Your session expired",
      message: "Sign in again before making changes. Your current page is still here.",
    },
    "provider-outage": {
      icon: AlertCircle,
      title: "A connected service is unavailable",
      message: "The affected change was not confirmed. Your current ExpenseOps data is still available.",
    },
    slow: {
      icon: LoaderCircle,
      title: "Still working",
      message: "This request is taking longer than usual. You can keep using other parts of ExpenseOps.",
    },
    stale: {
      icon: AlertCircle,
      title: "Some information may be out of date",
      message: "ExpenseOps kept the last information it loaded because one or more updates failed.",
    },
  }[status];
  const Icon = content.icon;

  return (
    <aside
      role={status === "session-expired" ? "alert" : "status"}
      aria-live={status === "session-expired" ? "assertive" : "polite"}
      className="pointer-events-none fixed inset-x-4 top-20 z-[110] mx-auto flex max-w-2xl items-start gap-3 rounded-card border border-slate-200 bg-white p-4 shadow-primary"
    >
      <Icon aria-hidden="true" className={`mt-0.5 size-5 shrink-0 ${status === "slow" ? "animate-spin text-indigo-600 motion-reduce:animate-none" : "text-slate-700"}`} />
      <div className="min-w-0 flex-1">
        <p className="text-sm font-semibold text-slate-950">{content.title}</p>
        <p className="mt-0.5 text-sm leading-5 text-slate-600">{content.message}</p>
      </div>
      {status === "session-expired" ? (
        <Button className="pointer-events-auto" size="sm" onClick={() => window.location.assign("/auth/login")}>
          Sign in
        </Button>
      ) : status === "slow" || status === "provider-outage" || status === "stale" ? (
        <button
          type="button"
          aria-label={status === "slow" ? "Dismiss slow request message" : status === "provider-outage" ? "Dismiss provider outage message" : "Dismiss stale information message"}
          onClick={() => status === "slow" ? setSlowRequests(new Set()) : status === "provider-outage" ? setProviderOutage(false) : setStalePaths(new Set())}
          className="pointer-events-auto inline-flex size-11 shrink-0 items-center justify-center rounded-control text-slate-500 transition-colors hover:bg-slate-100 hover:text-slate-900 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-600 focus-visible:ring-offset-2"
        >
          <X aria-hidden="true" className="size-4" />
        </button>
      ) : null}
    </aside>
  );
}

export { ApiStatusBanner };
