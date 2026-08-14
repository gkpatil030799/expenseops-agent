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
      padding="none"
      className={cn(
        "flex min-h-0 flex-col justify-between px-4 py-4 sm:min-h-28 sm:flex-row sm:items-end sm:gap-5 sm:px-6 sm:py-5",
        compact ? "sm:min-h-24" : "sm:min-h-32",
        className,
      )}
      data-ui="page-header"
    >
      <div className="min-w-0 w-full max-w-3xl">
        {eyebrow || actions ? (
          <div className={cn("mb-2 flex items-center justify-between gap-3 sm:mb-0 sm:block sm:min-h-0", actions && "min-h-11")}>
            {eyebrow ? (
              <div className="text-xs font-semibold uppercase tracking-[0.18em] text-indigo-200 sm:mb-3">
                {eyebrow}
              </div>
            ) : <span />}
          {actions ? <div className="flex shrink-0 items-center gap-2 sm:hidden">{actions}</div> : null}
          </div>
        ) : null}
        <h1 className="min-w-0 text-display-mobile tracking-tight text-white sm:text-display">{title}</h1>
        <p className="mt-1.5 max-w-2xl text-sm leading-5 text-slate-300 sm:mt-2 sm:leading-6">{description}</p>
      </div>
      {actions ? <div className="hidden shrink-0 flex-wrap items-center gap-2 sm:flex sm:justify-end">{actions}</div> : null}
    </Surface>
  );
}
