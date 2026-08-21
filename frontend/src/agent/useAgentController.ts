import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError } from "@/lib/api";
import { isActionPending } from "./reviewSessionFlow";
import {
  AGENT_RESPONSE_UNAVAILABLE_MESSAGE,
  AgentProtocolError,
} from "./validation";
import {
  AgentStreamError,
  advanceAgentReviewSession,
  archiveAgentConversation,
  cancelAgentAction,
  confirmAgentAction,
  createAgentConversation,
  interpretAgentReviewMessage,
  listAgentConversations,
  loadAgentConversation,
  proposeAgentReviewAction,
  skipAgentReviewCandidate,
  startAgentReviewSession,
  stopAgentReviewSession,
  submitAgentFeedback,
  streamAgentTurn,
} from "./api";
import type {
  AgentActionConfirmationBlock,
  AgentConversation,
  AgentFeedbackCreate,
  AgentFeedbackOut,
  AgentMessage,
  AgentPageContext,
  AgentReviewSessionInterpretResult,
  AgentReviewSessionOut,
  AgentStreamEvent,
  AgentStructuredResponse,
} from "./contracts";

export type AgentUiError = {
  code: string;
  message: string;
  retryable: boolean;
  correlationId?: string | null;
};

export type AgentController = {
  conversations: AgentConversation[];
  activeConversation: AgentConversation | null;
  messages: AgentMessage[];
  initializing: boolean;
  loadingConversation: boolean;
  conversationBusy: boolean;
  sending: boolean;
  progressMessage: string | null;
  streamingText: string;
  streamingResponse: AgentStructuredResponse | null;
  error: AgentUiError | null;
  canRetry: boolean;
  announcement: string;
  openConversation: (conversation: AgentConversation) => Promise<void>;
  newConversation: () => Promise<void>;
  archiveConversation: () => Promise<void>;
  sendMessage: (text: string) => Promise<void>;
  retryLastMessage: () => Promise<void>;
  submitFeedback: (
    messagePublicId: string,
    payload: AgentFeedbackCreate,
  ) => Promise<AgentFeedbackOut>;
  confirmAction: (
    block: AgentActionConfirmationBlock,
  ) => Promise<AgentActionConfirmationBlock>;
  cancelAction: (
    block: AgentActionConfirmationBlock,
  ) => Promise<AgentActionConfirmationBlock>;
  isActionPending: (proposalId: string) => boolean;
  reload: () => Promise<void>;
  clearError: () => void;
  reviewSession: AgentReviewSessionOut | null;
  reviewSessionBusy: boolean;
  reviewSessionError: AgentUiError | null;
  startReviewSession: () => Promise<void>;
  proposeReviewAction: (
    action: "mark_personal" | "post_splitwise_expense",
    options?: { participantNames?: string[]; groupName?: string | null },
  ) => Promise<AgentActionConfirmationBlock | null>;
  interpretReviewMessage: (text: string) => Promise<AgentReviewSessionInterpretResult | null>;
  advanceReviewSession: (proposalPublicId: string) => Promise<void>;
  skipReviewCandidate: () => Promise<void>;
  stopReviewSession: () => Promise<void>;
  clearReviewSession: () => void;
};

type RetryAttempt = {
  conversationPublicId: string;
  text: string;
  clientMessageId: string;
  pageContext: AgentPageContext | null;
  reuseClientMessageId: boolean;
};

// Splitwise posts are handed to the durable outbox worker and can genuinely
// take longer than any UI should block on -- the manual (non-Agent) split
// flow does not wait for them at all; it reports "queued" immediately and
// lets the post settle in the background. This short poll exists only to
// catch the common fast case so a quick confirmation feels instant; it is
// deliberately not sized to cover the slow tail. Ending on "confirmed" or
// "executing" is an expected resting state, not a failure to reach one --
// see reviewAdvanceDecision and the "queued" statusCopy for how each surface
// presents that state honestly instead of appearing stuck.
const ACTION_STATUS_POLL_DELAYS_MS = [300, 600, 900, 1_200] as const;

function findActionBlock(
  messages: AgentMessage[],
  proposalId: string,
): AgentActionConfirmationBlock | null {
  for (const message of messages) {
    for (const block of message.structured_response?.blocks || []) {
      if (block.type === "action_confirmation" && block.proposal_id === proposalId) return block;
    }
  }
  return null;
}

function waitFor(milliseconds: number): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, milliseconds));
}

export function useAgentController(pageContext: AgentPageContext | null): AgentController {
  const [conversations, setConversations] = useState<AgentConversation[]>([]);
  const [activeConversation, setActiveConversation] = useState<AgentConversation | null>(null);
  const [messages, setMessages] = useState<AgentMessage[]>([]);
  const [initializing, setInitializing] = useState(true);
  const [loadingConversation, setLoadingConversation] = useState(false);
  const [conversationActionPending, setConversationActionPending] = useState(false);
  const [sending, setSending] = useState(false);
  const [progressMessage, setProgressMessage] = useState<string | null>(null);
  const [streamingText, setStreamingText] = useState("");
  const [streamingResponse, setStreamingResponse] = useState<AgentStructuredResponse | null>(null);
  const [error, setError] = useState<AgentUiError | null>(null);
  const [canRetry, setCanRetry] = useState(false);
  const [announcement, setAnnouncement] = useState("");
  const [pendingActionIds, setPendingActionIds] = useState<Set<string>>(() => new Set());
  const pendingActionIdsRef = useRef<Set<string>>(new Set());
  const streamController = useRef<AbortController | null>(null);
  const sendingRef = useRef(false);
  const conversationBusyRef = useRef(true);
  const activeRef = useRef<AgentConversation | null>(null);
  const loadSequence = useRef(0);
  const retryAttempt = useRef<RetryAttempt | null>(null);

  const loadConversationById = useCallback(async (conversation: AgentConversation) => {
    const sequence = ++loadSequence.current;
    conversationBusyRef.current = true;
    setLoadingConversation(true);
    setError(null);
    try {
      const detail = await loadAgentConversation(conversation.public_id);
      if (sequence !== loadSequence.current) return;
      activeRef.current = detail.conversation;
      setActiveConversation(detail.conversation);
      setMessages(detail.messages);
      retryAttempt.current = null;
      setCanRetry(false);
    } catch (cause) {
      if (sequence !== loadSequence.current) return;
      setError(uiError(cause, "ExpenseOps could not load this conversation."));
    } finally {
      if (sequence === loadSequence.current) {
        conversationBusyRef.current = false;
        setLoadingConversation(false);
      }
    }
  }, []);

  const refreshConversations = useCallback(async () => {
    const values = await listAgentConversations();
    setConversations(values);
    return values;
  }, []);

  const initialize = useCallback(async () => {
    setInitializing(true);
    setError(null);
    try {
      const values = await refreshConversations();
      if (values[0]) await loadConversationById(values[0]);
    } catch (cause) {
      setError(uiError(cause, "ExpenseOps could not load Agent conversations."));
    } finally {
      conversationBusyRef.current = false;
      setInitializing(false);
    }
  }, [loadConversationById, refreshConversations]);

  useEffect(() => {
    void initialize();
    return () => {
      streamController.current?.abort();
      loadSequence.current += 1;
    };
  }, [initialize]);

  const newConversation = useCallback(async () => {
    if (sendingRef.current || conversationBusyRef.current) return;
    conversationBusyRef.current = true;
    setConversationActionPending(true);
    loadSequence.current += 1;
    streamController.current?.abort();
    setError(null);
    setStreamingText("");
    setStreamingResponse(null);
    setProgressMessage(null);
    retryAttempt.current = null;
    setCanRetry(false);
    try {
      const conversation = await createAgentConversation();
      activeRef.current = conversation;
      setActiveConversation(conversation);
      setMessages([]);
      setConversations((current) => [conversation, ...current]);
      setAnnouncement("New conversation ready.");
    } catch (cause) {
      setError(uiError(cause, "ExpenseOps could not start a conversation."));
    } finally {
      conversationBusyRef.current = false;
      setConversationActionPending(false);
    }
  }, []);

  const openConversation = useCallback(
    async (conversation: AgentConversation) => {
      if (
        sendingRef.current ||
        conversationBusyRef.current ||
        conversation.public_id === activeRef.current?.public_id
      ) return;
      streamController.current?.abort();
      setStreamingText("");
      setStreamingResponse(null);
      setProgressMessage(null);
      retryAttempt.current = null;
      setCanRetry(false);
      await loadConversationById(conversation);
    },
    [loadConversationById],
  );

  const archiveConversation = useCallback(async () => {
    const conversation = activeRef.current;
    if (!conversation || sendingRef.current || conversationBusyRef.current) return;
    conversationBusyRef.current = true;
    setConversationActionPending(true);
    setError(null);
    try {
      await archiveAgentConversation(conversation.public_id);
      const remaining = conversations.filter(
        (value) => value.public_id !== conversation.public_id,
      );
      setConversations(remaining);
      activeRef.current = null;
      setActiveConversation(null);
      setMessages([]);
      retryAttempt.current = null;
      setCanRetry(false);
      if (remaining[0]) await loadConversationById(remaining[0]);
      setAnnouncement("Conversation archived.");
    } catch (cause) {
      setError(uiError(cause, "ExpenseOps could not archive this conversation."));
    } finally {
      conversationBusyRef.current = false;
      setConversationActionPending(false);
    }
  }, [conversations, loadConversationById]);

  const executeMessage = useCallback(
    async (rawText: string, retry: RetryAttempt | null = null) => {
      const text = rawText.trim();
      if (!text || sendingRef.current || conversationBusyRef.current) return;
      sendingRef.current = true;
      setSending(true);
      setError(null);
      setStreamingText("");
      setStreamingResponse(null);
      setProgressMessage("Thinking…");
      setAnnouncement("ExpenseOps Agent is working.");
      const controller = new AbortController();
      streamController.current = controller;
      try {
        let conversation = activeRef.current;
        if (retry && conversation?.public_id !== retry.conversationPublicId) {
          throw new AgentStreamError({
            code: "conversation_changed",
            message: "Open the original conversation before retrying this request.",
            retryable: false,
          });
        }
        if (!conversation) {
          conversation = await createAgentConversation(text.slice(0, 80));
          activeRef.current = conversation;
          setActiveConversation(conversation);
          setConversations((current) => [conversation as AgentConversation, ...current]);
        }
        const clientMessageId =
          retry?.reuseClientMessageId === true ? retry.clientMessageId : createMessageId();
        // A retry must preserve the original turn snapshot, including an
        // intentional null after the user cleared page context.
        const contextSnapshot = retry ? retry.pageContext : pageContext;
        const attempt: RetryAttempt = {
          conversationPublicId: conversation.public_id,
          text,
          clientMessageId,
          pageContext: contextSnapshot,
          reuseClientMessageId: true,
        };
        retryAttempt.current = attempt;
        setCanRetry(false);
        const optimistic: AgentMessage = {
          public_id: `pending-${clientMessageId}`,
          conversation_public_id: conversation.public_id,
          role: "user",
          text,
          structured_response: null,
          client_message_id: clientMessageId,
          feedback_eligible: false,
          feedback: null,
          created_at: new Date().toISOString(),
        };
        setMessages((current) =>
          current.some((message) => message.client_message_id === clientMessageId)
            ? current
            : [...current, optimistic],
        );
        let terminalType: "run_completed" | "run_failed" | null = null;
        let completedAssistant: AgentMessage | null = null;
        await streamAgentTurn({
          conversationPublicId: conversation.public_id,
          text,
          clientMessageId,
          pageContext: contextSnapshot,
          signal: controller.signal,
          onEvent: (event) => {
            if (event.type === "assistant_completed") completedAssistant = event.message;
            if (event.type === "run_completed" || event.type === "run_failed") {
              terminalType = event.type;
            }
            applyEvent(event, {
              setProgressMessage,
              setStreamingText,
              setStreamingResponse,
              setError,
              setAnnouncement,
            });
          },
        });
        if (completedAssistant) {
          const assistant = completedAssistant as AgentMessage;
          setMessages((current) =>
            current.some((message) => message.public_id === assistant.public_id)
              ? current
              : [...current, assistant],
          );
        }
        if (terminalType === "run_completed") {
          retryAttempt.current = null;
          setCanRetry(false);
        } else if (terminalType === "run_failed") {
          retryAttempt.current = { ...attempt, reuseClientMessageId: false };
          setCanRetry(true);
        }
        const detail = await loadAgentConversation(conversation.public_id);
        activeRef.current = detail.conversation;
        setActiveConversation(detail.conversation);
        setMessages(detail.messages);
        await refreshConversations();
      } catch (cause) {
        if (controller.signal.aborted) return;
        setError(uiError(cause, "The Agent response was interrupted. Please retry."));
        if (retryAttempt.current) setCanRetry(true);
        const conversation = activeRef.current;
        if (conversation) {
          try {
            const detail = await loadAgentConversation(conversation.public_id);
            setMessages(detail.messages);
            const triggerIndex = detail.messages.findIndex(
              (message) =>
                message.role === "user" &&
                message.client_message_id === retryAttempt.current?.clientMessageId,
            );
            const hasCanonicalAssistant =
              triggerIndex >= 0 &&
              detail.messages.slice(triggerIndex + 1).some((message) => message.role === "assistant");
            if (hasCanonicalAssistant) {
              retryAttempt.current = null;
              setCanRetry(false);
            }
          } catch {
            // Keep the optimistic state and explicit retry affordance visible.
          }
        }
      } finally {
        if (streamController.current === controller) streamController.current = null;
        sendingRef.current = false;
        setSending(false);
        setProgressMessage(null);
        setStreamingText("");
        setStreamingResponse(null);
      }
    },
    [pageContext, refreshConversations],
  );

  const sendMessage = useCallback(
    async (text: string) => executeMessage(text),
    [executeMessage],
  );

  const retryLastMessage = useCallback(async () => {
    const attempt = retryAttempt.current;
    if (attempt) await executeMessage(attempt.text, attempt);
  }, [executeMessage]);

  const reload = useCallback(async () => {
    const conversation = activeRef.current;
    setError(null);
    if (conversation) await loadConversationById(conversation);
    else await initialize();
  }, [initialize, loadConversationById]);

  const submitFeedback = useCallback(
    async (messagePublicId: string, payload: AgentFeedbackCreate) => {
      const feedback = await submitAgentFeedback(messagePublicId, payload);
      setMessages((current) =>
        current.map((message) =>
          message.public_id === messagePublicId ? { ...message, feedback } : message,
        ),
      );
      return feedback;
    },
    [],
  );

  const updateActionBlock = useCallback((updated: AgentActionConfirmationBlock) => {
    setMessages((current) =>
      current.map((message) => {
        if (!message.structured_response) return message;
        let changed = false;
        const blocks = message.structured_response.blocks.map((block) => {
          if (
            block.type === "action_confirmation" &&
            block.proposal_id === updated.proposal_id
          ) {
            changed = true;
            return updated;
          }
          return block;
        });
        return changed
          ? { ...message, structured_response: { ...message.structured_response, blocks } }
          : message;
      }),
    );
  }, []);

  const decideAction = useCallback(
    async (
      block: AgentActionConfirmationBlock,
      decision: "confirm" | "cancel",
    ) => {
      if (pendingActionIdsRef.current.has(block.proposal_id)) return block;
      pendingActionIdsRef.current.add(block.proposal_id);
      setPendingActionIds(new Set(pendingActionIdsRef.current));
      try {
        const updated =
          decision === "confirm"
            ? await confirmAgentAction(block.proposal_id, {
                proposal_version: block.proposal_version,
              })
            : await cancelAgentAction(block.proposal_id, {
                proposal_version: block.proposal_version,
              });
        updateActionBlock(updated);
        setAnnouncement(
          updated.status === "completed"
            ? "Agent action completed."
            : updated.status === "cancelled"
              ? "Agent action cancelled."
              : "Agent action status updated.",
        );
        const conversation = activeRef.current;
        let latest = updated;
        if (conversation) {
          let detail = await loadAgentConversation(conversation.public_id);
          setMessages(detail.messages);
          latest = findActionBlock(detail.messages, block.proposal_id) || latest;
          if (decision === "confirm" && isActionPending(latest.status)) {
            for (const delay of ACTION_STATUS_POLL_DELAYS_MS) {
              await waitFor(delay);
              detail = await loadAgentConversation(conversation.public_id);
              setMessages(detail.messages);
              latest = findActionBlock(detail.messages, block.proposal_id) || latest;
              if (!isActionPending(latest.status)) break;
            }
          }
        }
        return latest;
      } finally {
        pendingActionIdsRef.current.delete(block.proposal_id);
        setPendingActionIds(new Set(pendingActionIdsRef.current));
      }
    },
    [updateActionBlock],
  );

  const confirmAction = useCallback(
    (block: AgentActionConfirmationBlock) => decideAction(block, "confirm"),
    [decideAction],
  );
  const cancelAction = useCallback(
    (block: AgentActionConfirmationBlock) => decideAction(block, "cancel"),
    [decideAction],
  );

  const [reviewSession, setReviewSession] = useState<AgentReviewSessionOut | null>(null);
  const [reviewSessionBusy, setReviewSessionBusy] = useState(false);
  const [reviewSessionError, setReviewSessionError] = useState<AgentUiError | null>(null);
  const reviewSessionRef = useRef<AgentReviewSessionOut | null>(null);
  const reviewSessionBusyRef = useRef(false);
  reviewSessionRef.current = reviewSession;

  const ensureConversation = useCallback(async (): Promise<AgentConversation> => {
    if (activeRef.current) return activeRef.current;
    const conversation = await createAgentConversation();
    activeRef.current = conversation;
    setActiveConversation(conversation);
    setConversations((current) => [conversation, ...current]);
    return conversation;
  }, []);

  const startReviewSession = useCallback(async () => {
    if (reviewSessionBusyRef.current) return;
    reviewSessionBusyRef.current = true;
    setReviewSessionBusy(true);
    setReviewSessionError(null);
    try {
      const conversation = await ensureConversation();
      const session = await startAgentReviewSession(conversation.public_id);
      setReviewSession(session);
    } catch (cause) {
      setReviewSessionError(uiError(cause, "ExpenseOps could not start the review session."));
    } finally {
      reviewSessionBusyRef.current = false;
      setReviewSessionBusy(false);
    }
  }, [ensureConversation]);

  const proposeReviewAction = useCallback(
    async (
      action: "mark_personal" | "post_splitwise_expense",
      options?: { participantNames?: string[]; groupName?: string | null },
    ) => {
      if (reviewSessionBusyRef.current) return null;
      const session = reviewSessionRef.current;
      if (!session) return null;
      reviewSessionBusyRef.current = true;
      setReviewSessionBusy(true);
      setReviewSessionError(null);
      try {
        return await proposeAgentReviewAction(session.public_id, { action, ...options });
      } catch (cause) {
        setReviewSessionError(uiError(cause, "ExpenseOps could not prepare that action."));
        return null;
      } finally {
        reviewSessionBusyRef.current = false;
        setReviewSessionBusy(false);
      }
    },
    [],
  );

  const interpretReviewMessage = useCallback(async (text: string) => {
    if (reviewSessionBusyRef.current) return null;
    const session = reviewSessionRef.current;
    if (!session) return null;
    reviewSessionBusyRef.current = true;
    setReviewSessionBusy(true);
    setReviewSessionError(null);
    try {
      return await interpretAgentReviewMessage(session.public_id, text);
    } catch (cause) {
      setReviewSessionError(uiError(cause, "ExpenseOps could not read that message."));
      return null;
    } finally {
      reviewSessionBusyRef.current = false;
      setReviewSessionBusy(false);
    }
  }, []);

  const advanceReviewSession = useCallback(async (proposalPublicId: string) => {
    const session = reviewSessionRef.current;
    if (!session) return;
    setReviewSessionBusy(true);
    try {
      setReviewSession(await advanceAgentReviewSession(session.public_id, proposalPublicId));
    } catch (cause) {
      setReviewSessionError(uiError(cause, "ExpenseOps could not update the review session."));
    } finally {
      setReviewSessionBusy(false);
    }
  }, []);

  const skipReviewCandidate = useCallback(async () => {
    const session = reviewSessionRef.current;
    if (!session) return;
    setReviewSessionBusy(true);
    try {
      setReviewSession(await skipAgentReviewCandidate(session.public_id));
    } catch (cause) {
      setReviewSessionError(uiError(cause, "ExpenseOps could not skip this item."));
    } finally {
      setReviewSessionBusy(false);
    }
  }, []);

  const stopReviewSession = useCallback(async () => {
    const session = reviewSessionRef.current;
    if (!session) return;
    setReviewSessionBusy(true);
    try {
      setReviewSession(await stopAgentReviewSession(session.public_id));
    } catch (cause) {
      setReviewSessionError(uiError(cause, "ExpenseOps could not stop the review session."));
    } finally {
      setReviewSessionBusy(false);
    }
  }, []);

  const clearReviewSession = useCallback(() => {
    setReviewSession(null);
    setReviewSessionError(null);
  }, []);

  return {
    conversations,
    activeConversation,
    messages,
    initializing,
    loadingConversation,
    conversationBusy: initializing || loadingConversation || conversationActionPending,
    sending,
    progressMessage,
    streamingText,
    streamingResponse,
    error,
    canRetry,
    announcement,
    openConversation,
    newConversation,
    archiveConversation,
    sendMessage,
    retryLastMessage,
    submitFeedback,
    confirmAction,
    cancelAction,
    isActionPending: (proposalId: string) => pendingActionIds.has(proposalId),
    reload,
    clearError: () => setError(null),
    reviewSession,
    reviewSessionBusy,
    reviewSessionError,
    startReviewSession,
    proposeReviewAction,
    interpretReviewMessage,
    advanceReviewSession,
    skipReviewCandidate,
    stopReviewSession,
    clearReviewSession,
  };
}

function applyEvent(
  event: AgentStreamEvent,
  setters: {
    setProgressMessage: (value: string | null) => void;
    setStreamingText: (updater: (value: string) => string) => void;
    setStreamingResponse: (value: AgentStructuredResponse | null) => void;
    setError: (value: AgentUiError | null) => void;
    setAnnouncement: (value: string) => void;
  },
): void {
  switch (event.type) {
    case "run_started":
      setters.setProgressMessage(event.resumed ? "Restoring the saved answer…" : "Thinking…");
      return;
    case "tool_started":
      setters.setProgressMessage(event.message);
      setters.setAnnouncement(event.message);
      return;
    case "tool_completed":
      setters.setProgressMessage(event.message);
      setters.setAnnouncement(event.message);
      return;
    case "assistant_delta":
      setters.setStreamingText((current) => current + event.delta);
      return;
    case "structured_response":
      setters.setStreamingResponse(event.response);
      setters.setProgressMessage(null);
      return;
    case "assistant_completed":
      return;
    case "run_completed":
      setters.setProgressMessage(null);
      setters.setAnnouncement("Agent response complete.");
      return;
    case "run_failed":
      setters.setProgressMessage(null);
      setters.setError({
        code: event.code,
        message: event.message,
        retryable: event.retryable,
      });
      setters.setAnnouncement("The Agent request could not be completed.");
  }
}

function uiError(cause: unknown, fallback: string): AgentUiError {
  if (cause instanceof AgentStreamError) {
    return {
      code: cause.code,
      message: cause.message,
      retryable: cause.retryable,
      correlationId: cause.correlationId,
    };
  }
  if (cause instanceof AgentProtocolError) {
    return {
      code: "invalid_agent_response",
      message: AGENT_RESPONSE_UNAVAILABLE_MESSAGE,
      retryable: true,
    };
  }
  if (cause instanceof ApiError && typeof cause.detail === "string" && cause.detail.trim()) {
    // Action tools reject with a specific, user-facing reason (an unresolved
    // friend, an ambiguous group, a changed target). Replacing it with the
    // caller's generic fallback tells the user only that something failed.
    return {
      code: "agent_request_failed",
      message: cause.detail,
      retryable: cause.retryable,
      correlationId: cause.correlationId,
    };
  }
  return { code: "agent_request_failed", message: fallback, retryable: true };
}

function createMessageId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return `web-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}
