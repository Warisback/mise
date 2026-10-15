# Contract: API routes and data shapes

Locked before build. If you change it, change it here AND in any agent brief, or neither.
Amounts are always integers in pence. Currency `gbp`.

## Routes (all under `/api`)

| Method | Route | Body | Returns |
|---|---|---|---|
| GET | `/health` | | `{ok, stripe_key, anthropic_key, model, memory, tools[]}` |
| GET | `/capabilities` | | `{test_mode, balance, invoices, billing_meters, issuing, connect}` each `"ok"` or `"unavailable: ..."` |
| GET | `/policy` | | `{company, clauses: Clause[], counterparties: string[]}` |
| GET | `/triggers` | | `[{name, source, content}]` from `data/triggers/` |
| POST | `/runs` | `{content, source}` | `{run_id}`: the agent runs in the background |
| GET | `/runs/{run_id}?after=<event_id>` | | `{run_id, status: running\|done\|error, events: Event[]}` |
| GET | `/actions` | | `Action[]` newest first |
| GET | `/actions/{id}/why` | | `{action: Action, clauses: Clause[], memory: string[]}` |
| POST | `/actions/{id}/approve` | `{approver}` | tool result (executes the held action) |
| POST | `/actions/{id}/reject` | `{approver}` | `{action_id, status: "rejected"}` |
| POST | `/ask` | `{question}` | `{answer}` grounded in the audit trail and memory |
| GET | `/summary` | | `{spent, billed, collected, blocked, pending_approval, actions, currency}` |
| POST | `/webhooks/stripe` | Stripe event | `{received}`: `invoice.paid` counts toward `collected` |
| POST | `/demo/reset` | | `{ok}`: clears the audit trail and memory |
| GET | `/forecast` | | `Forecast` for the next service day |
| GET | `/forecast/week` | | `Forecast[]`: the next 7 days, day 1 = `/forecast` |
| GET | `/plan` | | `OrderLine[]`: what to buy, per supplier, before negotiation |
| POST | `/demo/deliver` | `{action_id, amount?}` | `Delivery`: the supplier charges the order's card. `amount` defaults to the agreed total; pass more to show the decline |

| GET | `/report.csv` | | Bookkeeper report: one row per order (date, supplier, items, qty, agreed unit price, net, VAT, gross, category, stripe_id, status, clauses, reason) |

`/summary` also returns `{over_order_avoided, negotiated_savings, overcharge_blocked, saved_total, saved_per_year, food_cost_pct}` (pence, except `food_cost_pct`).

Telegram (supplier side): `/start harbour` links a chat to a supplier; any message containing a price is read as a quote; `/charge <pounds>` charges that order's card.

## Shapes

```ts
Clause = { id: "SP-1", title: string, text: string, rule: { type, applies_to: "spend"|"collect", amount? } }

Action = {
  id: "act_…", ts: number, run_id, tool, kind: "spend"|"collect"|"inbound",
  amount: number, currency, counterparty,
  outcome: "allow"|"needs_approval"|"deny",
  status: "evaluating"|"executed"|"blocked"|"pending_approval"|"rejected"|"failed",
  clauses: string[], decision_reasons: string[],
  reason: string, evidence: string[], input: object,
  stripe_id?, stripe_type?, stripe_url?, result?, error?, approved_by?
}

Event = { id: number, ts, run_id, type: "trigger"|"agent_text"|"tool_call"|"tool_result"|"negotiation"|"error"|"done", data: object }
// negotiation event data: { supplier, item, round, from: "agent"|"supplier", message, unit_price }

Forecast = { date, weekday, weather, temp, rain, event_name, bookings, walk_ins, no_shows, covers, drivers: string[], model: string }

OrderLine = {
  item, supplier, unit: "kg"|"l"|"each",
  need: number, on_hand: number, qty: number,      // qty = need - on_hand, capped at 2 days' cover (SP-8)
  habit_qty: number,                               // what the owner usually orders on this weekday
  list_price: number, last_price: number           // pence per unit
}

Delivery = { action_id, supplier, amount, agreed, approved: boolean, decline_reason?: string, stripe_id, stripe_url }
```

Spend actions (`place_order`) carry `input.lines: [{item, qty, unit, unit_price, list_price, habit_qty}]` so `/summary` can work out the savings.

## Demo keys (dashboard)
`L` loads the next sample trigger and runs it · `D` delivers: supplier overcharges first (declined), then charges the agreed total · `G` jumps to the money screen · `R` resets
