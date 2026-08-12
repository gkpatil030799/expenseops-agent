import { AlertCircle, CheckCircle2, Info, TriangleAlert } from "lucide-react";
import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

const styles = {
  info: {
    icon: Info,
    className: "border-indigo-200 bg-ui-primary-tint text-indigo-950",
  },
  success: {
    icon: CheckCircle2,
    className: "border-emerald-200 bg-emerald-50 text-emerald-950",
  },
  warning: {
    icon: TriangleAlert,
    className: "border-amber-200 bg-amber-50 text-amber-950",
  },
  error: {
    icon: AlertCircle,
    className: "border-rose-200 bg-rose-50 text-rose-950",
  },
} as const;

export function StatusMessage({
  tone = "info",
  title,
  children,
  actions,
  className,
}: {
  tone?: keyof typeof styles;
  title: string;
  children?: ReactNode;
  actions?: ReactNode;
  className?: string;
}) {
  const style = styles[tone];
  const Icon = style.icon;
  return (
    <div
      role={tone === "error" ? "alert" : "status"}
      className={cn(
        "flex flex-col gap-3 rounded-card border px-4 py-3 sm:flex-row sm:items-start sm:justify-between",
        style.className,
        className,
      )}
    >
      <div className="flex min-w-0 items-start gap-3">
        <Icon className="mt-0.5 h-5 w-5 shrink-0" aria-hidden="true" />
        <div className="min-w-0">
          <p className="text-sm font-semibold">{title}</p>
          {children ? <div className="mt-0.5 text-sm opacity-85">{children}</div> : null}
        </div>
      </div>
      {actions ? <div className="flex shrink-0 items-center gap-2">{actions}</div> : null}
    </div>
  );
}
