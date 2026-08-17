export type ApiErrorKind = "aborted" | "http" | "malformed" | "network" | "offline" | "provider" | "session";

export class ApiError extends Error {
  readonly kind: ApiErrorKind;
  readonly status: number | null;
  readonly correlationId: string;
  readonly detail: string;
  readonly retryable: boolean;
  readonly payload: unknown;

  constructor({
    kind,
    message,
    status = null,
    correlationId,
    detail = message,
    retryable = false,
    payload = null,
  }: {
    kind: ApiErrorKind;
    message: string;
    status?: number | null;
    correlationId: string;
    detail?: string;
    retryable?: boolean;
    payload?: unknown;
  }) {
    super(message);
    this.name = "ApiError";
    this.kind = kind;
    this.status = status;
    this.correlationId = correlationId;
    this.detail = detail;
    this.retryable = retryable;
    this.payload = payload;
  }
}

export type ApiStatusDetail =
  | { state: "slow"; requestId: string; path: string }
  | { state: "settled"; requestId: string; path: string }
  | { state: "session-expired"; requestId: string; path: string }
  | { state: "offline"; requestId: string; path: string }
  | { state: "provider-outage"; requestId: string; path: string }
  | { state: "stale"; requestId: string; path: string }
  | { state: "fresh"; requestId: string; path: string };

const SLOW_REQUEST_MS = 2_500;

export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const requestId = createRequestId();
  const readsData = !options.method || options.method.toUpperCase() === "GET";
  const headers = new Headers(options.headers);
  headers.set("Accept", "application/json");
  headers.set("X-Request-ID", requestId);
  const browserManagedBody =
    (typeof FormData !== "undefined" && options.body instanceof FormData) ||
    (typeof Blob !== "undefined" && options.body instanceof Blob);
  if (options.body != null && !headers.has("Content-Type") && !browserManagedBody) {
    headers.set("Content-Type", "application/json");
  }

  let slow = false;
  const slowTimer = globalThis.setTimeout(() => {
    slow = true;
    dispatchApiStatus({ state: "slow", requestId, path });
  }, SLOW_REQUEST_MS);

  try {
    let response: Response;
    try {
      response = await fetch(path, {
        credentials: "same-origin",
        ...options,
        headers,
      });
    } catch (cause) {
      if (isAbortError(cause) || options.signal?.aborted) {
        throw new ApiError({
          kind: "aborted",
          message: "Request cancelled",
          correlationId: requestId,
          payload: cause,
        });
      }
      const offline = typeof navigator !== "undefined" && navigator.onLine === false;
      if (offline) dispatchApiStatus({ state: "offline", requestId, path });
      if (readsData) dispatchApiStatus({ state: "stale", requestId, path });
      throw new ApiError({
        kind: offline ? "offline" : "network",
        message: offline ? "You appear to be offline." : "ExpenseOps could not reach the server.",
        correlationId: requestId,
        retryable: true,
        payload: cause,
      });
    }

    const correlationId = response.headers.get("X-Request-ID") || requestId;
    const raw = response.status === 204 || response.status === 205 ? "" : await response.text();
    const parsed = parseResponseBody(raw);

    if (!response.ok) {
      const detail = errorDetail(parsed, response.statusText || "Request failed");
      const sessionExpired = response.status === 401;
      const providerOutage = response.status === 502 || response.status === 503 || response.status === 504;
      if (sessionExpired) dispatchApiStatus({ state: "session-expired", requestId: correlationId, path });
      if (providerOutage) dispatchApiStatus({ state: "provider-outage", requestId: correlationId, path });
      if (readsData) dispatchApiStatus({ state: "stale", requestId: correlationId, path });
      throw new ApiError({
        kind: sessionExpired ? "session" : providerOutage ? "provider" : "http",
        message: detail,
        detail,
        status: response.status,
        correlationId,
        retryable: response.status === 408 || response.status === 429 || response.status >= 500,
        payload: parsed,
      });
    }

    if (!raw.trim()) {
      if (readsData) dispatchApiStatus({ state: "fresh", requestId: correlationId, path });
      return undefined as T;
    }
    if (parsed === MALFORMED) {
      if (readsData) dispatchApiStatus({ state: "stale", requestId: correlationId, path });
      throw new ApiError({
        kind: "malformed",
        message: "The server returned an unreadable response.",
        status: response.status,
        correlationId,
        retryable: true,
        payload: raw,
      });
    }
    if (readsData) dispatchApiStatus({ state: "fresh", requestId: correlationId, path });
    return parsed as T;
  } finally {
    globalThis.clearTimeout(slowTimer);
    if (slow) dispatchApiStatus({ state: "settled", requestId, path });
  }
}

export function apiErrorMessage(error: unknown, fallback = "The request failed.") {
  if (error instanceof ApiError) return error.detail;
  if (typeof error === "string" && error.trim()) return error;
  if (error && typeof error === "object") {
    const detail = (error as { detail?: unknown }).detail;
    if (typeof detail === "string" && detail.trim()) return detail;
  }
  return fallback;
}

const MALFORMED = Symbol("malformed-response");

function parseResponseBody(raw: string): unknown | typeof MALFORMED {
  if (!raw.trim()) return undefined;
  try {
    return JSON.parse(raw) as unknown;
  } catch {
    return MALFORMED;
  }
}

function errorDetail(payload: unknown, fallback: string) {
  if (payload === MALFORMED || payload == null) return fallback;
  if (typeof payload === "string" && payload.trim()) return payload;
  if (typeof payload === "object") {
    const detail = (payload as { detail?: unknown }).detail;
    if (typeof detail === "string" && detail.trim()) return detail;
  }
  return fallback;
}

function createRequestId() {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") return crypto.randomUUID();
  return `web-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

function dispatchApiStatus(detail: ApiStatusDetail) {
  if (typeof window === "undefined") return;
  window.dispatchEvent(new CustomEvent<ApiStatusDetail>("expenseops:api-status", { detail }));
}

function isAbortError(value: unknown) {
  return typeof DOMException !== "undefined" && value instanceof DOMException && value.name === "AbortError";
}
