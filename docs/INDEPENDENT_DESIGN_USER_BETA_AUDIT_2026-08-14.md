# Independent Readiness Review — ExpenseOps

**Source:** Independent audit supplied by the user as an attached text export
**Repository:** `gkpatil030799/expenseops-agent`
**Reviewed revision:** `fce8c5b` (`main`)
**Audit date:** August 14, 2026
**Planned launch date in the review:** August 15, 2026
**Evaluation standard:** First pre-approved design-user beta, with a separate GA verdict

This is a normalized, durable Markdown copy of the independent review supplied after the original
Claude Code artifact could not be read anonymously. Its substantive findings and release-scope
distinction are preserved. Reconciliation with the current full-application audit appears in
[FULL_APPLICATION_LAUNCH_REAUDIT_2026-08-14.md](./FULL_APPLICATION_LAUNCH_REAUDIT_2026-08-14.md).

## Scope: what “launch” means

The review distinguishes two releases:

1. **First design-user beta:** one pre-approved, trusted external user is invited through
   [first-design-user-onboarding.md](./first-design-user-onboarding.md). The review scores the code
   against this narrower, operator-supported release.
2. **General availability:** the product is opened to all customers. The historical backend and UX
   audits already returned NO-GO, and the independent review does not change that conclusion.

## Bottom line

### First design-user beta — Conditional GO

The independent review considers the architecture and code suitable for the narrow beta after three
classes of conditions close:

- fix the environment-dependent failing test;
- complete the live infrastructure preflight; and
- communicate the deletion limitation explicitly.

The review originally characterized these as one small engineering fix plus operator confirmations.
The later live-production audit found additional blockers; see the reconciliation document before
using this conditional verdict.

### General availability — NO-GO

Backups/PITR activation, a timed restore drill, alert-delivery proof, and full GA validation/re-audit
remain open.

## Independent scorecard for the design-user beta bar

| Discipline | Score/status | Independent assessment |
| --- | --- | --- |
| Security | 9/10 | RLS design, OIDC, CSRF Origin checks, fail-closed readiness, and encrypted key rotation are strong in code |
| Backend correctness | 8/10 | Financial journal and durable outbox are implemented; one environment-dependent test failed |
| UX | 7.5/10 | Most historical P0/P1 trust failures appear remediated for an operator-supported beta |
| UI/visual | 8/10 | Consistent system with Axe and cross-browser Playwright evidence; bundle/lint debt remains |
| Documentation | 8.5/10 | Honest limitations and accurate environment setup overall; Railway deployment claim is wrong |
| Live operations | Unverifiable | Railway backup, runtime role, OAuth test-user, and webhook state were outside this code-only review |

## Backend and reliability

### Blocker — backend suite is environment-dependent

The independent run reported `582 passed, 1 failed` in
`test_location_and_while_out_api_work_with_current_location`. The test expected
`estimates_are_routed` to be false, but received true because ambient Google routing configuration
caused the API path to make a live request to Google Routes.

Evidence cited:

- `tests/test_location_aware_household_ops.py:560`
- `app/services/route_planning_service.py`

The review judges this likely a leaky test rather than a product regression but correctly rejects a
red, environment-dependent suite as a release baseline. It recommends mocking/injecting the routing
provider and auditing the suite for other tests that depend on provider credentials being absent.

### Pass — financial correctness foundations are real

The review directly inspected transaction and outbox/journal code and confirmed implemented
idempotency keys, leases, and ambiguous-state recovery. It considers the historical duplicate-post
and crash-after-provider-success concerns materially addressed in the core architecture.

### Fix soon — migration execution is not infrastructure-as-code

`railway.json` is only a schema stub. The dedicated migration job described by the production
runbook is not represented in the repository, leaving actual behavior dependent on manual Railway
configuration and operator memory.

### Later — lint is at its warning ceiling

The review observed 20 warnings with a `--max-warnings=20` ceiling. The next warning can break CI for
an unrelated change. It recommends removing the warnings or tightening the threshold after cleanup.

## Security

### Pass — readiness is designed to fail closed

The review confirmed `/readiness` independently checks migration state, RLS policies, runtime role,
OIDC, shared rate limiting, trusted hosts, and HTTPS requirements and can return 503 when production
requirements are not met.

### Pass — general CSRF, CSP, and error-handling architecture

The review found:

- explicit Origin checks for cookie-authenticated state changes;
- restrictive general Content Security Policy wiring;
- generic unhandled errors with correlation IDs rather than provider trace leakage;
- versioned encryption keys and a rotation runbook; and
- no tracked `.env`, database, or obvious secret file.

This conclusion was based on code inspection. The subsequent production-browser review found that
the otherwise restrictive CSP blocks Plaid Link specifically; see the reconciliation.

### Confirm live — code controls depend on Railway state

The review could not verify whether production uses the restricted runtime database identity,
whether PITR/backups are active, or whether restore and webhook checks have passed. It requires the
first-design-user preflight to be treated as mandatory.

## UX and UI

### Pass — historical trust failures appear closed for the beta scope

The independent review traced code for draft constraints, route fingerprints/staleness, and
per-currency Insights isolation and concluded that several historical trust failures were fixed in
the core code.

### Later — deletion limitation must be disclosed

The review relied on historical V6 evidence saying self-service deletion was absent and could be
handled by the operator for one design user if disclosed. Current code now exposes self-service
deletion, while the later audit found its implementation incomplete. That newer evidence supersedes
the historical assumption.

### Later — initial JavaScript bundle is large

The production build emits one approximately 604 KB minified / 167 KB gzip bundle without route
splitting. The review treats this as acceptable for one user on a good connection but debt before
the audience grows.

## README and documentation

### Pass — candid limitations

The independent review praises the README for documenting real limitations rather than presenting
marketing-only claims. It found the integration environment-variable instructions broadly aligned
with the code.

### Fix — Railway deployment claim is false

The README says `railway.json` runs migrations and configures `/health`. The file contains only its
schema declaration and encodes neither behavior. The review requires either the documentation or the
infrastructure file to become the source of truth.

### Note — no license declared

The repository explicitly states that it is all-rights-reserved. The review treats this as
acceptable for a private beta but recommends a deliberate licensing decision before wider public
distribution or contribution.

## Independent prelaunch checklist

The review requires:

1. Mock or inject Google Routes in the failing test and run the full suite green on the exact
   release revision.
2. Audit ordinary tests for implicit live-provider access.
3. Complete the existing first-design-user preflight:
   - `/health` and `/readiness` green;
   - fresh backup;
   - expected Alembic revision;
   - both Google OAuth test users configured;
   - all four redirect URIs configured;
   - Telegram webhook verified through its secret header.
4. Run owner bootstrap, verify the migrated data checklist, remove `OIDC_BOOTSTRAP_EMAIL`, and remove
   legacy user-bound environment variables.
5. Tell the design user explicitly about the deletion limitation.

## Independent-review limitation

The source review states that it read code, ran test/build suites, and checked security middleware,
auth, CI, Docker, Railway configuration files, and historical audit documents. It also states that
it is not a substitute for a penetration test, assistive-technology accessibility review, legal
review, or live Railway/provider verification.
