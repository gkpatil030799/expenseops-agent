import type { ReactNode } from "react";

import { cn } from "@/lib/utils";
import { Surface } from "@/components/ui/surface";

type PageHeaderProps = {
  title: string;
  description: ReactNode;
  eyebrow?: ReactNode;
  actions?: ReactNode;
  compact?: boolean;
  className?: string;
};

export function PageHeader({
  title,
  description,
  eyebrow,
  actions,
  compact = false,
  className,
}: PageHeaderProps) {
  return (
    <Surface
      variant="command"
      className={cn(
        "flex min-h-36 flex-col justify-between gap-5 px-5 py-5 sm:min-h-28 sm:flex-row sm:items-end sm:px-6",
        compact ? "sm:min-h-24" : "sm:min-h-32",
        className,
      )}
    >
      <div className="min-w-0 max-w-3xl">
        {eyebrow ? (
          <div className="mb-3 text-xs font-semibold uppercase tracking-[0.18em] text-indigo-200">
            {eyebrow}
          </div>
        ) : null}
        <h1 className="text-display-mobile tracking-tight text-white sm:text-display">{title}</h1>
        <p className="mt-2 max-w-2xl text-sm leading-6 text-slate-300">{description}</p>
      </div>
      {actions ? <div className="flex shrink-0 flex-wrap items-center gap-2">{actions}</div> : null}
    </Surface>
  );
}
