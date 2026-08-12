import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

export function FilterToolbar({
  children,
  activeFilters,
  className,
}: {
  children: ReactNode;
  activeFilters?: ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("space-y-2", className)}>
      <div className="flex min-h-12 min-w-0 flex-wrap items-center gap-2 rounded-card border border-ui-border bg-white p-2 shadow-card sm:min-h-16 sm:p-3">
        {children}
      </div>
      {activeFilters ? (
        <div className="flex flex-wrap items-center gap-2" aria-label="Active filters">
          {activeFilters}
        </div>
      ) : null}
    </div>
  );
}
