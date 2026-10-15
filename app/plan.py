"""Order plan: forecast covers x dish mix x recipe - stock on hand, per supplier.
SP-8: perishables are bought for one day's need plus a 5% buffer, never more than 2 days' cover."""

import json
import math

from . import config
from .forecast import FORECAST

BUFFER = 1.05


def _j(name: str):
    return json.loads((config.DATA_DIR / name).read_text(encoding="utf-8"))


MENU, SUPPLIERS, STOCK, HABIT = _j("menu.json"), _j("suppliers.json"), _j("stock.json"), _j("habit_orders.json")


def supplier_of(item: str) -> tuple[str, dict]:
    for name, s in SUPPLIERS.items():
        if item in s["items"]:
            return name, s["items"][item]
    raise KeyError(item)


def build(covers: int | None = None) -> list[dict]:
    covers = covers or FORECAST["covers"]
    need: dict[str, float] = {}
    for dish in MENU["dishes"]:
        for item, per in dish["uses"].items():
            need[item] = need.get(item, 0.0) + dish["per_cover"] * covers * per
    lines = []
    for item, n in need.items():
        sup, p = supplier_of(item)
        on_hand = STOCK.get(item, 0)
        qty = max(0, math.ceil(n * BUFFER - on_hand))
        if p.get("perishable"):
            qty = min(qty, max(0, math.floor(2 * n - on_hand)))
        lines.append({
            "item": item, "supplier": sup, "unit": p["unit"], "need": round(n, 1), "on_hand": on_hand, "qty": qty,
            "habit_qty": HABIT.get(item, qty), "list_price": p["list"], "last_price": p["last"],
        })
    return [ln for ln in lines if ln["qty"] > 0]


def for_supplier(name: str) -> list[dict]:
    return [ln for ln in build() if ln["supplier"].lower() == name.lower()]
