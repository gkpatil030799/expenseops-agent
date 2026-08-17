# Proactive Attention Center

## Status and boundary

Day 13 adds a bounded, read-only Attention Center behind
`AGENT_PROACTIVE_ENABLED`. The flag remains off in checked-in release configuration. No
production feature was enabled and no deployment infrastructure was changed.

When the flag is false:

- `/api/context` does not advertise the Attention surface;
- the frontend does not render the tab or call `/api/attention`;
- attention evaluation and Telegram delivery raise a feature-disabled error before any tool
  call;
- the existing read and confirmation-gated write Agent remain unchanged.

The feature never creates an `AgentActionProposal`, `FinancialOperation`, or outbox event. It
does not call an OpenAI model and cannot post, purchase, edit a receipt, or complete an errand.

## Build versus integrate

ExpenseOps already had the required mature primitives, so Day 13 integrates them instead of
building a second agent, scheduler, or prioritization engine:

- Day 6 `attention_summary` is the only attention ranking and rendering contract.
- The existing tenant-bound Agent READ registry supplies transaction reviews, receipt reviews,
  integration status, replenishment predictions, relevant promotions, and errands.
- Promotion Intelligence remains the source of deal relevance.
- Replenishment remains the source of likely-due household state.
- The existing Telegram service is the optional delivery channel.
- PostgreSQL remains canonical state; no cache, vector database, or external notification
  platform was added.

The only custom infrastructure is user-owned attention preferences and a small delivery ledger
for deduplication, cooldowns, and daily caps. This is necessary because no existing ExpenseOps
table represented these controls or delivery outcomes.

## Deterministic triggers and composition

Opening or refreshing the in-app Attention tab is the implemented deterministic trigger. The
service performs one bounded, validated READ for each enabled category and composes the result
with the code-owned Day 6 priority/domain ordering. This is not an autonomous loop and does not
increase the interactive Agent budget of three tool calls and four provider turns; there is no
provider turn at all.

The optional `/api/attention/telegram-digest` operation is a tenant-bound digest delivery seam
for an approved scheduler or manual invocation. This repository does not install a background
job or modify Railway. Immediate delivery is stored as a prepared preference but is intentionally
inactive; a digest invocation skips it. Event-hook delivery should be considered only after human
review and measured notification quality.

## User controls and anti-spam behavior

Preferences are scoped by both workspace and user and protected by FORCE RLS:

- master enable/disable;
- enabled canonical categories;
- in-app and Telegram channels;
- digest or prepared-immediate mode;
- IANA timezone and wrapping quiet hours;
- maximum Telegram alerts per local day;
- Telegram cooldown.

The in-app center is pull-based and sends no notification. Telegram digest delivery:

1. checks the rollout flag and channel/mode controls;
2. applies timezone-aware quiet hours;
3. composes current canonical evidence;
4. skips an empty digest;
5. hashes the strict canonical response and local date;
6. enforces exact duplicate, cooldown, and daily-cap checks;
7. requires exactly one enabled Telegram identity owned by the authenticated workspace/user and
   passes that chat ID explicitly (there is no process-level fallback);
8. persists a unique pending claim before the send, and counts it toward cooldowns and daily caps
   only after a confirmed successful delivery.

The service persists a unique `pending` claim before contacting Telegram. Concurrent invocations
therefore skip before sending. Success advances the claim to `sent`; a provider exception becomes
`ambiguous` and is never blindly retried, while a known failure becomes `failed`. Telegram copy
escapes all canonical data before HTML delivery.

## Security and tenancy

The service derives workspace and user identifiers only from the authenticated SQLAlchemy
session. Client payloads contain preferences, never identity. Every evidence call goes through
the same strict Agent tool schemas, server-issued dispatch proof, read-only policy, tenant
context, output validation, and hostile-key checks used by the interactive Agent.

Migration `20260817_0032` enables and forces workspace RLS on both new tables with identical
`USING` and `WITH CHECK` workspace predicates. Application queries additionally constrain user
ownership. Corrupt stored category data fails closed before any evidence call.

## Accepted beta limitations

- The center evaluates current state on page load/refresh; it is not a push-updating live feed.
- No scheduler is installed. Telegram digest delivery is available as a safe seam but remains
  operationally unwired under the no-deploy/no-Railway rule.
- Immediate Telegram triggers are prepared but inactive.
- Evidence reads remain sequential for predictable tenant-session and audit behavior.
- Source projections are bounded. The existing `items_truncated` contract truthfully warns when
  matching records may be omitted.

These limitations preserve privacy, reliability, and rollout control while giving users a useful
in-app surface without requiring an Agent question.

## Day 13 validation record

- Backend: 1,312 passed, 17 opt-in/environment skips.
- Explicit Day 7 security/release map: 106 passed.
- Frontend unit: 105 passed; TypeScript and production build passed.
- ESLint: zero errors and 20 pre-existing warnings.
- Playwright: 299 passed and 157 configured skips across Chromium, mobile Chromium, Firefox,
  WebKit, visual regression, and accessibility coverage.
- Day 13 focused browser matrix: 13 passed and 3 configured mobile-project skips; mobile widths
  320, 375, and 390 px passed without document overflow.
- Alembic: fresh install and `20260817_0031 -> 20260817_0032` upgrade passed; current head is
  `20260817_0032`; autogenerate check found no drift.
- Dependencies: Python and npm audits found no known vulnerabilities.
- Live synthetic read checks: Day 10 lifestyle smoke passed 1/1; the final six-scenario Agent
  observation passed 6/6 with exact tool sets and no execution anomaly.
- Registered surface: 12 tools total (8 READ and 4 proposal-only); 16,231 JSON-schema bytes,
  approximately 4,058 tokens at four bytes/token. The read-only subset is 11,054 bytes.
- Deterministic composition benchmark: application median 0.088 ms / p95 0.422 ms and total
  median 0.140 ms / p95 0.820 ms across 2,500 measured scenario repetitions. This excludes
  database handlers and provider latency and is not an SLO.
