import {
  Archive,
  Bot,
  CheckCircle2,
  ChevronRight,
  History,
  LoaderCircle,
  MessageSquarePlus,
  PanelRightClose,
  ReceiptText,
  RotateCcw,
  Send,
  Sparkles,
  SquarePen,
  ThumbsDown,
  ThumbsUp,
  User,
  Users,
  X,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ResponsiveSheet } from "@/components/ui/responsive-sheet";
import { Surface } from "@/components/ui/surface";
import type { ReviewItem } from "@/types";

import {
  ActionConfirmationCard,
  AgentResponseRenderer,
  type AgentNavigationRequest,
} from "./AgentResponseRenderer";
import { formatDate, formatMinor } from "./format";
import type {
  AgentActionConfirmationBlock,
  AgentFeedbackCreate,
  AgentFeedbackOut,
  AgentFeedbackReason,
  AgentMessage,
  AgentPageContext,
  AgentReviewSessionOut,
} from "./contracts";
import { useAgentController, type AgentController } from "./useAgentController";

export default function AgentExperience({
  mode,
  pageContext,
  contextLabel,
  onClearContext,
  onRestoreContext,
  onClose,
  onNavigate,
  readOnly,
  reviewItems = [],
}: {
  mode: "panel" | "page";
  pageContext: AgentPageContext | null;
  contextLabel: string;
  onClearContext?: () => void;
  onRestoreContext?: () => void;
  onClose?: () => void;
  onNavigate?: (request: AgentNavigationRequest) => void;
  readOnly: boolean;
  reviewItems?: ReviewItem[];
}) {
  const controller = useAgentController(pageContext);
  const [draft, setDraft] = useState("");
  const [historyOpen, setHistoryOpen] = useState(false);
  const [showJumpToLatest, setShowJumpToLatest] = useState(false);
  const [reviewProposal, setReviewProposal] = useState<AgentActionConfirmationBlock | null>(null);
  const [reviewClarifyMessage, setReviewClarifyMessage] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const composerRef = useRef<HTMLTextAreaElement>(null);
  const autoScrollRef = useRef(true);
  const initialFocusSetRef = useRef(false);

  async function beginReviewSession() {
    setReviewProposal(null);
    setReviewClarifyMessage(null);
    await controller.startReviewSession();
  }

  async function proposeMarkPersonal() {
    setReviewClarifyMessage(null);
    const block = await controller.proposeReviewAction("mark_personal");
    if (block) setReviewProposal(block);
  }

  async function proposeRecommendedSplit() {
    const current = controller.reviewSession?.current;
    if (!current) return;
    setReviewClarifyMessage(null);
    const block = await controller.proposeReviewAction("post_splitwise_expense", {
      participantNames: current.recommended_participant_names,
      groupName: current.recommended_group_name,
    });
    if (block) setReviewProposal(block);
  }

  async function interpretTypedReviewMessage(text: string) {
    setReviewClarifyMessage(null);
    const result = await controller.interpretReviewMessage(text);
    if (!result) return;
    if (result.status === "proposed" && result.confirmation) {
      setReviewProposal(result.confirmation);
      return;
    }
    setReviewClarifyMessage(
      result.message || "I couldn't quite tell who to split this with — try again, or use the buttons below.",
    );
  }

  async function confirmReviewProposal(block: AgentActionConfirmationBlock) {
    const updated = await controller.confirmAction(block);
    if (updated.status === "completed") {
      setReviewProposal(null);
      await controller.advanceReviewSession(updated.proposal_id);
    } else {
      setReviewProposal(updated);
    }
    return updated;
  }

  async function cancelReviewProposal(block: AgentActionConfirmationBlock) {
    const updated = await controller.cancelAction(block);
    setReviewProposal(null);
    return updated;
  }

  async function skipReviewCandidate() {
    setReviewProposal(null);
    setReviewClarifyMessage(null);
    await controller.skipReviewCandidate();
  }

  async function stopReviewSession() {
    setReviewProposal(null);
    setReviewClarifyMessage(null);
    await controller.stopReviewSession();
  }

  useEffect(() => {
    if (controller.conversationBusy || initialFocusSetRef.current) return;
    composerRef.current?.focus();
    initialFocusSetRef.current = true;
  }, [controller.conversationBusy]);

  useEffect(() => {
    const element = scrollRef.current;
    if (!element || !autoScrollRef.current) return;
    element.scrollTo({ top: element.scrollHeight, behavior: controller.sending ? "smooth" : "auto" });
  }, [controller.messages, controller.progressMessage, controller.sending, controller.streamingResponse, controller.streamingText]);

  useEffect(() => {
    if (!onClose || historyOpen) return;
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [historyOpen, onClose]);

  async function submit() {
    const value = draft.trim();
    if (!value || controller.sending) return;
    setDraft("");
    const current = controller.reviewSession?.current;
    const reviewCandidateActionable =
      !readOnly && current && !reviewProposal && current.available_actions.includes("mark_personal");
    if (reviewCandidateActionable) {
      await interpretTypedReviewMessage(value);
    } else {
      await controller.sendMessage(value);
    }
    composerRef.current?.focus();
  }

  const hasConversation = Boolean(controller.activeConversation);
  const hasConversationHistory = Boolean(
    controller.messages.length || controller.streamingText || controller.streamingResponse,
  );
  return (
    <Surface
      variant="command"
      padding="none"
      className={
        mode === "panel"
          ? "relative flex h-full min-h-0 min-w-0 flex-col overflow-hidden rounded-none border-indigo-200 shadow-primary lg:sticky lg:top-3 lg:h-[calc(100dvh-6.5rem)] lg:min-h-[38rem] lg:rounded-card"
          : "relative flex h-[calc(100dvh-10.5rem)] min-h-0 min-w-0 flex-col overflow-hidden border-indigo-200 shadow-card md:h-[calc(100dvh-8rem)]"
      }
      data-testid={`agent-${mode}`}
    >
      <header className="border-b border-slate-200 bg-gradient-to-r from-slate-950 to-indigo-950 px-4 py-3 text-white">
        <div className="flex items-center justify-between gap-3">
          <div className="flex min-w-0 items-center gap-3">
            <span className="flex size-10 shrink-0 items-center justify-center rounded-control bg-white/10 text-indigo-100 ring-1 ring-white/15">
              <Bot className="size-5" aria-hidden="true" />
            </span>
            <div className="min-w-0">
              <h1 className="truncate text-base font-semibold">ExpenseOps Agent</h1>
              <p className="truncate text-xs text-indigo-100">
                {readOnly
                  ? "Read-only · grounded in your data"
                  : "Controlled actions · confirmation required"}
              </p>
            </div>
          </div>
          <div className="flex shrink-0 items-center gap-1">
            <ConversationHistory
              open={historyOpen}
              onOpenChange={setHistoryOpen}
              conversations={controller.conversations}
              activeId={controller.activeConversation?.public_id || null}
              loading={controller.initializing}
              disabled={controller.conversationBusy || controller.sending}
              onSelect={async (conversation) => {
                setHistoryOpen(false);
                await controller.openConversation(conversation);
              }}
            />
            <button
              type="button"
              className="flex size-11 items-center justify-center rounded-control text-indigo-100 hover:bg-white/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white"
              onClick={() => void controller.newConversation()}
              aria-label="Start new Agent conversation"
              disabled={controller.sending || controller.conversationBusy}
            >
              <MessageSquarePlus className="size-5" aria-hidden="true" />
            </button>
            {onClose ? (
              <button
                type="button"
                className="flex size-11 items-center justify-center rounded-control text-indigo-100 hover:bg-white/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white"
                onClick={onClose}
                aria-label="Close ExpenseOps Agent"
              >
                <PanelRightClose className="size-5" aria-hidden="true" />
              </button>
            ) : null}
          </div>
        </div>
        <div className="mt-3 flex min-w-0 items-center gap-2">
          <Badge className="min-w-0 flex-1 border-white/15 bg-white/10 text-indigo-50" variant="secondary">
            <span className="truncate">
              {pageContext ? `Using context: ${contextLabel}` : "No page context"}
            </span>
          </Badge>
          {pageContext && onClearContext ? (
            <button
              type="button"
              className="flex min-h-11 shrink-0 items-center gap-1 rounded-control px-2 text-xs font-semibold text-indigo-100 hover:bg-white/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white"
              onClick={onClearContext}
              aria-label="Clear page context"
            >
              <X className="size-4" aria-hidden="true" /> Clear
            </button>
          ) : !pageContext && onRestoreContext ? (
            <button
              type="button"
              className="flex min-h-11 shrink-0 items-center gap-1 rounded-control px-2 text-xs font-semibold text-indigo-100 hover:bg-white/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white"
              onClick={onRestoreContext}
              aria-label="Restore page context"
            >
              <RotateCcw className="size-4" aria-hidden="true" /> Restore
            </button>
          ) : null}
          {hasConversation ? (
            <button
              type="button"
              className="flex min-h-11 shrink-0 items-center gap-1.5 rounded-control px-2 text-xs font-semibold text-indigo-100 hover:bg-white/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white"
              onClick={() => void controller.archiveConversation()}
              disabled={controller.sending || controller.conversationBusy}
            >
              <Archive className="size-4" aria-hidden="true" /> Archive
            </button>
          ) : null}
        </div>
      </header>

      <div
        ref={scrollRef}
        className="min-h-0 flex-1 overflow-y-auto overscroll-contain bg-slate-50/70 px-3 py-4 sm:px-4"
        aria-busy={controller.conversationBusy || controller.sending}
        onScroll={(event) => {
          const element = event.currentTarget;
          const nearBottom = element.scrollHeight - element.scrollTop - element.clientHeight < 120;
          autoScrollRef.current = nearBottom;
          setShowJumpToLatest(!nearBottom);
        }}
      >
        {!readOnly && controller.reviewSession ? (
          <ReviewSessionCard
            session={controller.reviewSession}
            busy={controller.reviewSessionBusy}
            errorMessage={controller.reviewSessionError?.message || null}
            clarifyMessage={reviewClarifyMessage}
            proposal={reviewProposal}
            isActionPending={controller.isActionPending}
            onPersonal={() => void proposeMarkPersonal()}
            onSplitRecommended={() => void proposeRecommendedSplit()}
            onConfirmProposal={confirmReviewProposal}
            onCancelProposal={cancelReviewProposal}
            onSkip={() => void skipReviewCandidate()}
            onStop={() => void stopReviewSession()}
            onDone={() => controller.clearReviewSession()}
            onOpenInWebReview={
              onNavigate
                ? () => {
                    const current = controller.reviewSession?.current;
                    if (!current?.transaction) return;
                    onNavigate({
                      target_surface: "expense_review",
                      entity: { kind: "transaction", public_id: String(current.transaction.id) },
                    });
                  }
                : undefined
            }
          />
        ) : !readOnly && reviewItems.length && hasConversationHistory ? (
          <AgentReviewItems items={reviewItems} onReviewInAgent={() => void beginReviewSession()} />
        ) : null}
        {controller.initializing || controller.loadingConversation ? (
          <ConversationSkeleton />
        ) : hasConversationHistory ? (
          <ol className="space-y-4" aria-label="Agent conversation messages">
            {controller.messages.map((message) => (
              <li key={message.public_id}>
                <AgentMessageView
                  message={message}
                  onNavigate={onNavigate}
                  onSubmitFeedback={controller.submitFeedback}
                  onConfirmAction={readOnly ? undefined : controller.confirmAction}
                  onCancelAction={readOnly ? undefined : controller.cancelAction}
                  isActionPending={controller.isActionPending}
                />
              </li>
            ))}
            {controller.streamingText || controller.streamingResponse || controller.progressMessage ? (
              <li>
                <StreamingAssistant
                  text={controller.streamingText}
                  response={controller.streamingResponse}
                  progress={controller.progressMessage}
                  onNavigate={onNavigate}
                />
              </li>
            ) : null}
          </ol>
        ) : controller.reviewSession ? null : (
          <AgentWelcome
            disabled={controller.conversationBusy || controller.sending}
            onPrompt={(value) => void controller.sendMessage(value)}
            reviewItemCount={!readOnly ? reviewItems.length : 0}
            onStartReview={() => void beginReviewSession()}
          />
        )}
      </div>

      {showJumpToLatest ? (
        <button
          type="button"
          className="absolute bottom-28 left-1/2 z-10 min-h-11 -translate-x-1/2 rounded-full border border-slate-200 bg-white px-4 text-xs font-semibold text-slate-800 shadow-card focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-600"
          onClick={() => {
            const element = scrollRef.current;
            if (!element) return;
            autoScrollRef.current = true;
            setShowJumpToLatest(false);
            element.scrollTo({ top: element.scrollHeight, behavior: "smooth" });
          }}
        >
          Jump to latest
        </button>
      ) : null}

      {controller.error ? (
        <div role="alert" className="border-t border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-900">
          <p className="font-semibold">Agent request needs attention</p>
          <p className="mt-1 leading-5">{controller.error.message}</p>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            {controller.error.retryable && controller.canRetry ? (
              <Button size="sm" variant="outline" onClick={() => void controller.retryLastMessage()} disabled={controller.sending || controller.conversationBusy}>
                Retry
              </Button>
            ) : null}
            <Button size="sm" variant="ghost" onClick={() => void controller.reload()} disabled={controller.sending || controller.conversationBusy}>
              Reload conversation
            </Button>
            {controller.error.correlationId ? (
              <span className="text-xs text-rose-700">Support ID {controller.error.correlationId}</span>
            ) : null}
          </div>
        </div>
      ) : null}

      <form
        className="border-t border-slate-200 bg-white p-3 pb-[max(0.75rem,env(safe-area-inset-bottom))] sm:p-4"
        onSubmit={(event) => {
          event.preventDefault();
          void submit();
        }}
      >
        <label htmlFor={`agent-composer-${mode}`} className="sr-only">Ask ExpenseOps Agent</label>
        <div className="flex items-end gap-2 rounded-card border border-slate-300 bg-white p-2 shadow-sm focus-within:border-indigo-500 focus-within:ring-2 focus-within:ring-indigo-500/20">
          <textarea
            ref={composerRef}
            id={`agent-composer-${mode}`}
            value={draft}
            onChange={(event) => setDraft(event.target.value.slice(0, 4_000))}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
                event.preventDefault();
                void submit();
              }
            }}
            rows={1}
            disabled={controller.conversationBusy}
            placeholder="Ask about spending, household, receipts, deals, or errands"
            className="max-h-32 min-h-11 min-w-0 flex-1 resize-none bg-transparent px-2 py-2.5 text-base leading-6 text-slate-950 outline-none placeholder:text-slate-500 disabled:opacity-60 sm:text-sm"
          />
          <Button
            type="submit"
            size="icon"
            aria-label={controller.sending ? "Agent response in progress" : "Send message"}
            disabled={controller.sending || controller.conversationBusy || !draft.trim()}
          >
            {controller.sending ? (
              <LoaderCircle className="size-5 animate-spin motion-reduce:animate-none" aria-hidden="true" />
            ) : (
              <Send className="size-5" aria-hidden="true" />
            )}
          </Button>
        </div>
        <p className="mt-2 text-center text-[11px] leading-4 text-slate-500">
          {readOnly
            ? "Read-only. ExpenseOps will not post, split, buy, or change anything here."
            : "Actions require an exact preview and a separate confirmation. Purchases remain disabled."}
        </p>
      </form>
      <p className="sr-only" aria-live="polite" aria-atomic="true">{controller.announcement}</p>
    </Surface>
  );
}

function AgentReviewItems({
  items,
  onReviewInAgent,
}: {
  items: ReviewItem[];
  onReviewInAgent: () => void;
}) {
  return (
    <section className="mb-4 rounded-card border border-indigo-200 bg-indigo-50 p-3" aria-labelledby="agent-review-heading">
      <div className="flex items-center justify-between gap-2">
        <div>
          <p className="text-xs font-semibold uppercase tracking-wide text-indigo-700">Needs decision</p>
          <h2 id="agent-review-heading" className="text-sm font-semibold text-slate-950">
            {items.length} review item{items.length === 1 ? "" : "s"}
          </h2>
        </div>
        <Badge variant="secondary">Live inbox</Badge>
      </div>
      <div className="mt-2 grid gap-2">
        {items.slice(0, 3).map((item) => {
          const transaction = item.transaction;
          const receipt = item.receipt;
          const label = transaction
            ? transaction.merchant_name || transaction.name
            : receipt?.merchant_name || "Receipt";
          const detail = transaction
            ? `${transaction.currency} ${(Math.abs(transaction.amount_cents) / 100).toFixed(2)}${transaction.pending ? " · Pending" : ""}`
            : item.kind === "receipt_match_needed"
              ? "Receipt match needed"
              : "Itemized split ready";
          return (
            <button
              key={item.public_id}
              type="button"
              className="flex min-h-11 min-w-0 items-center justify-between gap-3 rounded-control border border-indigo-100 bg-white px-3 py-2 text-left hover:border-indigo-300 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-600"
              onClick={onReviewInAgent}
            >
              <span className="min-w-0">
                <span className="block truncate text-sm font-semibold text-slate-900">{label}</span>
                <span className="block truncate text-xs text-slate-600">{detail}</span>
              </span>
              <span className="shrink-0 text-xs font-semibold text-indigo-700">
                Review in Agent
              </span>
            </button>
          );
        })}
      </div>
      {items.length > 3 ? <p className="mt-2 text-xs text-indigo-700">And {items.length - 3} more in Review.</p> : null}
    </section>
  );
}

function ReviewSessionCard({
  session,
  busy,
  errorMessage,
  clarifyMessage,
  proposal,
  isActionPending,
  onPersonal,
  onSplitRecommended,
  onConfirmProposal,
  onCancelProposal,
  onSkip,
  onStop,
  onDone,
  onOpenInWebReview,
}: {
  session: AgentReviewSessionOut;
  busy: boolean;
  errorMessage: string | null;
  clarifyMessage: string | null;
  proposal: AgentActionConfirmationBlock | null;
  isActionPending: AgentController["isActionPending"];
  onPersonal: () => void;
  onSplitRecommended: () => void;
  onConfirmProposal: (
    block: AgentActionConfirmationBlock,
  ) => Promise<AgentActionConfirmationBlock>;
  onCancelProposal: (
    block: AgentActionConfirmationBlock,
  ) => Promise<AgentActionConfirmationBlock>;
  onSkip: () => void;
  onStop: () => void;
  onDone: () => void;
  onOpenInWebReview?: () => void;
}) {
  const { progress, current } = session;
  const position = progress.total_candidates - progress.remaining + 1;

  if (proposal) {
    return (
      <section className="mb-4" aria-label="Confirm review action">
        <ActionConfirmationCard
          block={proposal}
          pending={isActionPending(proposal.proposal_id)}
          onConfirm={onConfirmProposal}
          onCancel={onCancelProposal}
        />
      </section>
    );
  }

  if (!current) {
    return (
      <section className="mb-4 rounded-card border border-emerald-200 bg-emerald-50 p-4" aria-live="polite">
        <div className="flex items-center gap-2 text-emerald-900">
          <CheckCircle2 className="size-5 shrink-0" aria-hidden="true" />
          <p className="font-semibold">
            {progress.status === "cancelled" ? "Review stopped" : "You're all caught up"}
          </p>
        </div>
        <p className="mt-1 text-sm leading-5 text-emerald-900">
          {progress.reviewed} purchase{progress.reviewed === 1 ? "" : "s"} reviewed:
          {" "}
          {progress.personal} personal, {progress.split} split, {progress.skipped} skipped.
        </p>
        <Button size="sm" variant="outline" className="mt-3" onClick={onDone}>
          Close
        </Button>
      </section>
    );
  }

  const { transaction, receipt } = current;
  const title = transaction?.merchant || receipt?.merchant || "Purchase";
  const canActOnTransaction = Boolean(transaction) && current.available_actions.includes("mark_personal");

  return (
    <section
      className="mb-4 rounded-card border border-indigo-200 bg-white p-4 shadow-sm"
      aria-label={`Review purchase ${position} of ${progress.total_candidates}`}
    >
      <div className="flex items-center justify-between gap-2">
        <p className="text-xs font-semibold uppercase tracking-wide text-indigo-700" role="status">
          Review purchase {position} of {progress.total_candidates}
        </p>
        {receipt && !transaction ? <ReceiptText className="size-4 text-indigo-600" aria-hidden="true" /> : null}
      </div>
      <p className="mt-2 truncate text-base font-semibold text-slate-950">{title}</p>
      <p className="mt-0.5 text-sm text-slate-600">
        {transaction
          ? `${formatMinor(Math.abs(transaction.amount_cents), transaction.currency || "USD")}${transaction.occurred_on ? ` · ${formatDate(transaction.occurred_on)}` : ""}${transaction.pending ? " · Pending" : ""}`
          : receipt
            ? `${receipt.total_cents !== null ? formatMinor(receipt.total_cents, receipt.currency || "USD") : ""} · ${receipt.line_count} item${receipt.line_count === 1 ? "" : "s"}`
            : null}
      </p>
      {current.recommendation_label ? (
        <p className="mt-2 text-xs leading-5 text-indigo-700">{current.recommendation_label}</p>
      ) : null}

      {canActOnTransaction ? (
        <div className="mt-3 flex flex-col gap-2">
          <Button type="button" className="min-h-11 justify-start gap-2" variant="outline" disabled={busy} onClick={onPersonal}>
            <User className="size-4" aria-hidden="true" /> Personal
          </Button>
          {current.recommended_participant_names.length || current.recommended_group_name ? (
            <Button type="button" className="min-h-11 justify-start gap-2" disabled={busy} onClick={onSplitRecommended}>
              <Users className="size-4" aria-hidden="true" />
              {current.recommended_group_name
                ? `Split with ${current.recommended_group_name}`
                : `Split with ${current.recommended_participant_names.join(", ")}`}
            </Button>
          ) : null}
          {onOpenInWebReview ? (
            <Button type="button" className="min-h-11 justify-start gap-2" variant="ghost" disabled={busy} onClick={onOpenInWebReview}>
              <SquarePen className="size-4" aria-hidden="true" /> Customize in full Expense Review
            </Button>
          ) : null}
        </div>
      ) : receipt ? (
        <div className="mt-3">
          <p className="text-xs leading-5 text-slate-600">
            Itemized receipt splits aren&rsquo;t supported inside the Agent yet
            {onOpenInWebReview ? " — use Household Ops to assign items, or skip for now." : " — skip for now."}
          </p>
        </div>
      ) : null}

      {canActOnTransaction ? (
        <p className="mt-3 text-xs leading-5 text-slate-500">
          Or type who to split this with, like &ldquo;split with Priya&rdquo;.
        </p>
      ) : null}

      <div className="mt-3 flex flex-wrap gap-2 border-t border-slate-100 pt-3">
        <Button type="button" size="sm" variant="ghost" disabled={busy} onClick={onSkip}>
          Skip for now
        </Button>
        <Button type="button" size="sm" variant="ghost" disabled={busy} onClick={onStop}>
          Stop review
        </Button>
      </div>
      {clarifyMessage ? (
        <p className="mt-2 text-sm text-indigo-700" role="status">{clarifyMessage}</p>
      ) : null}
      {errorMessage ? <p className="mt-2 text-sm text-rose-700" role="alert">{errorMessage}</p> : null}
    </section>
  );
}

function AgentMessageView({
  message,
  onNavigate,
  onSubmitFeedback,
  onConfirmAction,
  onCancelAction,
  isActionPending,
}: {
  message: AgentMessage;
  onNavigate?: (request: AgentNavigationRequest) => void;
  onSubmitFeedback: (
    messagePublicId: string,
    payload: AgentFeedbackCreate,
  ) => Promise<AgentFeedbackOut>;
  onConfirmAction?: AgentController["confirmAction"];
  onCancelAction?: AgentController["cancelAction"];
  isActionPending: AgentController["isActionPending"];
}) {
  if (message.role === "user") {
    return (
      <div className="flex justify-end">
        <div className="max-w-[88%] rounded-2xl rounded-br-md bg-indigo-600 px-4 py-3 text-sm leading-6 text-white shadow-sm">
          <p className="whitespace-pre-wrap [overflow-wrap:anywhere]">{message.text}</p>
        </div>
      </div>
    );
  }
  return (
    <article className="min-w-0 max-w-[96%] rounded-2xl rounded-bl-md border border-slate-200 bg-white p-4 shadow-sm" aria-label="ExpenseOps Agent response">
      <h2 className="sr-only">ExpenseOps answer</h2>
      {message.text ? <p className="whitespace-pre-wrap text-sm leading-6 text-slate-800 [overflow-wrap:anywhere]">{message.text}</p> : null}
      {message.structured_response ? (
        <AgentResponseRenderer
          response={message.structured_response}
          onNavigate={onNavigate}
          onConfirmAction={onConfirmAction}
          onCancelAction={onCancelAction}
          isActionPending={isActionPending}
        />
      ) : null}
      {message.feedback_eligible ? (
        <AgentFeedbackControls
          messagePublicId={message.public_id}
          feedback={message.feedback}
          onSubmitFeedback={onSubmitFeedback}
        />
      ) : null}
    </article>
  );
}

const FEEDBACK_REASONS: readonly [AgentFeedbackReason, string][] = [
  ["wrong_data", "Wrong data"],
  ["didnt_understand", "Didn't understand me"],
  ["too_slow", "Too slow"],
  ["other", "Other"],
];

function AgentFeedbackControls({
  messagePublicId,
  feedback,
  onSubmitFeedback,
}: {
  messagePublicId: string;
  feedback: AgentFeedbackOut | null;
  onSubmitFeedback: (
    messagePublicId: string,
    payload: AgentFeedbackCreate,
  ) => Promise<AgentFeedbackOut>;
}) {
  const [editingReason, setEditingReason] = useState(false);
  const [reason, setReason] = useState<AgentFeedbackReason | "">(feedback?.reason || "");
  const [pending, setPending] = useState(false);
  const [statusMessage, setStatusMessage] = useState("");
  const [errorMessage, setErrorMessage] = useState("");
  const reasonRef = useRef<HTMLSelectElement>(null);
  const notHelpfulRef = useRef<HTMLButtonElement>(null);
  const reasonId = `agent-feedback-reason-${messagePublicId}`;

  useEffect(() => {
    if (!editingReason) setReason(feedback?.reason || "");
  }, [editingReason, feedback]);

  async function save(payload: AgentFeedbackCreate) {
    if (pending) return;
    setPending(true);
    setErrorMessage("");
    setStatusMessage("");
    try {
      await onSubmitFeedback(messagePublicId, payload);
      setEditingReason(false);
      setStatusMessage("Thanks—feedback saved.");
    } catch {
      setErrorMessage("Feedback could not be saved. Try again.");
    } finally {
      setPending(false);
    }
  }

  function startNotHelpfulFeedback() {
    setEditingReason(true);
    setErrorMessage("");
    setStatusMessage("");
    window.requestAnimationFrame(() => reasonRef.current?.focus());
  }

  function cancelNotHelpfulFeedback() {
    setEditingReason(false);
    setReason(feedback?.reason || "");
    setErrorMessage("");
    window.requestAnimationFrame(() => notHelpfulRef.current?.focus());
  }

  return (
    <section className="mt-4 border-t border-slate-100 pt-3" aria-label="Agent response feedback">
      <p className="text-xs font-medium text-slate-600">Was this helpful?</p>
      <div className="mt-2 flex flex-wrap items-center gap-2" role="group" aria-label="Rate this Agent response">
        <Button
          type="button"
          size="sm"
          variant={feedback?.rating === "helpful" ? "secondary" : "ghost"}
          className="min-h-11"
          aria-pressed={feedback?.rating === "helpful"}
          disabled={pending}
          onClick={() => void save({ rating: "helpful", reason: null })}
        >
          <ThumbsUp className="size-4" aria-hidden="true" /> Helpful
        </Button>
        <Button
          ref={notHelpfulRef}
          type="button"
          size="sm"
          variant={feedback?.rating === "not_helpful" ? "secondary" : "ghost"}
          className="min-h-11"
          aria-pressed={feedback?.rating === "not_helpful"}
          aria-expanded={editingReason}
          aria-controls={editingReason ? `${reasonId}-form` : undefined}
          disabled={pending}
          onClick={startNotHelpfulFeedback}
        >
          <ThumbsDown className="size-4" aria-hidden="true" /> Not helpful
        </Button>
        {feedback && !statusMessage && !editingReason ? (
          <span className="text-xs text-slate-500">Feedback saved</span>
        ) : null}
      </div>

      {editingReason ? (
        <form
          id={`${reasonId}-form`}
          className="mt-3 rounded-control border border-slate-200 bg-slate-50 p-3"
          onSubmit={(event) => {
            event.preventDefault();
            void save({ rating: "not_helpful", reason: reason || null });
          }}
        >
          <label htmlFor={reasonId} className="block text-xs font-medium text-slate-700">
            What could be better? <span className="font-normal text-slate-500">(optional)</span>
          </label>
          <select
            ref={reasonRef}
            id={reasonId}
            value={reason}
            onChange={(event) => setReason(event.target.value as AgentFeedbackReason | "")}
            disabled={pending}
            className="mt-2 min-h-11 w-full min-w-0 rounded-control border border-slate-300 bg-white px-3 text-sm text-slate-900 outline-none focus-visible:border-indigo-500 focus-visible:ring-2 focus-visible:ring-indigo-500/20"
          >
            <option value="">Choose a reason</option>
            {FEEDBACK_REASONS.map(([value, label]) => (
              <option key={value} value={value}>{label}</option>
            ))}
          </select>
          <div className="mt-3 flex flex-wrap gap-2">
            <Button type="submit" size="sm" className="min-h-11" disabled={pending}>
              {pending ? "Saving…" : "Send feedback"}
            </Button>
            <Button
              type="button"
              size="sm"
              variant="ghost"
              className="min-h-11"
              disabled={pending}
              onClick={cancelNotHelpfulFeedback}
            >
              Cancel
            </Button>
          </div>
        </form>
      ) : null}

      {statusMessage ? (
        <p className="mt-2 text-xs text-emerald-700" role="status">{statusMessage}</p>
      ) : null}
      {errorMessage ? (
        <p className="mt-2 text-xs text-rose-700" role="alert">{errorMessage}</p>
      ) : null}
    </section>
  );
}

function StreamingAssistant({
  text,
  response,
  progress,
  onNavigate,
}: {
  text: string;
  response: AgentMessage["structured_response"];
  progress: string | null;
  onNavigate?: (request: AgentNavigationRequest) => void;
}) {
  return (
    <article className="min-w-0 max-w-[96%] rounded-2xl rounded-bl-md border border-indigo-200 bg-white p-4 shadow-sm" aria-label="ExpenseOps Agent response in progress">
      <h2 className="sr-only">ExpenseOps answer in progress</h2>
      {progress ? (
        <div className="mb-3 flex items-center gap-2 text-sm font-medium text-indigo-700">
          <LoaderCircle className="size-4 animate-spin motion-reduce:animate-none" aria-hidden="true" />
          {progress}
        </div>
      ) : null}
      {response ? (
        <AgentResponseRenderer response={response} onNavigate={onNavigate} />
      ) : text ? (
        <p className="whitespace-pre-wrap text-sm leading-6 text-slate-800 [overflow-wrap:anywhere]">
          {text}
          <span className="ml-0.5 inline-block h-4 w-0.5 animate-pulse bg-indigo-500 align-middle motion-reduce:animate-none" aria-hidden="true" />
        </p>
      ) : (
        <div className="space-y-2" aria-hidden="true">
          <div className="ui-skeleton h-3 w-4/5" />
          <div className="ui-skeleton h-3 w-2/3" />
        </div>
      )}
    </article>
  );
}

function AgentWelcome({
  onPrompt,
  disabled,
  reviewItemCount,
  onStartReview,
}: {
  onPrompt: (prompt: string) => void;
  disabled: boolean;
  reviewItemCount: number;
  onStartReview: () => void;
}) {
  const prompts = [
    "How much did I spend last month?",
    "What household items might I need this week?",
    "Which receipts need my attention?",
    "Do I have deals relevant to things I need?",
  ];
  return (
    <div className="mx-auto flex min-h-[22rem] max-w-sm flex-col items-center justify-center px-3 text-center">
      <span className="flex size-14 items-center justify-center rounded-2xl bg-indigo-100 text-indigo-700">
        <Sparkles className="size-6" aria-hidden="true" />
      </span>
      <h2 className="mt-4 text-xl font-semibold text-slate-950">Ask ExpenseOps</h2>
      <p className="mt-2 text-sm leading-6 text-slate-600">
        Explore grounded ExpenseOps data across spending, household, receipts, deals, errands, and
        integrations without changing anything.
      </p>
      <div className="mt-5 grid w-full gap-2">
        {reviewItemCount > 0 ? (
          <>
            <button
              type="button"
              className="flex min-h-12 items-center justify-between gap-3 rounded-card border border-slate-200 bg-white px-4 py-3 text-left text-sm font-semibold text-slate-800 shadow-sm hover:border-indigo-300 hover:bg-indigo-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-600"
              onClick={onStartReview}
              disabled={disabled}
            >
              <span className="flex items-center gap-2">
                <CheckCircle2 className="size-4 shrink-0 text-indigo-600" aria-hidden="true" />
                Review my {reviewItemCount} pending purchase{reviewItemCount === 1 ? "" : "s"}
              </span>
              <ChevronRight className="size-4 shrink-0 text-indigo-600" aria-hidden="true" />
            </button>
            <p className="mt-1 text-sm leading-6 text-slate-600">
              Or ask something else
            </p>
          </>
        ) : null}
        {prompts.map((prompt) => (
          <button
            key={prompt}
            type="button"
            className="flex min-h-12 items-center justify-between gap-3 rounded-card border border-slate-200 bg-white px-4 py-3 text-left text-sm font-medium text-slate-800 shadow-sm hover:border-indigo-300 hover:bg-indigo-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-600"
            onClick={() => onPrompt(prompt)}
            disabled={disabled}
          >
            {prompt}<ChevronRight className="size-4 shrink-0 text-indigo-600" aria-hidden="true" />
          </button>
        ))}
      </div>
    </div>
  );
}

function ConversationHistory({
  open,
  onOpenChange,
  conversations,
  activeId,
  loading,
  disabled,
  onSelect,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  conversations: ReturnType<typeof useAgentController>["conversations"];
  activeId: string | null;
  loading: boolean;
  disabled: boolean;
  onSelect: (conversation: ReturnType<typeof useAgentController>["conversations"][number]) => void;
}) {
  return (
    <ResponsiveSheet
      open={open}
      onOpenChange={onOpenChange}
      title="Agent conversations"
      description="Your private conversation history in this workspace."
      side="right"
      trigger={
        <button
          type="button"
          className="flex size-11 items-center justify-center rounded-control text-indigo-100 hover:bg-white/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white"
          aria-label="Open Agent conversation history"
          disabled={disabled}
        >
          <History className="size-5" aria-hidden="true" />
        </button>
      }
    >
      {loading ? (
        <ConversationSkeleton />
      ) : conversations.length ? (
        <div className="space-y-2">
          {conversations.map((conversation) => (
            <button
              key={conversation.public_id}
              type="button"
              className={`flex min-h-14 w-full items-center justify-between gap-3 rounded-control border px-3 py-2 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-600 ${activeId === conversation.public_id ? "border-indigo-300 bg-indigo-50 text-indigo-950" : "border-slate-200 bg-white text-slate-800 hover:bg-slate-50"}`}
              aria-current={activeId === conversation.public_id ? "true" : undefined}
              onClick={() => onSelect(conversation)}
            >
              <span className="min-w-0">
                <span className="block truncate text-sm font-semibold">{conversation.title || "Untitled conversation"}</span>
                <span className="mt-0.5 block text-xs text-slate-500">Updated {relativeDate(conversation.updated_at)}</span>
              </span>
              <ChevronRight className="size-4 shrink-0" aria-hidden="true" />
            </button>
          ))}
        </div>
      ) : (
        <p className="rounded-card border border-dashed border-slate-300 p-5 text-center text-sm text-slate-600">No saved conversations yet.</p>
      )}
    </ResponsiveSheet>
  );
}

function ConversationSkeleton() {
  return (
    <div className="space-y-4" aria-label="Loading Agent conversation">
      <div className="ui-skeleton ml-auto h-16 w-3/4" />
      <div className="ui-skeleton h-28 w-11/12" />
      <div className="ui-skeleton h-20 w-4/5" />
    </div>
  );
}

function relativeDate(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "recently";
  return new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric" }).format(date);
}
