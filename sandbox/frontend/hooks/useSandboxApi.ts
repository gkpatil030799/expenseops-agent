import { useCallback, useEffect, useState } from "react";

import { sandboxApiClient } from "../api";
import type { SandboxEvent, SandboxStatus } from "../types";

export function useSandboxApi() {
  const [status, setStatus] = useState<SandboxStatus | null>(null);
  const [events, setEvents] = useState<SandboxEvent[]>([]);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState<string | null>(null);

  const loadStatus = useCallback(async () => {
    try {
      const data = await sandboxApiClient.status();
      setStatus(data);
      return data;
    } catch (err) {
      setError(err);
      throw err;
    }
  }, []);

  const loadEvents = useCallback(async (traceId?: string) => {
    try {
      const data = await sandboxApiClient.events({ trace_id: traceId || undefined, limit: 100 });
      setEvents(data.events || []);
      return data.events || [];
    } catch (err) {
      setError(err);
      throw err;
    }
  }, []);

  const runAction = useCallback(async <T,>(label: string, action: () => Promise<T>) => {
    setLoading(label);
    setError(null);
    try {
      return await action();
    } catch (err) {
      setError(err);
      throw err;
    } finally {
      setLoading(null);
    }
  }, []);

  useEffect(() => {
    void loadStatus();
    void loadEvents();
  }, [loadEvents, loadStatus]);

  return {
    status,
    events,
    error,
    loading,
    setError,
    loadStatus,
    loadEvents,
    runAction,
  };
}
