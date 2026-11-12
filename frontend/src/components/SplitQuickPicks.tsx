import { Button } from "@/components/ui/button";
import type { SplitSuggestion } from "@/splitSuggestions";

export function SplitQuickPicks<T extends { id: number }>({
  kind, suggestions, loading, error, disabled, selectedIds, name, onSelect, onRetry,
}: {
  kind: "friends" | "groups";
  suggestions: SplitSuggestion<T>[];
  loading: boolean;
  error: boolean;
  disabled: boolean;
  selectedIds: number[];
  name: (item: T) => string;
  onSelect: (item: T) => void;
  onRetry: () => void;
}) {
  const frequent = suggestions.some((suggestion) => suggestion.count > 0);
  const label = frequent ? `Frequently used ${kind}` : `Your Splitwise ${kind}`;
  return (
    <section aria-label={label} className="space-y-2">
      <p className="text-xs font-semibold text-ui-muted">{label}</p>
      {loading ? <p role="status" className="text-xs text-ui-muted">Loading suggestions…</p> : (
        <div className="flex flex-wrap gap-2">
          {suggestions.filter((suggestion) => !frequent || suggestion.count > 0).slice(0, 6).map(({ item, count }) => (
            <Button
              key={item.id}
              type="button"
              variant="outline"
              className="min-h-11 max-w-full"
              disabled={disabled || selectedIds.includes(item.id)}
              aria-label={`Select ${name(item)}`}
              onClick={() => onSelect(item)}
            >
              <span className="truncate">{name(item)}</span>
              {count > 0 ? <span className="text-xs text-ui-muted">{count} {count === 1 ? "split" : "splits"}</span> : null}
            </Button>
          ))}
          {!suggestions.length && !error ? <p className="text-xs text-ui-muted">No suggestions yet. Search below to choose {kind}.</p> : null}
        </div>
      )}
      {error && !loading ? (
        <p className="text-xs text-ui-muted">
          Some suggestions could not be loaded. You can still search below.{" "}
          <button type="button" className="underline" disabled={disabled} onClick={onRetry}>Retry suggestions</button>
        </p>
      ) : null}
    </section>
  );
}
