# Unified ExpenseOps Agent Foundation

## Purpose and current safety posture

The unified ExpenseOps Agent is a new orchestration boundary over the product's existing domain
services. It is additive: Expense Review, Insights, Activity, Household Ops, Deals, Settings,
Telegram, Plaid, Splitwise, Gmail, receipts, routing, and background workers continue to work
without it.

This foundation provides persistence, typed contracts, policy enforcement, durable proposals,
page context, and observability for a future in-app agent. It deliberately does **not** provide a
visible chat interface, execute a real financial action, post to Splitwise, purchase anything, or
change existing product behavior.

All agent feature flags default to off:

- `AGENT_ENABLED=false`
- `AGENT_READ_TOOLS_ENABLED=false`
- `AGENT_WRITE_ACTIONS_ENABLED=false`
- `AGENT_PROACTIVE_ENABLED=false`
- `AGENT_PURCHASING_ENABLED=false`

Feature flags control rollout, not authorization. Server-side authentication, workspace scope,
tool policy, proposal state, and domain-service validation remain authoritative even when a flag is
enabled.

## Relationship to existing agent and Telegram code

ExpenseOps already uses the word "agent" in two narrower workflows:

- `app/services/agent_service.py` classifies an ingested transaction and builds the existing
  approval question. It is a deterministic transaction-review service, not a general
  orchestrator.
- `app/api/telegram_routes.py` and its parser/context/state services implement Telegram-specific
  interaction flows. They manage Telegram updates, buttons, conversational slot filling, receipts,
  group management, and explicit split confirmation.

Both are preserved. The unified in-app agent is separate and must not turn either module into a
general-purpose model loop. Future typed tools will call mature ExpenseOps domain services rather
than calling Telegram route functions or copying their business logic.

```text
Web or future native client
            |
            | authenticated request + validated page context
            v
Unified agent orchestration
   |        |          |
   |        |          +---- durable run/tool/proposal records
   |        +--------------- code-enforced tool policy
   +------------------------ typed domain-tool boundary
                                  |
                                  v
                    Existing ExpenseOps services
                                  |
             database / Plaid / Splitwise / Gmail / Maps
```

The model is never the source of truth for financial calculations, transaction state, receipt
decisions, replenishment predictions, promotion scoring, route selection, or provider operations.

## Persistence model

Agent records use the existing SQLAlchemy, Alembic, authentication, and tenant-scoping patterns.
They are normal ExpenseOps data, not a second datastore or authorization system.

### Conversations and messages

A conversation is owned by the authenticated workspace and user. Messages are ordered children of
that conversation and use only the platform-neutral `user` and `assistant` roles. Assistant
messages can retain a versioned `structured_response_json` payload so a web or native client can
re-render the same semantic response later. Tool activity belongs in run and tool-call records,
not in customer-visible message history.

Authenticated, feature-gated endpoints already support capability discovery, conversation create,
list, retrieve, archive, and user-message append. Conversation retrieval has bounded message
pagination and returns the total, offset, and whether more messages exist. A client-generated
message ID makes retries idempotent.

Archiving is a lifecycle boundary, not just a display preference. An archived conversation rejects
new messages, runs, proposals, and confirmations, and archiving atomically cancels its proposals
that are still awaiting confirmation. Account deletion removes the user's private agent records in
dependency-safe order. Automated retention and expiry cleanup remain future operational work.

### Runs and tool calls

An agent run records the execution boundary needed to explain and operate the system, including:

- conversation/user association;
- status and timing;
- model and prompt-version identifiers;
- validated `page_context_json`;
- request/correlation identifiers;
- aggregate token and cost metadata when the provider supplies it;
- sanitized error metadata.

Tool-call records attach to a run and preserve the registered tool identity, operation kind,
confirmation policy, status, duration, and sanitized input/output metadata. Tool-call storage must
not become a place to retain provider credentials or unbounded raw email, receipt, promotion, or
model content.

The intended read-tool lifecycle is deliberately ordered so a failed call is still observable:

```text
registry.prepare(...)
        -> persist proposed tool call
        -> mark running
        -> registry.execute_read(...)
        -> finalize completed or failed with bounded metadata
```

Preparation validates the allowlisted tool, server-owned feature flags, authenticated session
context, strict input schema, and secret/JSON guardrails before persistence. Execution re-resolves
the registered tool and validates its typed output. Prepared dispatches carry a short-lived,
tenant-bound integrity proof issued only by that registry instance; hand-built, modified,
cross-user, cross-workspace, expired, or newly disabled dispatches are rejected. Run and tool-call
transitions use guarded state updates so competing terminal transitions cannot silently overwrite
each other.

### Durable action proposals

`AgentActionProposal` is the durable boundary for a future write or external action. It stores:

- an immutable normalized action in `normalized_parameters_json`;
- a safe, code-produced customer preview in `preview_json`;
- proposal version, idempotency identity, parameter hash, and action fingerprint;
- creator, workspace, conversation, run, and tool associations;
- expiry and lifecycle state;
- execution lifecycle metadata without mutating the normalized action.

Supported lifecycle states are:

```text
awaiting_confirmation
confirmed
executing
completed
cancelled
expired
failed
ambiguous
```

For a write or external action, the registry derives the customer preview in code, with trusted
server context available for resolving canonical facts, from the same strictly validated arguments
that become the normalized action. Proposal creation requires that
the prepared dispatch and its previously persisted tool call match. A stable action fingerprint
prevents multiple active proposals for the same owner and exact action, while an owner-scoped
idempotency key makes retries return the existing proposal. Confirmation recomputes the parameter
hash and the action fingerprint covering tool identity, exact parameters, and displayed preview
before changing state; an integrity mismatch becomes `ambiguous`
instead of proceeding.

The normalized parameters stored before confirmation are the parameters a future executor must
consume. A client will confirm using only `proposal_id` and `proposal_version`; it must not
reconstruct or resend the financial operation. A second model response is never used to regenerate
an approved action. In this foundation, confirmation stops at the durable `confirmed` state. There
is no proposal executor, so confirming a proposal cannot produce a provider or financial side
effect.

## Typed tools and policy enforcement

The central tool registry describes each allowed tool with a stable name/version, input and output
contracts, operation kind, capability, confirmation requirements, and handler. The operation kinds
are:

- `read` — may eventually run automatically when both the agent and read-tool flags are enabled;
- `write` — cannot execute directly from model output;
- `external_action` — cannot execute directly and requires the durable proposal workflow.

Tool names are resolved only from the registry. Model output cannot select an arbitrary Python
callable, SQL statement, shell command, URL, provider endpoint, or module path. Policy is evaluated
in application code before handler invocation. Purchasing is a separate tool capability with an
additional server-owned flag. Existing domain services remain responsible for their own validation,
idempotency, authorization, and provider safeguards.

`prepare` never invokes a tool handler. It returns validated normalized arguments for a read, or
validated arguments plus a code-derived preview for a consequential action. Only a prepared read
can enter `execute_read`; write and external-action handlers are never called by this foundation.

The foundation contains no consequential registered production tool. Enabling a feature flag does
not create a path to a real financial or purchasing operation.

## Page context

Page context is a small, versioned, strictly validated description of what the customer is viewing.
It is not a DOM snapshot and does not contain rendered text. The contract includes:

- an allowlisted semantic surface, such as `expense_insights` or `household_receipts`;
- allowlisted typed filters, such as dates, merchant, category, currency, or the selected view;
- at most one semantic entity reference, such as a transaction, receipt, deal, or errand.

The current contract version is `1.0`. Unknown fields and unsupported values are rejected by the
backend. Text and collection sizes are bounded.

Page context never contains or selects a workspace or user. The server derives both from the
authenticated request and re-resolves every referenced entity inside that workspace. Client/model
entity IDs are hints, not proof of access.

## Structured responses

`AgentStructuredResponse` is a versioned semantic response, not HTML. Version `1.0` supports
discriminated blocks for:

- text;
- transaction lists;
- spending summaries;
- replenishment summaries;
- deal lists;
- receipt summaries;
- errand summaries;
- integration status;
- semantic in-app navigation;
- action confirmation;
- errors;
- empty states.

Amounts use integer minor units and an explicit currency. Dates/times use ISO strings. Navigation
uses semantic surfaces and entity references instead of URLs, CSS selectors, DOM IDs, or callback
names. Deal blocks likewise identify stored deals semantically. Confirmation blocks carry the
durable proposal ID/version and code-generated summary, not an executable client payload.

The TypeScript mirror lives at `frontend/src/agent/contracts.ts`. It intentionally imports no
React, browser, or styling types, so the same JSON shapes are suitable for the current web client
and a future React Native client. Backend Pydantic models are the canonical runtime validators.

## Security and prompt-injection boundary

The model and all external/user content are untrusted. Receipt text, Gmail content, promotion text,
merchant names, tool output, and user-entered notes are data, never system or developer
instructions.

The unified agent must never receive or persist:

- OAuth access or refresh tokens;
- Plaid access tokens;
- Splitwise credentials;
- Telegram bot secrets;
- OpenAI or Google Maps keys;
- database credentials or application encryption keys.

It also has no arbitrary SQL, shell, Python, filesystem, or URL-fetch capability. Tools return the
minimum domain data required for a response. Logs and stored errors use identifiers and bounded,
sanitized metadata instead of raw sensitive payloads.

## Tenancy and authorization

Every conversation, message, run, tool call, proposal, and execution is workspace scoped using the
current ExpenseOps tenancy mechanism. User ownership is recorded where the interaction is personal.
No agent-supplied parameter can set or override workspace identity.

Repository and endpoint operations must load a parent record inside the current authenticated
workspace before loading children. Guessing a UUID from another workspace must produce the same
not-found behavior as a nonexistent record. Database RLS remains an additional boundary, not a
replacement for application scoping. Trusted workers must establish explicit workspace and user
context before touching agent records or domain services.

## Observability and data minimization

Runs and tool calls preserve enough metadata to answer:

- which prompt/model/tool versions were used;
- which code policy decision was made;
- whether a proposal was created instead of an action being executed;
- latency and status by stage;
- token/cost totals when available;
- which request/correlation ID supports an incident investigation.

Raw secrets, authorization headers, provider payloads, full mailbox content, and unnecessary model
transcripts are excluded. Operational logs refer to opaque record IDs and sanitized error codes.
Account deletion is integrated with agent conversations, messages, runs, tool calls, and proposals.
Retention schedules, automatic expiry sweeps, and export policy still require explicit operational
decisions before a broader launch.

## Web and mobile integration

There is intentionally no new visible agent entry point in this foundation. With all flags off,
the existing web UI performs no agent calls and renders exactly as before.

The authenticated conversation API is an integration seam, not a shipped chatbot. There is no
model-provider loop, response renderer, desktop panel, mobile agent screen, action-confirmation
endpoint, or proposal executor in this phase.

A later web integration should use a dedicated agent module/controller rather than add more state
to the large `App.tsx`. The existing responsive sheet primitive can support a desktop contextual
panel and a mobile full-screen presentation, while the renderer maps semantic response blocks to
platform-native components.

Future clients should:

1. fetch authenticated capabilities from the backend;
2. construct only an allowlisted page-context contract;
3. render supported response blocks by `schema_version` and `type`;
4. treat unknown versions/types safely;
5. confirm actions by proposal ID/version only;
6. avoid caching financial responses or sensitive conversation data in service workers or
   lock-screen previews.

## Next implementation phase

The next bounded phase can add a read-only vertical slice without weakening the foundation:

1. Introduce the model-provider adapter and prompt builder with explicit untrusted-data boundaries.
2. Register one or two existing-service-backed read tools, such as spending insights or transaction
   search.
3. Add orchestration and run endpoints, and evolve the existing bounded offset pagination to a
   cursor if production scale requires it.
4. Build a minimal renderer for the existing structured response contracts.
5. Add the contextual desktop panel and mobile full-screen shell behind `AGENT_ENABLED`.
6. Add evaluations for tool selection, prompt injection, tenant isolation, grounded answers, cost,
   and latency.

Write actions, Splitwise posting, proactive behavior, purchasing, native mobile, and vector memory
remain out of scope until their respective flags, confirmation flow, privacy review, and failure
recovery gates are complete.

## Known technical debt and follow-up decisions

- The TypeScript contracts are currently mirrored manually from backend Pydantic models. Generate
  client types from the versioned OpenAPI/JSON Schema contract before multiple clients evolve in
  parallel.
- The generic web `api<T>` helper checks JSON syntax but does not runtime-validate `T`. The future
  agent client should add a narrow envelope/version guard or generated validator at its trust
  boundary.
- Existing page subviews keep local React state. A later integration needs small page-owned context
  adapters rather than scraping state or moving all product state into a global agent store.
- Conversation export, retention schedules, automatic proposal-expiry cleanup, and production query
  budgets require explicit operational proof before broad rollout. User account deletion is already
  integrated.
- Proposal execution leases, retries, reconciliation, and ambiguous provider outcomes must reuse
  the durable outbox/financial-operation patterns before any real write tool is registered.
