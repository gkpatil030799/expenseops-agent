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
    <main className="min-h-screen bg-[radial-gradient(circle_at_top,rgba(79,70,229,0.06),transparent_30rem)]">
      <div className={cn("page-frame flex min-w-0 flex-col gap-5 py-3 sm:py-5", className)}>
        {navigation}
        <div className="min-w-0 space-y-5">{children}</div>
      </div>
      {mobileNavigation}
    </main>
  );
}

export { AppShell };
