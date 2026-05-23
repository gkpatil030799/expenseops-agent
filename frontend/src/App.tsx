import { useEffect, useMemo, useState, type ComponentType } from "react";
import {
  Activity,
  BadgeDollarSign,
  CheckCircle2,
  Clock3,
  Link2,
  RefreshCw,
  RotateCcw,
  Search,
  Split,
  UserCheck,
  UsersRound,
  X,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { api } from "@/lib/api";
import type { Friend, Group, SplitwiseUser, Transaction } from "@/types";

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
  const [recentTransactions, setRecentTransactions] = useState<Transaction[]>([]);
  const [selectedFriendsByTx, setSelectedFriendsByTx] = useState<Record<number, Friend[]>>({});
  const [friendResultsByTx, setFriendResultsByTx] = useState<Record<number, Friend[]>>({});
  const [friendQueriesByTx, setFriendQueriesByTx] = useState<Record<number, string>>({});
  const [groupQueriesByTx, setGroupQueriesByTx] = useState<Record<number, string>>({});
  const [groupResultsByTx, setGroupResultsByTx] = useState<Record<number, Group[]>>({});
  const [selectedGroupByTx, setSelectedGroupByTx] = useState<Record<number, Group | null>>({});
  const [groupMembersByTx, setGroupMembersByTx] = useState<Record<number, Friend[]>>({});
  const [selectedGroupMembersByTx, setSelectedGroupMembersByTx] = useState<
    Record<number, Friend[]>
  >({});
  const [currentSplitwiseUser, setCurrentSplitwiseUser] = useState<SplitwiseUser | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [log, setLog] = useState<unknown>({ status: "Ready" });

  const pendingTotal = useMemo(
    () => transactions.reduce((total, tx) => total + Math.abs(tx.amount_cents), 0) / 100,
    [transactions],
  );

  useEffect(() => {
    void loadTransactions();
    void loadRecentActivity();
    void loadCurrentSplitwiseUser();
  }, []);

  async function run<T>(label: string, action: () => Promise<T>, reload = false) {
    setBusy(label);
    try {
      const data = await action();
      setLog(data);
      if (reload) await refreshReviewData();
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

  async function refreshReviewData() {
    await loadTransactions();
    await loadRecentActivity();
  }

  async function loadRecentActivity() {
    await run(
      "recent",
      async () => {
        const statuses = ["personal", "posted", "shared_draft"];
        const groups = await Promise.all(
          statuses.map((status) =>
            api<Transaction[]>(`/transactions?status=${encodeURIComponent(status)}&limit=20`),
          ),
        );
        const merged = groups
          .flat()
          .sort((a, b) => b.updated_at.localeCompare(a.updated_at))
          .slice(0, 12);
        setRecentTransactions(merged);
        return { recent_activity: merged.length };
      },
      false,
    );
  }

  async function loadCurrentSplitwiseUser() {
    try {
      setCurrentSplitwiseUser(await api<SplitwiseUser>("/splitwise/me"));
    } catch {
      setCurrentSplitwiseUser(null);
    }
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

  async function undoTransaction(id: number) {
    await run(
      `undo-${id}`,
      () => api(`/transactions/${id}/undo`, { method: "POST", body: "{}" }),
      true,
    );
  }

  async function submitSplit(id: number, confirm: boolean) {
    const selectedGroup = selectedGroupByTx[id];
    const friends = selectedGroup
      ? selectedNonPayerGroupMembers(id)
      : selectedFriendsByTx[id] || [];
    await run(
      `${confirm ? "split" : "draft"}-${id}`,
      () =>
        api<SplitResponse>(`/transactions/${id}/split/equal`, {
          method: "POST",
          body: JSON.stringify({
            friend_user_ids: friends.map((friend) => friend.id),
            group_id: selectedGroup?.id ?? null,
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

  async function searchGroups(txId: number) {
    const query = groupQueriesByTx[txId] || "";
    await run(`groups-${txId}`, async () => {
      const groups = await api<Group[]>(`/splitwise/groups?q=${encodeURIComponent(query)}`);
      setGroupResultsByTx((current) => ({ ...current, [txId]: groups.slice(0, 8) }));
      return { group_results: groups.slice(0, 8) };
    });
  }

  async function selectGroup(txId: number, group: Group) {
    await run(`group-${txId}`, async () => {
      const members = await api<Friend[]>(`/splitwise/groups/${group.id}/members`);
      setSelectedGroupByTx((current) => ({ ...current, [txId]: group }));
      setGroupMembersByTx((current) => ({ ...current, [txId]: members }));
      setSelectedGroupMembersByTx((current) => ({ ...current, [txId]: [] }));
      return { selected_group: group, members };
    });
  }

  function clearGroup(txId: number) {
    setSelectedGroupByTx((current) => ({ ...current, [txId]: null }));
    setGroupMembersByTx((current) => ({ ...current, [txId]: [] }));
    setSelectedGroupMembersByTx((current) => ({ ...current, [txId]: [] }));
  }

  function selectGroupMember(txId: number, member: Friend) {
    if (member.id === currentSplitwiseUser?.id) return;
    setSelectedGroupMembersByTx((current) => {
      const existing = current[txId] || [];
      if (existing.some((item) => item.id === member.id)) return current;
      return { ...current, [txId]: [...existing, member] };
    });
  }

  function removeGroupMember(txId: number, memberId: number) {
    setSelectedGroupMembersByTx((current) => ({
      ...current,
      [txId]: (current[txId] || []).filter((member) => member.id !== memberId),
    }));
  }

  function selectedNonPayerGroupMembers(txId: number) {
    return (selectedGroupMembersByTx[txId] || []).filter(
      (member) => member.id !== currentSplitwiseUser?.id,
    );
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
                    groupQuery={groupQueriesByTx[transaction.id] || ""}
                    groupResults={groupResultsByTx[transaction.id] || []}
                    selectedGroup={selectedGroupByTx[transaction.id] || null}
                    groupMembers={groupMembersByTx[transaction.id] || []}
                    selectedGroupMembers={selectedNonPayerGroupMembers(transaction.id)}
                    currentUserId={currentSplitwiseUser?.id ?? null}
                    onQueryChange={(value) =>
                      setFriendQueriesByTx((current) => ({ ...current, [transaction.id]: value }))
                    }
                    onGroupQueryChange={(value) =>
                      setGroupQueriesByTx((current) => ({ ...current, [transaction.id]: value }))
                    }
                    onSearch={() => searchFriends(transaction.id)}
                    onSearchGroups={() => searchGroups(transaction.id)}
                    onSelectFriend={(friend) => selectFriend(transaction.id, friend)}
                    onRemoveFriend={(friendId) => removeFriend(transaction.id, friendId)}
                    onSelectGroup={(group) => selectGroup(transaction.id, group)}
                    onClearGroup={() => clearGroup(transaction.id)}
                    onSelectGroupMember={(member) => selectGroupMember(transaction.id, member)}
                    onRemoveGroupMember={(memberId) =>
                      removeGroupMember(transaction.id, memberId)
                    }
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

          <aside className="space-y-4">
            <RecentActivity
              transactions={recentTransactions}
              busy={busy}
              onUndo={undoTransaction}
            />
            <ActivityLog log={log} />
          </aside>
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
  groupQuery,
  groupResults,
  selectedGroup,
  groupMembers,
  selectedGroupMembers,
  currentUserId,
  onQueryChange,
  onGroupQueryChange,
  onSearch,
  onSearchGroups,
  onSelectFriend,
  onRemoveFriend,
  onSelectGroup,
  onClearGroup,
  onSelectGroupMember,
  onRemoveGroupMember,
  onPersonal,
  onDraft,
  onSplit,
}: {
  transaction: Transaction;
  busy: string | null;
  query: string;
  friendResults: Friend[];
  selectedFriends: Friend[];
  groupQuery: string;
  groupResults: Group[];
  selectedGroup: Group | null;
  groupMembers: Friend[];
  selectedGroupMembers: Friend[];
  currentUserId: number | null;
  onQueryChange: (value: string) => void;
  onGroupQueryChange: (value: string) => void;
  onSearch: () => void;
  onSearchGroups: () => void;
  onSelectFriend: (friend: Friend) => void;
  onRemoveFriend: (friendId: number) => void;
  onSelectGroup: (group: Group) => void;
  onClearGroup: () => void;
  onSelectGroupMember: (member: Friend) => void;
  onRemoveGroupMember: (memberId: number) => void;
  onPersonal: () => void;
  onDraft: () => void;
  onSplit: () => void;
}) {
  const title = transaction.merchant_name || transaction.name;
  const disabled = busy !== null;
  const selectedParticipantCount = selectedGroup
    ? selectedGroupMembers.length
    : selectedFriends.length;

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

        <GroupPicker
          query={groupQuery}
          groups={groupResults}
          selectedGroup={selectedGroup}
          members={groupMembers}
          selectedMembers={selectedGroupMembers}
          currentUserId={currentUserId}
          disabled={disabled}
          onQueryChange={onGroupQueryChange}
          onSearch={onSearchGroups}
          onSelectGroup={onSelectGroup}
          onClearGroup={onClearGroup}
          onSelectMember={onSelectGroupMember}
          onRemoveMember={onRemoveGroupMember}
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
          <Button onClick={onSplit} disabled={disabled || selectedParticipantCount === 0}>
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

function GroupPicker({
  query,
  groups,
  selectedGroup,
  members,
  selectedMembers,
  currentUserId,
  disabled,
  onQueryChange,
  onSearch,
  onSelectGroup,
  onClearGroup,
  onSelectMember,
  onRemoveMember,
}: {
  query: string;
  groups: Group[];
  selectedGroup: Group | null;
  members: Friend[];
  selectedMembers: Friend[];
  currentUserId: number | null;
  disabled: boolean;
  onQueryChange: (value: string) => void;
  onSearch: () => void;
  onSelectGroup: (group: Group) => void;
  onClearGroup: () => void;
  onSelectMember: (member: Friend) => void;
  onRemoveMember: (memberId: number) => void;
}) {
  const selectedMemberIds = new Set(selectedMembers.map((member) => member.id));

  return (
    <div className="space-y-3 rounded-md border border-slate-200 bg-slate-50 p-3">
      <div className="flex items-center gap-2 text-sm font-medium text-slate-700">
        <UsersRound className="h-4 w-4" />
        Group split
      </div>

      <div className="flex flex-col gap-2 sm:flex-row">
        <Input
          value={query}
          onChange={(event) => onQueryChange(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") onSearch();
          }}
          placeholder="Search Splitwise group"
          disabled={disabled}
        />
        <Button variant="outline" onClick={onSearch} disabled={disabled}>
          <Search className="h-4 w-4" />
          Search
        </Button>
      </div>

      {selectedGroup ? (
        <div className="flex flex-wrap items-center gap-2">
          <span className="inline-flex items-center gap-2 rounded-md bg-slate-900 px-3 py-1.5 text-sm font-medium text-white">
            {selectedGroup.name}
          </span>
          <Button variant="ghost" size="sm" onClick={onClearGroup} disabled={disabled}>
            <X className="h-4 w-4" />
            Clear group
          </Button>
        </div>
      ) : null}

      {groups.length > 0 && !selectedGroup ? (
        <div className="grid gap-2 sm:grid-cols-2">
          {groups.map((group) => (
            <button
              key={group.id}
              type="button"
              className="rounded-md border border-slate-200 bg-white px-3 py-2 text-left text-sm transition hover:bg-slate-50"
              onClick={() => onSelectGroup(group)}
            >
              <span className="block font-medium text-slate-900">{group.name}</span>
              <span className="block truncate text-xs text-slate-500">
                Splitwise group {group.id}
              </span>
            </button>
          ))}
        </div>
      ) : null}

      {selectedGroup ? (
        <div className="space-y-2">
          {selectedMembers.length ? (
            <div className="flex flex-wrap gap-2">
              {selectedMembers.map((member) => (
                <button
                  key={member.id}
                  className="inline-flex items-center gap-2 rounded-md bg-teal-50 px-3 py-1.5 text-sm font-medium text-teal-800"
                  onClick={() => onRemoveMember(member.id)}
                  type="button"
                >
                  {member.display_name}
                  <X className="h-3.5 w-3.5" />
                </button>
              ))}
            </div>
          ) : (
            <p className="text-sm text-slate-500">No group members selected.</p>
          )}

          {members.length ? (
            <div className="grid gap-2 sm:grid-cols-2">
              {members.map((member) => {
                const isCurrentUser = member.id === currentUserId;
                return (
                  <button
                    key={member.id}
                    type="button"
                    className="rounded-md border border-slate-200 bg-white px-3 py-2 text-left text-sm transition hover:bg-slate-50 disabled:opacity-60"
                    onClick={() => onSelectMember(member)}
                    disabled={isCurrentUser || selectedMemberIds.has(member.id)}
                  >
                    <span className="flex flex-wrap items-center gap-2 font-medium text-slate-900">
                      {member.display_name}
                      {isCurrentUser ? (
                        <span className="rounded bg-slate-100 px-1.5 py-0.5 text-xs text-slate-600">
                          You / payer
                        </span>
                      ) : null}
                    </span>
                    <span className="block truncate text-xs text-slate-500">
                      {member.email || `Splitwise ID ${member.id}`}
                    </span>
                  </button>
                );
              })}
            </div>
          ) : (
            <p className="text-sm text-slate-500">No members found for this group.</p>
          )}
        </div>
      ) : null}
    </div>
  );
}

function RecentActivity({
  transactions,
  busy,
  onUndo,
}: {
  transactions: Transaction[];
  busy: string | null;
  onUndo: (id: number) => void;
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Recent activity</CardTitle>
        <CardDescription>Completed transactions that can be moved back to review</CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {transactions.length ? (
          transactions.map((transaction) => (
            <div
              key={transaction.id}
              className="flex items-center justify-between gap-3 rounded-md border border-slate-200 p-3"
            >
              <div className="min-w-0">
                <p className="truncate text-sm font-medium text-slate-900">
                  {transaction.merchant_name || transaction.name}
                </p>
                <p className="text-xs text-slate-500">
                  {transaction.iso_currency_code} {transaction.amount} · {transaction.status}
                </p>
              </div>
              {transaction.can_undo_transaction ? (
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => onUndo(transaction.id)}
                  disabled={busy !== null}
                >
                  <RotateCcw className="h-4 w-4" />
                  Undo
                </Button>
              ) : null}
            </div>
          ))
        ) : (
          <p className="text-sm text-slate-500">No recent completed transactions.</p>
        )}
      </CardContent>
    </Card>
  );
}

function ActivityLog({ log }: { log: unknown }) {
  return (
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
  );
}

export default App;
