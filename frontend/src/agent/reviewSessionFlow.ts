import type { AgentProposalState } from "./contracts";

/**
 * Splitwise posts are handed to the durable outbox worker rather than executed
 * inline, so a confirmed proposal sits in one of these states until the worker
 * claims it and the provider call returns.
 */
export function isActionPending(status: AgentProposalState): boolean {
  return status === "confirmed" || status === "executing";
}

export type ReviewAdvanceDecision = "advance" | "pending" | "hold";

/**
 * The review queue may only advance once the server marks the proposal
 * completed -- advance_after_proposal ignores any other status -- so a proposal
 * still owned by the executor is reported separately from a terminal failure.
 */
export function reviewAdvanceDecision(status: AgentProposalState): ReviewAdvanceDecision {
  if (status === "completed") return "advance";
  if (isActionPending(status)) return "pending";
  return "hold";
}
