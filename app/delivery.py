"""Delivery: the supplier charges the order's capped card (Telegram /charge or the D key).
The card's Stripe spending limit is the agreed total (SP-9), so an overcharge is declined by Stripe itself.
CAP_CHECK=code makes us refuse an overcharge before Stripe sees it (fallback if test-mode limits misbehave)."""

import os

from . import audit, telegram
from . import stripe_ops as so
from .memory import memory
from .policy import gbp

CAP_CHECK = os.getenv("CAP_CHECK", "code")  # code | stripe


def _order(supplier: str | None, action_id: str | None) -> dict | None:
    if action_id:
        return audit.get_action(action_id)
    for a in audit.list_actions(200):
        if a["tool"] == "place_order" and a["status"] == "executed" and (
            not supplier or a["counterparty"].lower() == supplier.lower()
        ):
            return a
    return None


def charge(supplier: str | None = None, amount: int | None = None, action_id: str | None = None, via="dashboard") -> dict:
    order = _order(supplier, action_id)
    if not order or not order.get("stripe_id"):
        raise ValueError("No placed order with a card for that supplier yet.")
    supplier, agreed, card = order["counterparty"], int(order["amount"]), order["stripe_id"]
    amount = int(amount or agreed)
    lines = (order.get("input") or {}).get("lines") or []
    terms = "; ".join(f"{ln['qty']}{ln['unit']} {ln['item'].replace('_', ' ')} at {gbp(ln['unit_price'])}" for ln in lines)

    if CAP_CHECK == "code" and amount > agreed:
        res = {"stripe_id": card, "stripe_type": "issuing_card", "stripe_url": f"{so.DASH}/issuing/cards/{card}",
               "result": {"approved": False, "status": "declined_by_agent"}}
    else:
        res = so.simulate_card_purchase(card, amount, supplier, settle_if_verification_blocked=amount <= agreed)
    approved = bool(res["result"].get("approved"))

    reason = (f"{supplier} charged {gbp(amount)}: paid, matches the agreed total." if approved else
              f"{supplier} tried to charge {gbp(amount)}; the card is capped at the agreed {gbp(agreed)}, so it was declined.")
    aid = audit.new_action(
        run_id="delivery", tool="delivery_charge", kind="delivery", amount=amount, currency="gbp",
        counterparty=supplier, outcome="allow" if approved else "deny", status="executed" if approved else "blocked",
        clauses=["SP-9"], decision_reasons=[f"Card capped at {gbp(agreed)} (SP-9)."], reason=reason,
        evidence=[f"Order {order['id']}: {terms}"], input={"order_action_id": order["id"], "agreed": agreed, "via": via},
        stripe_id=res["stripe_id"], stripe_type=res["stripe_type"], stripe_url=res["stripe_url"], result=res["result"],
    )
    memory.remember(f"Delivery from {supplier}: charge of {gbp(amount)} {'paid' if approved else 'DECLINED'} "
                    f"(agreed {gbp(agreed)} on order {order['id']}: {terms}).", {"action_id": aid})
    if approved:
        telegram.send(supplier, f"Paid {gbp(amount)}. Thanks, see you next time.")
    else:
        telegram.send(supplier, f"Card declined: we agreed {terms}, total {gbp(agreed)}. Please charge {gbp(agreed)}.")
    return {"action_id": aid, "order_action_id": order["id"], "supplier": supplier, "amount": amount, "agreed": agreed, "approved": approved,
            "decline_reason": None if approved else f"Over the agreed {gbp(agreed)} (SP-9)",
            "stripe_id": res["stripe_id"], "stripe_url": res["stripe_url"]}
