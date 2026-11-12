import { useEffect, useMemo, useState } from "react";

import { api } from "@/lib/api";
import { splitSuggestionsForTransactions } from "@/splitSuggestions";
import type { Friend, Group, Transaction } from "@/types";

export function useSplitSuggestions(
  enabled: boolean,
  transactions: Transaction[],
  currentUserId: number | null,
) {
  const [friends, setFriends] = useState<Friend[]>([]);
  const [groups, setGroups] = useState<Group[]>([]);
  const [history, setHistory] = useState<Transaction[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    if (!enabled) return;
    const controller = new AbortController();
    const options = { signal: controller.signal };
    setLoading(true);
    setError(false);
    void Promise.allSettled([
      api<Friend[]>("/splitwise/friends", options),
      api<Group[]>("/splitwise/groups", options),
      api<Transaction[]>("/transactions?status=posted&limit=200", options),
    ]).then(([friendResult, groupResult, historyResult]) => {
      if (controller.signal.aborted) return;
      if (friendResult.status === "fulfilled") setFriends(friendResult.value);
      if (groupResult.status === "fulfilled") setGroups(groupResult.value);
      if (historyResult.status === "fulfilled") setHistory(historyResult.value);
      setError([friendResult, groupResult, historyResult].some((result) => result.status === "rejected"));
      setLoading(false);
    });
    return () => controller.abort();
  }, [enabled, attempt]);

  const suggestions = useMemo(() => splitSuggestionsForTransactions(
    [...history, ...transactions], friends, groups, currentUserId,
  ), [history, transactions, friends, groups, currentUserId]);

  return { ...suggestions, loading, error, retry: () => setAttempt((value) => value + 1) };
}

export type SplitSuggestions = ReturnType<typeof useSplitSuggestions>;
