import { cn } from "@/lib/utils";

function initials(value: string) {
  const words = value.trim().split(/\s+/).filter(Boolean);
  if (!words.length) return "?";
  return words
    .slice(0, 2)
    .map((word) => word[0]?.toUpperCase())
    .join("");
}

export function MerchantAvatar({ name, className }: { name: string; className?: string }) {
  return (
    <span
      aria-hidden="true"
      className={cn(
        "flex h-10 w-10 shrink-0 items-center justify-center rounded-control bg-ui-primary-tint text-xs font-semibold text-indigo-700 ring-1 ring-indigo-100",
        className,
      )}
    >
      {initials(name)}
    </span>
  );
}
