import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

type AppShellProps = {
  navigation: ReactNode;
  children: ReactNode;
  mobileNavigation?: ReactNode;
  className?: string;
};

function AppShell({ navigation, children, mobileNavigation, className }: AppShellProps) {
  return (
    <div className="min-h-screen bg-[radial-gradient(circle_at_top,rgba(79,70,229,0.06),transparent_30rem)]">
      <a
        href="#main-content"
        className="fixed left-4 top-3 z-[100] -translate-y-20 rounded-control bg-slate-950 px-4 py-3 text-sm font-semibold text-white shadow-primary transition-transform focus:translate-y-0"
      >
        Skip to main content
      </a>
      <div className={cn("page-frame flex min-w-0 flex-col gap-5 py-3 sm:py-5", className)}>
        {navigation}
        <main id="main-content" tabIndex={-1} className="min-w-0 space-y-5">{children}</main>
      </div>
      {mobileNavigation}
    </div>
  );
}

export { AppShell };
