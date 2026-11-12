import { describe, expect, it } from "vitest";
import { splitSuggestionsForTransactions } from "@/splitSuggestions";
import type { Friend, Transaction } from "@/types";

const friends: Friend[] = [1, 2, 3, 4].map((id) => ({
  id, display_name: id === 1 ? "You" : id === 4 ? "Alex" : "Sam",
  first_name: null, last_name: null, email: null,
}));
const groups = [{ id: 44, name: "Housemates" }, { id: 55, name: "Travel" }];
const tx = (id: number, payload: unknown, overrides: Partial<Transaction> = {}) => ({
  id, status: "posted", updated_at: `2026-08-${10 + id}T00:00:00Z`,
  splitwise_payload_json: JSON.stringify(payload), ...overrides,
} as Transaction);

describe("split suggestions", () => {
  it("ranks real flattened payloads by frequency then recency and resolves current names by ID", () => {
    const history = [
      tx(1, { group_id: "44", users__0__user_id: 1, users__1__user_id: "2" }),
      tx(2, { group_id: 44, users__0__user_id: 1, users__1__user_id: 2 }),
      tx(3, { group_id: 55, users__0__user_id: 1, users__1__user_id: 3 }),
      tx(4, { users__0__user_id: 4 }),
    ];
    const result = splitSuggestionsForTransactions(history, friends, groups, 1);
    expect(result.friends.map(({ item, count }) => [item.id, count])).toEqual([[2, 2], [4, 1], [3, 1]]);
    expect(result.groups.map(({ item, count }) => [item.name, count])).toEqual([["Housemates", 2], ["Travel", 1]]);
  });

  it("deduplicates transactions and participants, supports legacy arrays, and ignores failed/draft splits", () => {
    const posted = tx(1, { group_id: 44, users: [{ user_id: 2 }, { user_id: "2" }, null] });
    const result = splitSuggestionsForTransactions([
      posted, posted,
      tx(2, { users__0__user_id: 3 }, { status: "shared_draft" }),
      tx(3, { users__0__user_id: 3 }, { status: "error" }),
      tx(4, {}, { splitwise_payload_json: "broken json" }),
      tx(5, null),
      tx(6, { group_id: 0, users__0__user_id: "bad", users__1__user_id: 999 }),
    ], friends, groups, 1);
    expect(result.friends.map(({ item, count }) => [item.id, count])).toEqual([[2, 1], [4, 0], [3, 0]]);
    expect(result.groups[0].count).toBe(1);
  });

  it("uses the latest transaction state after undo and offers contacts for new users", () => {
    const result = splitSuggestionsForTransactions([
      tx(1, { users__0__user_id: 2 }),
      tx(1, null, { status: "ask_user" }),
    ], friends, [...groups, { id: 0, name: "Non-group expenses" }], 1);
    expect(result.friends.every(({ count }) => count === 0)).toBe(true);
    expect(result.friends[0].item.display_name).toBe("Alex");
    expect(result.groups).toHaveLength(2);
  });
});
