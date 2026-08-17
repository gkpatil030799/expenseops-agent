# Lifestyle & Dining Intelligence (Day 10)

## Status

Day 10 adds a read-only lifestyle view over canonical ExpenseOps transactions. It introduces no
write tool, background action, notification, household item, dependency, or database migration.

## Build vs integrate

- ExpenseOps' corrected `SpendingInsightsService` remains the financial authority. The new
  `canonical_rows` seam is reused for purchase/credit direction, pending/removed exclusion,
  currency, review state, and card-vs-actual-share projection.
- `LifestyleDiningService` is a bounded subtype projection over those rows, not a second spending
  engine. It performs no per-transaction model call.
- The existing OpenAI Agents SDK runtime, Responses API model, structured output, registry,
  budgets, persistence, grounding, SSE, and semantic renderer are reused. Official OpenAI
  documentation confirms the configured GPT-4.1 mini supports image input, function calling,
  Structured Outputs, and the Responses API:
  https://developers.openai.com/api/docs/models/gpt-4.1-mini
- Existing `PromotionRankingService` remains the only deal-ranking system. Its existing factual
  merchant/category affinity can benefit from the same transaction history; Day 10 does not add a
  recommendation or spending-pressure path.
- Day 9 receipt classifications remain authoritative for receipt learning. `routine_consumption`
  and `dining_or_experience` lines remain non-replenishment evidence and never become household
  staples through Day 10.

## Classification boundary

The projection supports `coffee`, `restaurants`, `delivery`, `nightlife`, and `all`.

- Provider category evidence is primary.
- A very small generic merchant-language fallback (`coffee`, `cafe`, `espresso`) helps legacy
  generic Food & Drink rows without creating a merchant catalog.
- Groceries/supermarkets are excluded from lifestyle activity.
- Nightlife is included only for explicit bar/nightlife/pub category evidence.
- Food & Dining rows without sufficient subtype evidence remain `uncertain`. They are counted and
  disclosed, but excluded from subtype totals instead of being guessed.

No output infers health, addiction, relationships, religion, protected traits, or moral quality.

## Canonical metrics

`get_lifestyle_dining_insights` v1.0 is one strict READ tool with:

- an explicit date range (maximum remains 730 days);
- activity type;
- optional merchant/account/currency scope;
- personal/shared/all review scope;
- card or actual-share basis;
- optional equal-length immediately preceding comparison.

The output includes purchase-only total/frequency/average, credits separately, personal/shared/
unreviewed reconciliation, weekday/weekend reconciliation, top merchants, activity mix, current/
prior comparison, and current/prior unknown Splitwise allocation counts. Card-basis unknown counts
must be zero. Unknown actual-share allocations are omitted, never guessed.

The Agent canonical composer—not the model—constructs the `lifestyle_summary` v1.0 card and all
financial prose. Tool results remain tenant-scoped and flow through the existing evidence bundle.
The total run budget remains three tool calls, four provider turns, 30 seconds, and 800 output
tokens.

## Deterministic measurement

Command:

```bash
.venv/bin/python scripts/benchmark_lifestyle_day10.py --repetitions 100 --warmups 10
```

On the fixed 120-transaction SQLite fixture:

| Scenario | Median service latency | p95 service latency |
|---|---:|---:|
| Coffee | 1.394 ms | 1.463 ms |
| Restaurants | 1.397 ms | 1.451 ms |
| Delivery | 1.390 ms | 1.469 ms |
| Nightlife | 1.394 ms | 1.453 ms |
| All known lifestyle | 1.441 ms | 1.500 ms |

This measures local query/classification/projection only, not network/provider latency and not an
SLO. The read registry grows from seven tools / 9,426 compact JSON bytes at the Day 9 checkpoint to
eight tools / 10,996 bytes: +1,570 bytes (+16.66%). Budgets were not increased.

## Limitations

- Classification is deliberately bounded. New or ambiguous provider labels remain uncertain.
- Merchant fallback is lexical, not a continuously maintained merchant directory.
- Weekday/weekend reflects transaction dates, not local purchase time because no reliable user
  transaction timezone exists.
- Credits are reported separately; they do not reduce purchase spend or activity frequency.
- Actual-share analytics can be incomplete when Splitwise allocation evidence is missing; the
  omission is explicit.
- Lifestyle history does not itself tell the user to spend, and it does not create a new deal
  ranking architecture.
