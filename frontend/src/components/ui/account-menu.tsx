import * as DropdownMenu from "@radix-ui/react-dropdown-menu";
import { Check, ChevronDown, LogOut, Settings2, UserRound } from "lucide-react";

import { cn } from "@/lib/utils";

type AccountMenuAction = {
  label: string;
  onSelect: () => void;
  icon?: typeof Settings2;
  destructive?: boolean;
};

type AccountMenuProps = {
  displayName: string;
  workspaceName: string;
  email?: string | null;
  avatarUrl?: string | null;
  actions?: AccountMenuAction[];
  workspaces?: Array<{ id: number; name: string; active?: boolean; onSelect: () => void }>;
  compact?: boolean;
};

function AccountMenu({
  displayName,
  workspaceName,
  email,
  avatarUrl,
  actions = [],
  workspaces = [],
  compact = false,
}: AccountMenuProps) {
  return (
    <DropdownMenu.Root>
      <DropdownMenu.Trigger asChild>
        <button
          type="button"
          className={cn(
            "touch-target inline-flex max-w-full items-center gap-2 rounded-control border border-ui-border bg-white text-left text-sm text-ui-text shadow-card transition-colors duration-hover hover:border-indigo-200 hover:bg-ui-primary-tint",
            compact ? "px-2" : "px-3",
          )}
          aria-label={`Open account menu for ${displayName}`}
        >
          {avatarUrl ? (
            <img className="h-7 w-7 rounded-full object-cover" src={avatarUrl} alt="" referrerPolicy="no-referrer" />
          ) : (
            <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-ui-primary-tint text-ui-primary">
              <UserRound className="h-4 w-4" aria-hidden="true" />
            </span>
          )}
          <span className={cn("min-w-0", compact && "sr-only")}>
            <span className="block truncate font-semibold text-ink">{workspaceName}</span>
            <span className="block truncate text-xs text-ui-muted">{displayName}</span>
          </span>
          <ChevronDown className={cn("h-4 w-4 shrink-0", compact && "hidden")} aria-hidden="true" />
        </button>
      </DropdownMenu.Trigger>
      <DropdownMenu.Portal>
        <DropdownMenu.Content
          align="end"
          sideOffset={8}
          className="z-50 w-72 rounded-card border border-ui-border bg-white p-2 shadow-primary"
        >
          <div className="px-2 py-2">
            <p className="truncate text-sm font-semibold text-ink">{displayName}</p>
            {email ? <p className="truncate text-xs text-ui-muted">{email}</p> : null}
          </div>
          {workspaces.length ? (
            <>
              <DropdownMenu.Separator className="my-1 h-px bg-ui-border" />
              <DropdownMenu.Label className="px-2 py-1 text-xs font-semibold uppercase tracking-wide text-ui-muted">
                Workspaces
              </DropdownMenu.Label>
              {workspaces.map((workspace) => (
                <DropdownMenu.Item
                  key={workspace.id}
                  onSelect={workspace.onSelect}
                  className="flex min-h-11 cursor-pointer items-center justify-between rounded-control px-2 text-sm text-ui-text outline-none hover:bg-slate-50 focus:bg-ui-primary-tint focus:text-ink"
                >
                  <span className="truncate">{workspace.name}</span>
                  {workspace.active ? <Check className="h-4 w-4 text-ui-primary" aria-label="Current workspace" /> : null}
                </DropdownMenu.Item>
              ))}
            </>
          ) : null}
          {actions.length ? <DropdownMenu.Separator className="my-1 h-px bg-ui-border" /> : null}
          {actions.map((action) => {
            const Icon = action.icon ?? (action.destructive ? LogOut : Settings2);
            return (
              <DropdownMenu.Item
                key={action.label}
                onSelect={action.onSelect}
                className={cn(
                  "flex min-h-11 cursor-pointer items-center gap-2 rounded-control px-2 text-sm outline-none focus:bg-ui-primary-tint",
                  action.destructive ? "text-ui-error focus:bg-rose-50" : "text-ui-text focus:text-ink",
                )}
              >
                <Icon className="h-4 w-4" aria-hidden="true" />
                {action.label}
              </DropdownMenu.Item>
            );
          })}
        </DropdownMenu.Content>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  );
}

export { AccountMenu };
export type { AccountMenuAction };
