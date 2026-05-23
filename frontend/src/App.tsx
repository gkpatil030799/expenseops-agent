import { useEffect, useMemo, useState, type ComponentType } from "react";
import {
  Activity,
  AlertCircle,
  BadgeDollarSign,
  BarChart3,
  Bot,
  CalendarDays,
  CheckCircle2,
  ChevronDown,
  Clock3,
  Layers3,
  Link2,
  MessageCircle,
  PieChart,
  RefreshCw,
  RotateCcw,
  Search,
  Split,
  Sparkles,
  UserCheck,
  UsersRound,
  WalletCards,
  X,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import {
  analyticsForTransactions,
  buildDashboardEvents,
  filterEvents,
  filterTransactions,
  memoryForTransactions,
} from "@/dashboardLogic";
import { api } from "@/lib/api";
import type {
  DashboardEvent,
  DashboardFilters,
  CustomSplitMode,
  Friend,
  Group,
  MemoryEntry,
  SplitwiseUser,
  Transaction,
} from "@/types";

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
  const [allTransactions, setAllTransactions] = useState<Transaction[]>([]);
  const [filters, setFilters] = useState<DashboardFilters>({
    merchant: "",
    group: "",
    status: "",
    dateFrom: "",
    dateTo: "",
  });
  const [analyticsDays, setAnalyticsDays] = useState(30);
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
  const [customModeByTx, setCustomModeByTx] = useState<Record<number, CustomSplitMode>>({});
  const [payerIncludedByTx, setPayerIncludedByTx] = useState<Record<number, boolean>>({});
  const [customValuesByTx, setCustomValuesByTx] = useState<Record<number, Record<number, string>>>(
    {},
  );
  const [expandedTransactions, setExpandedTransactions] = useState<Record<number, boolean>>({});
  const [currentSplitwiseUser, setCurrentSplitwiseUser] = useState<SplitwiseUser | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [log, setLog] = useState<unknown>({ status: "Ready" });

  const pendingTotal = useMemo(
    () => transactions.reduce((total, tx) => total + Math.abs(tx.amount_cents), 0) / 100,
    [transactions],
  );
  const lastSyncLabel = useMemo(() => {
    const latest = allTransactions[0]?.updated_at;
    return latest ? new Date(latest).toLocaleString() : "Not synced yet";
  }, [allTransactions]);
  const pendingReviewTransactions = useMemo(
    () => filterTransactions(transactions, filters),
    [transactions, filters],
  );
  const timelineEvents = useMemo(
    () => filterEvents(buildDashboardEvents(allTransactions), filters),
    [allTransactions, filters],
  );
  const analytics = useMemo(
    () => analyticsForTransactions(allTransactions, analyticsDays),
    [allTransactions, analyticsDays],
  );
  const memory = useMemo(() => memoryForTransactions(allTransactions), [allTransactions]);

  useEffect(() => {
    void loadTransactions();
    void loadRecentActivity();
    void loadCurrentSplitwiseUser();
  }, []);

  useEffect(() => {
    const timer = window.setInterval(() => {
      void refreshDashboardQuietly();
    }, 15000);
    return () => window.clearInterval(timer);
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
        setAllTransactions((current) => mergeTransactions(current, data));
        return { loaded_transactions: data.length };
      },
      false,
    );
  }

  async function refreshReviewData() {
    await loadTransactions();
    await loadRecentActivity();
  }

  async function refreshDashboardQuietly() {
    try {
      const [pending, recent] = await Promise.all([
        api<Transaction[]>("/transactions?status=ask_user"),
        loadRecentActivityData(),
      ]);
      setTransactions(pending);
      setRecentTransactions(recent);
      setAllTransactions((current) => mergeTransactions(current, [...pending, ...recent]));
    } catch {
      // Quiet polling should not interrupt the active workflow or overwrite the log panel.
    }
  }

  async function loadRecentActivity() {
    await run(
      "recent",
      async () => {
        const merged = await loadRecentActivityData();
        setRecentTransactions(merged);
        setAllTransactions((current) => mergeTransactions(current, merged));
        return { recent_activity: merged.length };
      },
      false,
    );
  }

  async function loadRecentActivityData() {
    const statuses = ["personal", "posted", "shared_draft"];
    const groups = await Promise.all(
      statuses.map((status) =>
        api<Transaction[]>(`/transactions?status=${encodeURIComponent(status)}&limit=20`),
      ),
    );
    return groups
      .flat()
      .sort((a, b) => b.updated_at.localeCompare(a.updated_at))
      .slice(0, 12);
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

  async function submitCustomSplit(transaction: Transaction, confirm: boolean) {
    const txId = transaction.id;
    const selectedGroup = selectedGroupByTx[txId];
    const friends = selectedGroup
      ? selectedNonPayerGroupMembers(txId)
      : selectedFriendsByTx[txId] || [];
    const mode = customModeByTx[txId] || "equal";
    const payerIncluded = payerIncludedByTx[txId] ?? true;
    const values = customValuesByTx[txId] || {};
    const participantSplits = [
      ...(payerIncluded && currentSplitwiseUser
        ? [{ id: currentSplitwiseUser.id, display_name: "You" }]
        : []),
      ...friends,
    ].map((participant) => {
      const value = values[participant.id] || "";
      return {
        user_id: participant.id,
        display_name: participant.display_name,
        amount: mode === "exact_amounts" ? value || "0" : null,
        percentage: mode === "percentages" ? value || "0" : null,
        shares: mode === "shares" ? value || "0" : null,
      };
    });

    await run(
      `${confirm ? "custom-split" : "custom-preview"}-${txId}`,
      () =>
        api<SplitResponse>(`/transactions/${txId}/split/custom`, {
          method: "POST",
          body: JSON.stringify({
            group_id: selectedGroup?.id ?? null,
            payer_user_id: currentSplitwiseUser?.id ?? null,
            payer_included: payerIncluded,
            split_mode: mode,
            participant_splits: participantSplits,
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

  function updateCustomValue(txId: number, userId: number, value: string) {
    setCustomValuesByTx((current) => ({
      ...current,
      [txId]: { ...(current[txId] || {}), [userId]: value },
    }));
  }

  function setTransactionExpanded(txId: number, expanded: boolean) {
    setExpandedTransactions((current) => ({ ...current, [txId]: expanded }));
  }

  function selectedNonPayerGroupMembers(txId: number) {
    return (selectedGroupMembersByTx[txId] || []).filter(
      (member) => member.id !== currentSplitwiseUser?.id,
    );
  }

  function updateFilter<K extends keyof DashboardFilters>(key: K, value: DashboardFilters[K]) {
    setFilters((current) => ({ ...current, [key]: value }));
  }

  function selectMemoryFriend(name: string) {
    const firstPending = transactions[0];
    if (!firstPending) return;
    setFriendQueriesByTx((current) => ({ ...current, [firstPending.id]: name }));
  }

  function selectMemoryGroup(name: string) {
    const firstPending = transactions[0];
    if (!firstPending) return;
    setGroupQueriesByTx((current) => ({ ...current, [firstPending.id]: name }));
  }

  return (
    <main className="min-h-screen bg-[linear-gradient(180deg,#f8fafc_0%,#f1f5f9_42%,#f8fafc_100%)]">
      <section className="mx-auto flex w-full max-w-[1500px] flex-col gap-5 px-4 py-5 sm:px-6 lg:px-8">
        <Header onPlaid={openPlaidLink} onSync={syncTransactions} busy={busy} />

        <div className="grid gap-3 md:grid-cols-3 xl:grid-cols-4">
          <MetricCard
            icon={Clock3}
            label="Pending approvals"
            value={String(transactions.length)}
            tone="emerald"
          />
          <MetricCard
            icon={BadgeDollarSign}
            label="Pending amount"
            value={`$${pendingTotal.toFixed(2)}`}
            tone="blue"
          />
          <OperationalState
            pendingCount={transactions.length}
            busy={busy}
            lastSyncLabel={lastSyncLabel}
          />
        </div>

        <SearchFilters filters={filters} onChange={updateFilter} />

        <AnalyticsDashboard
          analytics={analytics}
          days={analyticsDays}
          onDaysChange={setAnalyticsDays}
        />

        <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_360px]">
          <section className="space-y-4">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <h2 className="text-xl font-semibold text-slate-950">Pending transactions</h2>
                <p className="mt-1 text-sm text-slate-500">
                  Search Splitwise friends by name, select them, then approve the split.
                </p>
              </div>
              <Button variant="outline" onClick={loadTransactions} disabled={busy !== null}>
                <RefreshCw className="h-4 w-4" />
                Refresh
              </Button>
            </div>

            {pendingReviewTransactions.length ? (
              <div className="grid gap-4">
                {pendingReviewTransactions.map((transaction) => (
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
                    currentUserName={splitwiseUserDisplayName(currentSplitwiseUser)}
                    customMode={customModeByTx[transaction.id] || "equal"}
                    payerIncluded={payerIncludedByTx[transaction.id] ?? true}
                    customValues={customValuesByTx[transaction.id] || {}}
                    expanded={Boolean(expandedTransactions[transaction.id])}
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
                    onCustomModeChange={(mode) =>
                      setCustomModeByTx((current) => ({ ...current, [transaction.id]: mode }))
                    }
                    onPayerIncludedChange={(included) =>
                      setPayerIncludedByTx((current) => ({
                        ...current,
                        [transaction.id]: included,
                      }))
                    }
                    onCustomValueChange={(userId, value) =>
                      updateCustomValue(transaction.id, userId, value)
                    }
                    onExpandedChange={(expanded) =>
                      setTransactionExpanded(transaction.id, expanded)
                    }
                    allGroupsOpen={false}
                    onPersonal={() => markPersonal(transaction.id)}
                    onDraft={() => submitSplit(transaction.id, false)}
                    onPostSplit={() => {
                      const mode = customModeByTx[transaction.id] || "equal";
                      const payerIncluded = payerIncludedByTx[transaction.id] ?? true;
                      if (mode === "equal" && payerIncluded) {
                        return submitSplit(transaction.id, true);
                      }
                      return submitCustomSplit(transaction, true);
                    }}
                    onPreviewCustom={() => submitCustomSplit(transaction, false)}
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
            <AgentMemoryPanel
              friends={memory.friends}
              groups={memory.groups}
              onSelectFriend={selectMemoryFriend}
              onSelectGroup={selectMemoryGroup}
            />
            <ActivityTimeline events={timelineEvents} />
            <ActivityLog log={log} />
          </aside>
        </div>
      </section>
    </main>
  );
}

function mergeTransactions(current: Transaction[], incoming: Transaction[]) {
  const byId = new Map(current.map((transaction) => [transaction.id, transaction]));
  incoming.forEach((transaction) => byId.set(transaction.id, transaction));
  return [...byId.values()].sort((a, b) => b.updated_at.localeCompare(a.updated_at));
}

function splitwiseUserDisplayName(user: SplitwiseUser | null) {
  if (!user) return "You";
  return [user.first_name, user.last_name].filter(Boolean).join(" ") || user.email || "You";
}

function SearchFilters({
  filters,
  onChange,
}: {
  filters: DashboardFilters;
  onChange: <K extends keyof DashboardFilters>(key: K, value: DashboardFilters[K]) => void;
}) {
  return (
    <div className="rounded-lg border border-slate-200/80 bg-white/85 p-2 shadow-sm shadow-slate-950/[0.03] backdrop-blur">
      <div className="grid gap-2 md:grid-cols-[1.15fr_1fr_160px_150px_150px]">
        <div className="relative">
          <Search className="pointer-events-none absolute left-3 top-2.5 h-4 w-4 text-slate-400" />
          <Input
            className="h-9 pl-9"
            value={filters.merchant}
            onChange={(event) => onChange("merchant", event.target.value)}
            placeholder="Merchant"
          />
        </div>
        <Input
          className="h-9"
          value={filters.group}
          onChange={(event) => onChange("group", event.target.value)}
          placeholder="Group"
        />
        <select
          className="h-9 rounded-md border border-slate-200 bg-white px-3 text-sm text-slate-700 outline-none transition focus:border-emerald-500 focus:ring-2 focus:ring-emerald-500/20"
          value={filters.status}
          onChange={(event) => onChange("status", event.target.value)}
        >
          <option value="">All statuses</option>
          <option value="ask_user">ask_user</option>
          <option value="personal">personal</option>
          <option value="posted">posted</option>
          <option value="shared_draft">shared_draft</option>
        </select>
        <Input
          className="h-9"
          type="date"
          value={filters.dateFrom}
          onChange={(event) => onChange("dateFrom", event.target.value)}
        />
        <Input
          className="h-9"
          type="date"
          value={filters.dateTo}
          onChange={(event) => onChange("dateTo", event.target.value)}
        />
      </div>
    </div>
  );
}

function AnalyticsDashboard({
  analytics,
  days,
  onDaysChange,
}: {
  analytics: ReturnType<typeof analyticsForTransactions>;
  days: number;
  onDaysChange: (days: number) => void;
}) {
  const ratioTotal = analytics.personalCount + analytics.sharedCount || 1;
  const sharedPercent = Math.round((analytics.sharedCount / ratioTotal) * 100);

  return (
    <section className="grid gap-4 xl:grid-cols-[340px_minmax(0,1fr)]">
      <Card className="overflow-hidden">
        <CardHeader>
          <div className="flex items-center justify-between gap-3">
            <div>
              <CardTitle className="text-base">Analytics</CardTitle>
              <CardDescription>Shared spend and behavior patterns</CardDescription>
            </div>
            <select
              className="h-8 rounded-md border border-slate-200 bg-white px-2 text-xs text-slate-700"
              value={days}
              onChange={(event) => onDaysChange(Number(event.target.value))}
            >
              <option value={7}>7 days</option>
              <option value={30}>30 days</option>
              <option value={90}>90 days</option>
            </select>
          </div>
        </CardHeader>
        <CardContent className="grid gap-4">
          <div className="rounded-lg border border-emerald-100 bg-emerald-50/80 p-4">
            <p className="text-xs font-medium uppercase tracking-wide text-emerald-700">
              Total shared spend
            </p>
            <p className="mt-1 text-4xl font-semibold tracking-normal text-emerald-950">
              ${(analytics.totalSharedSpend / 100).toFixed(2)}
            </p>
          </div>
          <div>
            <div className="mb-2 flex items-center gap-2 text-xs font-medium uppercase tracking-wide text-slate-500">
              <PieChart className="h-3.5 w-3.5" />
              Personal vs shared
            </div>
            <div className="h-2 overflow-hidden rounded-full bg-slate-100">
              <div className="h-full bg-emerald-600" style={{ width: `${sharedPercent}%` }} />
            </div>
            <div className="mt-2 flex items-center justify-between text-xs text-slate-500">
              <span>{analytics.personalCount} personal</span>
              <span>{sharedPercent}% shared</span>
            </div>
          </div>
        </CardContent>
      </Card>
      <Card className="bg-white/90">
        <CardHeader>
          <div className="flex items-center gap-2">
            <BarChart3 className="h-4 w-4 text-emerald-700" />
            <CardTitle className="text-base">Top patterns</CardTitle>
          </div>
        </CardHeader>
        <CardContent className="grid gap-4 md:grid-cols-3">
          <MiniBarList title="Merchants" items={analytics.topMerchants} />
          <MiniBarList title="Split partners" items={analytics.topPartners} />
          <MiniBarList title="Groups" items={analytics.topGroups} />
        </CardContent>
      </Card>
    </section>
  );
}

function MiniBarList({ title, items }: { title: string; items: MemoryEntry[] }) {
  const max = Math.max(1, ...items.map((item) => item.count));
  return (
    <div className="space-y-2">
      <p className="text-sm font-medium text-slate-700">{title}</p>
      {items.length ? (
        items.map((item) => (
          <div key={item.id}>
            <div className="mb-1 flex justify-between gap-2 text-xs text-slate-500">
              <span className="truncate">{item.name}</span>
              <span>{item.count}</span>
            </div>
            <div className="h-1.5 overflow-hidden rounded-full bg-slate-100">
              <div
                className="h-full bg-emerald-600"
                style={{ width: `${Math.max(12, (item.count / max) * 100)}%` }}
              />
            </div>
          </div>
        ))
      ) : (
        <p className="text-sm text-slate-500">No data yet.</p>
      )}
    </div>
  );
}

function ActivityTimeline({ events }: { events: DashboardEvent[] }) {
  const [expanded, setExpanded] = useState<string | null>(null);
  const eventStyles: Record<
    DashboardEvent["type"],
    { badge: string; dot: string; icon: ComponentType<{ className?: string }> }
  > = {
    transaction_detected: {
      badge: "bg-slate-100 text-slate-700",
      dot: "bg-slate-400",
      icon: WalletCards,
    },
    telegram_sent: { badge: "bg-sky-50 text-sky-700", dot: "bg-sky-500", icon: MessageCircle },
    recommendation_generated: {
      badge: "bg-indigo-50 text-indigo-700",
      dot: "bg-indigo-500",
      icon: Sparkles,
    },
    split_confirmed: {
      badge: "bg-amber-50 text-amber-700",
      dot: "bg-amber-500",
      icon: CheckCircle2,
    },
    split_posted: {
      badge: "bg-emerald-50 text-emerald-700",
      dot: "bg-emerald-500",
      icon: Split,
    },
    undo_completed: {
      badge: "bg-amber-50 text-amber-700",
      dot: "bg-amber-500",
      icon: RotateCcw,
    },
  };

  return (
    <Card className="overflow-hidden">
      <CardHeader>
        <div className="flex items-center gap-2">
          <Activity className="h-4 w-4 text-sky-600" />
          <CardTitle>Activity timeline</CardTitle>
        </div>
        <CardDescription>Chronological transaction events</CardDescription>
      </CardHeader>
      <CardContent className="space-y-1">
        {events.length ? (
          events.slice(0, 20).map((event) => {
            const style = eventStyles[event.type];
            const Icon = style.icon;
            return (
              <button
                key={event.id}
                type="button"
                className="group flex w-full gap-3 rounded-md px-1.5 py-2 text-left transition hover:bg-slate-50"
                onClick={() => setExpanded((current) => (current === event.id ? null : event.id))}
              >
                <span className="relative mt-1 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-white shadow-sm ring-1 ring-slate-200">
                  <span className={`absolute -left-1 top-3 h-2 w-2 rounded-full ${style.dot}`} />
                  <Icon className="h-3.5 w-3.5 text-slate-600" />
                </span>
                <span className="min-w-0 flex-1 border-b border-slate-100 pb-3">
                  <span className="flex flex-wrap items-center justify-between gap-2">
                    <span className="truncate text-sm font-medium text-slate-900">
                      {event.merchant}
                    </span>
                    <Badge className={style.badge}>{event.type.replace(/_/g, " ")}</Badge>
                  </span>
                  <span className="mt-1 block text-xs text-slate-500">
                    {event.currency} {event.amount}
                    {event.participants.length ? ` · ${event.participants.join(", ")}` : ""}
                  </span>
                  <span className="mt-1 flex items-center gap-1 text-xs text-slate-400">
                    <CalendarDays className="h-3 w-3" />
                    {new Date(event.timestamp).toLocaleString()}
                  </span>
                  {expanded === event.id ? (
                    <pre className="mt-2 max-h-48 overflow-auto rounded-md bg-slate-950 p-2 text-xs text-slate-100">
                      {JSON.stringify(event.details, null, 2)}
                    </pre>
                  ) : null}
                </span>
              </button>
            );
          })
        ) : (
          <p className="text-sm text-slate-500">No activity matches the current filters.</p>
        )}
      </CardContent>
    </Card>
  );
}

function AgentMemoryPanel({
  friends,
  groups,
  onSelectFriend,
  onSelectGroup,
}: {
  friends: MemoryEntry[];
  groups: MemoryEntry[];
  onSelectFriend: (name: string) => void;
  onSelectGroup: (name: string) => void;
}) {
  return (
    <Card className="bg-white/90">
      <CardHeader>
        <div className="flex items-center gap-2">
          <Bot className="h-4 w-4 text-indigo-600" />
          <CardTitle>Agent memory</CardTitle>
        </div>
        <CardDescription>Frequent friends and groups from past splits</CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <MemoryList title="Friends" items={friends} onSelect={onSelectFriend} />
        <MemoryList title="Groups" items={groups} onSelect={onSelectGroup} />
      </CardContent>
    </Card>
  );
}

function MemoryList({
  title,
  items,
  onSelect,
}: {
  title: string;
  items: MemoryEntry[];
  onSelect: (name: string) => void;
}) {
  return (
    <div className="space-y-2">
      <p className="text-sm font-medium text-slate-700">{title}</p>
      {items.length ? (
        <div className="flex flex-wrap gap-2">
          {items.map((item) => (
            <button
              key={item.id}
              type="button"
              className="rounded-md border border-slate-200 bg-white px-2.5 py-1.5 text-sm text-slate-700 transition hover:border-emerald-200 hover:bg-emerald-50 hover:text-emerald-900"
              onClick={() => onSelect(item.name)}
            >
              {item.name}
              <span className="ml-1 text-xs text-slate-500">x{item.count}</span>
            </button>
          ))}
        </div>
      ) : (
        <p className="text-sm text-slate-500">No memory yet.</p>
      )}
    </div>
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
    <div className="flex flex-col gap-5 rounded-lg border border-slate-200/80 bg-white/90 px-5 py-5 shadow-sm shadow-slate-950/[0.04] backdrop-blur lg:flex-row lg:items-center lg:justify-between">
      <div>
        <div className="flex items-center gap-2 text-sm font-medium text-emerald-700">
          <span className="flex h-7 w-7 items-center justify-center rounded-md bg-emerald-700 text-white shadow-sm">
            <Split className="h-4 w-4" />
          </span>
          ExpenseOps Agent
        </div>
        <h1 className="mt-3 text-3xl font-semibold tracking-normal text-slate-950">
          Shared expense command center
        </h1>
        <p className="mt-2 max-w-2xl text-sm leading-6 text-slate-500">
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
  tone = "emerald",
}: {
  icon: ComponentType<{ className?: string }>;
  label: string;
  value: string;
  tone?: "emerald" | "blue" | "amber";
}) {
  const tones = {
    emerald: "bg-emerald-50 text-emerald-700 ring-emerald-100",
    blue: "bg-sky-50 text-sky-700 ring-sky-100",
    amber: "bg-amber-50 text-amber-700 ring-amber-100",
  };

  return (
    <Card className="bg-white/90">
      <CardContent className="flex items-center justify-between p-4">
        <div>
          <p className="text-xs font-medium uppercase tracking-wide text-slate-500">{label}</p>
          <p className="mt-1 text-2xl font-semibold tracking-normal text-slate-950">{value}</p>
        </div>
        <div className={`rounded-md p-2.5 ring-1 ${tones[tone]}`}>
          <Icon className="h-5 w-5" />
        </div>
      </CardContent>
    </Card>
  );
}

function OperationalState({
  pendingCount,
  busy,
  lastSyncLabel,
}: {
  pendingCount: number;
  busy: string | null;
  lastSyncLabel: string;
}) {
  return (
    <Card className="md:col-span-3 xl:col-span-2">
      <CardContent className="grid gap-3 p-4 sm:grid-cols-3">
        <StatusPill
          icon={pendingCount ? AlertCircle : CheckCircle2}
          label="Approval queue"
          value={pendingCount ? `${pendingCount} pending` : "Clear"}
          tone={pendingCount ? "amber" : "emerald"}
        />
        <StatusPill icon={MessageCircle} label="Telegram" value="Connected" tone="blue" />
        <StatusPill
          icon={RefreshCw}
          label="Auto-sync"
          value={busy ? "Working" : lastSyncLabel}
          tone="slate"
        />
      </CardContent>
    </Card>
  );
}

function StatusPill({
  icon: Icon,
  label,
  value,
  tone,
}: {
  icon: ComponentType<{ className?: string }>;
  label: string;
  value: string;
  tone: "emerald" | "amber" | "blue" | "slate";
}) {
  const tones = {
    emerald: "bg-emerald-50 text-emerald-700",
    amber: "bg-amber-50 text-amber-700",
    blue: "bg-sky-50 text-sky-700",
    slate: "bg-slate-100 text-slate-600",
  };

  return (
    <div className="flex min-w-0 items-center gap-3">
      <span
        className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-md ${tones[tone]}`}
      >
        <Icon className="h-4 w-4" />
      </span>
      <span className="min-w-0">
        <span className="block text-xs font-medium uppercase tracking-wide text-slate-400">
          {label}
        </span>
        <span className="block truncate text-sm font-medium text-slate-900">{value}</span>
      </span>
    </div>
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
  currentUserName,
  customMode,
  payerIncluded,
  customValues,
  expanded,
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
  onCustomModeChange,
  onPayerIncludedChange,
  onCustomValueChange,
  onExpandedChange,
  allGroupsOpen,
  onPersonal,
  onDraft,
  onPostSplit,
  onPreviewCustom,
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
  currentUserName: string;
  customMode: CustomSplitMode;
  payerIncluded: boolean;
  customValues: Record<number, string>;
  expanded: boolean;
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
  onCustomModeChange: (mode: CustomSplitMode) => void;
  onPayerIncludedChange: (included: boolean) => void;
  onCustomValueChange: (userId: number, value: string) => void;
  onExpandedChange: (expanded: boolean) => void;
  allGroupsOpen: boolean;
  onPersonal: () => void;
  onDraft: () => void;
  onPostSplit: () => void;
  onPreviewCustom: () => void;
}) {
  const title = transaction.merchant_name || transaction.name;
  const disabled = busy !== null;
  const selectedParticipantCount = selectedGroup
    ? selectedGroupMembers.length
    : selectedFriends.length;
  const [splitMode, setSplitMode] = useState<"people" | "group">(
    allGroupsOpen ? "group" : "people",
  );
  const hasSplitWork =
    selectedFriends.length > 0 ||
    Boolean(selectedGroup) ||
    selectedGroupMembers.length > 0 ||
    Object.values(customValues).some(Boolean) ||
    customMode !== "equal" ||
    payerIncluded === false;
  const isExpanded = expanded || hasSplitWork;
  const customParticipants = [
    ...(payerIncluded && currentUserId
      ? [{ id: currentUserId, display_name: currentUserName }]
      : []),
    ...(selectedGroup ? selectedGroupMembers : selectedFriends),
  ];
  const customValidation = buildCustomSplitPreview(
    transaction,
    customMode,
    customParticipants,
    customValues,
  );

  return (
    <Card
      className={`border-slate-200/70 bg-white/95 shadow-sm shadow-slate-950/[0.025] transition hover:border-emerald-200/80 hover:shadow-lg hover:shadow-emerald-950/[0.05] ${
        isExpanded ? "border-emerald-200/80 ring-1 ring-emerald-100" : ""
      }`}
    >
      <CardHeader className="gap-3 pb-2">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
          <div className="min-w-0">
            <CardTitle className="truncate text-base">{title}</CardTitle>
            <CardDescription className="mt-1 text-xs">
              {transaction.iso_currency_code} {transaction.amount}
              {transaction.date ? ` · ${transaction.date}` : ""}
            </CardDescription>
          </div>
          <div className="flex shrink-0 flex-wrap gap-1.5">
            {transaction.pending ? (
              <Badge className="bg-amber-50 text-amber-700">Pending</Badge>
            ) : (
              <Badge className="bg-emerald-50 text-emerald-700">Settled</Badge>
            )}
            <Badge variant="outline">{transaction.status}</Badge>
            <ClassificationBadge transaction={transaction} />
          </div>
        </div>

        <div className="flex flex-col gap-3 border-t border-slate-100 pt-3 sm:flex-row sm:items-center sm:justify-between">
          <p className="min-w-0 text-sm text-slate-600">
            {transaction.agent_question || "Review this transaction."}
          </p>
          <div className="flex shrink-0 flex-wrap items-center gap-2">
            <Button
              variant="ghost"
              className="text-slate-600 hover:bg-slate-100 hover:text-slate-950"
              onClick={onPersonal}
              disabled={disabled}
            >
              <UserCheck className="h-4 w-4" />
              Personal
            </Button>
            <Button
              onClick={() => onExpandedChange(true)}
              disabled={disabled}
              className="shadow-md shadow-emerald-950/10"
            >
              <Split className="h-4 w-4" />
              Split / Review
            </Button>
            <Button variant="outline" onClick={onDraft} disabled={disabled}>
              <Clock3 className="h-4 w-4" />
              Draft
            </Button>
            <Button
              variant="ghost"
              size="icon"
              className="h-8 w-8"
              onClick={() => onExpandedChange(!isExpanded)}
              aria-label={isExpanded ? "Collapse transaction" : "Expand transaction"}
            >
              <ChevronDown
                className={`h-4 w-4 transition-transform ${isExpanded ? "rotate-180" : ""}`}
              />
            </Button>
          </div>
        </div>
      </CardHeader>

      {isExpanded ? (
        <CardContent className="space-y-3 overflow-hidden pt-0 transition-all duration-200">
        <div className="rounded-lg border border-slate-200/80 bg-slate-50/70 p-2.5">
          <div className="mb-2 grid grid-cols-2 rounded-full bg-slate-100/90 p-1 ring-1 ring-slate-200/70">
            <button
              type="button"
              className={`inline-flex items-center justify-center gap-2 rounded-full px-3 py-1.5 text-sm font-medium transition-all ${
                splitMode === "people"
                  ? "bg-emerald-600 text-white shadow-sm shadow-emerald-950/15"
                  : "text-slate-600 hover:bg-white/70 hover:text-slate-950"
              }`}
              onClick={() => setSplitMode("people")}
            >
              <UserCheck className="h-3.5 w-3.5" />
              People
            </button>
            <button
              type="button"
              className={`inline-flex items-center justify-center gap-2 rounded-full px-3 py-1.5 text-sm font-medium transition-all ${
                splitMode === "group"
                  ? "bg-emerald-600 text-white shadow-sm shadow-emerald-950/15"
                  : "text-slate-600 hover:bg-white/70 hover:text-slate-950"
              }`}
              onClick={() => setSplitMode("group")}
            >
              <UsersRound className="h-3.5 w-3.5" />
              Group
            </button>
          </div>

          <div className="transition-opacity duration-150">
            {splitMode === "people" ? (
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
            ) : (
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
            )}
          </div>
        </div>

        <CustomSplitPanel
          mode={customMode}
          payerIncluded={payerIncluded}
          participants={customParticipants}
          selectedParticipantCount={selectedParticipantCount}
          values={customValues}
          validation={customValidation}
          disabled={disabled}
          onModeChange={onCustomModeChange}
          onPayerIncludedChange={onPayerIncludedChange}
          onValueChange={onCustomValueChange}
          onPreview={onPreviewCustom}
          onPost={onPostSplit}
        />
        </CardContent>
      ) : null}
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

function CustomSplitPanel({
  mode,
  payerIncluded,
  participants,
  selectedParticipantCount,
  values,
  validation,
  disabled,
  onModeChange,
  onPayerIncludedChange,
  onValueChange,
  onPreview,
  onPost,
}: {
  mode: CustomSplitMode;
  payerIncluded: boolean;
  participants: Array<{ id: number; display_name: string }>;
  selectedParticipantCount: number;
  values: Record<number, string>;
  validation: CustomSplitPreview;
  disabled: boolean;
  onModeChange: (mode: CustomSplitMode) => void;
  onPayerIncludedChange: (included: boolean) => void;
  onValueChange: (userId: number, value: string) => void;
  onPreview: () => void;
  onPost: () => void;
}) {
  const isEqualMode = mode === "equal";
  const canPost = !disabled && selectedParticipantCount > 0 && validation.valid;
  const postLabel = isEqualMode ? "Post equal split" : "Post custom split";
  const emptyMessage = "Select at least one friend or group member before posting.";

  return (
    <div className="rounded-lg border border-slate-200/80 bg-white p-2.5 shadow-sm shadow-slate-950/[0.02]">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap gap-1 rounded-full bg-slate-100 p-1">
          {[
            ["equal", "Equal"],
            ["exact_amounts", "Amounts"],
            ["percentages", "%"],
            ["shares", "Shares"],
          ].map(([value, label]) => (
            <button
              key={value}
              type="button"
              className={`rounded-full px-2.5 py-1 text-xs font-medium transition ${
                mode === value
                  ? "bg-emerald-600 text-white shadow-sm"
                  : "text-slate-600 hover:bg-white"
              }`}
              onClick={() => onModeChange(value as CustomSplitMode)}
            >
              {label}
            </button>
          ))}
        </div>
        <label className="inline-flex items-center gap-2 text-xs font-medium text-slate-600">
          <input
            type="checkbox"
            className="h-4 w-4 rounded border-slate-300 text-emerald-600"
            checked={payerIncluded}
            onChange={(event) => onPayerIncludedChange(event.target.checked)}
          />
          Include me in split
        </label>
      </div>

      <div className="mt-2 grid gap-2">
        {participants.length ? (
          participants.map((participant) => (
            <div
              key={participant.id}
              className="grid items-center gap-2 rounded-md bg-slate-50 px-2 py-1.5 sm:grid-cols-[minmax(0,1fr)_130px]"
            >
              <span className="truncate text-sm font-medium text-slate-800">
                {participant.display_name}
              </span>
              {mode === "equal" ? (
                <span className="text-right text-xs text-slate-500">
                  {validation.previewById[participant.id] || "$0.00"}
                </span>
              ) : (
                <Input
                  className="h-8 text-right"
                  inputMode="decimal"
                  value={values[participant.id] || ""}
                  placeholder={mode === "percentages" ? "0%" : mode === "shares" ? "1" : "0.00"}
                  onChange={(event) => onValueChange(participant.id, event.target.value)}
                  disabled={disabled}
                />
              )}
            </div>
          ))
        ) : (
          <p className="px-1 py-1 text-xs text-slate-500">
            {emptyMessage}
          </p>
        )}
      </div>

      <div className="mt-2 flex flex-wrap items-center justify-between gap-2 border-t border-slate-100 pt-2">
        <div className="text-xs text-slate-500">
          <span
            className={
              selectedParticipantCount > 0 && validation.valid
                ? "text-emerald-700"
                : "text-amber-700"
            }
          >
            {selectedParticipantCount > 0 ? validation.message : emptyMessage}
          </span>
        </div>
        <div className="flex gap-2">
          {!isEqualMode ? (
            <Button
              variant="outline"
              size="sm"
              onClick={onPreview}
              disabled={disabled || selectedParticipantCount === 0 || !validation.valid}
            >
              Preview split
            </Button>
          ) : null}
          <Button
            size="sm"
            onClick={onPost}
            disabled={!canPost}
            className="min-w-[144px] shadow-md shadow-emerald-950/10"
          >
            <CheckCircle2 className="h-4 w-4" />
            {postLabel}
          </Button>
        </div>
      </div>
    </div>
  );
}

type CustomSplitPreview = {
  valid: boolean;
  message: string;
  previewById: Record<number, string>;
};

function buildCustomSplitPreview(
  transaction: Transaction,
  mode: CustomSplitMode,
  participants: Array<{ id: number; display_name: string }>,
  values: Record<number, string>,
): CustomSplitPreview {
  const total = Number(transaction.amount || "0");
  if (participants.length === 0) {
    return { valid: false, message: "No participants selected.", previewById: {} };
  }
  if (mode === "equal") {
    const share = total / participants.length;
    return {
      valid: true,
      message: `Covered $${total.toFixed(2)} · approx. $${share.toFixed(2)} each`,
      previewById: Object.fromEntries(participants.map((participant) => [participant.id, `$${share.toFixed(2)}`])),
    };
  }
  const nums = participants.map((participant) => Number(values[participant.id] || "0"));
  if (nums.some((value) => Number.isNaN(value) || value < 0)) {
    return { valid: false, message: "Values must be non-negative numbers.", previewById: {} };
  }
  if (mode === "exact_amounts") {
    const covered = nums.reduce((sum, value) => sum + value, 0);
    const remaining = total - covered;
    return {
      valid: Math.abs(remaining) < 0.01,
      message: `Covered $${covered.toFixed(2)} · remaining $${remaining.toFixed(2)}`,
      previewById: Object.fromEntries(participants.map((participant, index) => [participant.id, `$${nums[index].toFixed(2)}`])),
    };
  }
  if (mode === "percentages") {
    const percentageTotal = nums.reduce((sum, value) => sum + value, 0);
    return {
      valid: Math.abs(percentageTotal - 100) < 0.001,
      message: `Percent total ${percentageTotal.toFixed(2)}%`,
      previewById: Object.fromEntries(participants.map((participant, index) => [participant.id, `$${((total * nums[index]) / 100).toFixed(2)}`])),
    };
  }
  const shareTotal = nums.reduce((sum, value) => sum + value, 0);
  return {
    valid: shareTotal > 0,
    message: `Share units ${shareTotal.toFixed(2)} · total $${total.toFixed(2)}`,
    previewById: Object.fromEntries(participants.map((participant, index) => [participant.id, `$${((total * nums[index]) / shareTotal).toFixed(2)}`])),
  };
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
    <div className="space-y-2.5">
      <div className="flex flex-col gap-2 sm:flex-row">
        <Input
          className="h-9"
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
        <div className="flex flex-wrap gap-1.5">
          {selectedFriends.map((friend) => (
            <ParticipantChip
              key={friend.id}
              label={friend.display_name}
              onRemove={() => onRemoveFriend(friend.id)}
            />
          ))}
        </div>
      ) : (
        <p className="px-1 text-xs text-slate-500">No friends selected.</p>
      )}

      {results.length ? (
        <div className="grid gap-2 sm:grid-cols-2">
          {results.map((friend) => (
            <button
              key={friend.id}
              type="button"
              className="rounded-md border border-slate-200/80 bg-white px-3 py-2 text-left text-sm shadow-sm shadow-slate-950/[0.02] transition hover:border-emerald-200 hover:bg-emerald-50/60 hover:shadow"
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

function ParticipantChip({ label, onRemove }: { label: string; onRemove: () => void }) {
  return (
    <button
      className="inline-flex max-w-full items-center gap-1.5 rounded-full bg-emerald-50 px-2.5 py-1 text-sm font-medium text-emerald-800 ring-1 ring-emerald-100 transition hover:bg-emerald-100"
      onClick={onRemove}
      type="button"
    >
      <span className="truncate">{label}</span>
      <X className="h-3.5 w-3.5 shrink-0" />
    </button>
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
    <div className="space-y-2.5">
      <div className="flex flex-col gap-2 sm:flex-row">
        <Input
          className="h-9"
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
          <span className="inline-flex items-center gap-2 rounded-full bg-slate-900 px-2.5 py-1 text-sm font-medium text-white shadow-sm">
            <UsersRound className="h-3.5 w-3.5" />
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
              className="rounded-md border border-slate-200/80 bg-white px-3 py-2 text-left text-sm shadow-sm shadow-slate-950/[0.02] transition hover:border-emerald-200 hover:bg-emerald-50/60 hover:shadow"
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
            <div className="flex flex-wrap gap-1.5">
              {selectedMembers.map((member) => (
                <ParticipantChip
                  key={member.id}
                  label={member.display_name}
                  onRemove={() => onRemoveMember(member.id)}
                />
              ))}
            </div>
          ) : (
            <p className="px-1 text-xs text-slate-500">No group members selected.</p>
          )}

          {members.length ? (
            <div className="grid gap-2 sm:grid-cols-2">
              {members.map((member) => {
                const isCurrentUser = member.id === currentUserId;
                return (
                  <button
                    key={member.id}
                    type="button"
                    className="rounded-md border border-slate-200/80 bg-white px-3 py-2 text-left text-sm shadow-sm shadow-slate-950/[0.02] transition hover:border-emerald-200 hover:bg-emerald-50/60 hover:shadow disabled:bg-slate-50 disabled:shadow-none disabled:opacity-70"
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
    <Card className="overflow-hidden">
      <CardHeader>
        <div className="flex items-center gap-2">
          <Layers3 className="h-4 w-4 text-emerald-700" />
          <CardTitle>Recent activity</CardTitle>
        </div>
        <CardDescription>Completed transactions that can be moved back to review</CardDescription>
      </CardHeader>
      <CardContent className="space-y-1">
        {transactions.length ? (
          transactions.map((transaction) => (
            <div
              key={transaction.id}
              className="flex items-center justify-between gap-3 rounded-md px-1.5 py-2 transition hover:bg-slate-50"
            >
              <div className="flex min-w-0 items-center gap-3">
                <ActivityIcon status={transaction.status} />
                <div className="min-w-0">
                  <p className="truncate text-sm font-medium text-slate-900">
                    {transaction.merchant_name || transaction.name}
                  </p>
                  <p className="text-xs text-slate-500">
                    {transaction.iso_currency_code} {transaction.amount} · {transaction.status}
                  </p>
                  <p className="text-xs text-slate-400">
                    {new Date(transaction.updated_at).toLocaleString()}
                  </p>
                </div>
              </div>
              {transaction.can_undo_transaction ? (
                <Button
                  variant="outline"
                  size="sm"
                  className="text-amber-700 hover:border-amber-200 hover:bg-amber-50"
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

function ActivityIcon({ status }: { status: Transaction["status"] }) {
  const styles: Record<string, string> = {
    personal: "bg-emerald-50 text-emerald-700",
    posted: "bg-emerald-50 text-emerald-700",
    shared_draft: "bg-amber-50 text-amber-700",
    ask_user: "bg-sky-50 text-sky-700",
  };
  const Icon =
    status === "shared_draft" ? Clock3 : status === "ask_user" ? MessageCircle : CheckCircle2;

  return (
    <span
      className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-full ${
        styles[status] || "bg-slate-100 text-slate-600"
      }`}
    >
      <Icon className="h-4 w-4" />
    </span>
  );
}

function ActivityLog({ log }: { log: unknown }) {
  return (
    <Card className="bg-slate-950 text-white">
      <CardHeader>
        <CardTitle className="text-white">Activity log</CardTitle>
        <CardDescription className="text-slate-400">Latest API response or error</CardDescription>
      </CardHeader>
      <CardContent>
        <pre className="max-h-[520px] overflow-auto rounded-md bg-black/30 p-4 text-xs leading-5 text-slate-100">
          {JSON.stringify(log, null, 2)}
        </pre>
      </CardContent>
    </Card>
  );
}

export default App;
