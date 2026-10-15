"""Tools the agent can call. Every money tool goes through the same gate:

  policy.evaluate -> audit row -> (Stripe call if allowed) -> audit update -> memory write

Turn tools on/off with ENABLED_TOOLS in .env (comma list) once you've picked a track.
"""

import json
import os
import time
from dataclasses import dataclass
from typing import Callable

import stripe

from . import audit, config, negotiate, plan, telegram
from . import stripe_ops as so
from .forecast import FORECAST
from .memory import memory
from .policy import gbp, policy

MONEY_FIELDS = {
    "reason": {"type": "string", "description": "One sentence: why this action, in plain English."},
    "evidence": {
        "type": "array",
        "items": {"type": "string"},
        "description": "Exact quotes from the trigger or memory that justify this action and its amount.",
    },
}


@dataclass
class Tool:
    name: str
    description: str
    kind: str  # spend | collect | read
    props: dict
    required: list[str]
    run: Callable[[dict, dict], dict]  # (args, stripe_metadata) -> stripe_ops result
    amount: Callable[[dict], int] = lambda a: int(a.get("amount") or 0)
    counterparty: Callable[[dict], str] = lambda a: a.get("counterparty") or a.get("customer_name") or ""
    prepare: Callable[[dict], dict] | None = None  # fills in code-computed fields before the policy gate

    def schema(self) -> dict:
        props, req = dict(self.props), list(self.required)
        if self.kind != "read":
            props |= MONEY_FIELDS
            req += ["reason", "evidence"]
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": {"type": "object", "properties": props, "required": req},
        }


def _str(d="") -> dict:
    return {"type": "string", "description": d}


def _int(d="") -> dict:
    return {"type": "integer", "description": d}


AMOUNT = _int("Amount in minor units (pence). £12.50 = 1250.")


def _pay_with_card(a, md):
    card = so.issue_virtual_card(f"{a['counterparty']} Agent Card", a["amount"], metadata=md)
    buy = so.simulate_card_purchase(card["stripe_id"], a["amount"], a["counterparty"])
    buy["result"]["card"] = card["stripe_id"]
    return buy


def _prepare_order(a: dict) -> dict:
    """Quantities and prices come from the negotiation record, never from the model."""
    if a.get("lines"):  # already prepared (approval re-run): the audit row is the record
        return a
    deal = negotiate.DEALS.get(a.get("negotiation_id", ""))
    if not deal:
        raise ValueError(f"Unknown negotiation_id {a.get('negotiation_id')!r}: call negotiate first.")
    if deal["status"] != "agreed":
        raise ValueError(f"No agreed price with {deal['supplier']} ({deal['status']}); escalate to Marco instead.")
    return {**a, "counterparty": deal["supplier"], "lines": deal["lines"]}


def _place_order(a, md):
    card = so.issue_virtual_card(f"{a['counterparty']} Order", _order_total(a), metadata=md)
    terms = "; ".join(f"{ln['qty']}{ln['unit']} {ln['item'].replace('_', ' ')} at {gbp(ln['unit_price'])}" for ln in a["lines"])
    telegram.send(a["counterparty"], f"Order confirmed for tomorrow: {terms}. Total {gbp(_order_total(a))}. "
                  f"Payment card ends {card['result']['last4']}. At delivery, charge it with /charge {_order_total(a) / 100:g}")
    card["result"]["lines"] = a["lines"]
    return card


def _order_total(a: dict) -> int:
    return sum(int(ln["qty"]) * int(ln["unit_price"]) for ln in a.get("lines") or [])


def _pay_contractor(a, md):
    found = policy.counterparty(a["counterparty"])
    acct = (found[1] if found else {}).get("connect_account")
    if not acct:
        raise ValueError(f"No connect_account set for {a['counterparty']!r} in policy.yaml")
    return so.transfer_to_connected_account(acct, a["amount"], a.get("reason", ""), metadata=md)


ALL_TOOLS = [
    # ---- read ----
    Tool(
        "recall_memory",
        "Search the agent's memory: past payments, bills, customer promises, disputes and policy history.",
        "read",
        {"query": _str("What to look up")},
        ["query"],
        lambda a, _: {"memories": memory.recall(a["query"])},
    ),
    Tool(
        "list_recent_actions",
        "List the agent's most recent money actions from the audit trail (use to avoid duplicates).",
        "read",
        {},
        [],
        lambda a, _: {
            "actions": [
                {k: r[k] for k in ("id", "tool", "counterparty", "amount", "status", "clauses")}
                for r in audit.list_actions(20)
            ]
        },
    ),
    # ---- restaurant buying (track 1) ----
    Tool(
        "forecast_covers",
        "Tomorrow's covers forecast: confirmed bookings + predicted walk-ins - predicted no-shows, with the drivers (weather, events, payday) and the model's accuracy.",
        "read",
        {},
        [],
        lambda a, _: FORECAST,
    ),
    Tool(
        "plan_order",
        "What to buy for tomorrow, per supplier: forecast covers x dish mix x recipe - stock on hand, with a 5% buffer and never more than 2 days' cover (SP-8). Also shows what Marco usually orders by habit.",
        "read",
        {},
        [],
        lambda a, _: {"lines": plan.build(), "covers": FORECAST["covers"]},
    ),
    Tool(
        "negotiate",
        "Haggle with one supplier over chat (Telegram when linked, otherwise a simulated supplier). Prices are set by code: open 5% under the last price, counter once, never above the last price (SP-7). Returns a negotiation_id, agreed prices and the transcript.",
        "read",
        {"supplier": _str("Supplier name from the plan")},
        ["supplier"],
        lambda a, _: negotiate.negotiate(a["supplier"], a.get("_run_id")),
    ),
    Tool(
        "place_order",
        "Place the agreed order: issues one virtual card capped at exactly the agreed total (SP-9) and sends the confirmation to the supplier. Quantities and prices come from the negotiation record.",
        "spend",
        {"negotiation_id": _str("From negotiate"), "counterparty": _str("Supplier name")},
        ["negotiation_id", "counterparty"],
        _place_order,
        amount=_order_total,
        prepare=_prepare_order,
    ),
]

_enabled = {t.strip() for t in os.getenv("ENABLED_TOOLS", "").split(",") if t.strip()}
TOOLS = {t.name: t for t in ALL_TOOLS if not _enabled or t.name in _enabled}


def schemas() -> list[dict]:
    return [t.schema() for t in TOOLS.values()]


def _memory_line(t: Tool, args: dict, status: str, clauses: list[str], extra: str = "") -> str:
    when = time.strftime("%Y-%m-%d %H:%M")
    cp, amt = t.counterparty(args), gbp(t.amount(args))
    ev = " | ".join(args.get("evidence") or [])
    return f"{when}: {t.name} {amt} for {cp} -> {status} under {', '.join(clauses) or 'no clause'}. Reason: {args.get('reason')}. Evidence: {ev}. {extra}".strip()


def execute(name: str, args: dict, run_id: str | None = None, approved_by: str | None = None, action_id=None) -> dict:
    t = TOOLS.get(name)
    if not t:
        return {"error": f"unknown tool {name}"}
    if t.kind == "read":
        return t.run({**args, "_run_id": run_id}, {})

    if t.prepare:
        if action_id is None:  # the model never supplies lines; only an approval re-run carries them
            args = {k: v for k, v in args.items() if k != "lines"}
        try:
            args = t.prepare(args)
        except ValueError as e:
            return {"status": "failed", "error": str(e)}
    amount, cp = t.amount(args), t.counterparty(args)
    action = {
        "kind": t.kind,
        "amount": amount,
        "counterparty": cp,
        "evidence": args.get("evidence"),
        "lines": args.get("lines"),
        "duplicate": audit.has_recent_duplicate(t.kind, cp, amount, exclude_id=action_id),
    }
    d = policy.evaluate(action, spent_today=audit.spent_today(), approved_by=approved_by)
    fields = dict(
        outcome=d.outcome, clauses=d.clauses, decision_reasons=d.reasons, approved_by=approved_by,
    )
    if action_id is None:
        action_id = audit.new_action(
            run_id=run_id, tool=name, kind=t.kind, amount=amount, currency=config.CURRENCY, counterparty=cp,
            reason=args.get("reason"), evidence=args.get("evidence") or [], input=args, status="evaluating", **fields,
        )
    else:
        audit.update_action(action_id, **fields)

    if d.outcome != "allow":
        status = "pending_approval" if d.outcome == "needs_approval" else "blocked"
        audit.update_action(action_id, status=status)
        memory.remember(_memory_line(t, args, status, d.clauses, " ".join(d.reasons)), {"action_id": action_id})
        if status == "pending_approval":
            terms = "; ".join(f"{ln['qty']}{ln['unit']} {ln['item'].replace('_', ' ')} at {gbp(ln['unit_price'])}"
                              for ln in args.get("lines") or [])
            telegram.ask_owner(action_id, f"Marco, Mise needs your OK: {gbp(amount)} to {cp}"
                               + (f" for {terms}" if terms else "") + f".\n{' '.join(d.reasons)}")
        return {"action_id": action_id, "status": status, "clauses": d.clauses, "why": d.reasons}

    md = {"agent_action_id": action_id, "policy_clauses": ",".join(d.clauses)}
    try:
        res = t.run(args, md)
    except (stripe.StripeError, ValueError, KeyError) as e:
        msg = getattr(e, "user_message", None) or str(e)
        audit.update_action(action_id, status="failed", error=msg)
        return {"action_id": action_id, "status": "failed", "error": msg}

    audit.update_action(
        action_id, status="executed", stripe_id=res["stripe_id"], stripe_type=res["stripe_type"],
        stripe_url=res["stripe_url"], result=res["result"],
    )
    memory.remember(_memory_line(t, args, "executed", d.clauses, f"Stripe {res['stripe_id']}."), {"action_id": action_id})
    return {"action_id": action_id, "status": "executed", "clauses": d.clauses, "why": d.reasons, **res}


def approve(action_id: str, approver: str) -> dict:
    a = audit.get_action(action_id)
    if not a or a["status"] != "pending_approval":
        return {"error": "not pending"}
    return execute(a["tool"], a["input"], a["run_id"], approved_by=approver, action_id=action_id)


def reject(action_id: str, approver: str) -> dict:
    a = audit.get_action(action_id)
    if not a or a["status"] != "pending_approval":
        return {"error": "not pending"}
    audit.update_action(action_id, status="rejected", approved_by=approver)
    memory.remember(f"{approver} rejected {a['tool']} {gbp(a['amount'])} for {a['counterparty']}.", {"action_id": action_id})
    return {"action_id": action_id, "status": "rejected"}


def as_tool_result(payload: dict) -> str:
    return json.dumps(payload, default=str)
