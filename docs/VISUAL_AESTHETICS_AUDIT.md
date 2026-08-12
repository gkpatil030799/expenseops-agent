# ExpenseOps Visual Aesthetics Audit

**Audit date:** August 12, 2026  
**Perspective:** Principal UI designer and frontend engineer  
**Scope:** Visual aesthetics, hierarchy, interaction clarity, responsive presentation, charts, theme, navigation, cards, typography, spacing, and screen-level composition.  
**Code changes made during audit:** None

## Executive assessment

ExpenseOps has a strong visual foundation, but it currently resembles a polished internal operations dashboard more than a premium consumer product.

The navy and indigo identity, neutral canvas, typography, cards, and chart styling should remain. The primary visual problem is competition: too many cards, controls, badges, filters, and charts receive equal weight. A more attractive and intuitive experience will come from removing visual noise, creating stronger hierarchy, and making one primary object or action dominate each viewport.

### Visual scorecard

| Area | Score |
| --- | ---: |
| Desktop aesthetics | 7.5/10 |
| Mobile aesthetics | 4/10 |
| Visual consistency | 7/10 |
| Information hierarchy | 5.5/10 |
| Interaction clarity | 5/10 |

## Design direction

Refine rather than reinvent:

- keep the dark navy-to-indigo page headers;
- keep indigo as the single primary action color;
- keep the light analytical canvas and neutral cards;
- reduce card inflation and nested borders;
- make each screen communicate one dominant task;
- build deliberate desktop and mobile compositions rather than shrinking desktop layouts.

## Highest-impact changes

### 1. Rebuild the global navigation

Current issues:

- Settings interrupts the product navigation order.
- Expense Review uses a different selected color from other sections.
- Mobile navigation clips horizontally.
- The workspace badge is visually disconnected from account controls.

Recommended desktop structure:

```text
ExpenseOps       Expenses   Household   Deals              Workspace ▾
```

Recommended mobile structure:

```text
Top bar: ExpenseOps                         Workspace/avatar

Bottom navigation:
Expenses        Household        Deals        Account
```

Concrete changes:

- Rename “Expense review” to “Expenses.”
- Order sections as Expenses → Household → Deals.
- Move Settings into the workspace/avatar menu.
- Use indigo for every selected state.
- Make the desktop header approximately 64px tall.
- Use 44–48px mobile navigation targets.
- Eliminate document-level horizontal overflow.

Relevant implementation: `frontend/src/App.tsx`

### 2. Create one shared page-header system

Expense Review, Household Ops, and Deals currently use related but inconsistent dark headers. Settings has no comparable page identity.

Create one reusable page header with:

- navy-to-indigo surface;
- consistent 16px radius;
- optional eyebrow or contextual label;
- page title;
- one supporting sentence;
- no more than two actions;
- compact and expanded variants.

Use contextual titles:

- Review: “Review your expenses”
- Insights: “Spending insights”
- Activity: “Expense activity”
- Household: “Household operations”
- Deals: “Deals worth your attention”
- Settings: “Account & workspace”

Recommended dimensions:

- Desktop height: 112–128px
- Mobile height: 136–160px
- Desktop title: 30–32px
- Mobile title: 25–27px

The Insights view should not continue showing an Expense Review title.

### 3. Stop making every section an equal white card

When every region uses the same border, radius, padding, and shadow, nothing looks important.

Define four surface levels:

| Surface | Usage |
| --- | --- |
| Command | Dark headers and major recommendations |
| Primary card | Main task, route, chart, or review object |
| Secondary panel | KPIs, supporting analysis, integrations |
| Row | History, members, merchants, and receipt lines |

Rules:

- Reserve noticeable shadows for headers, overlays, sticky controls, and primary cards.
- Use flat surfaces and borders for ordinary content.
- Prefer dividers to nested cards.
- Avoid multiple bordered boxes inside another bordered box.

### 4. Collapse filters throughout the application

Review and Insights devote excessive vertical space to controls.

For Expense Review, replace the large filter card with a 48px toolbar:

```text
Search transactions        Date ▾        Filters (2)
```

For Insights:

- Keep `7D`, `30D`, `3M`, `YTD`, and `Custom` visible.
- Put month and quarter variants inside a date-range menu.
- Limit the desktop sticky filter bar to approximately 64px.
- On mobile, show date range, spending basis, and `Filters (n)` only.
- Open advanced filters in a desktop popover or mobile bottom sheet.
- Render active filters as removable chips.

Relevant implementation: `frontend/src/App.tsx`, `frontend/src/components/InsightsDashboard.tsx`

### 5. Simplify each transaction card

The current collapsed card displays a colored danger border, multiple badges, several actions, a recommendation, and a disclosure control at once.

Recommended structure:

```text
[Merchant initial]  Merchant                          $125.00
                    Account • Category • Date          Needs review

Recommendation: Likely shared

[ Personal ]                                [ Split expense ]
```

Concrete changes:

- Remove rose and amber left borders based only on transaction amount.
- Reserve rose for actual failures.
- Use slate for spending and emerald for refunds or credits.
- Replace simultaneous status pills with one authoritative status.
- Show AI classification as a sentence rather than another badge.
- Move Draft and secondary actions into an overflow menu.
- Add a 36–40px merchant initial or logo.
- Display account/card and category.
- Use full-width two-column actions on mobile.
- Target 112–128px collapsed height on desktop and 156–180px on mobile.

### 6. Turn splitting into a visible three-step flow

The expanded transaction experience should communicate progress:

```text
1  Choose people
2  Choose split
3  Review and post
```

Design changes:

- Use one softly tinted workspace surface.
- Replace nested bordered cards with section dividers.
- Add avatars or initials to participant rows.
- Render selected participants as compact chips.
- Keep allocation validation and totals visible.
- Make the summary and Post action sticky on mobile.
- Apply indigo emphasis only to the active step.

### 7. Give Insights a stronger narrative

The dashboard currently reads like a gallery of charts. It should answer questions in order:

1. How much did I spend?
2. What changed?
3. Where did the money go?
4. Which merchants or people drove it?
5. What deeper pattern exists?

Recommended order:

```text
Spending insights + exact date range
KPI hierarchy
What Changed
Spend Over Time + Category Composition
Top Merchants + Personal/Shared
Category Trend and deeper analysis
```

Move “What Changed” directly beneath KPIs.

### 8. Create true KPI hierarchy

All five metrics currently look equally important.

Recommended twelve-column desktop composition:

- Total Spend: four columns
- Personal Spend: two columns
- Shared Spend: two columns
- Transactions: two columns
- Average Transaction: two columns

Total Spend should use:

- a subtle indigo tint;
- a 32–36px value;
- the strongest comparison treatment.

Supporting metrics should use:

- 22–24px values;
- smaller neutral delta pills.

On mobile:

- render Total Spend full width;
- render remaining metrics in a two-column grid.

Show the exact comparison range once above the metrics, for example:

> Compared with July 14–August 12

### 9. Upgrade chart presentation

#### Spend Over Time

- Desktop height: approximately 320px
- Mobile height: approximately 240px
- Use a 2px line.
- Add a subtle 6–8% indigo area fill for Total.
- Show no more than five X-axis labels.
- Use 11–12px axis text.
- Format large values compactly, such as `$1k`.
- Add a vertical crosshair and visible focus points.
- Replace native browser tooltips with a dark anchored tooltip.
- Add a visible Personal/Shared legend.
- Differentiate series using both color and line style.

#### Category composition

- Increase donut diameter from 144px to approximately 168px.
- Place the legend beside the chart at normal laptop widths.
- Make legend rows at least 40px tall.
- Align amounts and percentages using tabular numerals.
- Highlight the associated slice when a legend row is focused or hovered.
- Explain which categories are grouped into synthetic “Other.”

#### Category Trend

- Add Y-axis ticks and horizontal gridlines.
- Increase visible text to at least 11px.
- Skip date labels adaptively.
- Add edge fades on mobile to signal horizontal scrolling.
- Use a target height of 280–300px.

### 10. Remove redundant analytics

The category donut and category bars convey substantially overlapping information.

Combine them into one card:

```text
Category spending
[ Composition ] [ Comparison ]
```

Place Personal vs Shared beside Shared With on desktop. A two-value bar should not occupy a complete full-width card.

Only genuinely interactive rows should look clickable. Remove hover, pointer, and focus treatments from no-op chart rows.

### 11. Make Household Today a command center

Four large zero-value cards produce a long empty mobile page.

When everything is clear, use one compact surface:

```text
✓ Household is caught up
No receipts, unresolved errands, or staples need attention.
```

When work exists, show real prioritized objects:

```text
Next action
Resolve destination for “Return Amazon package”

Also waiting
1 receipt • 2 likely staples
```

Additional changes:

- Use a household-wide page title rather than an errand-specific title.
- Keep the complete “While I’m Out” experience primarily under Errands.
- On Today, show a compact route recommendation rather than the complete builder.
- Structure route building as Start → Stops → End → Time.
- Use a visual route rail for generated plans.
- Compress all-clear metrics.
- Use tab edge fades on mobile.

Relevant implementation: `frontend/src/components/HouseholdOpsPage.tsx`

### 12. Redesign Settings around clear sections

Settings currently appears as a long wall of equally weighted cards.

Recommended desktop composition:

```text
┌──────────────────┬─────────────────────────────────────┐
│ Account          │ Selected settings content           │
│ Workspace        │                                     │
│ Members          │                                     │
│ Connections      │                                     │
│ Expense tools    │                                     │
│ Learned behavior │                                     │
│ Privacy          │                                     │
└──────────────────┴─────────────────────────────────────┘
```

On mobile, use a list of settings destinations.

Additional changes:

- Hide the onboarding checklist after setup completes.
- During onboarding, show explicit progress such as “2 of 4 essentials complete.”
- Add recognizable provider logos to Gmail, Plaid, Telegram, and Splitwise.
- Show the connected identity under each integration.
- Separate personal connections from workspace-managed services.
- Move Sign out into the account menu.
- Place destructive controls in a dedicated Danger zone.

Relevant implementation: `frontend/src/components/AccountSettingsPage.tsx`

## Deals-specific improvements

- Use the shared navy-to-indigo header treatment.
- When Gmail is disconnected and no deals exist, combine the warning and empty state into one purposeful onboarding surface.
- Add merchant logo or initial tiles.
- Make the offer value, such as `25% off`, more prominent than supporting copy.
- Keep Open Deal and Save visible.
- Move Dismiss, Not relevant, and Mute merchant into an overflow menu.
- Show a compact trust or destination-domain label near Open Deal.
- Use expiry urgency sparingly:
  - neutral for distant or unknown expiry;
  - amber within seven days;
  - rose only when expiring today.
- Avoid a large empty panel beneath a separate Gmail connection warning.

Relevant implementation: `frontend/src/components/PromotionsPage.tsx`

## Expense Review finishing details

- Remove duplicated “Recently handled” and “Recent activity” headings.
- Keep five recent rows on the main Review screen.
- Use 56–64px history rows.
- Align merchant to the left, amount/status centrally, and time/Undo to the right.
- Keep Undo visible on touch layouts.
- Use tabular numerals for all financial amounts.
- Retain only one refresh or synchronization affordance.

## Recommended visual tokens

### Colors

```text
Canvas                 #F6F7FB
Primary ink            #0F172A
Secondary text         #475569
Muted text             #64748B
Border                 #E2E8F0
Primary indigo         #4F46E5
Primary hover          #4338CA
Primary tint           #EEF2FF
Success                #059669
Warning                #B45309
Error                  #E11D48
```

### Dimensions

```text
Page max width         1360px
Desktop gutter         24–32px
Mobile gutter          16px
Page section gap       24–32px
Card radius            16px
Control radius         10–12px
Card padding desktop   24px
Card padding mobile    16–20px
Desktop control height 40px
Mobile control height  44–48px
```

### Typography

```text
Display/page title     32/38, semibold
Mobile page title      26/32, semibold
Section heading        20/28, semibold
Card title             16/24, semibold
Body                   14/21
Caption                12/17
Minimum visible text   12px
Financial figures      tabular numerals
```

### Shadows and motion

- Standard cards: border plus `0 1px 2px rgba(15, 23, 42, 0.04)`.
- Reserve larger shadows for overlays and sticky controls.
- Hover transition: 150ms.
- Disclosure transition: 200ms.
- Sheet or modal transition: 220–250ms.
- Avoid bouncing or decorative animation.
- Honor reduced-motion preferences.

## Recommended implementation order

1. Global navigation and mobile shell
2. Shared page-header and surface variants
3. Simplified Expense Review cards and filters
4. Insights hierarchy and chart composition
5. Household Today and route-builder hierarchy
6. Settings information architecture
7. Deals card hierarchy
8. Responsive, accessibility, and visual-regression validation

## Final recommendation

The strongest aesthetic direction is not a visual rewrite. Preserve the dark header, indigo identity, light canvas, and existing neutral card language. Make the product feel premium by reducing competition, removing redundant surfaces, simplifying each decision, and ensuring every viewport has one unmistakable primary object or action.
