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
  it("advances on completed", () => {
    expect(reviewAdvanceDecision("completed")).toBe("advance");
  });

  it("advances on a still-executing or still-queued split, matching advance_after_proposal", () => {
    // The user's decision is made at confirmation time; execution continuing
    // in the background is not a reason to hold the queue. A provider
    // failure surfaces through the separate recovery flow, not by reopening
    // this transaction as an actionable review candidate, so advancing here
    // cannot mask one.
    expect(reviewAdvanceDecision("executing")).toBe("advance");
    expect(reviewAdvanceDecision("confirmed")).toBe("advance");
  });

  it("holds the card on terminal failure so the outcome stays visible", () => {
    for (const status of ["failed", "expired", "ambiguous", "cancelled"] as const) {
      expect(reviewAdvanceDecision(status)).toBe("hold");
    }
  });

  it("holds on an unconfirmed proposal", () => {
    expect(reviewAdvanceDecision("awaiting_confirmation")).toBe("hold");
  });
});
