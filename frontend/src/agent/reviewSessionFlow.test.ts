import { describe, expect, it } from "vitest";

import { isActionPending, reviewAdvanceDecision } from "./reviewSessionFlow";

describe("isActionPending", () => {
  it("treats confirmed as pending so polling starts before the worker claims it", () => {
    expect(isActionPending("confirmed")).toBe(true);
  });

  it("treats executing as pending", () => {
    expect(isActionPending("executing")).toBe(true);
  });

  it("treats terminal states as settled", () => {
    for (const status of ["completed", "cancelled", "expired", "failed", "ambiguous"] as const) {
      expect(isActionPending(status)).toBe(false);
    }
  });

  it("does not treat an unconfirmed proposal as pending execution", () => {
    expect(isActionPending("awaiting_confirmation")).toBe(false);
  });
});

describe("reviewAdvanceDecision", () => {
  it("advances only on completed, matching advance_after_proposal", () => {
    expect(reviewAdvanceDecision("completed")).toBe("advance");
  });

  it("reports a still-executing split as pending rather than advancing", () => {
    // Advancing here would silently no-op server-side and strand the queue.
    expect(reviewAdvanceDecision("executing")).toBe("pending");
    expect(reviewAdvanceDecision("confirmed")).toBe("pending");
  });

  it("holds the card on terminal failure so the outcome stays visible", () => {
    for (const status of ["failed", "expired", "ambiguous", "cancelled"] as const) {
      expect(reviewAdvanceDecision(status)).toBe("hold");
    }
  });
});
