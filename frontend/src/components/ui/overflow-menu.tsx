import * as DropdownMenu from "@radix-ui/react-dropdown-menu";
import { MoreHorizontal, type LucideIcon } from "lucide-react";

import { cn } from "@/lib/utils";

export type OverflowMenuItem = {
  label: string;
  icon?: LucideIcon;
  destructive?: boolean;
  disabled?: boolean;
  onSelect: () => void;
};

export function OverflowMenu({
  label = "More actions",
  items,
  align = "end",
}: {
  label?: string;
  items: OverflowMenuItem[];
  align?: "start" | "center" | "end";
}) {
  return (
    <DropdownMenu.Root>
      <DropdownMenu.Trigger
        aria-label={label}
        className="flex h-11 min-h-11 w-11 min-w-11 items-center justify-center rounded-control text-slate-600 hover:bg-slate-100 hover:text-ink"
      >
        <MoreHorizontal className="h-5 w-5" aria-hidden="true" />
      </DropdownMenu.Trigger>
      <DropdownMenu.Portal>
        <DropdownMenu.Content
          align={align}
          sideOffset={6}
          className="z-50 min-w-48 rounded-control border border-ui-border bg-white p-1.5 shadow-primary"
        >
          {items.map((item) => {
            const Icon = item.icon;
            return (
              <DropdownMenu.Item
                key={item.label}
                disabled={item.disabled}
                onSelect={item.onSelect}
                className={cn(
                  "flex min-h-10 cursor-default select-none items-center gap-2 rounded-md px-3 py-2 text-sm outline-none data-[disabled]:pointer-events-none data-[disabled]:opacity-50",
                  item.destructive
                    ? "text-ui-error focus:bg-rose-50"
                    : "text-slate-700 focus:bg-slate-100 focus:text-ink",
                )}
              >
                {Icon ? <Icon className="h-4 w-4" aria-hidden="true" /> : null}
                {item.label}
              </DropdownMenu.Item>
            );
          })}
        </DropdownMenu.Content>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  );
}
