import { useCallback, useEffect, useRef, useState } from "react";

import {
  AgentProtocolError,
} from "./validation";
import {
  AgentStreamError,
  archiveAgentConversation,
  createAgentConversation,
  listAgentConversations,
  loadAgentConversation,
  submitAgentFeedback,
  streamAgentTurn,
} from "./api";
import type {
  AgentConversation,
  AgentFeedbackCreate,
  AgentFeedbackOut,
  AgentMessage,
  AgentPageContext,
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
  reload: () => Promise<void>;
  clearError: () => void;
};

type RetryAttempt = {
  conversationPublicId: string;
  text: string;
  clientMessageId: string;
  pageContext: AgentPageContext | null;
  reuseClientMessageId: boolean;
};

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
    reload,
    clearError: () => setError(null),
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
    return { code: "invalid_agent_response", message: cause.message, retryable: true };
  }
  return { code: "agent_request_failed", message: fallback, retryable: true };
}

function createMessageId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return `web-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}
