# Design-user metrics baseline

These are definitions, not reported results. Do not populate values until real design-user events
exist. Timestamps come from `audit_events`; qualitative time-saved estimates come from interviews.

## Onboarding

- `time_to_first_successful_login`: invitation/session start to `user_first_login`.
- `time_to_first_integration`: `user_first_login` to first successful `gmail_connected`,
  `telegram_connected`, `plaid_connected`, or `splitwise_connected`.
- `time_to_onboarding_complete`: `onboarding_started` to `onboarding_completed`. For the first beta,
  completion means Gmail and Telegram are both connected.

## Adoption

- `successful_workflows_per_user_per_week`: count of successful receipt, promotion,
  replenishment, errand, and expense workflow completion events per user and calendar week.
- `active_days_per_week`: distinct UTC dates with a successful authenticated workflow per user.

## Quality

- `user_correction_rate`: workflows with an explicit correction divided by workflows where a
  correction could be made.
- `integration_failure_rate`: failed connection/sync attempts divided by all connection/sync
  attempts, grouped by provider.
- `workflow_failure_rate`: failed workflow attempts divided by all workflow attempts.

## Autonomy

- `workflows_completed_without_manual_developer_intervention`: successful workflows without an
  intervention recorded in the design-user observation log, divided by successful workflows.

## Productivity

- `estimated_minutes_saved_per_user_per_week`: user-reported counterfactual manual time minus
  observed ExpenseOps interaction time, summed weekly. Preserve the estimate source.

## AI cost

- `estimated_ai_cost_per_workflow/user`: sum recorded model token cost for a workflow or user when
  provider usage metadata and pricing are already available. Leave unavailable rather than
  estimating from missing data.

## Collection constraints

Never include email or receipt bodies, bank details, OAuth credentials, addresses, or provider
payloads. Use user/workspace IDs, event types, timestamps, outcomes, request IDs, and safe error
classes. A formal evaluation platform and model-routing system remain out of scope.

