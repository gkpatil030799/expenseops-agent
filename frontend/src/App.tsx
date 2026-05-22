import { useEffect, useMemo, useState, type ComponentType } from "react";
import {
  Activity,
  BadgeDollarSign,
  CheckCircle2,
  Clock3,
  Link2,
  RefreshCw,
  Search,
  Split,
  UserCheck,
  X,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { api } from "@/lib/api";
import type { Friend, Transaction } from "@/types";

type PlaidWindow = Window & {
  Plaid?: {
    create: (options: {
      token: string;
      onSuccess: (
        publicToken: string,
        metadata: { institution?: { name?: string } },
      ) => void;
      onExit: (err: unknown, metadata: unknown) => void;
    }) => { open: () => void };
  };
};

type LinkTokenResponse = { link_token: string };
type ExchangeResponse = { item_id: string; plaid_item_db_id: number };
type SplitResponse = { splitwise_expense_id: string | null; splitwise_response: unknown };

function App() {
  const [transactions, setTransactions] = useState<Transaction[]>([]);
  const [selectedFriendsByTx, setSelectedFriendsByTx] = useState<Record<number, Friend[]>>({});
  const [friendResultsByTx, setFriendResultsByTx] = useState<Record<number, Friend[]>>({});
  const [friendQueriesByTx, setFriendQueriesByTx] = useState<Record<number, string>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [log, setLog] = useState<unknown>({ status: "Ready" });

  const pendingTotal = useMemo(
    () => transactions.reduce((total, tx) => total + Math.abs(tx.amount_cents), 0) / 100,
    [transactions],
  );

  useEffect(() => {
    void loadTransactions();
  }, []);

  async function run<T>(label: string, action: () => Promise<T>, reload = false) {
    setBusy(label);
    try {
      const data = await action();
      setLog(data);
      if (reload) await loadTransactions();
    } catch (error) {
      setLog(error);
    } finally {
      setBusy(null);
    }
  }

  async function loadTransactions() {
    await run(
      "transactions",
      async () => {
        const data = await api<Transaction[]>("/transactions?status=ask_user");
        setTransactions(data);
        return { loaded_transactions: data.length };
      },
      false,
    );
  }

  async function openPlaidLink() {
    await run("plaid", async () => {
      const plaid = (window as PlaidWindow).Plaid;
      if (!plaid) throw { detail: "Plaid Link script is not loaded yet." };
      const tokenData = await api<LinkTokenResponse>("/plaid/link-token", {
        method: "POST",
        body: "{}",
      });
      plaid
        .create({
          token: tokenData.link_token,
          onSuccess: async (publicToken, metadata) => {
            await run(
              "exchange",
              async () => {
                const data = await api<ExchangeResponse>("/plaid/exchange-public-token", {
                  method: "POST",
                  body: JSON.stringify({
                    public_token: publicToken,
                    institution_name: metadata.institution?.name,
                  }),
                });
                await syncTransactions();
                return data;
              },
              false,
            );
          },
          onExit: (err, metadata) => setLog({ err, metadata }),
        })
        .open();
      return { status: "Plaid Link opened" };
    });
  }

  async function syncTransactions() {
    await run(
      "sync",
      () => api<Record<string, unknown>>("/plaid/sync", { method: "POST", body: "{}" }),
      true,
    );
  }

  async function markPersonal(id: number) {
    await run(
      `personal-${id}`,
      () => api(`/transactions/${id}/personal`, { method: "POST", body: "{}" }),
      true,
    );
  }

  async function submitSplit(id: number, confirm: boolean) {
    const friends = selectedFriendsByTx[id] || [];
    await run(
      `${confirm ? "split" : "draft"}-${id}`,
      () =>
        api<SplitResponse>(`/transactions/${id}/split/equal`, {
          method: "POST",
          body: JSON.stringify({
            friend_user_ids: friends.map((friend) => friend.id),
            confirm,
          }),
        }),
      true,
    );
  }

  async function searchFriends(txId: number) {
    const query = friendQueriesByTx[txId] || "";
    await run(`friends-${txId}`, async () => {
      const friends = await api<Friend[]>(`/splitwise/friends?q=${encodeURIComponent(query)}`);
      setFriendResultsByTx((current) => ({ ...current, [txId]: friends.slice(0, 8) }));
      return { friend_results: friends.slice(0, 8) };
    });
  }

  function selectFriend(txId: number, friend: Friend) {
    setSelectedFriendsByTx((current) => {
      const existing = current[txId] || [];
      if (existing.some((item) => item.id === friend.id)) return current;
      return { ...current, [txId]: [...existing, friend] };
    });
  }

  function removeFriend(txId: number, friendId: number) {
    setSelectedFriendsByTx((current) => ({
      ...current,
      [txId]: (current[txId] || []).filter((friend) => friend.id !== friendId),
    }));
  }

  return (
    <main className="min-h-screen bg-slate-50">
      <section className="mx-auto flex w-full max-w-7xl flex-col gap-6 px-5 py-6 lg:px-8">
        <Header onPlaid={openPlaidLink} onSync={syncTransactions} busy={busy} />

        <div className="grid gap-4 md:grid-cols-3">
          <MetricCard icon={Clock3} label="Pending reviews" value={String(transactions.length)} />
          <MetricCard
            icon={BadgeDollarSign}
            label="Pending amount"
            value={`$${pendingTotal.toFixed(2)}`}
          />
          <MetricCard icon={Activity} label="Workflow" value={busy ? "Working" : "Ready"} />
        </div>

        <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_360px]">
          <section className="space-y-4">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <h2 className="text-xl font-semibold text-slate-950">Pending transactions</h2>
                <p className="text-sm text-slate-500">
                  Search Splitwise friends by name, select them, then approve the split.
                </p>
              </div>
              <Button variant="outline" onClick={loadTransactions} disabled={busy !== null}>
                <RefreshCw className="h-4 w-4" />
                Refresh
              </Button>
            </div>

            {transactions.length ? (
              <div className="grid gap-4">
                {transactions.map((transaction) => (
                  <TransactionCard
                    key={transaction.id}
                    transaction={transaction}
                    busy={busy}
                    query={friendQueriesByTx[transaction.id] || ""}
                    friendResults={friendResultsByTx[transaction.id] || []}
                    selectedFriends={selectedFriendsByTx[transaction.id] || []}
                    onQueryChange={(value) =>
                      setFriendQueriesByTx((current) => ({ ...current, [transaction.id]: value }))
                    }
                    onSearch={() => searchFriends(transaction.id)}
                    onSelectFriend={(friend) => selectFriend(transaction.id, friend)}
                    onRemoveFriend={(friendId) => removeFriend(transaction.id, friendId)}
                    onPersonal={() => markPersonal(transaction.id)}
                    onDraft={() => submitSplit(transaction.id, false)}
                    onSplit={() => submitSplit(transaction.id, true)}
                  />
                ))}
              </div>
            ) : (
              <Card>
                <CardContent className="flex min-h-40 items-center justify-center text-sm text-slate-500">
                  No transactions waiting for review.
                </CardContent>
              </Card>
            )}
          </section>

          <ActivityLog log={log} />
        </div>
      </section>
    </main>
  );
}

function Header({
  onPlaid,
  onSync,
  busy,
}: {
  onPlaid: () => void;
  onSync: () => void;
  busy: string | null;
}) {
  return (
    <div className="flex flex-col gap-5 rounded-lg border border-slate-200 bg-white px-5 py-5 shadow-sm lg:flex-row lg:items-center lg:justify-between">
      <div>
        <div className="flex items-center gap-2 text-sm font-medium text-teal-700">
          <Split className="h-4 w-4" />
          ExpenseOps Agent
        </div>
        <h1 className="mt-2 text-3xl font-semibold tracking-normal text-slate-950">
          Shared expense command center
        </h1>
        <p className="mt-2 max-w-2xl text-sm text-slate-500">
          Link card transactions, review pending expenses, and post approved splits to Splitwise.
        </p>
      </div>
      <div className="flex flex-wrap gap-3">
        <Button onClick={onPlaid} disabled={busy !== null}>
          <Link2 className="h-4 w-4" />
          Connect Plaid
        </Button>
        <Button variant="secondary" onClick={onSync} disabled={busy !== null}>
          <RefreshCw className="h-4 w-4" />
          Manual sync
        </Button>
      </div>
    </div>
  );
}

function MetricCard({
  icon: Icon,
  label,
  value,
}: {
  icon: ComponentType<{ className?: string }>;
  label: string;
  value: string;
}) {
  return (
    <Card>
      <CardContent className="flex items-center justify-between p-5">
        <div>
          <p className="text-sm text-slate-500">{label}</p>
          <p className="mt-1 text-2xl font-semibold text-slate-950">{value}</p>
        </div>
        <div className="rounded-md bg-teal-50 p-3 text-teal-700">
          <Icon className="h-5 w-5" />
        </div>
      </CardContent>
    </Card>
  );
}

function TransactionCard({
  transaction,
  busy,
  query,
  friendResults,
  selectedFriends,
  onQueryChange,
  onSearch,
  onSelectFriend,
  onRemoveFriend,
  onPersonal,
  onDraft,
  onSplit,
}: {
  transaction: Transaction;
  busy: string | null;
  query: string;
  friendResults: Friend[];
  selectedFriends: Friend[];
  onQueryChange: (value: string) => void;
  onSearch: () => void;
  onSelectFriend: (friend: Friend) => void;
  onRemoveFriend: (friendId: number) => void;
  onPersonal: () => void;
  onDraft: () => void;
  onSplit: () => void;
}) {
  const title = transaction.merchant_name || transaction.name;
  const disabled = busy !== null;

  return (
    <Card>
      <CardHeader className="gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <CardTitle>{title}</CardTitle>
          <CardDescription>
            {transaction.iso_currency_code} {transaction.amount}
            {transaction.date ? ` · ${transaction.date}` : ""}
          </CardDescription>
        </div>
        <div className="flex flex-wrap gap-2">
          {transaction.pending ? <Badge variant="secondary">Pending</Badge> : <Badge>Settled</Badge>}
          <Badge variant="outline">{transaction.status}</Badge>
          <ClassificationBadge transaction={transaction} />
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        {transaction.agent_question ? (
          <p className="rounded-md bg-slate-50 px-3 py-2 text-sm text-slate-600">
            {transaction.agent_question}
          </p>
        ) : null}

        <FriendPicker
          query={query}
          results={friendResults}
          selectedFriends={selectedFriends}
          disabled={disabled}
          onQueryChange={onQueryChange}
          onSearch={onSearch}
          onSelectFriend={onSelectFriend}
          onRemoveFriend={onRemoveFriend}
        />

        <div className="flex flex-wrap gap-2">
          <Button variant="outline" onClick={onPersonal} disabled={disabled}>
            <UserCheck className="h-4 w-4" />
            Personal
          </Button>
          <Button variant="secondary" onClick={onDraft} disabled={disabled}>
            <Clock3 className="h-4 w-4" />
            Create draft only
          </Button>
          <Button onClick={onSplit} disabled={disabled || selectedFriends.length === 0}>
            <CheckCircle2 className="h-4 w-4" />
            Split equally
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

function ClassificationBadge({ transaction }: { transaction: Transaction }) {
  const suggestion = transaction.classification_suggestion || "unsure";
  const labels = {
    likely_personal: "Likely personal",
    likely_shared: "Likely shared",
    unsure: "Unsure",
  };
  const variants = {
    likely_personal: "secondary",
    likely_shared: "default",
    unsure: "outline",
  } as const;

  return (
    <Badge variant={variants[suggestion]} title={transaction.classification_reason || undefined}>
      {labels[suggestion]}
    </Badge>
  );
}

function FriendPicker({
  query,
  results,
  selectedFriends,
  disabled,
  onQueryChange,
  onSearch,
  onSelectFriend,
  onRemoveFriend,
}: {
  query: string;
  results: Friend[];
  selectedFriends: Friend[];
  disabled: boolean;
  onQueryChange: (value: string) => void;
  onSearch: () => void;
  onSelectFriend: (friend: Friend) => void;
  onRemoveFriend: (friendId: number) => void;
}) {
  return (
    <div className="space-y-3">
      <div className="flex flex-col gap-2 sm:flex-row">
        <Input
          value={query}
          onChange={(event) => onQueryChange(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") onSearch();
          }}
          placeholder="Search Splitwise friend"
          disabled={disabled}
        />
        <Button variant="outline" onClick={onSearch} disabled={disabled}>
          <Search className="h-4 w-4" />
          Search
        </Button>
      </div>

      {selectedFriends.length ? (
        <div className="flex flex-wrap gap-2">
          {selectedFriends.map((friend) => (
            <button
              key={friend.id}
              className="inline-flex items-center gap-2 rounded-md bg-teal-50 px-3 py-1.5 text-sm font-medium text-teal-800"
              onClick={() => onRemoveFriend(friend.id)}
              type="button"
            >
              {friend.display_name}
              <X className="h-3.5 w-3.5" />
            </button>
          ))}
        </div>
      ) : (
        <p className="text-sm text-slate-500">No friends selected.</p>
      )}

      {results.length ? (
        <div className="grid gap-2 sm:grid-cols-2">
          {results.map((friend) => (
            <button
              key={friend.id}
              type="button"
              className="rounded-md border border-slate-200 bg-white px-3 py-2 text-left text-sm transition hover:bg-slate-50"
              onClick={() => onSelectFriend(friend)}
            >
              <span className="block font-medium text-slate-900">{friend.display_name}</span>
              <span className="block truncate text-xs text-slate-500">
                {friend.email || `Splitwise ID ${friend.id}`}
              </span>
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}

function ActivityLog({ log }: { log: unknown }) {
  return (
    <aside className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle>Activity log</CardTitle>
          <CardDescription>Latest API response or error</CardDescription>
        </CardHeader>
        <CardContent>
          <pre className="max-h-[520px] overflow-auto rounded-md bg-slate-950 p-4 text-xs leading-5 text-slate-100">
            {JSON.stringify(log, null, 2)}
          </pre>
        </CardContent>
      </Card>
    </aside>
  );
}

export default App;
