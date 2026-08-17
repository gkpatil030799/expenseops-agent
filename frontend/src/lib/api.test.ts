import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, api, apiErrorMessage } from "@/lib/api";

describe("api client", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn());
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("accepts successful 204 and empty responses", async () => {
    vi.mocked(fetch)
      .mockResolvedValueOnce(new Response(null, { status: 204 }))
      .mockResolvedValueOnce(new Response("", { status: 200 }));

    await expect(api<void>("/empty-204")).resolves.toBeUndefined();
    await expect(api<void>("/empty-200")).resolves.toBeUndefined();
  });

  it("parses JSON and forwards a request correlation ID", async () => {
    vi.mocked(fetch).mockResolvedValue(new Response(JSON.stringify({ ok: true }), {
      status: 200,
      headers: { "Content-Type": "application/json", "X-Request-ID": "server-request" },
    }));

    await expect(api<{ ok: boolean }>("/ok")).resolves.toEqual({ ok: true });
    const request = vi.mocked(fetch).mock.calls[0][1];
    expect(new Headers(request?.headers).get("X-Request-ID")).toBeTruthy();
  });

  it("lets the browser set multipart boundaries for receipt photos", async () => {
    vi.mocked(fetch).mockResolvedValue(
      new Response(JSON.stringify({ ok: true }), { status: 200 }),
    );
    const form = new FormData();
    form.append("file", new Blob(["synthetic"], { type: "image/jpeg" }), "receipt.jpg");

    await api("/api/replenishment/receipts/upload", { method: "POST", body: form });

    const request = vi.mocked(fetch).mock.calls[0][1];
    expect(new Headers(request?.headers).has("Content-Type")).toBe(false);
    expect(request?.body).toBe(form);
  });

  it("throws a structured error with server detail and correlation ID", async () => {
    vi.mocked(fetch).mockResolvedValue(new Response(JSON.stringify({ detail: "Provider unavailable" }), {
      status: 503,
      headers: { "X-Request-ID": "trace-503" },
    }));

    const error = await api("/failure").catch((value) => value);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ kind: "provider", status: 503, detail: "Provider unavailable", correlationId: "trace-503", retryable: true });
    expect(apiErrorMessage(error)).toBe("Provider unavailable");
  });

  it("distinguishes session expiry and malformed success responses", async () => {
    vi.mocked(fetch)
      .mockResolvedValueOnce(new Response(JSON.stringify({ detail: "Authentication required" }), { status: 401 }))
      .mockResolvedValueOnce(new Response("not-json", { status: 200 }));

    await expect(api("/session")).rejects.toMatchObject({ kind: "session", status: 401 });
    await expect(api("/malformed")).rejects.toMatchObject({ kind: "malformed", status: 200 });
  });

  it("distinguishes offline failures from reachable network failures", async () => {
    vi.stubGlobal("navigator", { onLine: false });
    vi.mocked(fetch).mockRejectedValue(new TypeError("Failed to fetch"));
    await expect(api("/offline")).rejects.toMatchObject({ kind: "offline", retryable: true });

    vi.stubGlobal("navigator", { onLine: true });
    await expect(api("/network")).rejects.toMatchObject({ kind: "network", retryable: true });
  });
});
