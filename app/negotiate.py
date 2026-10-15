"""Haggling. Numbers come from code; the model never picks a price.

Per supplier we haggle on the biggest line (the others stay at last price):
  open at 5% under the last price -> counter halfway between open and last -> never above last (SP-7).
Over Telegram when the supplier's chat is linked; otherwise a simulated supplier answers from its
secret floor price in data/suppliers.json.
"""

import re
import uuid

from . import audit, plan, telegram
from .policy import gbp

REPLY_TIMEOUT = 90  # seconds to wait for a human supplier on Telegram
YES = re.compile(r"\b(ok|okay|yes|yep|deal|done|fine|agreed|go on)\b", re.I)
PRICE = re.compile(r"£?\s*(\d+(?:\.\d{1,2})?)")

DEALS: dict[str, dict] = {}


def _round(pence: float, unit_price: int) -> int:
    step = 50 if unit_price >= 1000 else 5 if unit_price >= 100 else 1
    return int(round(pence / step) * step)


def _price(text: str, ref: int) -> int | None:
    """Read a unit price from free text. '19.50' -> 1950; '18' -> 1800 (pounds when it reads like pounds)."""
    for m in PRICE.finditer(text):
        v = float(m.group(1))
        p = round(v * 100) if v < ref / 10 else round(v)  # "19.5" vs "1950"
        if ref * 0.5 <= p <= ref * 2:
            return p
    return None


def _q(qty, unit) -> str:
    return f"{qty}{unit}" if unit in ("kg", "l") else f"{qty}" if unit == "each" else f"{qty} {unit}es" if unit == "bunch" else f"{qty} {unit}"


def _say(run_id, deal, frm, message, unit_price=None, rnd=0):
    deal["transcript"].append({"from": frm, "message": message, "unit_price": unit_price})
    audit.log_event(run_id, "negotiation", {"supplier": deal["supplier"], "item": deal["item"], "round": rnd,
                                            "from": frm, "message": message, "unit_price": unit_price})


def _supplier_reply(deal, sup_item, offer, rnd, live) -> str:
    if live:
        r = telegram.wait_reply(deal["supplier"], REPLY_TIMEOUT)
        if r is not None:
            return r
        deal["notes"].append(f"No Telegram reply in {REPLY_TIMEOUT}s; simulated supplier answered.")
    # Simulated supplier: asks list first, then accepts anything at or above its floor.
    if rnd == 1:
        return f"Can do {gbp(sup_item['list'])}/{sup_item['unit']} today, prices are up."
    if offer >= sup_item["floor"]:
        return "ok, deal"
    return f"Best I can do is {gbp(sup_item['floor'])}."


def negotiate(supplier: str, run_id: str | None = None) -> dict:
    name, _ = next(((n, s) for n, s in plan.SUPPLIERS.items() if n.lower() == supplier.lower()), (None, None))
    if not name:
        return {"status": "failed", "error": f"Unknown supplier {supplier!r}"}
    lines = plan.for_supplier(name)
    if not lines:
        return {"status": "nothing_to_order", "supplier": name}
    key = max(lines, key=lambda ln: ln["qty"] * ln["last_price"])
    sup_item = plan.SUPPLIERS[name]["items"][key["item"]]
    last, unit, qty = key["last_price"], key["unit"], key["qty"]
    item_label = key["item"].replace("_", " ")
    deal = {"id": "neg_" + uuid.uuid4().hex[:8], "supplier": name, "item": key["item"], "transcript": [], "notes": [],
            "status": "open", "asked": None}
    live = telegram.linked(name)
    if live:
        telegram.drain(name)

    def send(msg, price=None, rnd=0):
        _say(run_id, deal, "agent", msg, price, rnd)
        if live:
            telegram.send(name, msg)

    others = [ln for ln in lines if ln is not key]
    basket = ", ".join(f"{_q(ln['qty'], ln['unit'])} {ln['item'].replace('_', ' ')}" for ln in others)
    opening = _round(last * 0.95, last)
    send(f"Hi, it's Mise ordering for Marco's, Camden. Tomorrow's delivery: {_q(qty, unit)} {item_label}"
         + (f", plus {basket} at your usual prices" if basket else "")
         + f". Can you do {gbp(opening)}/{unit} on the {item_label}?", opening, 1)

    agreed = None
    r1 = _supplier_reply(deal, sup_item, opening, 1, live)
    p1 = _price(r1, last)
    _say(run_id, deal, "supplier", r1, p1, 1)
    deal["asked"] = p1
    if YES.search(r1) and (p1 is None or p1 <= opening):
        agreed = opening
    elif p1 is not None and p1 <= opening:
        agreed = p1
    else:
        counter = _round((opening + last) / 2, last)
        if p1 is not None and p1 <= counter:
            agreed = p1
        else:
            send(f"Last order was {gbp(last)}/{unit} and this is {_q(qty, unit)}. {gbp(counter)}/{unit} and it's a deal?",
                 counter, 2)
            r2 = _supplier_reply(deal, sup_item, counter, 2, live)
            p2 = _price(r2, last)
            _say(run_id, deal, "supplier", r2, p2, 2)
            if YES.search(r2) and (p2 is None or p2 <= counter):
                agreed = counter
            elif p2 is not None and p2 <= last:
                agreed = p2
    if agreed is None:
        deal["status"] = "no_deal"
        send(f"I can't go above {gbp(last)}/{unit}, the last price we paid. I'll check with Marco.", None, 3)
    else:
        deal["status"] = "agreed"
        total = agreed * qty + sum(ln["qty"] * ln["last_price"] for ln in others)
        send(f"Deal: {_q(qty, unit)} {item_label} at {gbp(agreed)}/{unit}. Order total {gbp(total)}. "
             "Confirmation and payment card to follow.", agreed, 3)

    deal["lines"] = [
        {"item": ln["item"], "qty": ln["qty"], "unit": ln["unit"], "habit_qty": ln["habit_qty"],
         "list_price": ln["list_price"], "last_price": ln["last_price"],
         "unit_price": (agreed if ln is key else ln["last_price"]) if agreed is not None or ln is not key else None}
        for ln in lines
    ]
    deal["total"] = sum(ln["qty"] * ln["unit_price"] for ln in deal["lines"] if ln["unit_price"] is not None)
    DEALS[deal["id"]] = deal
    return {"negotiation_id": deal["id"], "supplier": name, "status": deal["status"], "channel": "telegram" if live else "simulated",
            "lines": deal["lines"], "total": deal["total"], "asked_unit_price": deal["asked"], "agreed_unit_price": agreed,
            "last_unit_price": last, "transcript": deal["transcript"], "notes": deal["notes"]}
