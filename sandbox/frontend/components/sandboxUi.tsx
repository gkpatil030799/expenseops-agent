import { CheckCircle2, Clock3, Copy, XCircle, AlertTriangle, Circle } from "lucide-react";
import type React from "react";
import type { ComponentType } from "react";

export function statusTone(value?: string | null) {
  const normalized = (value || "").toLowerCase();
  if (["success", "succeeded", "completed", "processed", "ready", "connected", "active"].includes(normalized)) {
    return "bg-emerald-50 text-emerald-700 ring-emerald-100";
  }
  if (["failed", "error", "blocked", "missing", "disabled", "wrong env"].includes(normalized)) {
    return "bg-red-50 text-red-700 ring-red-100";
  }
  if (["fallback", "unknown", "warning", "queued", "skipped"].includes(normalized)) {
    return "bg-amber-50 text-amber-700 ring-amber-100";
  }
  if (["started", "running", "syncing"].includes(normalized)) {
    return "bg-sky-50 text-sky-700 ring-sky-100";
  }
  return "bg-slate-100 text-slate-700 ring-slate-200";
}

export function StatusPill({ value, label }: { value?: string | null; label?: string }) {
  return (
    <span className={`inline-flex items-center rounded-full px-2.5 py-1 text-xs font-semibold ring-1 ${statusTone(value)}`}>
      {label || value || "unknown"}
    </span>
  );
}

export function statusIcon(value?: string | null): ComponentType<{ className?: string }> {
  const normalized = (value || "").toLowerCase();
  if (["success", "succeeded", "completed", "processed", "ready"].includes(normalized)) return CheckCircle2;
  if (["failed", "error", "blocked"].includes(normalized)) return XCircle;
  if (["fallback", "unknown", "warning", "missing", "skipped"].includes(normalized)) return AlertTriangle;
  if (["started", "running", "syncing"].includes(normalized)) return Clock3;
  return Circle;
}

export function CopyButton({ value, label = "Copy" }: { value: string; label?: string }) {
  return (
    <button
      type="button"
      onClick={() => void navigator.clipboard?.writeText(value)}
      className="inline-flex items-center gap-1 rounded-md border border-slate-200 bg-white px-2 py-1 text-xs font-medium text-slate-600 shadow-sm transition hover:border-slate-300 hover:text-slate-950"
      title={label}
    >
      <Copy className="h-3.5 w-3.5" />
      <span className="sr-only">{label}</span>
    </button>
  );
}

export function Button({
  children,
  variant = "primary",
  disabled,
  onClick,
  type = "button",
}: {
  children: React.ReactNode;
  variant?: "primary" | "secondary" | "danger" | "ghost";
  disabled?: boolean;
  onClick?: () => void;
  type?: "button" | "submit";
}) {
  const styles = {
    primary: "bg-slate-950 text-white hover:bg-slate-800",
    secondary: "border border-slate-200 bg-white text-slate-700 hover:border-slate-300 hover:text-slate-950",
    danger: "border border-red-200 bg-red-50 text-red-700 hover:bg-red-100",
    ghost: "text-slate-600 hover:bg-slate-100 hover:text-slate-950",
  };
  return (
    <button
      type={type}
      disabled={disabled}
      onClick={onClick}
      className={`inline-flex items-center justify-center gap-2 rounded-md px-3.5 py-2 text-sm font-semibold shadow-sm transition disabled:cursor-not-allowed disabled:opacity-50 ${styles[variant]}`}
    >
      {children}
    </button>
  );
}

export function Card({
  children,
  className = "",
}: {
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <section className={`rounded-lg border border-slate-200 bg-white shadow-sm ${className}`}>
      {children}
    </section>
  );
}

export function formatDateTime(value?: string | null) {
  if (!value) return "none";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString([], {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
    second: "2-digit",
  });
}

export function shortId(value?: string | null, size = 8) {
  if (!value) return "none";
  if (value.length <= size * 2 + 3) return value;
  return `${value.slice(0, size)}...${value.slice(-size)}`;
}

export function humanizeEventType(type: string) {
  const labels: Record<string, string> = {
    sandbox_e2e_started: "E2E started",
    sandbox_e2e_completed: "E2E completed",
    sandbox_e2e_failed: "E2E failed",
    sandbox_transaction_create_succeeded: "Transaction created",
    sandbox_webhook_already_detached: "Webhook already detached",
    sandbox_item_webhook_detached: "Webhook detached for create-only",
    sandbox_webhook_already_attached: "Webhook already attached",
    sandbox_item_webhook_attached: "Webhook attached for webhook test",
    sandbox_webhook_fire_succeeded: "Webhook fired",
    plaid_webhook_received: "Webhook received",
    plaid_transactions_sync_started: "Sync started",
    plaid_transactions_sync_completed: "Sync completed",
    plaid_transactions_sync_failed: "Sync failed",
    sandbox_telegram_send_started: "Telegram sending",
    sandbox_telegram_send_succeeded: "Telegram sent",
    sandbox_telegram_send_skipped_duplicate: "Telegram duplicate skipped",
    sandbox_integrity_error: "Integrity error",
  };
  return labels[type] || type.replace(/_/g, " ");
}

export function traceFromName(name?: string | null) {
  return name?.match(/\[trace:(sandbox_[^\]]+)\]/)?.[1] || null;
}
