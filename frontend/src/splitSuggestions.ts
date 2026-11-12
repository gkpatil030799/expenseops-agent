import type { Friend, Group, Transaction } from "@/types";

export type SplitSuggestion<T> = { item: T; count: number; lastUsed: string };

// Stored expense requests use flattened Splitwise form keys. Older records
// can contain a users array instead. Match identities by ID, never by name.
export function splitSuggestionsForTransactions(
  transactions: Transaction[],
  friends: Friend[],
  groups: Group[],
  currentUserId: number | null,
) {
  const friendUsage = new Map<number, { count: number; lastUsed: string }>();
  const groupUsage = new Map<number, { count: number; lastUsed: string }>();
  const record = (usage: typeof friendUsage, id: number, date: string) => {
    if (!Number.isSafeInteger(id) || id <= 0) return;
    const previous = usage.get(id);
    usage.set(id, {
      count: (previous?.count || 0) + 1,
      lastUsed: previous && previous.lastUsed > date ? previous.lastUsed : date,
    });
  };
  for (const tx of new Map(transactions.map((tx) => [tx.id, tx])).values()) {
    if (tx.status !== "posted" || !tx.splitwise_payload_json) continue;
    try {
      const payload = JSON.parse(tx.splitwise_payload_json);
      if (!payload || typeof payload !== "object" || Array.isArray(payload)) continue;
      record(groupUsage, Number(payload.group_id), tx.updated_at);
      const ids = new Set<number>();
      for (const [key, value] of Object.entries(payload)) {
        if (/^users__\d+__user_id$/.test(key)) ids.add(Number(value));
      }
      if (Array.isArray(payload.users)) {
        for (const user of payload.users) {
          if (user && typeof user === "object") ids.add(Number(user.user_id));
        }
      }
      ids.forEach((id) => record(friendUsage, id, tx.updated_at));
    } catch {
      // A malformed historical payload must not prevent choosing participants.
    }
  }
  function rank<T extends { id: number }>(items: T[], usage: typeof friendUsage, name: (item: T) => string) {
    return [...new Map(items.map((item) => [item.id, item])).values()]
      .map((item): SplitSuggestion<T> => ({ item, count: 0, lastUsed: "", ...usage.get(item.id) }))
      .sort((a, b) => b.count - a.count || b.lastUsed.localeCompare(a.lastUsed)
        || name(a.item).localeCompare(name(b.item)));
  }
  return {
    friends: rank(friends.filter((friend) => friend.id !== currentUserId), friendUsage, (friend) => friend.display_name),
    groups: rank(groups.filter((group) => group.id > 0), groupUsage, (group) => group.name),
  };
}
