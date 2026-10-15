# Mise: the 11pm order, done for you

Working name. Rename freely. Track 1 (agents that spend).

## One line
An agent that forecasts tomorrow's customers for an independent restaurant, works out what to buy, haggles with suppliers and pays them on a capped card, all inside the owner's written rules, and can explain every pound.

## The person and the decision
Marco owns and cooks at a 60-seat independent restaurant. He takes bookings and walk-ins. Every night around 11pm, after service, he orders tomorrow's fish, veg and dairy by WhatsApp, going on gut feel. If he over-orders, fish goes in the bin. If he under-orders, a dish comes off the menu. He accepts whatever price the supplier sends, and he only spots an overcharge, if he ever does, when the invoice turns up.

The moment: **the nightly order.** Mise does it for him.

## Why it wins
**Works end to end (35).** At delivery the supplier charges a real Stripe Issuing virtual card in test mode and the money is captured. The video shows the payment in the Stripe dashboard.

**Autonomy with control (25).** A nightly trigger starts it. It places orders up to £150 with no human involved, and anything above that (tonight, the £162 fish order) waits for Marco to tap approve. The rules live in `policy.yaml` and are enforced in code, not by the model. The card cap is enforced by Stripe itself, so when the supplier tries to overcharge, the card declines. That's control the model can't talk its way around.

**Explains every pound (20).** Ask "why did you pay Harbour Fish £162?" and it answers with the forecast (bookings, predicted walk-ins, the weather), the order maths (portions × recipe − stock on hand, at most 2 days' cover), the quotes it turned down, and the clause ids that allowed it. Every action is stored in the audit trail and in Cognee memory.

**Product and creativity (20).** Two ideas nobody else has put together:
- **Demand-backed spending limits.** Finance teams set fixed monthly budgets. Mise sets tomorrow's buying limit from tomorrow's predicted customers: no customers, no budget.
- **The card is the purchase order.** Normally the order, the delivery and the invoice get checked against each other about 30 days later in accounts payable. Here that check happens at the moment of payment: the agreed price is the card cap, so an overcharge gets declined at the till. (Personal story for the pitch: "at my day job I've spent hours chasing POs and invoices between procurement and accounts payable".)

Chains have forecasting tools (Supy, Nory), but those suggest an order and leave a purchasing manager to place it. A single-site owner has no purchasing manager. We place the order, agree the price and pay.

**For the finance side:** 37% of UK small businesses hand their accounting to an outside firm (YouGov); the rest keep the books themselves in software. Either way, Mise produces a **bookkeeper report**: one row per order with supplier, items, agreed price, VAT, category, the Stripe payment id, clause ids and the reason. It's ready for the accountant, the owner or any ledger (Briefcase included), and **food cost %** sits on the money screen because that's the number restaurant accountants watch. We have no Briefcase integration tonight; it's a pitch line, not a claim.

## The money arc
The final screen shows **this week's savings, split three ways**:
- **Over-ordering avoided:** what Marco usually orders on that weekday minus what the forecast needed, priced at the list price.
- **Negotiated:** (asking price − agreed price) × quantity.
- **Overcharges blocked:** the declined amount.

Then: total × 52 = per year. Keep the yearly figure under WRAP's ~£10k-per-outlet waste estimate so it stays believable.

## The assumptions
1. **Spending limits on test-mode Issuing cards actually decline an over-limit test charge, and the UK Issuing balance can be funded in test mode.** Check it yourself by 19:05, with a £10 card and a £12 charge. If it fails, the delivery route checks the cap in our own code and still pays through Issuing (or Connect).
2. **Single-site restaurants don't already use forecast-to-order tools.** Supy needs till integration, mapped recipes and sales history, which supports this. Check: get one quote from a restaurant or café owner (a text is fine) and use it in the pitch.
3. **Suppliers will deal with an agent over chat.** Tonight the haggling runs over **Telegram**, with a teammate on their phone playing the fishmonger: the messages are real, the person is acting. WhatsApp needs a business account, a public webhook and approved message templates, and there's no time for that. Pitch line: "in production this is the WhatsApp thread Marco already uses."

## Demo beats
1. "It's 11pm. Marco just finished service and still has to order tomorrow's fish by gut feel. WRAP estimates restaurants bin about £10k of food a year each." *(problem, money)*
2. Friday forecast: **165 customers.** 124 booked + 51 walk-ins − 10 no-shows. Real forecast of 9.4mm of rain costs 22 walk-ins, and a gig nearby adds 12. *(customer moment)*
3. The order: 9kg of sea bass, against the 12kg Marco would normally order. Max 2 days' cover (SP-8). *(money)*
4. **On Telegram** (phone on screen): the agent messages Harbour Fish. Harbour replies £19.50/kg. The agent counters using the last price and the volume. Agreed at **£18.00/kg**. It is never allowed to go above the last price (SP-7). *(money)*
5. The fish order is £162, over the £150 limit (SP-3), so it waits for Marco. One tap approves it, and a card capped at **£162** is created. Veg and dairy go through on their own. *(control)*
6. **HERO:** at delivery, Harbour texts "Delivered, charging £207, prices went up". **Declined.** The agent replies "We agreed £18/kg, £162." Harbour charges £162 and money moves in Stripe. *(money, control)*
7. "Why did you pay Harbour £162?" It replies with the forecast, the order maths, the quotes and the clause ids. *(explains every pound)*
8. **Money screen:** this week's £ saved = over-ordering avoided + negotiated + overcharges blocked, and the yearly figure.

## Killer questions
**"What happens when the forecast is wrong and you run out?"**
> "Then a dish comes off the menu for one night. That costs less than binning fish that goes off in two days. Marco sets the buffer in his rules, and every night the forecast gets checked against what actually sold, so it gets better every week."

**"Nory and Supy already do forecasting. What's new?"**
> "They suggest an order to a chain's purchasing manager. A one-site owner doesn't have one. We place the order, agree the price and pay, inside rules the owner wrote, and the card itself refuses an overcharge."

**"Why would a supplier haggle with a bot?"**
> "They already haggle with Marco on WhatsApp at 11pm. The agent sends the same messages from the same thread and pays the moment the goods arrive, and small suppliers want that more than anything. Tonight the suppliers are simulated. The payments are real Stripe test-mode money."

## Risks and cut order
Most likely to fail first: Issuing balance funding, or the spending limit not declining test charges. Second: Cognee being slow to start.

Cut in this order:
1. Cognee (fall back to local memory)
2. The live trained model (use a forecast that's already calculated and show the drivers)
3. Multi-round haggling (one counter-offer, done deterministically)
4. The veg approval beat

**Never cut:** the capped card and the declined overcharge, the "why" answer with clause ids, and the money screen. Those three are the argument.

## Honest odds
Guess: about a 1 in 4 chance of a top-three finish. What would move it most: the overcharge decline happening for real in Stripe, on video, with the dashboard showing it.
