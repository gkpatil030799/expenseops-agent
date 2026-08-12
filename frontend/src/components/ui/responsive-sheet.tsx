import * as Dialog from "@radix-ui/react-dialog";
import { X } from "lucide-react";
import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

export function ResponsiveSheet({
  trigger,
  title,
  description,
  children,
  footer,
  side = "right",
}: {
  trigger: ReactNode;
  title: string;
  description?: string;
  children: ReactNode;
  footer?: ReactNode;
  side?: "left" | "right";
}) {
  return (
    <Dialog.Root>
      <Dialog.Trigger asChild>{trigger}</Dialog.Trigger>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-slate-950/45 backdrop-blur-[1px] data-[state=closed]:animate-none" />
        <Dialog.Content
          className={cn(
            "fixed inset-x-0 bottom-0 z-50 max-h-[88dvh] overflow-y-auto rounded-t-card border border-ui-border bg-white p-5 shadow-primary focus:outline-none sm:inset-y-0 sm:w-full sm:max-w-md sm:rounded-none sm:p-6",
            side === "right" ? "sm:left-auto sm:right-0 sm:border-l" : "sm:left-0 sm:right-auto sm:border-r",
          )}
        >
          <div className="pr-10">
            <Dialog.Title className="text-lg font-semibold text-ink">{title}</Dialog.Title>
            {description ? (
              <Dialog.Description className="mt-1 text-sm leading-5 text-ui-text">
                {description}
              </Dialog.Description>
            ) : null}
          </div>
          <Dialog.Close
            aria-label="Close"
            className="absolute right-4 top-4 flex h-11 w-11 items-center justify-center rounded-control text-slate-600 hover:bg-slate-100 sm:h-10 sm:w-10"
          >
            <X className="h-5 w-5" aria-hidden="true" />
          </Dialog.Close>
          <div className="mt-6">{children}</div>
          {footer ? <div className="sticky bottom-0 mt-6 border-t border-ui-border bg-white pt-4">{footer}</div> : null}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
