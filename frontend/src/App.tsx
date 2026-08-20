import {
  lazy,
  Suspense,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ComponentType,
  type RefObject,
  type ReactNode,
} from "react";
import * as Dialog from "@radix-ui/react-dialog";
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
  House,
  Layers3,
  Link2,
  MessageCircle,
  PieChart,
  RefreshCw,
  RotateCcw,
  Search,
  Settings2,
  SlidersHorizontal,
  Split,
  Sparkles,
  UserCheck,
  UsersRound,
  WalletCards,
  Tags,
  X,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { AccountMenu } from "@/components/ui/account-menu";
import { AppShell } from "@/components/ui/app-shell";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { FilterToolbar } from "@/components/ui/filter-toolbar";
import { Input } from "@/components/ui/input";
import { MerchantAvatar } from "@/components/ui/merchant-avatar";
import { OverflowMenu } from "@/components/ui/overflow-menu";
import { PageHeader } from "@/components/ui/page-header";
import { ResponsiveSheet } from "@/components/ui/responsive-sheet";
import { GroupManagementPanel } from "@/components/GroupManagementPanel";
import { HouseholdOpsPage } from "@/components/HouseholdOpsPage";
import { PromotionsPage } from "@/components/PromotionsPage";
import { AccountSettingsPage } from "@/components/AccountSettingsPage";
import { InsightsDashboard } from "@/components/InsightsDashboard";
import {
  analyticsForTransactions,
  filterTransactions,
  memoryForTransactions,
} from "@/dashboardLogic";
import { ApiError, api, apiErrorMessage } from "@/lib/api";
import { authenticationView } from "@/onboardingLogic";
import { parseReviewInbox } from "@/reviewInbox";
import { SandboxLabPage } from "$sandbox/SandboxLabPage";
import type {
  DashboardFilters,
  AIMemory,
  CustomSplitMode,
  Friend,
  FinancialActivityEvent,
  FinancialActivityPage,
  Group,
  MemoryEntry,
  ReviewInboxPage,
  ReviewItem,
  ReviewRecommendation,
  SplitwiseUser,
  StructuredMemoryMetrics,
  StructuredMemorySettings,
  Transaction,
} from "@/types";
import {
  agentPageContextKey,
  baseAgentContext,
  buildExpenseActivityContext,
  buildExpenseReviewContext,
  isAgentNavigationRequest,
  sameAgentContextDescriptor,
  type AgentContextDescriptor,
  type AgentContextPublisher,
  type AgentNavigationRequest,
} from "@/agent/pageContext";

const AgentExperience = lazy(() => import("@/agent/AgentExperience"));
const AttentionCenterPage = lazy(() =>
  import("@/components/AttentionCenterPage").then((module) => ({
    default: module.AttentionCenterPage,
  })),
);

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
type TransactionActionResponse = { message: string };
type AccountContext = {
  user: { id: number; email: string; display_name: string; avatar_url?: string | null };
  workspace: { id: number; name: string; workspace_type: string };
  features?: {
    agent?: { enabled: boolean; read_only: boolean; proactive_enabled?: boolean };
  };
};
type WorkspaceView = "expenses" | "household" | "promotions" | "settings" | "agent";
type ExpenseTab = "review" | "insights" | "activity" | "attention";
type ActionNotice = { tone: "success" | "error"; text: string; correlationId?: string };

function splitResponseQueued(response: SplitResponse): boolean {
  const value = response.splitwise_response;
  return Boolean(value && typeof value === "object" && "queued" in value && value.queued);
}

function App() {
  if (
    window.location.pathname === "/sandbox-lab" ||
    window.location.pathname === "/sandbox" ||
    window.location.pathname === "/dev/sandbox"
  ) {
    return <SandboxLabPage />;
  }

  return <DashboardApp />;
}

function DashboardApp() {
  const [transactions, setTransactions] = useState<Transaction[]>([]);
  const [recoveryTransactions, setRecoveryTransactions] = useState<Transaction[]>([]);
  const [recentTransactions, setRecentTransactions] = useState<Transaction[]>([]);
  const [allTransactions, setAllTransactions] = useState<Transaction[]>([]);
  const [reviewInbox, setReviewInbox] = useState<ReviewInboxPage | null>(null);
  const [financialActivity, setFinancialActivity] = useState<FinancialActivityPage | null>(null);
  const [financialActivityLoading, setFinancialActivityLoading] = useState(false);
  const [financialActivityError, setFinancialActivityError] = useState("");
  const [financialActivityReload, setFinancialActivityReload] = useState(0);
  const [aiMemories, setAiMemories] = useState<AIMemory[]>([]);
  const [memorySettings, setMemorySettings] = useState<StructuredMemorySettings>({
    transaction_learning_enabled: true,
  });
  const [memoryMetrics, setMemoryMetrics] = useState<StructuredMemoryMetrics | null>(null);
  const [filters, setFilters] = useState<DashboardFilters>({
    merchant: "",
    group: "",
    status: "",
    dateFrom: "",
    dateTo: "",
  });
  const [analyticsDays, setAnalyticsDays] = useState(30);
  const [expenseTab, setExpenseTab] = useState<ExpenseTab>(() => {
    const value = new URLSearchParams(window.location.search).get("tab");
    return value === "insights" || value === "activity" || value === "attention"
      ? value
      : "review";
  });
  const [syncError, setSyncError] = useState(false);
  const [lastSyncedAt, setLastSyncedAt] = useState<Date | null>(null);
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
  const [focusedTransactionId, setFocusedTransactionId] = useState<number | null>(null);
  const [currentSplitwiseUser, setCurrentSplitwiseUser] = useState<SplitwiseUser | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [transactionActionById, setTransactionActionById] = useState<Record<number, string>>({});
  const [transactionNoticeById, setTransactionNoticeById] = useState<Record<number, ActionNotice>>({});
  const [reviewNotice, setReviewNotice] = useState<ActionNotice | null>(null);
  const [reviewInboxStale, setReviewInboxStale] = useState(false);
  const pendingTransactionActions = useRef(new Set<number>());
  const reviewSeenRequests = useRef(new Map<string, string>());
  const [log, setLog] = useState<unknown>({ status: "Ready" });
  const [onboardingNotice, setOnboardingNotice] = useState<{ tone: "success" | "error"; text: string; action?: "switch-account" } | null>(null);
  const [accountContext, setAccountContext] = useState<AccountContext | null>(null);
  const [authResolved, setAuthResolved] = useState(false);
  const [activeWorkspace, setActiveWorkspace] = useState<WorkspaceView>(() => {
    const value = new URLSearchParams(window.location.search).get("workspace");
    return value === "household" || value === "promotions" || value === "settings" || value === "agent" ? value : "expenses";
  });
  const [agentPanelOpen, setAgentPanelOpen] = useState(false);
  const [wideAgentPanel, setWideAgentPanel] = useState(() =>
    window.matchMedia("(min-width: 1024px)").matches,
  );
  const agentLauncherRef = useRef<HTMLButtonElement | null>(null);
  const [agentSourceContext, setAgentSourceContext] = useState<AgentContextDescriptor>(() =>
    baseContextForWorkspace(activeWorkspace, expenseTab),
  );
  const [clearedAgentContextKey, setClearedAgentContextKey] = useState<string | null>(null);
  const [agentNavigationRequest, setAgentNavigationRequest] = useState<AgentNavigationRequest | null>(null);

  const pendingTotal = useMemo(
    () => transactions.reduce((total, tx) => total + Math.abs(tx.amount_cents), 0) / 100,
    [transactions],
  );
  const lastSyncLabel = useMemo(() => {
    const latest = allTransactions[0]?.updated_at;
    return latest ? new Date(latest).toLocaleString() : "Not synced yet";
  }, [allTransactions]);
  const pendingReviewTransactions = useMemo(
    () => filterTransactions(transactions, { ...filters, group: "", status: "" }),
    [transactions, filters],
  );
  const reviewRecommendations = useMemo(
    () => new Map(
      (reviewInbox?.items || [])
        .filter((item) => item.transaction && item.recommendation)
        .map((item) => [item.transaction!.id, item.recommendation!]),
    ),
    [reviewInbox],
  );
  const receiptReviewItems = useMemo(
    () => (reviewInbox?.items || []).filter((item) => item.receipt !== null),
    [reviewInbox],
  );
  const analytics = useMemo(
    () => analyticsForTransactions(allTransactions, analyticsDays),
    [allTransactions, analyticsDays],
  );
  const memory = useMemo(() => memoryForTransactions(allTransactions), [allTransactions]);
  const agentEnabled = Boolean(accountContext?.features?.agent?.enabled);
  const proactiveAttentionEnabled = Boolean(
    accountContext?.features?.agent?.proactive_enabled,
  );
  const currentAgentContextKey = useMemo(
    () => agentPageContextKey(agentSourceContext.pageContext),
    [agentSourceContext.pageContext],
  );
  const effectiveAgentPageContext = clearedAgentContextKey === currentAgentContextKey
    ? null
    : agentSourceContext.pageContext;

  const publishAgentContext = useCallback<AgentContextPublisher>((descriptor) => {
    setAgentSourceContext((current) =>
      sameAgentContextDescriptor(current, descriptor) ? current : descriptor,
    );
  }, []);

  const clearAgentContext = useCallback(() => {
    setClearedAgentContextKey(currentAgentContextKey);
  }, [currentAgentContextKey]);

  const restoreAgentContext = useCallback(() => {
    setClearedAgentContextKey(null);
  }, []);

  useEffect(() => {
    setClearedAgentContextKey((current) =>
      current && current !== currentAgentContextKey ? null : current,
    );
  }, [currentAgentContextKey]);

  const expenseAgentContext = useMemo(() => {
    if (expenseTab === "activity") {
      const event = financialActivity?.events.find(
        (value) => value.transaction_id === focusedTransactionId,
      );
      return buildExpenseActivityContext(
        event?.transaction_id
          ? {
              publicId: event.transaction_id,
              label: event.merchant_name || `Transaction ${event.transaction_id}`,
            }
          : null,
      );
    }
    const transaction = pendingReviewTransactions.find(
      (value) => value.id === focusedTransactionId,
    );
    return buildExpenseReviewContext({
      merchant: filters.merchant,
      startDate: filters.dateFrom,
      endDate: filters.dateTo,
      status: filters.status,
      transaction: transaction
        ? {
            publicId: transaction.id,
            label: [
              transaction.merchant_name || transaction.name,
              transaction.date,
              formatTransactionAmount(transaction),
            ].filter(Boolean).join(" · "),
          }
        : null,
    });
  }, [expenseTab, filters.dateFrom, filters.dateTo, filters.merchant, filters.status, financialActivity, focusedTransactionId, pendingReviewTransactions]);

  useEffect(() => {
    if (activeWorkspace === "expenses" && expenseTab !== "insights") {
      publishAgentContext(expenseAgentContext);
    }
  }, [activeWorkspace, expenseAgentContext, expenseTab, publishAgentContext]);

  useEffect(() => {
    if (!authResolved || !accountContext || agentEnabled) return;
    setAgentPanelOpen(false);
    if (activeWorkspace === "agent") {
      const params = new URLSearchParams(window.location.search);
      params.set("workspace", "expenses");
      params.delete("tab");
      window.history.replaceState({}, "", `/?${params}`);
      setActiveWorkspace("expenses");
    }
  }, [accountContext, activeWorkspace, agentEnabled, authResolved]);

  useEffect(() => {
    if (!authResolved || !accountContext || proactiveAttentionEnabled) return;
    if (expenseTab !== "attention") return;
    const params = new URLSearchParams(window.location.search);
    params.set("workspace", "expenses");
    params.set("tab", "review");
    window.history.replaceState({}, "", `/?${params}`);
    setExpenseTab("review");
    setAgentSourceContext(baseContextForWorkspace("expenses", "review"));
  }, [accountContext, authResolved, expenseTab, proactiveAttentionEnabled]);

  useEffect(() => {
    const media = window.matchMedia("(min-width: 1024px)");
    const update = () => setWideAgentPanel(media.matches);
    update();
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, []);

  useEffect(() => {
    void api<AccountContext>("/api/context")
      .then((context) => {
        setAccountContext(context);
        void loadTransactions();
        void loadRecoveryTransactions();
        void loadRecentActivity();
        void loadCurrentSplitwiseUser();
        void loadAIMemories();
        void loadReviewInbox();
      })
      .finally(() => setAuthResolved(true));
  }, []);

  useEffect(() => {
    if (!accountContext) return;
    const timer = window.setInterval(() => {
      void refreshDashboardQuietly();
    }, 15000);
    return () => window.clearInterval(timer);
  }, [accountContext]);

  useEffect(() => {
    if (!accountContext) return;
    let cancelled = false;
    let failures = 0;
    let timer = 0;
    const poll = async () => {
      if (document.visibilityState === "visible") {
        try {
          const page = parseReviewInbox(await api<unknown>("/api/review-inbox?limit=100"));
          if (!cancelled) {
            setReviewInbox(page);
            setReviewInboxStale(false);
          }
          failures = 0;
        } catch {
          if (!cancelled) setReviewInboxStale(true);
          failures = Math.min(failures + 1, 4);
        }
      }
      if (!cancelled) {
        const base = Math.min(60_000, 10_000 * 2 ** failures);
        timer = window.setTimeout(poll, base + Math.floor(Math.random() * 2_000));
      }
    };
    timer = window.setTimeout(poll, 10_000);
    return () => { cancelled = true; window.clearTimeout(timer); };
  }, [accountContext]);

  useEffect(() => {
    const inboxVisible =
      (activeWorkspace === "expenses" && expenseTab === "review") ||
      activeWorkspace === "agent" ||
      agentPanelOpen;
    if (!inboxVisible || !reviewInbox) return;
    const unread = reviewInbox.items
      .filter(
        (item) =>
          item.unread && reviewSeenRequests.current.get(item.public_id) !== item.updated_at,
      )
      .slice(0, 100);
    if (!unread.length) return;
    unread.forEach((item) => reviewSeenRequests.current.set(item.public_id, item.updated_at));
    setReviewInbox((current) => current ? {
      ...current,
      unread_count: Math.max(0, current.unread_count - unread.length),
      items: current.items.map((item) => unread.some((row) => row.public_id === item.public_id)
        ? { ...item, unread: false }
        : item),
    } : current);
    void Promise.allSettled(
      unread.map((item) => api(`/api/review-inbox/${item.public_id}/seen`, { method: "POST" })),
    ).then((results) => {
      results.forEach((result, index) => {
        if (result.status === "rejected") {
          const item = unread[index];
          if (reviewSeenRequests.current.get(item.public_id) === item.updated_at) {
            reviewSeenRequests.current.delete(item.public_id);
          }
        }
      });
    });
  }, [activeWorkspace, agentPanelOpen, expenseTab, reviewInbox]);

  useEffect(() => {
    if (!accountContext || activeWorkspace !== "expenses" || expenseTab !== "activity") return;
    setFinancialActivityLoading(true);
    setFinancialActivityError("");
    void api<FinancialActivityPage>("/api/insights/financial-activity?limit=200")
      .then(setFinancialActivity)
      .catch((error) =>
        setFinancialActivityError(apiErrorMessage(error, "Activity could not be loaded.")),
      )
      .finally(() => setFinancialActivityLoading(false));
  }, [accountContext, activeWorkspace, expenseTab, financialActivityReload]);

  useEffect(() => {
    const onPopState = () => {
      const params = new URLSearchParams(window.location.search);
      const workspace = params.get("workspace");
      const nextWorkspace: WorkspaceView =
        workspace === "household" ||
          workspace === "promotions" ||
          workspace === "settings" ||
          (workspace === "agent" && agentEnabled)
          ? workspace
          : "expenses";
      const tab = params.get("tab");
      const nextTab =
        tab === "insights" ||
        tab === "activity" ||
        (tab === "attention" && proactiveAttentionEnabled)
          ? tab
          : "review";
      if (
        nextWorkspace !== "agent"
        && (nextWorkspace !== activeWorkspace || nextTab !== expenseTab)
      ) {
        setAgentNavigationRequest(null);
        setFocusedTransactionId(null);
        setAgentSourceContext(baseContextForWorkspace(nextWorkspace, nextTab));
      }
      setActiveWorkspace(nextWorkspace);
      setExpenseTab(nextTab);
    };
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, [activeWorkspace, agentEnabled, expenseTab, proactiveAttentionEnabled]);

  useEffect(() => {
    if (!accountContext) return;
    const params = new URLSearchParams(window.location.search);
    const invitation = params.get("invite");
    if (invitation) {
      setOnboardingNotice(null);
      void api("/api/workspaces/invitations/accept", {
        method: "POST",
        body: JSON.stringify({ token: invitation }),
      }).then(() => {
        params.delete("invite");
        params.set("workspace", "settings");
        window.location.replace(`/?${params.toString()}&joined=1`);
      }).catch((error) => {
        const detail = error instanceof ApiError ? error.detail : typeof error === "object" && error && "detail" in error && typeof error.detail === "string" ? error.detail : "The invitation could not be accepted.";
        const wrongAccount = detail.toLowerCase().includes("email does not match");
        if (!wrongAccount) params.delete("invite");
        params.set("workspace", "settings");
        window.history.replaceState({}, "", `/?${params.toString()}`);
        setActiveWorkspace("settings");
        setOnboardingNotice({ tone: "error", text: wrongAccount ? `${detail} Sign out and continue with the invited Google account.` : `${detail} Ask the workspace owner for a new link.`, action: wrongAccount ? "switch-account" : undefined });
      });
      return;
    }
    if (params.get("connect") === "plaid") {
      params.delete("connect");
      window.history.replaceState({}, "", `/?${params.toString()}`);
      void openPlaidLink();
    }
    if (params.get("joined") === "1") {
      params.delete("joined");
      window.history.replaceState({}, "", `/?${params.toString()}`);
      setOnboardingNotice({ tone: "success", text: "Invitation accepted. You are now in the shared workspace." });
    }
  }, [accountContext]);

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

  async function runTransactionAction<T>({
    id,
    label,
    success,
    action,
    reload = true,
  }: {
    id: number;
    label: string;
    success?: string | ((data: T) => string);
    action: () => Promise<T>;
    reload?: boolean;
  }) {
    if (pendingTransactionActions.current.has(id)) return;
    pendingTransactionActions.current.add(id);
    setTransactionActionById((current) => ({ ...current, [id]: label }));
    setTransactionNoticeById((current) => {
      const next = { ...current };
      delete next[id];
      return next;
    });
    try {
      const data = await action();
      setLog(data);
      if (success) {
        setReviewNotice({
          tone: "success",
          text: typeof success === "function" ? success(data) : success,
        });
      }
      if (reload) await refreshReviewData();
    } catch (error) {
      setLog(error);
      const correlationId = error instanceof ApiError ? error.correlationId : undefined;
      setTransactionNoticeById((current) => ({
        ...current,
        [id]: {
          tone: "error",
          text: apiErrorMessage(error, "This action could not be completed. Your transaction is still available."),
          correlationId,
        },
      }));
    } finally {
      pendingTransactionActions.current.delete(id);
      setTransactionActionById((current) => {
        const next = { ...current };
        delete next[id];
        return next;
      });
    }
  }

  async function loadTransactions() {
    await run(
      "transactions",
      async () => {
        const data = await loadReviewTransactionsData();
        setTransactions(data);
        setAllTransactions((current) => mergeTransactions(current, data));
        return { loaded_transactions: data.length };
      },
      false,
    );
  }

  async function loadReviewTransactionsData() {
    const groups = await Promise.all(
      ["ask_user", "shared_draft"].map((status) =>
        api<Transaction[]>(`/transactions?status=${status}`),
      ),
    );
    return Array.from(
      new Map(groups.flat().map((transaction) => [transaction.id, transaction])).values(),
    ).sort((left, right) => right.updated_at.localeCompare(left.updated_at));
  }

  async function loadReviewInbox() {
    try {
      const page = parseReviewInbox(await api<unknown>("/api/review-inbox?limit=100"));
      setReviewInbox(page);
      setReviewInboxStale(false);
    } catch {
      setReviewInboxStale(true);
    }
  }

  async function refreshReviewData() {
    await Promise.all([loadTransactions(), loadRecoveryTransactions(), loadReviewInbox()]);
    await loadRecentActivity();
  }

  async function loadRecoveryTransactions() {
    const statuses = ["error", "post_ambiguous", "undo_ambiguous", "reconciliation_required", "posting", "undoing"];
    const groups = await Promise.all(
      statuses.map((status) => api<Transaction[]>(`/transactions?status=${status}&limit=50`)),
    );
    const data = groups.flat().sort((a, b) => b.updated_at.localeCompare(a.updated_at));
    setRecoveryTransactions(data);
    setAllTransactions((current) => mergeTransactions(current, data));
  }

  async function refreshDashboardQuietly() {
    try {
      const [pending, recent] = await Promise.all([
        loadReviewTransactionsData(),
        loadRecentActivityData(),
      ]);
      setTransactions(pending);
      setRecentTransactions(recent);
      setAllTransactions((current) => mergeTransactions(current, [...pending, ...recent]));
      setAiMemories(await api<AIMemory[]>("/ai/memory"));
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

  async function loadAIMemories() {
    try {
      const [memories, settings, metrics] = await Promise.all([
        api<AIMemory[]>("/ai/memory"),
        api<StructuredMemorySettings>("/ai/memory/settings"),
        api<StructuredMemoryMetrics>("/ai/memory/metrics"),
      ]);
      setAiMemories(memories);
      setMemorySettings(settings);
      setMemoryMetrics(metrics);
    } catch {
      setAiMemories([]);
    }
  }

  async function setMemoryLearning(enabled: boolean) {
    const settings = await api<StructuredMemorySettings>("/ai/memory/settings", {
      method: "PATCH",
      body: JSON.stringify({ transaction_learning_enabled: enabled }),
    });
    setMemorySettings(settings);
  }

  async function createMemoryPreference(payload: {
    merchant: string;
    preference: "personal" | "shared";
    participant_names: string[];
    group_name: string | null;
  }) {
    await api<AIMemory>("/ai/memory/preferences", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    await loadAIMemories();
  }

  async function updateMemoryPreference(
    id: number,
    payload: {
      preference: "personal" | "shared";
      participant_names: string[];
      group_name: string | null;
    },
  ) {
    await api<AIMemory>(`/ai/memory/${id}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    });
    await loadAIMemories();
  }

  async function sendMemoryFeedback(id: number, outcome: "accepted" | "rejected") {
    await api(`/ai/memory/${id}/feedback`, {
      method: "POST",
      body: JSON.stringify({ outcome }),
    });
    await loadAIMemories();
  }

  async function deleteAIMemory(id: number) {
    await run(
      `delete-ai-memory-${id}`,
      async () => {
        await api(`/ai/memory/${id}`, { method: "DELETE" });
        await loadAIMemories();
        return { deleted_ai_memory: id };
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
    setBusy("sync"); setSyncError(false);
    try {
      const value = await api<Record<string, unknown>>("/plaid/sync", { method: "POST", body: "{}" });
      setLog(value); setLastSyncedAt(new Date()); await refreshReviewData();
    } catch (error) { setLog(error); setSyncError(true); }
    finally { setBusy(null); }
  }

  function changeExpenseTab(tab: ExpenseTab) {
    if (tab === "attention" && !proactiveAttentionEnabled) return;
    if (activeWorkspace === "expenses" && expenseTab === tab) return;
    const params = new URLSearchParams(window.location.search); params.set("workspace", "expenses"); params.set("tab", tab);
    window.history.pushState({}, "", `/?${params}`);
    setAgentNavigationRequest(null);
    setFocusedTransactionId(null);
    setAgentSourceContext(baseContextForWorkspace("expenses", tab));
    setActiveWorkspace("expenses");
    setExpenseTab(tab);
  }

  function changeWorkspace(workspace: WorkspaceView) {
    if (workspace === "agent" && !agentEnabled) return;
    if (workspace === activeWorkspace) return;
    const params = new URLSearchParams(window.location.search); params.set("workspace", workspace);
    if (workspace !== "expenses") params.delete("tab");
    window.history.pushState({}, "", `/?${params}`);
    if (workspace !== "agent") {
      setAgentNavigationRequest(null);
      setFocusedTransactionId(null);
      setAgentSourceContext(baseContextForWorkspace(workspace, expenseTab));
    }
    setActiveWorkspace(workspace);
  }

  function openAgentPanel() {
    if (!agentEnabled) return;
    setAgentPanelOpen(true);
  }

  function closeAgentPanel() {
    setAgentPanelOpen(false);
    window.requestAnimationFrame(() => agentLauncherRef.current?.focus());
  }

  function navigateFromAgent(request: AgentNavigationRequest) {
    if (!isAgentNavigationRequest(request)) return;
    setAgentNavigationRequest(request);

    if (request.target_surface === "home") {
      const params = new URLSearchParams(window.location.search);
      params.set("workspace", "expenses");
      params.set("tab", "review");
      window.history.pushState({}, "", `/?${params}`);
      setFocusedTransactionId(null);
      setAgentSourceContext(baseContextForWorkspace("expenses", "review"));
      setExpenseTab("review");
      setActiveWorkspace("expenses");
      return;
    }

    const expenseTabs = {
      expense_review: "review",
      expense_insights: "insights",
      expense_activity: "activity",
    } as const;
    if (request.target_surface in expenseTabs) {
      const tab = expenseTabs[request.target_surface as keyof typeof expenseTabs];
      const transactionId = request.entity?.kind === "transaction"
        ? Number(request.entity.public_id)
        : null;
      const params = new URLSearchParams(window.location.search);
      params.set("workspace", "expenses");
      params.set("tab", tab);
      window.history.pushState({}, "", `/?${params}`);
      setFocusedTransactionId(transactionId);
      setAgentSourceContext(
        tab === "activity"
          ? buildExpenseActivityContext(transactionId ? { publicId: transactionId } : null)
          : baseContextForWorkspace("expenses", tab),
      );
      setExpenseTab(tab);
      setActiveWorkspace("expenses");
      return;
    }

    const workspaceBySurface = {
      deals: "promotions",
      household_today: "household",
      household_errands: "household",
      household_receipts: "household",
      household_staples: "household",
      household_history: "household",
      settings: "settings",
      integrations: "settings",
    } as const;
    const workspace = workspaceBySurface[
      request.target_surface as keyof typeof workspaceBySurface
    ];
    if (!workspace) return;
    const params = new URLSearchParams(window.location.search);
    params.set("workspace", workspace);
    params.delete("tab");
    window.history.pushState({}, "", `/?${params}`);
    setAgentSourceContext(baseAgentContext(request.target_surface));
    setActiveWorkspace(workspace);
  }

  async function markPersonal(id: number) {
    await runTransactionAction({
      id,
      label: "Saving as personal…",
      success: "Transaction saved as personal.",
      action: () => api(`/transactions/${id}/personal`, { method: "POST", body: "{}" }),
    });
  }

  async function undoTransaction(id: number) {
    await runTransactionAction({
      id,
      label: "Moving back to review…",
      success: "Transaction moved back to the review queue.",
      action: () => api(`/transactions/${id}/undo`, { method: "POST", body: "{}" }),
    });
  }

  async function retryFinancialOperation(id: number) {
    await runTransactionAction({
      id,
      label: "Reconciling with Splitwise…",
      success: (data: TransactionActionResponse) => data.message,
      action: () => api<TransactionActionResponse>(`/transactions/${id}/recovery/retry`, { method: "POST", body: "{}" }),
    });
  }

  async function submitSplit(id: number, confirm: boolean) {
    const selectedGroup = selectedGroupByTx[id];
    const friends = selectedGroup
      ? selectedNonPayerGroupMembers(id)
      : selectedFriendsByTx[id] || [];
    await runTransactionAction({
      id,
      label: confirm ? "Posting split…" : "Saving draft…",
      success: (data: SplitResponse) =>
        confirm && splitResponseQueued(data)
          ? "Split queued securely. ExpenseOps will confirm it with Splitwise in the background."
          : confirm
            ? "Split posted to Splitwise."
            : "Split saved as a draft.",
      action: () =>
        api<SplitResponse>(`/transactions/${id}/split/equal`, {
          method: "POST",
          body: JSON.stringify({
            friend_user_ids: friends.map((friend) => friend.id),
            group_id: selectedGroup?.id ?? null,
            confirm,
          }),
        }),
    });
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

    await runTransactionAction({
      id: txId,
      label: confirm ? "Posting split…" : "Checking split…",
      success: (data: SplitResponse) =>
        confirm && splitResponseQueued(data)
          ? "Split queued securely. ExpenseOps will confirm it with Splitwise in the background."
          : confirm
            ? "Split posted to Splitwise."
            : "Split calculation checked.",
      action: () =>
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
    });
  }

  async function searchFriends(txId: number) {
    const query = friendQueriesByTx[txId] || "";
    await runTransactionAction({
      id: txId,
      label: "Searching friends…",
      reload: false,
      action: async () => {
        const friends = await api<Friend[]>(`/splitwise/friends?q=${encodeURIComponent(query)}`);
        setFriendResultsByTx((current) => ({ ...current, [txId]: friends.slice(0, 8) }));
        return { friend_results: friends.slice(0, 8) };
      },
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
    await runTransactionAction({
      id: txId,
      label: "Searching groups…",
      reload: false,
      action: async () => {
        const groups = await api<Group[]>(`/splitwise/groups?q=${encodeURIComponent(query)}`);
        setGroupResultsByTx((current) => ({ ...current, [txId]: groups.slice(0, 8) }));
        return { group_results: groups.slice(0, 8) };
      },
    });
  }

  async function selectGroup(txId: number, group: Group) {
    await runTransactionAction({
      id: txId,
      label: "Loading group…",
      reload: false,
      action: async () => {
        const members = await api<Friend[]>(`/splitwise/groups/${group.id}/members`);
        setSelectedGroupByTx((current) => ({ ...current, [txId]: group }));
        setGroupMembersByTx((current) => ({ ...current, [txId]: members }));
        setSelectedGroupMembersByTx((current) => ({ ...current, [txId]: [] }));
        return { selected_group: group, members };
      },
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

  const authView = authenticationView(authResolved, Boolean(accountContext));
  if (authView === "loading") return <AuthLoading />;
  if (authView === "sign-in" || !accountContext) return <SignInPage />;

  async function logout() {
    await fetch("/auth/logout", { method: "POST", credentials: "same-origin" });
    window.location.reload();
  }

  return (
    <AppShell
      className="pb-24 md:pb-5"
      navigation={
        <WorkspaceNavigation
          active={activeWorkspace}
          onChange={changeWorkspace}
          accountContext={accountContext}
          onLogout={logout}
          agentEnabled={agentEnabled}
          agentOpen={agentPanelOpen}
          agentLauncherRef={agentLauncherRef}
          onOpenAgent={openAgentPanel}
          reviewUnreadCount={reviewInbox?.unread_count || 0}
        />
      }
      mobileNavigation={
        <MobileNavigation
          active={activeWorkspace}
          onChange={changeWorkspace}
          agentEnabled={agentEnabled}
          reviewUnreadCount={reviewInbox?.unread_count || 0}
        />
      }
    >
      {activeWorkspace === "agent" && agentEnabled ? (
        <Suspense fallback={<AgentLoadingState />}>
          <AgentExperience
            mode="page"
            readOnly={accountContext?.features?.agent?.read_only ?? true}
            pageContext={effectiveAgentPageContext}
            contextLabel={agentSourceContext.label}
            onClearContext={clearAgentContext}
            onRestoreContext={restoreAgentContext}
            onNavigate={navigateFromAgent}
            reviewItems={reviewInbox?.items || []}
          />
        </Suspense>
      ) : (
        <div
          className={
            agentPanelOpen
              ? "min-w-0 lg:grid lg:grid-cols-[minmax(0,1fr)_minmax(22rem,25rem)] lg:gap-5"
              : "min-w-0"
          }
        >
          <div className="min-w-0 space-y-5">
        {onboardingNotice ? (
          <div role={onboardingNotice.tone === "error" ? "alert" : "status"} className={`flex items-start justify-between gap-3 rounded-xl border px-4 py-3 text-sm ${onboardingNotice.tone === "error" ? "border-rose-200 bg-rose-50 text-rose-800" : "border-emerald-200 bg-emerald-50 text-emerald-800"}`}>
            <span>{onboardingNotice.text}</span>
            {onboardingNotice.action === "switch-account" ? <Button size="sm" variant="outline" onClick={async () => { const returnPath = `${window.location.pathname}${window.location.search}`; await fetch("/auth/logout", { method: "POST", credentials: "same-origin" }); window.location.assign(`/auth/login?redirect_after=${encodeURIComponent(returnPath)}`); }}>Switch account</Button> : null}
            <button type="button" aria-label="Dismiss onboarding message" onClick={() => setOnboardingNotice(null)} className="rounded p-1 hover:bg-black/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500"><X className="h-4 w-4" /></button>
          </div>
        ) : null}

        {activeWorkspace === "settings" ? (
          <AccountSettingsPage
            context={accountContext}
            onAgentContextChange={publishAgentContext}
            agentNavigationRequest={agentNavigationRequest}
            splitwiseTools={<GroupManagementPanel currentUserId={currentSplitwiseUser?.id ?? null} />}
            learnedBehaviorTools={<div className="space-y-5"><AgentMemoryPanel friends={memory.friends} groups={memory.groups} onSelectFriend={selectMemoryFriend} onSelectGroup={selectMemoryGroup} loading={busy !== null && allTransactions.length === 0}/><AIFallbackMemoryPanel memories={aiMemories} settings={memorySettings} metrics={memoryMetrics} onSetLearning={setMemoryLearning} onCreate={createMemoryPreference} onUpdate={updateMemoryPreference} onFeedback={sendMemoryFeedback} onDelete={deleteAIMemory} loading={busy !== null && aiMemories.length === 0}/></div>}
          />
        ) : activeWorkspace === "household" ? (
          <HouseholdOpsPage
            onAgentContextChange={publishAgentContext}
            agentNavigationRequest={agentNavigationRequest}
          />
        ) : activeWorkspace === "promotions" ? (
          <PromotionsPage
            onAgentContextChange={publishAgentContext}
            agentNavigationRequest={agentNavigationRequest}
          />
        ) : (
          <>
        <Header active={expenseTab} onSync={syncTransactions} busy={busy} pendingCount={reviewInbox?.total_open ?? transactions.length} pendingTotal={pendingTotal} lastSyncLabel={lastSyncedAt ? relativeTime(lastSyncedAt) : lastSyncLabel} syncError={syncError} />
        <ExpenseTabs
          active={expenseTab}
          onChange={changeExpenseTab}
          showAttention={proactiveAttentionEnabled}
        />

        {expenseTab === "review" ? <div className="space-y-6">
        <ReviewFilters filters={filters} onChange={updateFilter} />
        {reviewInboxStale ? (
          <p role="status" className="rounded-control border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
            Review updates are temporarily unavailable. Showing the most recent results and retrying automatically.
          </p>
        ) : null}
        {reviewNotice ? (
          <div
            role={reviewNotice.tone === "error" ? "alert" : "status"}
            className={`flex items-start justify-between gap-3 rounded-control border px-4 py-3 text-sm ${reviewNotice.tone === "error" ? "border-rose-200 bg-rose-50 text-rose-800" : "border-emerald-200 bg-emerald-50 text-emerald-800"}`}
          >
            <span>{reviewNotice.text}</span>
            <button type="button" aria-label="Dismiss transaction message" onClick={() => setReviewNotice(null)} className="inline-flex size-11 shrink-0 items-center justify-center rounded-control hover:bg-black/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-600"><X aria-hidden="true" className="size-4" /></button>
          </div>
        ) : null}
        <div>
          <section className="space-y-4">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <div className="inline-flex items-center gap-2 text-xs font-semibold uppercase text-slate-500">
                  <WalletCards className="h-3.5 w-3.5 text-slate-500" />
                  Review queue
                </div>
                <h2 className="mt-1 text-xl font-semibold text-slate-950">Needs your attention</h2>
              </div>
            </div>

            <ReviewReceiptTasks items={receiptReviewItems} onNavigate={navigateFromAgent} />
            {pendingReviewTransactions.length ? (
              <div className="grid gap-4">
                {pendingReviewTransactions.map((transaction) => (
                  <TransactionCard
                    key={transaction.id}
                    transaction={transaction}
                    recommendation={reviewRecommendations.get(transaction.id) || null}
                    busy={transactionActionById[transaction.id] || null}
                    actionNotice={transactionNoticeById[transaction.id] || null}
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
                    agentFocused={focusedTransactionId === transaction.id}
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
                    onUseRecommended={() => {
                      const recommendation = reviewRecommendations.get(transaction.id);
                      setTransactionExpanded(transaction.id, true);
                      if (recommendation?.participant_names.length) {
                        setFriendQueriesByTx((current) => ({
                          ...current,
                          [transaction.id]: recommendation.participant_names.join(", "),
                        }));
                      }
                      if (recommendation?.group_name) {
                        setGroupQueriesByTx((current) => ({
                          ...current,
                          [transaction.id]: recommendation.group_name || "",
                        }));
                      }
                    }}
                    onAgentFocus={() =>
                      setFocusedTransactionId((current) =>
                        current === transaction.id ? null : transaction.id,
                      )
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
            ) : receiptReviewItems.length || reviewInboxStale ? null : (
              <EmptyState
                icon={CheckCircle2}
                title="You're all caught up"
                description={`No transactions need review. Last synced ${lastSyncedAt ? relativeTime(lastSyncedAt) : lastSyncLabel}.`}
              />
            )}
          </section>
          {recoveryTransactions.length ? <section className="mt-6 space-y-3" aria-labelledby="recovery-title"><div><div className="inline-flex items-center gap-2 text-xs font-semibold uppercase text-amber-700"><RotateCcw className="h-3.5 w-3.5" />Recovery</div><h2 id="recovery-title" className="mt-1 text-xl font-semibold text-slate-950">Financial actions needing attention</h2><p className="mt-1 text-sm text-slate-600">ExpenseOps keeps uncertain or failed Splitwise actions visible until their provider state is confirmed.</p></div>{recoveryTransactions.map((transaction) => <Card key={transaction.id} className="border-amber-200"><CardContent className="flex flex-col gap-4 p-4 sm:p-4 sm:flex-row sm:items-center sm:justify-between"><div className="min-w-0"><p className="font-semibold text-slate-950">{transaction.merchant_name || transaction.name} · {formatTransactionAmount(transaction)}</p><p className="mt-1 text-sm text-slate-700">{transaction.last_error || statusDisplay(transaction.status)}</p><p className="mt-1 text-xs text-slate-500">The transaction has not been hidden or marked successful.</p></div><div className="flex shrink-0 flex-wrap gap-2"><Button variant="outline" disabled={Boolean(transactionActionById[transaction.id])} onClick={() => retryFinancialOperation(transaction.id)}><RotateCcw className="h-4 w-4" />{transactionActionById[transaction.id] || "Reconcile and retry"}</Button>{transaction.status === "reconciliation_required" ? <Button variant="ghost" disabled={Boolean(transactionActionById[transaction.id])} onClick={() => undoTransaction(transaction.id)}>Remove old split &amp; review</Button> : null}</div>{transactionNoticeById[transaction.id] ? <p role="alert" className="text-sm text-rose-700">{transactionNoticeById[transaction.id].text}</p> : null}</CardContent></Card>)}</section> : null}
          </div>
          <section aria-labelledby="recently-handled-title"><div className="mb-3 flex items-center justify-between"><h2 id="recently-handled-title" className="text-lg font-semibold text-slate-950">Recently handled</h2><Button variant="ghost" size="sm" onClick={()=>changeExpenseTab("activity")}>View all activity</Button></div>
            <RecentActivity
              transactions={recentTransactions.slice(0, 5)}
              loading={busy === "recent" && recentTransactions.length === 0}
              actionById={transactionActionById}
              onUndo={undoTransaction}
            />
          </section>
        </div> : expenseTab === "insights" ? <InsightsDashboard onAgentContextChange={publishAgentContext}/> : expenseTab === "activity" ? <ActivityTimeline page={financialActivity} loading={financialActivityLoading} error={financialActivityError} onRetry={() => setFinancialActivityReload((value) => value + 1)} focusedTransactionId={focusedTransactionId} onAgentFocus={setFocusedTransactionId}/> : proactiveAttentionEnabled ? <Suspense fallback={<div className="ui-skeleton min-h-64 rounded-2xl" aria-label="Loading Attention Center" />}><AttentionCenterPage onNavigate={navigateFromAgent}/></Suspense> : null}
          </>
        )}
          </div>
          {agentPanelOpen && agentEnabled && wideAgentPanel ? (
            <aside
              id="expenseops-agent-panel"
              aria-label="ExpenseOps Agent"
              className="fixed inset-0 z-50 min-w-0 bg-slate-50 lg:static lg:z-auto lg:bg-transparent"
            >
              <Suspense fallback={<AgentLoadingState />}>
                <AgentExperience
                  mode="panel"
                  readOnly={accountContext?.features?.agent?.read_only ?? true}
                  pageContext={effectiveAgentPageContext}
                  contextLabel={agentSourceContext.label}
                  onClearContext={clearAgentContext}
                  onRestoreContext={restoreAgentContext}
                  onClose={closeAgentPanel}
                  onNavigate={navigateFromAgent}
                  reviewItems={reviewInbox?.items || []}
                />
              </Suspense>
            </aside>
          ) : null}
          {agentPanelOpen && agentEnabled && !wideAgentPanel ? (
            <AgentFullscreenDialog onClose={closeAgentPanel}>
              <Suspense fallback={<AgentLoadingState />}>
                <AgentExperience
                  mode="panel"
                  readOnly={accountContext?.features?.agent?.read_only ?? true}
                  pageContext={effectiveAgentPageContext}
                  contextLabel={agentSourceContext.label}
                  onClearContext={clearAgentContext}
                  onRestoreContext={restoreAgentContext}
                  onClose={closeAgentPanel}
                  onNavigate={navigateFromAgent}
                  reviewItems={reviewInbox?.items || []}
                />
              </Suspense>
            </AgentFullscreenDialog>
          ) : null}
        </div>
      )}
    </AppShell>
  );
}

function WorkspaceNavigation({
  active,
  onChange,
  accountContext,
  onLogout,
  agentEnabled,
  agentOpen,
  agentLauncherRef,
  onOpenAgent,
  reviewUnreadCount,
}: {
  active: WorkspaceView;
  onChange: (value: WorkspaceView) => void;
  accountContext: AccountContext;
  onLogout: () => void;
  agentEnabled: boolean;
  agentOpen: boolean;
  agentLauncherRef: RefObject<HTMLButtonElement>;
  onOpenAgent: () => void;
  reviewUnreadCount: number;
}) {
  return (
    <>
      <header className="hidden h-16 min-w-0 items-center justify-between rounded-card border border-ui-border bg-white px-3 shadow-card md:flex">
        <div className="flex min-w-0 items-center gap-5">
          <button
            type="button"
            onClick={() => onChange("expenses")}
            className="flex shrink-0 items-center gap-2 rounded-control px-2 py-2 text-sm font-semibold text-ink"
            aria-label="Go to Expenses"
          >
            <span className="flex h-8 w-8 items-center justify-center rounded-control bg-ui-primary text-white shadow-primary">
              <Split className="h-4 w-4" aria-hidden="true" />
            </span>
            ExpenseOps
          </button>
          <nav className="flex items-center gap-1" aria-label="Primary navigation">
            <DesktopNavItem active={active === "expenses"} label="Expenses" icon={WalletCards} badge={reviewUnreadCount} onClick={() => onChange("expenses")} />
            <DesktopNavItem active={active === "household"} label="Household" icon={House} onClick={() => onChange("household")} />
            <DesktopNavItem active={active === "promotions"} label="Deals" icon={Tags} onClick={() => onChange("promotions")} />
            {agentEnabled ? (
              <button
                ref={agentLauncherRef}
                type="button"
                onClick={onOpenAgent}
                aria-expanded={agentOpen}
                aria-controls="expenseops-agent-panel"
                className={`touch-target inline-flex items-center gap-2 rounded-control px-3 text-sm font-medium transition-colors duration-hover ${agentOpen ? "bg-ui-primary-tint text-ui-primary" : "text-ui-text hover:bg-slate-50 hover:text-ink"}`}
              >
                <Bot className="h-4 w-4" aria-hidden="true" /> Agent
              </button>
            ) : null}
          </nav>
        </div>
        <AccountMenu
          displayName={accountContext.user.display_name}
          email={accountContext.user.email}
          workspaceName={accountContext.workspace.name}
          avatarUrl={accountContext.user.avatar_url}
          actions={[
            { label: "Settings", icon: Settings2, onSelect: () => onChange("settings") },
            { label: "Sign out", destructive: true, onSelect: onLogout },
          ]}
        />
      </header>
      <header className="flex h-14 min-w-0 items-center justify-between rounded-card border border-ui-border bg-white px-2 shadow-card md:hidden">
        <button
          type="button"
          onClick={() => onChange("expenses")}
          className="touch-target flex items-center gap-2 rounded-control px-2 text-sm font-semibold text-ink"
          aria-label="Go to Expenses"
        >
          <span className="flex h-8 w-8 items-center justify-center rounded-control bg-ui-primary text-white">
            <Split className="h-4 w-4" aria-hidden="true" />
          </span>
          ExpenseOps
        </button>
        <AccountMenu
          compact
          displayName={accountContext.user.display_name}
          email={accountContext.user.email}
          workspaceName={accountContext.workspace.name}
          avatarUrl={accountContext.user.avatar_url}
          actions={[
            { label: "Settings", icon: Settings2, onSelect: () => onChange("settings") },
            { label: "Sign out", destructive: true, onSelect: onLogout },
          ]}
        />
      </header>
    </>
  );
}

function DesktopNavItem({ active, label, icon: Icon, badge = 0, onClick }: { active: boolean; label: string; icon: ComponentType<{ className?: string }>; badge?: number; onClick: () => void }) {
  return <button type="button" onClick={onClick} aria-current={active ? "page" : undefined} aria-label={badge ? `${label}, ${badge} unread review items` : label} className={`touch-target inline-flex items-center gap-2 rounded-control px-3 text-sm font-medium transition-colors duration-hover ${active ? "bg-ui-primary-tint text-ui-primary" : "text-ui-text hover:bg-slate-50 hover:text-ink"}`}><Icon className="h-4 w-4" aria-hidden="true" />{label}{badge ? <Badge className="min-w-5 justify-center bg-rose-600 px-1.5 text-white">{badge > 99 ? "99+" : badge}</Badge> : null}</button>;
}

function MobileNavigation({
  active,
  onChange,
  agentEnabled,
  reviewUnreadCount,
}: {
  active: WorkspaceView;
  onChange: (value: WorkspaceView) => void;
  agentEnabled: boolean;
  reviewUnreadCount: number;
}) {
  const items = [
    { value: "expenses" as const, label: "Expenses", icon: WalletCards },
    { value: "household" as const, label: "Household", icon: House },
    { value: "promotions" as const, label: "Deals", icon: Tags },
    ...(agentEnabled ? [{ value: "agent" as const, label: "Agent", icon: Bot }] : []),
  ];
  return <nav className={`fixed inset-x-3 bottom-3 z-40 grid ${agentEnabled ? "grid-cols-4" : "grid-cols-3"} rounded-card border border-ui-border bg-white/95 p-1.5 shadow-primary backdrop-blur md:hidden`} aria-label="Primary mobile navigation">{items.map(({ value, label, icon: Icon }) => { const badge = value === "expenses" ? reviewUnreadCount : 0; return <button key={value} type="button" onClick={() => onChange(value)} aria-current={active === value ? "page" : undefined} aria-label={badge ? `${label}, ${badge} unread review items` : label} className={`relative flex min-h-14 flex-col items-center justify-center gap-1 rounded-control text-xs font-semibold transition-colors duration-hover ${active === value ? "bg-ui-primary text-white" : "text-ui-text hover:bg-slate-50 hover:text-ink"}`}><Icon className="h-5 w-5" aria-hidden="true" />{label}{badge ? <span className="absolute right-2 top-1.5 min-w-5 rounded-full bg-rose-600 px-1 text-[10px] leading-5 text-white">{badge > 99 ? "99+" : badge}</span> : null}</button>; })}</nav>;
}

function AgentLoadingState() {
  return (
    <div
      className="grid min-h-[32rem] place-items-center rounded-card border border-ui-border bg-white"
      role="status"
      aria-label="Loading ExpenseOps Agent"
    >
      <div className="space-y-3 text-center">
        <div className="ui-skeleton mx-auto size-12 rounded-2xl" />
        <div className="ui-skeleton h-4 w-40" />
      </div>
    </div>
  );
}

function AgentFullscreenDialog({
  children,
  onClose,
}: {
  children: ReactNode;
  onClose: () => void;
}) {
  return (
    <Dialog.Root open onOpenChange={(open) => { if (!open) onClose(); }}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-[44] bg-slate-950/45" />
        <Dialog.Content className="fixed inset-0 z-[45] overflow-hidden bg-slate-50 focus:outline-none">
          <Dialog.Title className="sr-only">ExpenseOps Agent</Dialog.Title>
          <Dialog.Description className="sr-only">
            A read-only assistant for grounded spending insights and transaction search.
          </Dialog.Description>
          {children}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

function baseContextForWorkspace(
  workspace: WorkspaceView,
  expenseTab: ExpenseTab,
): AgentContextDescriptor {
  if (workspace === "expenses") {
    const surface = {
      review: "expense_review",
      insights: "expense_insights",
      activity: "expense_activity",
      attention: "expense_review",
    } as const;
    return baseAgentContext(surface[expenseTab]);
  }
  if (workspace === "household") return baseAgentContext("household_today");
  if (workspace === "promotions") return baseAgentContext("deals");
  if (workspace === "settings") return baseAgentContext("settings");
  return baseAgentContext("home");
}

function AuthLoading() {
  return <main className="grid min-h-screen place-items-center bg-slate-50"><div className="ui-skeleton h-12 w-48" aria-label="Loading ExpenseOps" /></main>;
}

function SignInPage() {
  const returnPath = `${window.location.pathname}${window.location.search}${window.location.hash}`;
  const loginUrl = `/auth/login?redirect_after=${encodeURIComponent(returnPath)}`;
  const invitation = new URLSearchParams(window.location.search).has("invite");
  return <main className="grid min-h-screen place-items-center bg-[radial-gradient(circle_at_top,rgba(79,70,229,0.12),transparent_32rem)] p-5"><Card className="w-full max-w-md text-center"><CardHeader><div className="mx-auto flex h-12 w-12 items-center justify-center rounded-xl bg-indigo-600 text-white"><Split className="h-6 w-6" /></div><CardTitle className="mt-3 text-2xl">{invitation ? "Join your ExpenseOps workspace" : "ExpenseOps"}</CardTitle><CardDescription>{invitation ? "Sign in with the email address that received this invitation. You will return here to join the workspace." : "Sign in to your private expense and household workspace."}</CardDescription></CardHeader><CardContent><Button className="w-full" onClick={() => window.location.assign(loginUrl)}><UserCheck className="h-4 w-4" />{invitation ? "Sign in to accept invitation" : "Sign in"}</Button></CardContent></Card></main>;
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

function statusDisplay(status: string) {
  const labels: Record<string, string> = {
    ask_user: "Needs review",
    personal: "Personal",
    posted: "Posted",
    shared_draft: "Draft split",
    reconciliation_required: "Reconciliation required",
    removed: "Removed",
  };
  return labels[status] || status.replace(/_/g, " ");
}

function formatCurrency(value: number, currency = "USD") {
  try {
    return new Intl.NumberFormat("en-US", { style: "currency", currency }).format(value);
  } catch {
    return `${currency} ${value.toFixed(2)}`;
  }
}

function formatTransactionAmount(transaction: Pick<Transaction, "amount_cents" | "amount"> & Partial<Pick<Transaction, "iso_currency_code">>) {
  const amount = Number(transaction.amount);
  const currency = transaction.iso_currency_code || "USD";
  if (Number.isFinite(amount)) return formatCurrency(amount, currency);
  return formatCurrency(Math.abs(transaction.amount_cents) / 100, currency);
}

function ReviewFilters({
  filters,
  onChange,
}: {
  filters: DashboardFilters;
  onChange: <K extends keyof DashboardFilters>(key: K, value: DashboardFilters[K]) => void;
}) {
  const active = [
    filters.merchant ? { key: "merchant" as const, label: `Merchant: ${filters.merchant}` } : null,
    filters.dateFrom ? { key: "dateFrom" as const, label: `From: ${filters.dateFrom}` } : null,
    filters.dateTo ? { key: "dateTo" as const, label: `To: ${filters.dateTo}` } : null,
  ].filter((value): value is { key: "merchant" | "dateFrom" | "dateTo"; label: string } => Boolean(value));

  const fields = (
    <div className="grid gap-4">
      <label className="grid gap-1.5 text-sm font-medium text-ui-text">
        Merchant
        <div className="relative">
          <Search className="pointer-events-none absolute left-3 top-3.5 h-4 w-4 text-ui-muted" aria-hidden="true" />
          <Input className="pl-9" value={filters.merchant} onChange={(event) => onChange("merchant", event.target.value)} placeholder="Search merchant" />
        </div>
      </label>
      <div className="grid gap-3 sm:grid-cols-2">
        <label className="grid gap-1.5 text-sm font-medium text-ui-text">From<Input type="date" value={filters.dateFrom} onChange={(event) => onChange("dateFrom", event.target.value)} /></label>
        <label className="grid gap-1.5 text-sm font-medium text-ui-text">To<Input type="date" value={filters.dateTo} onChange={(event) => onChange("dateTo", event.target.value)} /></label>
      </div>
    </div>
  );

  return (
    <FilterToolbar
      activeFilters={active.length ? active.map((filter) => <button key={filter.key} type="button" onClick={() => onChange(filter.key, "")} className="inline-flex min-h-11 items-center gap-1.5 rounded-full border border-indigo-200 bg-ui-primary-tint px-3 text-xs font-medium text-indigo-800 hover:bg-indigo-100" aria-label={`Remove ${filter.label} filter`}>{filter.label}<X className="h-3.5 w-3.5" aria-hidden="true" /></button>) : undefined}
    >
      <div className="min-w-0 flex-1 px-2">
        <p className="text-sm font-semibold text-ink">Review queue</p>
        <p className="text-xs text-ui-muted">Only transactions that need a decision appear here.</p>
      </div>
      <ResponsiveSheet
        title="Filter review queue"
        description="Narrow this queue without changing what needs review."
        trigger={<Button variant="outline"><SlidersHorizontal className="h-4 w-4" aria-hidden="true" />Filters{active.length ? <Badge variant="secondary">{active.length}</Badge> : null}</Button>}
        footer={active.length ? <Button variant="outline" className="w-full" onClick={() => { onChange("merchant", ""); onChange("dateFrom", ""); onChange("dateTo", ""); }}>Clear filters</Button> : undefined}
      >
        {fields}
      </ResponsiveSheet>
    </FilterToolbar>
  );
}

function ReviewReceiptTasks({
  items,
  onNavigate,
}: {
  items: ReviewItem[];
  onNavigate: (request: AgentNavigationRequest) => void;
}) {
  if (!items.length) return null;
  return (
    <div className="grid gap-3" aria-label="Receipt decisions">
      {items.map((item) => {
        const receipt = item.receipt;
        if (!receipt) return null;
        const needsMatch = item.kind === "receipt_match_needed";
        return (
          <Card key={item.public_id} className="border-amber-200 bg-amber-50/50">
            <CardContent className="flex flex-col gap-3 p-4 sm:flex-row sm:items-center sm:justify-between">
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-2">
                  <Badge variant={needsMatch ? "warning" : "secondary"}>
                    {needsMatch ? "Receipt match needed" : "Itemized split ready"}
                  </Badge>
                  {item.unread ? <span className="text-xs font-semibold text-rose-700">New</span> : null}
                </div>
                <p className="mt-2 truncate font-semibold text-ink">
                  {receipt.merchant_name || "Receipt"}
                </p>
                <p className="mt-1 text-sm text-slate-600">
                  {receipt.total_cents == null ? "Total unavailable" : formatCurrency(receipt.total_cents / 100, receipt.currency)}
                  {` · ${receipt.line_count} item${receipt.line_count === 1 ? "" : "s"}`}
                </p>
                <p className="mt-1 text-xs text-slate-500">
                  {needsMatch
                    ? "Choose the matching purchase before any itemized split can be prepared."
                    : "Review item assignments; nothing is posted until you explicitly confirm."}
                </p>
              </div>
              <Button
                type="button"
                className="min-h-11 shrink-0"
                onClick={() => onNavigate({
                  target_surface: "household_receipts",
                  entity: { kind: "receipt", public_id: String(receipt.id) },
                })}
              >
                {needsMatch ? "Match receipt" : "Review itemized split"}
              </Button>
            </CardContent>
          </Card>
        );
      })}
    </div>
  );
}

function AnalyticsDashboard({
  analytics,
  days,
  onDaysChange,
  loading,
}: {
  analytics: ReturnType<typeof analyticsForTransactions>;
  days: number;
  onDaysChange: (days: number) => void;
  loading: boolean;
}) {
  const ratioTotal = analytics.personalCount + analytics.sharedCount;
  const hasRatioData = ratioTotal > 0;
  const sharedPercent = hasRatioData
    ? Math.round((analytics.sharedCount / ratioTotal) * 100)
    : 0;
  const personalPercent = hasRatioData ? 100 - sharedPercent : 0;

  return (
    <section className="grid gap-4 xl:grid-cols-[320px_minmax(0,1fr)]">
      <Card className="overflow-hidden">
        <CardHeader className="pb-4">
          <div className="flex items-center justify-between gap-3">
            <div>
              <CardTitle className="text-base">Spend intelligence</CardTitle>
              <CardDescription>Shared spend and review mix</CardDescription>
            </div>
            <select
              className="h-9 rounded-lg border border-slate-300 bg-white px-2.5 text-xs font-medium text-slate-700 transition hover:border-slate-400 focus:border-indigo-500 focus:ring-2 focus:ring-indigo-500/20"
              aria-label="Analytics time range"
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
          <div className="rounded-xl border border-slate-200 bg-slate-50/80 p-4">
            <p className="text-xs font-semibold uppercase tracking-wide text-slate-600">
              Total shared spend
            </p>
            {loading ? (
              <div className="ui-skeleton mt-2 h-9 w-36" />
            ) : (
              <p className="mt-1 text-3xl font-semibold tracking-tight text-slate-950">
                {formatCurrency(analytics.totalSharedSpend / 100)}
              </p>
            )}
            <p className="mt-2 text-xs leading-5 text-slate-600">
              {analytics.totalSharedSpend
                ? "Posted and draft splits in this window"
                : "Shared spending will appear after your first posted or draft split."}
            </p>
          </div>
          <div>
            <div className="mb-2 flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-slate-600">
              <PieChart className="h-3.5 w-3.5" />
              Personal vs shared
            </div>
            {loading ? (
              <div className="ui-skeleton h-2 w-full rounded-full" />
            ) : hasRatioData ? (
              <>
                <div className="flex h-2 overflow-hidden rounded-full bg-slate-200">
                  <div className="h-full bg-slate-400" style={{ width: `${personalPercent}%` }} />
                  <div className="h-full bg-indigo-600" style={{ width: `${sharedPercent}%` }} />
                </div>
                <div className="mt-2 flex items-center justify-between text-xs text-slate-600">
                  <span>{analytics.personalCount} personal</span>
                  <span>{sharedPercent}% shared</span>
                </div>
              </>
            ) : (
              <div className="rounded-lg border border-dashed border-slate-300 bg-slate-50/70 px-3 py-3 text-center text-xs text-slate-600">
                No classification data yet
              </div>
            )}
          </div>
        </CardContent>
      </Card>
      <Card>
        <CardHeader className="pb-4">
          <div className="flex items-center gap-2">
            <BarChart3 className="h-4 w-4 text-slate-600" />
            <CardTitle className="text-base">Top patterns</CardTitle>
          </div>
        </CardHeader>
        <CardContent className="grid gap-3 md:grid-cols-3">
          <MiniBarList title="Merchants" items={analytics.topMerchants} loading={loading} />
          <MiniBarList title="Split partners" items={analytics.topPartners} loading={loading} />
          <MiniBarList title="Groups" items={analytics.topGroups} loading={loading} />
        </CardContent>
      </Card>
    </section>
  );
}

function MiniBarList({
  title,
  items,
  loading,
}: {
  title: string;
  items: MemoryEntry[];
  loading: boolean;
}) {
  const max = Math.max(1, ...items.map((item) => item.count));
  return (
    <div className="min-h-32 rounded-xl border border-slate-200 bg-slate-50/60 p-3.5">
      <p className="mb-3 text-sm font-semibold text-slate-800">{title}</p>
      {loading ? (
        <SkeletonRows rows={3} />
      ) : items.length ? (
        <div className="space-y-2.5">
        {items.map((item) => (
          <div key={item.id}>
            <div className="mb-1 flex justify-between gap-2 text-xs text-slate-600">
              <span className="truncate">{item.name}</span>
              <span>{item.count}</span>
            </div>
            <div className="h-1.5 overflow-hidden rounded-full bg-slate-100">
              <div
                className="h-full bg-indigo-600"
                style={{ width: `${Math.max(12, (item.count / max) * 100)}%` }}
              />
            </div>
          </div>
        ))}
        </div>
      ) : (
        <PanelEmptyState icon={BarChart3} title="No pattern data yet" compact />
      )}
    </div>
  );
}

function ActivityTimeline({
  page,
  loading,
  error,
  onRetry,
  focusedTransactionId,
  onAgentFocus,
}: {
  page: FinancialActivityPage | null;
  loading: boolean;
  error: string;
  onRetry: () => void;
  focusedTransactionId: number | null;
  onAgentFocus: (transactionId: number | null) => void;
}) {
  const events = page?.events || [];
  return (
    <Card className="overflow-hidden">
      <CardHeader className="flex-row items-start justify-between gap-3 pb-4">
        <div>
          <div className="flex items-center gap-2">
            <Activity className="h-4 w-4 text-slate-600" />
            <CardTitle>Financial activity</CardTitle>
          </div>
          <CardDescription>
            Immutable decisions, provider attempts, and recovery outcomes
          </CardDescription>
        </div>
        {page ? (
          <Badge className="bg-slate-100 text-slate-700">
            {events.length} of {page.total}
          </Badge>
        ) : null}
      </CardHeader>
      <CardContent className="max-h-[620px] space-y-1 overflow-auto pr-3">
        {error && !page ? (
          <div
            role="alert"
            className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-900"
          >
            <span>{error}</span>
            <Button variant="outline" size="sm" onClick={onRetry}>
              <RefreshCw className="h-4 w-4" /> Retry
            </Button>
          </div>
        ) : loading && !page ? (
          <SkeletonRows rows={5} />
        ) : events.length ? (
          events.map((event) => {
            const style = financialActivityStyle(event);
            const Icon = style.icon;
            return (
              <div
                key={event.id}
                className={`group flex w-full gap-3 rounded-lg border px-2 py-2 text-left transition hover:border-slate-200 hover:bg-slate-50 ${
                  event.transaction_id === focusedTransactionId
                    ? "border-indigo-300 bg-indigo-50/60 ring-1 ring-indigo-200"
                    : "border-transparent"
                }`}
              >
                <span className="relative mt-1 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-white shadow-sm ring-1 ring-slate-200">
                  <span
                    className={`absolute -left-1 top-3 h-2 w-2 rounded-full ${style.dot}`}
                  />
                  <Icon className="h-3.5 w-3.5 text-slate-600" />
                </span>
                <span className="min-w-0 flex-1 border-b border-slate-100 pb-3">
                  <span className="flex flex-wrap items-center justify-between gap-2">
                    <span className="truncate text-sm font-medium text-slate-900">
                      {event.merchant_name || financialActionLabel(event.action)}
                    </span>
                    <Badge className={style.badge}>{event.outcome}</Badge>
                  </span>
                  <span className="mt-1 block text-sm text-slate-700">
                    {financialActionLabel(event.action)}
                    {event.amount_cents !== null
                      ? ` · ${formatFinancialActivityAmount(event)}`
                      : ""}
                  </span>
                  <span className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-slate-500">
                    <span>{event.actor_display_name}</span>
                    <span aria-hidden="true">·</span>
                    <span className="capitalize">{event.channel}</span>
                    {event.attempt ? (
                      <>
                        <span aria-hidden="true">·</span>
                        <span>Attempt {event.attempt}</span>
                      </>
                    ) : null}
                  </span>
                  <span className="mt-1 flex items-center gap-1 text-xs text-slate-500">
                    <CalendarDays className="h-3 w-3" />
                    {new Date(event.created_at).toLocaleString()}
                  </span>
                  {event.correlation_id ? (
                    <span className="mt-1 block truncate text-xs text-slate-500">
                      Support ID: {event.correlation_id}
                    </span>
                  ) : null}
                  {event.transaction_id ? (
                    <button
                      type="button"
                      className="mt-2 inline-flex min-h-11 items-center gap-2 rounded-control px-3 text-xs font-semibold text-indigo-700 hover:bg-indigo-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-600"
                      onClick={() => onAgentFocus(
                        focusedTransactionId === event.transaction_id ? null : event.transaction_id,
                      )}
                      aria-pressed={focusedTransactionId === event.transaction_id}
                      aria-label={`Use ${event.merchant_name || `transaction ${event.transaction_id}`} as Agent context`}
                      data-testid={`agent-context-activity-transaction-${event.id}`}
                    >
                      <Bot className="size-4" aria-hidden="true" />
                      {focusedTransactionId === event.transaction_id ? "In Agent context" : "Ask about this"}
                    </button>
                  ) : null}
                </span>
              </div>
            );
          })
        ) : (
          <PanelEmptyState
            icon={Activity}
            title="No financial activity yet"
            description="Decisions and provider attempts will appear here as an immutable history."
          />
        )}
      </CardContent>
    </Card>
  );
}

function financialActivityStyle(event: FinancialActivityEvent): {
  badge: string;
  dot: string;
  icon: ComponentType<{ className?: string }>;
} {
  if (event.outcome === "failed") {
    return { badge: "bg-rose-50 text-rose-700", dot: "bg-rose-500", icon: AlertCircle };
  }
  if (event.outcome === "ambiguous") {
    return { badge: "bg-amber-50 text-amber-800", dot: "bg-amber-500", icon: RotateCcw };
  }
  if (event.action.includes("delete") || event.action === "return_to_review") {
    return { badge: "bg-slate-100 text-slate-700", dot: "bg-slate-400", icon: RotateCcw };
  }
  return {
    badge: "bg-emerald-50 text-emerald-700",
    dot: "bg-emerald-500",
    icon: CheckCircle2,
  };
}

function financialActionLabel(action: string) {
  const labels: Record<string, string> = {
    mark_personal: "Marked personal",
    save_draft: "Saved split draft",
    return_to_review: "Returned to review",
    remove: "Removed transaction",
    splitwise_create: "Posted to Splitwise",
    splitwise_update: "Reconciled Splitwise amount",
    splitwise_delete: "Removed Splitwise expense",
    splitwise_delete_removed: "Removed superseded Splitwise expense",
  };
  return labels[action] || action.replace(/_/g, " ");
}

function formatFinancialActivityAmount(event: FinancialActivityEvent) {
  return new Intl.NumberFormat(undefined, {
    style: "currency",
    currency: event.currency_code || "USD",
  }).format((event.amount_cents || 0) / 100);
}

function AgentMemoryPanel({
  friends,
  groups,
  onSelectFriend,
  onSelectGroup,
  loading,
}: {
  friends: MemoryEntry[];
  groups: MemoryEntry[];
  onSelectFriend: (name: string) => void;
  onSelectGroup: (name: string) => void;
  loading: boolean;
}) {
  return (
    <Card>
      <CardHeader className="pb-4">
        <div className="flex items-center gap-2">
          <Bot className="h-4 w-4 text-indigo-600" />
          <CardTitle>Agent memory</CardTitle>
        </div>
        <CardDescription>Frequent friends and groups from past splits</CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <MemoryList title="Friends" items={friends} onSelect={onSelectFriend} loading={loading} />
        <MemoryList title="Groups" items={groups} onSelect={onSelectGroup} loading={loading} />
      </CardContent>
    </Card>
  );
}

function AIFallbackMemoryPanel({
  memories,
  settings,
  metrics,
  onSetLearning,
  onCreate,
  onUpdate,
  onFeedback,
  onDelete,
  loading,
}: {
  memories: AIMemory[];
  settings: StructuredMemorySettings;
  metrics: StructuredMemoryMetrics | null;
  onSetLearning: (enabled: boolean) => Promise<void>;
  onCreate: (payload: {
    merchant: string;
    preference: "personal" | "shared";
    participant_names: string[];
    group_name: string | null;
  }) => Promise<void>;
  onUpdate: (
    id: number,
    payload: {
      preference: "personal" | "shared";
      participant_names: string[];
      group_name: string | null;
    },
  ) => Promise<void>;
  onFeedback: (id: number, outcome: "accepted" | "rejected") => Promise<void>;
  onDelete: (id: number) => void;
  loading: boolean;
}) {
  const [merchant, setMerchant] = useState("");
  const [preference, setPreference] = useState<"personal" | "shared">("personal");
  const [participants, setParticipants] = useState("");
  const [groupName, setGroupName] = useState("");
  const [memoryBusy, setMemoryBusy] = useState(false);
  const [memoryError, setMemoryError] = useState("");

  const act = async (action: () => Promise<void>) => {
    setMemoryBusy(true);
    setMemoryError("");
    try {
      await action();
    } catch (error) {
      setMemoryError(apiErrorMessage(error, "The preference could not be saved."));
    } finally {
      setMemoryBusy(false);
    }
  };

  const parsedParticipants = participants
    .split(",")
    .map((value) => value.trim())
    .filter(Boolean);

  return (
    <Card>
      <CardHeader className="pb-4">
        <div className="flex items-center gap-2">
          <Sparkles className="h-4 w-4 text-indigo-600" />
          <CardTitle>Structured preferences</CardTitle>
        </div>
        <CardDescription>
          Explainable transaction suggestions from your preferences and confirmed corrections
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <label className="flex min-h-11 items-center justify-between gap-3 rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm font-medium text-slate-800">
          <span>
            Learn from confirmed transaction actions
            <span className="block text-xs font-normal text-slate-500">
              Chat transcripts are never stored as memory.
            </span>
          </span>
          <input
            type="checkbox"
            checked={settings.transaction_learning_enabled}
            disabled={memoryBusy}
            onChange={(event) => void act(() => onSetLearning(event.target.checked))}
            className="h-5 w-5 accent-indigo-600"
          />
        </label>

        <form
          className="grid gap-3 rounded-lg border border-slate-200 bg-slate-50/60 p-3"
          onSubmit={(event) => {
            event.preventDefault();
            void act(async () => {
              await onCreate({
                merchant: merchant.trim(),
                preference,
                participant_names: preference === "shared" ? parsedParticipants : [],
                group_name: preference === "shared" && groupName.trim() ? groupName.trim() : null,
              });
              setMerchant("");
              setParticipants("");
              setGroupName("");
            });
          }}
        >
          <p className="text-sm font-semibold text-slate-900">Add an explicit preference</p>
          <label className="grid gap-1 text-xs font-medium text-slate-700">
            Merchant
            <Input
              value={merchant}
              onChange={(event) => setMerchant(event.target.value)}
              maxLength={255}
              required
              placeholder="Costco"
            />
          </label>
          <label className="grid gap-1 text-xs font-medium text-slate-700">
            Usual treatment
            <select
              className="min-h-11 rounded-md border border-slate-300 bg-white px-3 text-sm"
              value={preference}
              onChange={(event) => setPreference(event.target.value as "personal" | "shared")}
            >
              <option value="personal">Personal</option>
              <option value="shared">Shared</option>
            </select>
          </label>
          {preference === "shared" ? (
            <div className="grid gap-3 sm:grid-cols-2">
              <label className="grid gap-1 text-xs font-medium text-slate-700">
                People, comma-separated
                <Input value={participants} onChange={(event) => setParticipants(event.target.value)} maxLength={500} placeholder="Gunjan" />
              </label>
              <label className="grid gap-1 text-xs font-medium text-slate-700">
                Splitwise group (optional)
                <Input value={groupName} onChange={(event) => setGroupName(event.target.value)} maxLength={255} placeholder="Apartment" />
              </label>
            </div>
          ) : null}
          <Button
            type="submit"
            className="min-h-11 w-fit"
            disabled={memoryBusy || !merchant.trim() || (preference === "shared" && !parsedParticipants.length && !groupName.trim())}
          >
            Save preference
          </Button>
        </form>

        {metrics ? (
          <p className="text-xs text-slate-500">
            Suggestions shown {metrics.shown} · accepted {metrics.accepted} · corrected {metrics.edited} · rejected {metrics.rejected}
          </p>
        ) : null}
        {memoryError ? <p role="alert" className="text-sm text-rose-700">{memoryError}</p> : null}
        {loading ? (
          <SkeletonRows rows={3} />
        ) : memories.length ? (
          memories.map((memory) => (
            <div
              key={memory.id}
              className="rounded-lg border border-slate-200 bg-slate-50/60 p-3"
            >
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <p className="truncate text-sm font-medium text-slate-900">
                    {memory.label}
                  </p>
                  <p className="mt-1 text-xs text-slate-500">
                    {memory.rationale}
                  </p>
                </div>
                <Button
                  size="icon"
                  variant="ghost"
                  className="h-7 w-7 shrink-0 text-slate-400 hover:text-red-600"
                  onClick={() => onDelete(memory.id)}
                  aria-label={`Delete preference ${memory.label}`}
                  title="Delete preference"
                >
                  <X className="h-3.5 w-3.5" />
                </Button>
              </div>
              {memory.final_participants.length ? (
                <div className="mt-2 flex flex-wrap gap-1.5">
                  {memory.final_participants.map((participant) => (
                    <span
                      key={`${memory.id}-${participant}`}
                      className="rounded-full bg-indigo-50 px-2 py-1 text-xs text-indigo-800"
                    >
                      {participant}
                    </span>
                  ))}
                </div>
              ) : null}
              <p className="mt-2 text-xs text-slate-500">
                Used {memory.usage_count} times
                {memory.last_used_at
                  ? ` · last used ${new Date(memory.last_used_at).toLocaleDateString()}`
                  : ""}
              </p>
              <div className="mt-3 flex flex-wrap gap-2">
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  className="min-h-11"
                  disabled={memoryBusy}
                  onClick={() => {
                    setMerchant(memory.merchant || "");
                    setPreference(memory.final_action === "personal" ? "personal" : "shared");
                    setParticipants(memory.final_participants.join(", "));
                    setGroupName(memory.final_group_name || "");
                  }}
                >
                  Edit preference
                </Button>
                {memory.final_action !== "personal" ? (
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    className="min-h-11"
                    disabled={memoryBusy}
                    onClick={() => void act(() => onUpdate(memory.id, {
                      preference: "personal",
                      participant_names: [],
                      group_name: null,
                    }))}
                  >
                    Change to personal
                  </Button>
                ) : null}
                <Button type="button" size="sm" variant="ghost" className="min-h-11" disabled={memoryBusy} onClick={() => void act(() => onFeedback(memory.id, "accepted"))}>Helpful</Button>
                <Button type="button" size="sm" variant="ghost" className="min-h-11" disabled={memoryBusy} onClick={() => void act(() => onFeedback(memory.id, "rejected"))}>Not helpful</Button>
              </div>
            </div>
          ))
        ) : (
          <PanelEmptyState
            icon={Sparkles}
            title="No structured preferences yet"
            description="Add one explicitly or confirm a transaction action to build explainable suggestions."
            compact
          />
        )}
      </CardContent>
    </Card>
  );
}

function MemoryList({
  title,
  items,
  onSelect,
  loading,
}: {
  title: string;
  items: MemoryEntry[];
  onSelect: (name: string) => void;
  loading: boolean;
}) {
  return (
    <div className="space-y-2">
      <p className="text-sm font-medium text-slate-700">{title}</p>
      {loading ? (
        <SkeletonRows rows={2} />
      ) : items.length ? (
        <div className="flex flex-wrap gap-2">
          {items.map((item) => (
            <button
              key={item.id}
              type="button"
              className="rounded-lg border border-slate-200 bg-white px-2.5 py-1.5 text-sm text-slate-700 transition hover:border-indigo-300 hover:bg-indigo-50 hover:text-indigo-900 focus-visible:border-indigo-400"
              onClick={() => onSelect(item.name)}
            >
              {item.name}
              <span className="ml-1 text-xs text-slate-500">x{item.count}</span>
            </button>
          ))}
        </div>
      ) : (
        <PanelEmptyState icon={Bot} title="No memory yet" compact />
      )}
    </div>
  );
}

function Header({
  active,
  onSync,
  busy,
  pendingCount,
  pendingTotal,
  lastSyncLabel,
  syncError,
}: {
  active: ExpenseTab;
  onSync: () => void;
  busy: string | null;
  pendingCount: number;
  pendingTotal: number;
  lastSyncLabel: string;
  syncError: boolean;
}) {
  const copy = {
    review: {
      title: "Expense Review",
      description: `${pendingCount} actionable item${pendingCount === 1 ? "" : "s"} need review · ${formatCurrency(pendingTotal)} in transaction decisions`,
    },
    insights: {
      title: "Spending Insights",
      description: "Understand where your money goes, how it changes, and what deserves attention.",
    },
    activity: {
      title: "Expense Activity",
      description: "Review recent decisions and return eligible transactions to the queue.",
    },
    attention: {
      title: "Attention Center",
      description: "Review bounded, deterministic signals without starting any action.",
    },
  }[active];
  return (
    <PageHeader
      eyebrow={<span className="inline-flex items-center gap-2"><WalletCards className="h-4 w-4" aria-hidden="true" />Expenses</span>}
      title={copy.title}
      description={<><span className="hidden text-slate-200 sm:block">{copy.description}</span><span role={syncError ? "alert" : "status"} className={`block text-xs sm:mt-1 ${syncError ? "text-rose-300" : "text-slate-400"}`}>{syncError ? "We couldn't sync transactions." : busy === "sync" ? "Syncing transactions…" : `Last synced ${lastSyncLabel}`}</span></>}
      actions={<Button
          onClick={onSync}
          disabled={busy !== null}
          variant="outline"
          className="relative border-white/15 bg-white/10 text-white hover:border-white/25 hover:bg-white/15 hover:text-white"
        >
          <RefreshCw className={`h-4 w-4 ${busy === "sync" ? "animate-spin" : ""}`}/><span className="sr-only sm:not-sr-only">{syncError ? "Try again" : "Sync"}</span>
        </Button>}
    />
  );
}

function ExpenseTabs({active,onChange,showAttention}:{active:ExpenseTab;onChange:(value:ExpenseTab)=>void;showAttention:boolean}) {
  const tabs: ExpenseTab[] = showAttention
    ? ["review", "insights", "activity", "attention"]
    : ["review", "insights", "activity"];
  return <nav className="flex gap-1 overflow-x-auto rounded-xl border border-slate-200 bg-white p-1 shadow-sm" aria-label="Expense Review views">{tabs.map(value=><button key={value} type="button" aria-current={active===value?"page":undefined} onClick={()=>onChange(value)} className={`min-h-11 min-w-[5.5rem] flex-1 rounded-lg px-3 py-2.5 text-sm font-semibold capitalize transition focus-visible:ring-2 focus-visible:ring-indigo-500 ${active===value?"bg-indigo-600 text-white":"text-slate-600 hover:bg-slate-50 hover:text-slate-950"}`}>{value}</button>)}</nav>
}

function relativeTime(value: Date) {
  const seconds = Math.max(0, Math.round((Date.now() - value.getTime()) / 1000));
  if (seconds < 60) return "just now";
  const minutes = Math.round(seconds / 60); return minutes < 60 ? `${minutes} minute${minutes===1?"":"s"} ago` : value.toLocaleString();
}

function EmptyState({
  icon: Icon,
  title,
  description,
}: {
  icon: ComponentType<{ className?: string }>;
  title: string;
  description: string;
}) {
  return (
    <Card className="border-dashed border-slate-300 bg-white">
      <CardContent className="flex min-h-40 flex-col items-center justify-center p-7 text-center sm:p-7">
        <span className="flex h-9 w-9 items-center justify-center rounded-md border border-slate-200 bg-white text-slate-500">
          <Icon className="h-5 w-5" />
        </span>
        <h3 className="mt-3 text-sm font-semibold text-slate-950">{title}</h3>
        <p className="mt-1 max-w-md text-sm leading-6 text-slate-500">{description}</p>
      </CardContent>
    </Card>
  );
}

function PanelEmptyState({
  icon: Icon,
  title,
  description,
  compact = false,
}: {
  icon: ComponentType<{ className?: string }>;
  title: string;
  description?: string;
  compact?: boolean;
}) {
  return (
    <div
      className={`flex flex-col items-center justify-center rounded-xl border border-dashed border-slate-300 bg-slate-50/70 px-4 text-center ${compact ? "min-h-24 py-4" : "min-h-32 py-6"}`}
    >
      <span className="flex h-9 w-9 items-center justify-center rounded-xl border border-slate-200 bg-white text-slate-500 shadow-sm">
        <Icon className="h-4 w-4" />
      </span>
      <p className="mt-2 text-sm font-semibold text-slate-800">{title}</p>
      {description ? (
        <p className="mt-1 max-w-xs text-xs leading-5 text-slate-600">{description}</p>
      ) : null}
    </div>
  );
}

function SkeletonRows({ rows = 3 }: { rows?: number }) {
  return (
    <div className="space-y-3" aria-label="Loading" role="status">
      {Array.from({ length: rows }, (_, index) => (
        <div key={index} className="space-y-1.5">
          <div className="ui-skeleton h-3.5" style={{ width: `${72 - index * 8}%` }} />
          <div className="ui-skeleton h-2 w-full" />
        </div>
      ))}
      <span className="sr-only">Loading content</span>
    </div>
  );
}

function MetricCard({
  icon: Icon,
  label,
  value,
  detail,
  tone = "teal",
  empty = false,
}: {
  icon: ComponentType<{ className?: string }>;
  label: string;
  value: string;
  detail: string;
  tone?: "teal" | "indigo" | "amber";
  empty?: boolean;
}) {
  void tone;

  return (
    <Card className="group overflow-hidden transition hover:border-slate-300 hover:shadow-md hover:shadow-slate-950/[0.06]">
      <CardContent className="p-5 sm:p-6">
        <div className="flex items-start justify-between gap-3">
          <div>
            <p className="text-xs font-semibold uppercase tracking-wide text-slate-600">{label}</p>
            <p className={`mt-2 text-4xl font-semibold tracking-tight ${empty ? "text-slate-500" : "text-slate-950"}`}>
              {value}
            </p>
          </div>
          <span className={`flex h-10 w-10 items-center justify-center rounded-xl ${empty ? "border border-dashed border-slate-300 bg-slate-50 text-slate-500" : "bg-indigo-50 text-indigo-700"}`}>
            <Icon className="h-5 w-5" />
          </span>
        </div>
        <p className="mt-3 text-sm text-slate-600">
          {empty ? (label === "Pending amount" ? "No open card spend" : "Nothing needs review") : detail}
        </p>
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
    <Card className="bg-slate-50/70 shadow-none">
      <CardContent className="grid gap-2 p-3 sm:grid-cols-3 sm:p-3">
        <StatusPill
          label="Approval queue"
          value={pendingCount ? `${pendingCount} pending` : "Clear"}
          connected={false}
        />
        <StatusPill label="Telegram connected" value="Review alerts ready" connected />
        <StatusPill
          label="Auto-sync"
          value={busy ? "Working" : lastSyncLabel}
          connected={!busy && lastSyncLabel !== "Not synced yet"}
        />
      </CardContent>
    </Card>
  );
}

function StatusPill({
  label,
  value,
  connected,
}: {
  label: string;
  value: string;
  connected: boolean;
}) {
  return (
    <div className="flex min-w-0 items-center gap-3 rounded-lg px-3 py-2.5 transition hover:bg-white">
      <span className={`h-2.5 w-2.5 shrink-0 rounded-full ring-4 ${connected ? "bg-emerald-500 ring-emerald-100" : "bg-slate-400 ring-slate-200"}`} />
      <span className="min-w-0">
        <span className="block text-xs font-semibold uppercase tracking-wide text-slate-600">
          {label}
        </span>
        <span className="block truncate text-sm font-medium text-slate-900">{value}</span>
      </span>
    </div>
  );
}

function TransactionCard({
  transaction,
  recommendation,
  busy,
  actionNotice,
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
  agentFocused,
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
  onUseRecommended,
  onAgentFocus,
  allGroupsOpen,
  onPersonal,
  onDraft,
  onPostSplit,
  onPreviewCustom,
}: {
  transaction: Transaction;
  recommendation: ReviewRecommendation | null;
  busy: string | null;
  actionNotice: ActionNotice | null;
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
  agentFocused: boolean;
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
  onUseRecommended: () => void;
  onAgentFocus: () => void;
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
      data-testid={`transaction-card-${transaction.id}`}
      className={`overflow-hidden border border-ui-border bg-white transition hover:border-slate-300 hover:shadow-primary ${
        agentFocused ? "ring-2 ring-indigo-300" : isExpanded ? "ring-1 ring-indigo-100" : ""
      }`}
    >
      <CardHeader className="gap-3 p-4 pb-3">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
          <div className="flex min-w-0 items-start gap-3">
            <MerchantAvatar name={title} />
            <div className="min-w-0">
              <CardTitle className="truncate text-lg">{title}</CardTitle>
              <CardDescription className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs">
                <span>{transaction.date || "Date unavailable"}</span>
                {transaction.institution_name ? <><span aria-hidden="true">·</span><span>{transaction.institution_name}</span></> : null}
                {transaction.category ? <><span aria-hidden="true">·</span><span>{transaction.category}</span></> : null}
                {transaction.payment_channel ? <><span aria-hidden="true">·</span><span className="capitalize">{transaction.payment_channel}</span></> : null}
                <><span aria-hidden="true">·</span><span>Plaid · {transaction.iso_currency_code}</span></>
              </CardDescription>
            </div>
          </div>
          <div className="flex shrink-0 flex-col items-start gap-2 sm:items-end">
            <p className={`financial-figure text-2xl font-semibold tracking-normal ${transaction.amount_cents < 0 ? "text-ui-success" : "text-ink"}`}>
              {formatTransactionAmount(transaction)}
            </p>
            <div className="flex flex-wrap justify-start gap-1.5 sm:justify-end">
              <Badge variant={transaction.pending ? "warning" : "secondary"}>{transaction.pending ? "Pending" : statusDisplay(transaction.status)}</Badge>
              <ClassificationBadge transaction={transaction} />
            </div>
          </div>
        </div>

        <div className="flex flex-col gap-3 border-t border-slate-100 pt-3 lg:flex-row lg:items-center lg:justify-between">
          <div className="min-w-0 text-sm leading-6 text-slate-600">
            <p>{transaction.agent_question || "Review this transaction."}</p>
            {recommendation?.suggestion === "likely_shared" ? (
              <p className="mt-1 text-xs font-medium text-indigo-700">
                Recommended from your preference: split
                {recommendation.participant_names.length ? ` with ${recommendation.participant_names.join(", ")}` : " as shared"}
                {recommendation.group_name ? ` in ${recommendation.group_name}` : ""}. You can edit before posting.
              </p>
            ) : null}
            {transaction.pending ? (
              <p className="mt-1 text-xs font-medium text-amber-800">
                You can prepare this decision now. ExpenseOps will not post to Splitwise until the final charge is available.
              </p>
            ) : null}
          </div>
          <div className="flex shrink-0 flex-wrap items-center gap-2">
            <Button
              type="button"
              variant={agentFocused ? "secondary" : "ghost"}
              className="min-h-11"
              onClick={onAgentFocus}
              aria-pressed={agentFocused}
              aria-label={`Use ${title} as Agent context`}
              data-testid={`agent-context-transaction-${transaction.id}`}
            >
              <Bot className="size-4" aria-hidden="true" />
              {agentFocused ? "In Agent context" : "Ask about this"}
            </Button>
            <Button
              variant="outline"
              className="border-slate-300 text-slate-800 hover:bg-slate-100 hover:text-ink"
              onClick={onPersonal}
              disabled={disabled}
            >
              <UserCheck className="h-4 w-4" />
              Personal
            </Button>
            <Button
              onClick={recommendation?.suggestion === "likely_shared" ? onUseRecommended : () => onExpandedChange(true)}
              disabled={disabled}
              className="bg-indigo-600 shadow-sm shadow-indigo-950/10 hover:bg-indigo-700"
            >
              <Split className="h-4 w-4" />
              {transaction.pending
                ? recommendation?.suggestion === "likely_shared"
                  ? "Prepare recommended split"
                  : "Prepare split"
                : recommendation?.suggestion === "likely_shared"
                  ? "Use recommended split"
                  : "Split"}
            </Button>
            {recommendation?.suggestion === "likely_shared" ? (
              <Button type="button" variant="ghost" onClick={() => onExpandedChange(true)} disabled={disabled}>
                Customize
              </Button>
            ) : null}
            <OverflowMenu label={`More actions for ${title}`} items={[
              { label: "Save as draft", icon: Clock3, disabled, onSelect: onDraft },
              { label: isExpanded ? "Collapse split details" : "Customize split", icon: ChevronDown, onSelect: () => onExpandedChange(!isExpanded) },
            ]} />
          </div>
        </div>
        {busy ? (
          <p role="status" aria-live="polite" className="flex items-center gap-2 rounded-control border border-indigo-100 bg-indigo-50 px-3 py-2 text-sm font-medium text-indigo-800">
            <RefreshCw aria-hidden="true" className="size-4 animate-spin motion-reduce:animate-none" />
            {busy}
          </p>
        ) : actionNotice ? (
          <p role="alert" className="rounded-control border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-800">
            {actionNotice.text}
            {actionNotice.correlationId ? <span className="mt-1 block text-xs font-medium">Support ID: {actionNotice.correlationId}</span> : null}
          </p>
        ) : null}
      </CardHeader>

      {isExpanded ? (
        <CardContent className="space-y-3 overflow-hidden p-4 pt-0 transition-all duration-200">
        <section aria-labelledby={`transaction-${transaction.id}-people`}>
        <div className="mb-2"><p className="text-xs font-semibold uppercase tracking-wide text-ui-primary">Step 1</p><h3 id={`transaction-${transaction.id}-people`} className="text-sm font-semibold text-ink">Choose people</h3></div>
        <div className="rounded-control border border-slate-200 bg-slate-50 p-2.5">
          <div className="mb-2 grid grid-cols-2 rounded-full bg-slate-100/90 p-1 ring-1 ring-slate-200/70">
            <button
              type="button"
              className={`inline-flex min-h-11 items-center justify-center gap-2 rounded-full px-3 py-1.5 text-sm font-medium transition-all ${
                splitMode === "people"
                  ? "bg-indigo-600 text-white shadow-sm shadow-indigo-950/15"
                  : "text-slate-600 hover:bg-white/70 hover:text-slate-950"
              }`}
              onClick={() => setSplitMode("people")}
            >
              <UserCheck className="h-3.5 w-3.5" />
              People
            </button>
            <button
              type="button"
              className={`inline-flex min-h-11 items-center justify-center gap-2 rounded-full px-3 py-1.5 text-sm font-medium transition-all ${
                splitMode === "group"
                  ? "bg-indigo-600 text-white shadow-sm shadow-indigo-950/15"
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
        </section>

        <section aria-labelledby={`transaction-${transaction.id}-split`}>
        <div className="mb-2"><p className="text-xs font-semibold uppercase tracking-wide text-ui-primary">Step 2</p><h3 id={`transaction-${transaction.id}-split`} className="text-sm font-semibold text-ink">Choose split</h3></div>
        <CustomSplitPanel
          mode={customMode}
          payerIncluded={payerIncluded}
          participants={customParticipants}
          selectedParticipantCount={selectedParticipantCount}
          values={customValues}
          validation={customValidation}
          disabled={disabled}
          postingBlocked={transaction.pending}
          onModeChange={onCustomModeChange}
          onPayerIncludedChange={onPayerIncludedChange}
          onValueChange={onCustomValueChange}
          onPreview={onPreviewCustom}
          onPost={onPostSplit}
        />
        </section>
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
  const classes = {
    likely_personal: "border border-slate-200 bg-slate-50 text-slate-700",
    likely_shared: "border border-indigo-200 bg-indigo-50 text-indigo-700",
    unsure: "border border-amber-200 bg-amber-50 text-amber-700",
  };

  return (
    <span className="grid justify-items-start gap-1 sm:justify-items-end">
      <Badge className={classes[suggestion]} title={transaction.classification_reason || undefined}>
        {labels[suggestion]}
      </Badge>
      {transaction.classification_preference_id && transaction.classification_reason ? (
        <span className="max-w-64 text-xs leading-4 text-slate-500">
          Why: {transaction.classification_reason}
        </span>
      ) : null}
    </span>
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
  postingBlocked,
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
  postingBlocked: boolean;
  onModeChange: (mode: CustomSplitMode) => void;
  onPayerIncludedChange: (included: boolean) => void;
  onValueChange: (userId: number, value: string) => void;
  onPreview: () => void;
  onPost: () => void;
}) {
  const isEqualMode = mode === "equal";
  const canPost = !disabled && !postingBlocked && selectedParticipantCount > 0 && validation.valid;
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
              className={`min-h-11 min-w-11 rounded-full px-2.5 py-1 text-xs font-medium transition ${
                mode === value
                  ? "bg-indigo-600 text-white shadow-sm"
                  : "text-slate-600 hover:bg-white"
              }`}
              onClick={() => onModeChange(value as CustomSplitMode)}
            >
              {label}
            </button>
          ))}
        </div>
        <label className="inline-flex min-h-11 items-center gap-2 text-xs font-medium text-slate-600">
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
                  className="h-11 text-right sm:h-8"
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

      <div className="sticky bottom-20 z-10 -mx-2.5 -mb-2.5 mt-3 flex flex-wrap items-center justify-between gap-3 border-t border-slate-200 bg-white/95 px-2.5 py-3 shadow-[0_-8px_18px_rgba(15,23,42,0.04)] backdrop-blur sm:static sm:m-0 sm:mt-3 sm:bg-white sm:p-0 sm:pt-3 sm:shadow-none">
        <div className="min-w-0 text-xs text-slate-500">
          <p className="mb-1 font-semibold uppercase tracking-wide text-ui-primary">Step 3 · Review and post</p>
          <span
            className={
              selectedParticipantCount > 0 && validation.valid
                ? "text-teal-700"
                : "text-amber-700"
            }
          >
            {postingBlocked
              ? "The final charge must post before ExpenseOps can send this split."
              : selectedParticipantCount > 0
                ? validation.message
                : emptyMessage}
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
            className="min-w-[144px] bg-indigo-600 shadow-md shadow-indigo-950/10 hover:bg-indigo-700"
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
          className="h-11 sm:h-9"
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
              className="rounded-md border border-slate-200/80 bg-white px-3 py-2 text-left text-sm shadow-sm shadow-slate-950/[0.02] transition hover:border-indigo-200 hover:bg-indigo-50/60 hover:shadow"
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
  const initials = label.trim().split(/\s+/).slice(0, 2).map((part) => part[0]?.toUpperCase()).join("") || "?";
  return (
    <button
      className="inline-flex min-h-11 max-w-full items-center gap-1.5 rounded-full bg-indigo-50 px-2.5 py-1 text-sm font-medium text-indigo-800 ring-1 ring-indigo-100 transition hover:bg-indigo-100"
      onClick={onRemove}
      type="button"
    >
      <span aria-hidden="true" className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-indigo-200 text-xs font-semibold text-indigo-900">{initials}</span>
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
          className="h-11 sm:h-9"
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
              className="rounded-md border border-slate-200/80 bg-white px-3 py-2 text-left text-sm shadow-sm shadow-slate-950/[0.02] transition hover:border-indigo-200 hover:bg-indigo-50/60 hover:shadow"
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
                    className="rounded-md border border-slate-200/80 bg-white px-3 py-2 text-left text-sm shadow-sm shadow-slate-950/[0.02] transition hover:border-indigo-200 hover:bg-indigo-50/60 hover:shadow disabled:bg-slate-50 disabled:shadow-none disabled:opacity-70"
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
  loading,
  actionById,
  onUndo,
}: {
  transactions: Transaction[];
  loading: boolean;
  actionById: Record<number, string>;
  onUndo: (id: number) => void;
}) {
  return (
    <Card className="overflow-hidden">
      <CardHeader className="pb-4">
        <div className="flex items-center gap-2">
          <Layers3 className="h-4 w-4 text-slate-600" />
          <CardTitle>Recent activity</CardTitle>
        </div>
        <CardDescription>Completed transactions that can be moved back to review</CardDescription>
      </CardHeader>
      <CardContent className="max-h-[520px] space-y-1 overflow-auto pr-3">
        {loading ? (
          <SkeletonRows rows={4} />
        ) : transactions.length ? (
          transactions.map((transaction) => (
            <div
              key={transaction.id}
              className="group flex items-center justify-between gap-3 rounded-lg border border-transparent px-2 py-2 transition hover:border-slate-200 hover:bg-slate-50"
            >
              <div className="flex min-w-0 items-center gap-3">
                <ActivityIcon status={transaction.status} />
                <div className="min-w-0">
                  <p className="truncate text-sm font-medium text-slate-900">
                    {transaction.merchant_name || transaction.name}
                  </p>
                  <p className="text-xs font-medium text-slate-500">
                    {formatTransactionAmount(transaction)} ·{" "}
                    {statusDisplay(transaction.status)}
                  </p>
                  <p className="text-xs text-slate-500">
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
                  disabled={Boolean(actionById[transaction.id])}
                >
                  <RotateCcw className={`h-4 w-4 ${actionById[transaction.id] ? "animate-spin motion-reduce:animate-none" : ""}`} />
                  {actionById[transaction.id] || "Undo"}
                </Button>
              ) : null}
            </div>
          ))
        ) : (
          <PanelEmptyState
            icon={Layers3}
            title="No recent activity yet"
            description="Completed transactions will appear here with available actions."
          />
        )}
      </CardContent>
    </Card>
  );
}

function ActivityIcon({ status }: { status: Transaction["status"] }) {
  const styles: Record<string, string> = {
    personal: "border-emerald-200 text-emerald-700",
    posted: "border-emerald-200 text-emerald-700",
    shared_draft: "border-amber-200 text-amber-700",
    reconciliation_required: "border-amber-300 text-amber-800",
    ask_user: "border-indigo-200 text-indigo-700",
  };
  const Icon =
    status === "shared_draft" ? Clock3 : status === "ask_user" ? MessageCircle : CheckCircle2;

  return (
    <span
      className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-full border bg-white ${
        styles[status] || "border-slate-200 text-slate-600"
      }`}
    >
      <Icon className="h-4 w-4" />
    </span>
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
        <pre className="max-h-[520px] overflow-auto rounded-md bg-black/30 p-4 text-xs leading-5 text-slate-100">
          {JSON.stringify(log, null, 2)}
        </pre>
      </CardContent>
    </Card>
  );
}

export default App;
