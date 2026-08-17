import {
  Archive,
  Bot,
  ChevronRight,
  History,
  LoaderCircle,
  MessageSquarePlus,
  PanelRightClose,
  RotateCcw,
  Send,
  Sparkles,
  ThumbsDown,
  ThumbsUp,
  X,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ResponsiveSheet } from "@/components/ui/responsive-sheet";
import { Surface } from "@/components/ui/surface";

import { AgentResponseRenderer, type AgentNavigationRequest } from "./AgentResponseRenderer";
import type {
  AgentFeedbackCreate,
  AgentFeedbackOut,
  AgentFeedbackReason,
  AgentMessage,
  AgentPageContext,
} from "./contracts";
import { useAgentController } from "./useAgentController";

export default function AgentExperience({
  mode,
  pageContext,
  contextLabel,
  onClearContext,
  onRestoreContext,
  onClose,
  onNavigate,
}: {
  mode: "panel" | "page";
  pageContext: AgentPageContext | null;
  contextLabel: string;
  onClearContext?: () => void;
  onRestoreContext?: () => void;
  onClose?: () => void;
  onNavigate?: (request: AgentNavigationRequest) => void;
}) {
  const controller = useAgentController(pageContext);
  const [draft, setDraft] = useState("");
  const [historyOpen, setHistoryOpen] = useState(false);
  const [showJumpToLatest, setShowJumpToLatest] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  const composerRef = useRef<HTMLTextAreaElement>(null);
  const autoScrollRef = useRef(true);
  const initialFocusSetRef = useRef(false);

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
    await controller.sendMessage(value);
    composerRef.current?.focus();
  }

  const hasConversation = Boolean(controller.activeConversation);
  return (
    <Surface
      variant="command"
      padding="none"
      className={
        mode === "panel"
          ? "relative flex h-full min-h-0 min-w-0 flex-col overflow-hidden rounded-none border-indigo-200 shadow-primary lg:sticky lg:top-3 lg:h-[calc(100dvh-1.5rem)] lg:min-h-[38rem] lg:rounded-card"
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
              <p className="truncate text-xs text-indigo-100">Read-only · grounded in your data</p>
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
        {controller.initializing || controller.loadingConversation ? (
          <ConversationSkeleton />
        ) : controller.messages.length || controller.streamingText || controller.streamingResponse ? (
          <ol className="space-y-4" aria-label="Agent conversation messages">
            {controller.messages.map((message) => (
              <li key={message.public_id}>
                <AgentMessageView
                  message={message}
                  onNavigate={onNavigate}
                  onSubmitFeedback={controller.submitFeedback}
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
        ) : (
          <AgentWelcome
            disabled={controller.conversationBusy || controller.sending}
            onPrompt={(value) => void controller.sendMessage(value)}
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
            disabled={controller.sending || controller.conversationBusy}
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
          Read-only. ExpenseOps will not post, split, buy, or change anything here.
        </p>
      </form>
      <p className="sr-only" aria-live="polite" aria-atomic="true">{controller.announcement}</p>
    </Surface>
  );
}

function AgentMessageView({
  message,
  onNavigate,
  onSubmitFeedback,
}: {
  message: AgentMessage;
  onNavigate?: (request: AgentNavigationRequest) => void;
  onSubmitFeedback: (
    messagePublicId: string,
    payload: AgentFeedbackCreate,
  ) => Promise<AgentFeedbackOut>;
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
    <article className="max-w-[96%] rounded-2xl rounded-bl-md border border-slate-200 bg-white p-4 shadow-sm" aria-label="ExpenseOps Agent response">
      {message.text ? <p className="whitespace-pre-wrap text-sm leading-6 text-slate-800 [overflow-wrap:anywhere]">{message.text}</p> : null}
      {message.structured_response ? (
        <AgentResponseRenderer response={message.structured_response} onNavigate={onNavigate} />
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
    <article className="max-w-[96%] rounded-2xl rounded-bl-md border border-indigo-200 bg-white p-4 shadow-sm" aria-label="ExpenseOps Agent response in progress">
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
}: {
  onPrompt: (prompt: string) => void;
  disabled: boolean;
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
