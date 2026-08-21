import type { AgentProposalState } from "./contracts";

/**
 * Splitwise posts are handed to the durable outbox worker rather than executed
 * inline, so a confirmed proposal sits in one of these states until the worker
 * claims it and the provider call returns.
 */
export function isActionPending(status: AgentProposalState): boolean {
  return status === "confirmed" || status === "executing";
}

export type ReviewAdvanceDecision = "advance" | "hold";

/**
 * The user's decision is made at confirmation time; execution continuing in
 * the background (confirmed/executing) is not a reason to hold the queue --
 * advance_after_proposal on the server accepts the same states. A provider
 * failure does not silently reopen the transaction as an actionable review
 * candidate, so advancing early here cannot mask one; it surfaces separately
 * through the recovery flow instead.
 */
export function reviewAdvanceDecision(status: AgentProposalState): ReviewAdvanceDecision {
  if (status === "awaiting_confirmation") return "hold";
  if (
    status === "cancelled" ||
    status === "expired" ||
    status === "failed" ||
    status === "ambiguous"
  ) {
    return "hold";
  }
  return "advance";
}
