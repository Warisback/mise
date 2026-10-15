"""The money screen and the bookkeeper report, both computed from the audit trail."""

import csv
import io
import time

from . import audit
from .forecast import FORECAST


def _orders(executed_only=True) -> list[dict]:
    return [a for a in audit.list_actions(500) if a["tool"] == "place_order"
            and (a["status"] == "executed" or not executed_only)]


def savings() -> dict:
    over = negotiated = ordered = habit_cost = 0
    for o in _orders():
        for ln in (o.get("input") or {}).get("lines") or []:
            q, up, lp = int(ln["qty"]), int(ln["unit_price"] or 0), int(ln["list_price"])
            over += max(0, int(ln.get("habit_qty") or q) - q) * lp
            negotiated += max(0, lp - up) * q
            ordered += q * up
            habit_cost += int(ln.get("habit_qty") or q) * lp
    blocked = sum(max(0, int(a["amount"]) - int((a.get("input") or {}).get("agreed") or 0))
                  for a in audit.list_actions(500) if a["tool"] == "delivery_charge" and a["status"] == "blocked")
    revenue = FORECAST["covers"] * FORECAST.get("avg_spend", 3800)
    total = over + negotiated + blocked
    return {
        "over_order_avoided": over, "negotiated_savings": negotiated, "overcharge_blocked": blocked,
        "saved_total": total, "saved_per_year": total * 52,
        "food_cost_pct": round(100 * ordered / revenue, 1) if revenue and ordered else 0.0,
        "habit_food_cost_pct": round(100 * habit_cost / revenue, 1) if revenue and habit_cost else 0.0,
        "forecast_revenue": revenue,
    }


def report_csv() -> str:
    """One row per order line. UK fresh food is zero-rated for VAT."""
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["date", "supplier", "item", "qty", "unit", "agreed_unit_price", "net", "vat", "vat_code", "gross",
                "category", "order_action_id", "stripe_card", "delivery_charge", "status", "clauses", "reason"])
    deliveries = {}
    for a in audit.list_actions(500):
        if a["tool"] == "delivery_charge" and a["status"] == "executed":
            deliveries[(a.get("input") or {}).get("order_action_id")] = a["stripe_id"]
    for o in reversed(_orders(executed_only=False)):
        # Weekday-first text so Excel keeps it as written instead of a too-narrow date cell (########).
        day = time.strftime("%a %d %b %Y %H:%M", time.localtime(o["ts"]))
        for ln in (o.get("input") or {}).get("lines") or []:
            net = int(ln["qty"]) * int(ln["unit_price"] or 0)
            w.writerow([day, o["counterparty"], ln["item"].replace("_", " "), ln["qty"], ln["unit"],
                        f"{int(ln['unit_price'] or 0) / 100:.2f}", f"{net / 100:.2f}", "0.00", "Zero-rated food",
                        f"{net / 100:.2f}", "Cost of sales: food", o["id"], o.get("stripe_id") or "",
                        deliveries.get(o["id"], ""), o["status"], " ".join(o.get("clauses") or []), o.get("reason") or ""])
    return buf.getvalue()
