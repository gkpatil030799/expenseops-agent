import type { DashboardEvent, DashboardFilters, MemoryEntry, Transaction } from "@/types";

export function buildDashboardEvents(transactions: Transaction[]): DashboardEvent[] {
  return transactions
    .flatMap((transaction) => {
      const merchant = transaction.merchant_name || transaction.name;
      const base = {
        transaction_id: transaction.id,
        merchant,
        amount: transaction.amount,
        currency: transaction.iso_currency_code,
        participants: parseParticipants(transaction),
        group_name: parseGroupName(transaction),
        status: transaction.status,
        timestamp: transaction.updated_at || transaction.created_at,
      };
      const events: DashboardEvent[] = [
        {
          ...base,
          id: `${transaction.id}-transaction_detected`,
          type: "transaction_detected",
          timestamp: transaction.created_at,
          details: { plaid_transaction_id: transaction.plaid_transaction_id },
        },
      ];

      if (transaction.classification_suggestion) {
        events.push({
          ...base,
          id: `${transaction.id}-recommendation_generated`,
          type: "recommendation_generated",
          details: {
            suggestion: transaction.classification_suggestion,
            reason: transaction.classification_reason,
          },
        });
      }

      if (transaction.status === "personal") {
        events.push({
          ...base,
          id: `${transaction.id}-split_confirmed-personal`,
          type: "split_confirmed",
          details: { decision: "personal" },
        });
      }

      if (transaction.status === "shared_draft") {
        events.push({
          ...base,
          id: `${transaction.id}-split_confirmed-draft`,
          type: "split_confirmed",
          details: { decision: "draft" },
        });
      }

      if (transaction.status === "posted" || transaction.splitwise_expense_id) {
        events.push({
          ...base,
          id: `${transaction.id}-split_posted`,
          type: "split_posted",
          details: { splitwise_expense_id: transaction.splitwise_expense_id },
        });
      }

      return events;
    })
    .sort((a, b) => b.timestamp.localeCompare(a.timestamp));
}

export function filterTransactions(
  transactions: Transaction[],
  filters: DashboardFilters,
): Transaction[] {
  return transactions.filter((transaction) => {
    const merchant = (transaction.merchant_name || transaction.name).toLowerCase();
    const groupName = parseGroupName(transaction)?.toLowerCase() || "";
    const date = transaction.date || transaction.created_at.slice(0, 10);

    if (filters.merchant && !merchant.includes(filters.merchant.toLowerCase())) return false;
    if (filters.group && !groupName.includes(filters.group.toLowerCase())) return false;
    if (filters.status && transaction.status !== filters.status) return false;
    if (filters.dateFrom && date < filters.dateFrom) return false;
    if (filters.dateTo && date > filters.dateTo) return false;
    return true;
  });
}

export function filterEvents(events: DashboardEvent[], filters: DashboardFilters): DashboardEvent[] {
  return events.filter((event) => {
    const date = event.timestamp.slice(0, 10);
    if (
      filters.merchant &&
      !event.merchant.toLowerCase().includes(filters.merchant.toLowerCase())
    ) {
      return false;
    }
    if (
      filters.group &&
      !(event.group_name || "").toLowerCase().includes(filters.group.toLowerCase())
    ) {
      return false;
    }
    if (filters.status && event.status !== filters.status) return false;
    if (filters.dateFrom && date < filters.dateFrom) return false;
    if (filters.dateTo && date > filters.dateTo) return false;
    return true;
  });
}

export function analyticsForTransactions(transactions: Transaction[], days: number) {
  const cutoff = new Date();
  cutoff.setDate(cutoff.getDate() - days);
  const recent = transactions.filter((transaction) => {
    const date = new Date(transaction.date || transaction.created_at);
    return Number.isNaN(date.getTime()) || date >= cutoff;
  });
  const shared = recent.filter((transaction) =>
    ["posted", "shared_draft"].includes(transaction.status),
  );
  const personal = recent.filter((transaction) => transaction.status === "personal");
  const totalSharedSpend = shared.reduce(
    (total, transaction) => total + Math.abs(transaction.amount_cents),
    0,
  );

  return {
    totalSharedSpend,
    personalCount: personal.length,
    sharedCount: shared.length,
    topMerchants: countBy(recent, (transaction) => transaction.merchant_name || transaction.name),
    topPartners: countNames(shared.flatMap(parseParticipants)),
    topGroups: countNames(shared.map(parseGroupName).filter(Boolean) as string[]),
  };
}

export function memoryForTransactions(transactions: Transaction[]) {
  const splitTransactions = transactions.filter((transaction) =>
    ["posted", "shared_draft"].includes(transaction.status),
  );
  return {
    friends: countNames(splitTransactions.flatMap(parseParticipants)),
    groups: countNames(splitTransactions.map(parseGroupName).filter(Boolean) as string[]),
  };
}

export function parseParticipants(transaction: Transaction): string[] {
  const payload = parsePayload(transaction);
  const users = Array.isArray(payload?.users) ? payload.users : [];
  return users
    .map((user) => {
      if (!user || typeof user !== "object") return "";
      const first = "first_name" in user ? String(user.first_name || "") : "";
      const last = "last_name" in user ? String(user.last_name || "") : "";
      const id = "user_id" in user ? String(user.user_id || "") : "";
      return `${first} ${last}`.trim() || (id ? `Splitwise user ${id}` : "");
    })
    .filter(Boolean);
}

export function parseGroupName(transaction: Transaction): string | null {
  const payload = parsePayload(transaction);
  if (!payload || typeof payload.group_id === "undefined" || payload.group_id === null) {
    return null;
  }
  return `Group ${payload.group_id}`;
}

function parsePayload(transaction: Transaction): Record<string, unknown> | null {
  if (!transaction.splitwise_payload_json) return null;
  try {
    const parsed = JSON.parse(transaction.splitwise_payload_json);
    return parsed && typeof parsed === "object" ? parsed : null;
  } catch {
    return null;
  }
}

function countBy(items: Transaction[], keyFn: (item: Transaction) => string): MemoryEntry[] {
  return countNames(items.map(keyFn));
}

function countNames(names: string[]): MemoryEntry[] {
  const counts = new Map<string, number>();
  names.forEach((name) => {
    if (!name) return;
    counts.set(name, (counts.get(name) || 0) + 1);
  });
  return [...counts.entries()]
    .map(([name, count]) => ({ id: name, name, count }))
    .sort((a, b) => b.count - a.count || a.name.localeCompare(b.name))
    .slice(0, 5);
}
