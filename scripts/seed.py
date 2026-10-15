"""Seed the demo restaurant. Real: London weather (Open-Meteo) and bank holidays (gov.uk).
Synthetic: bookings, walk-ins, no-shows, events, menu, suppliers, stock.

Run once (re-run any time; same seed -> same history):
    uv run python -m scripts.seed
"""

import csv
import json
import random
from datetime import date, timedelta

import httpx

from app import config

LAT, LON = 51.539, -0.143  # Camden, London
DAYS = 180
TOMORROW = date.today() + timedelta(days=1)
END = date.today() - timedelta(days=1)
START = END - timedelta(days=DAYS - 1)

BASE_BOOKINGS = [48, 54, 62, 80, 118, 128, 86]  # Mon..Sun
BASE_WALKINS = [22, 26, 32, 40, 62, 70, 45]
SEASON = {4: 0.92, 5: 0.97, 6: 1.03, 7: 1.06, 8: 0.98, 9: 1.0, 10: 0.97, 11: 0.95, 12: 1.15}

rng = random.Random(42)
D = config.DATA_DIR


def weather_history() -> dict[str, tuple[float, float]]:
    url = "https://archive-api.open-meteo.com/v1/archive"
    p = {"latitude": LAT, "longitude": LON, "start_date": START.isoformat(), "end_date": END.isoformat(),
         "daily": "temperature_2m_max,precipitation_sum", "timezone": "Europe/London"}
    try:
        d = httpx.get(url, params=p, timeout=15).json()["daily"]
        return {t: (tmax or 15.0, rain or 0.0) for t, tmax, rain in
                zip(d["time"], d["temperature_2m_max"], d["precipitation_sum"])}
    except Exception as e:  # offline at the venue: fall back to plausible weather
        print(f"weather history unavailable ({e!r}); using synthetic weather")
        return {}


def weather_tomorrow() -> tuple[float, float, str]:
    try:
        d = httpx.get("https://api.open-meteo.com/v1/forecast", timeout=15, params={
            "latitude": LAT, "longitude": LON, "daily": "temperature_2m_max,precipitation_sum",
            "timezone": "Europe/London", "forecast_days": 3}).json()["daily"]
        i = d["time"].index(TOMORROW.isoformat())
        return d["temperature_2m_max"][i], d["precipitation_sum"][i], "Open-Meteo forecast"
    except Exception as e:
        print(f"weather forecast unavailable ({e!r}); using 16.7C / 9.4mm")
        return 16.7, 9.4, "fallback"


def bank_holidays() -> set[str]:
    try:
        ev = httpx.get("https://www.gov.uk/bank-holidays.json", timeout=15).json()["england-and-wales"]["events"]
        return {e["date"] for e in ev}
    except Exception:
        return {"2026-05-04", "2026-05-25", "2026-08-31", "2026-12-25", "2026-12-28"}


def is_payday(d: date) -> bool:
    """Last Friday of the month, and the Saturday after it."""
    fri = d if d.weekday() == 4 else d - timedelta(days=1) if d.weekday() == 5 else None
    return bool(fri) and (fri + timedelta(days=7)).month != fri.month


def main() -> None:
    hist, bh = weather_history(), bank_holidays()
    rows = []
    for i in range(DAYS):
        d = START + timedelta(days=i)
        dow, s = d.weekday(), SEASON.get(d.month, 1.0)
        temp, rain = hist.get(d.isoformat(), (14 + 6 * rng.random(), max(0.0, rng.gauss(1, 3))))
        payday, holiday = int(is_payday(d)), int(d.isoformat() in bh or (d + timedelta(days=1)).isoformat() in bh)
        event = int(rng.random() < 0.15)
        bookings = max(0, round(BASE_BOOKINGS[dow] * s * rng.gauss(1, 0.08) + 6 * holiday))
        walk_ins = max(0, round(BASE_WALKINS[dow] * s + 1.6 * (temp - 16) - 2.4 * min(rain, 10)
                                + 9 * payday + 12 * holiday + 11 * event + rng.gauss(0, 4)))
        rate = max(0.0, 0.06 + (0.02 if dow in (4, 5) else 0) + rng.gauss(0, 0.015))
        no_shows = round(bookings * rate)
        rows.append({"date": d.isoformat(), "weekday": dow, "temp": round(temp, 1), "rain": round(rain, 1),
                     "payday": payday, "bank_holiday": holiday, "event": event, "bookings": bookings,
                     "walk_ins": walk_ins, "no_shows": no_shows, "covers": bookings + walk_ins - no_shows})

    with open(D / "sales_history.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)

    temp, rain, src = weather_tomorrow()
    tomorrow = {
        "date": TOMORROW.isoformat(), "weekday": TOMORROW.weekday(), "temp": temp, "rain": rain,
        "weather_source": src, "payday": int(is_payday(TOMORROW)),
        "bank_holiday": int(TOMORROW.isoformat() in bh or (TOMORROW + timedelta(days=1)).isoformat() in bh),
        "event": 1, "event_name": "Sold-out gig at the Roundhouse, 5 minutes' walk (synthetic)",
        "bookings": 124, "avg_spend": 3800,
    }
    _dump("tomorrow.json", tomorrow)

    _dump("menu.json", {"dishes": [
        {"name": "Grilled sea bass", "per_cover": 0.25, "uses": {"sea_bass": 0.20, "lemons": 0.5, "herbs": 0.1}},
        {"name": "Tomato & burrata", "per_cover": 0.30, "uses": {"tomatoes": 0.15, "burrata": 0.5, "herbs": 0.05}},
        {"name": "Courgette risotto", "per_cover": 0.25, "uses": {"courgettes": 0.15, "butter": 0.03, "cream": 0.05}},
        {"name": "House salad", "per_cover": 0.35, "uses": {"salad": 0.08, "tomatoes": 0.05, "lemons": 0.25}},
    ]})

    # Prices in pence per unit. last = what Marco paid last time; floor = the supplier's secret walk-away price.
    _dump("suppliers.json", {
        "Harbour Fish Co": {"telegram": "harbour", "items": {
            "sea_bass": {"unit": "kg", "list": 1950, "last": 1850, "floor": 1750, "perishable": True}},
            "delivery_overcharge": {"sea_bass": 2300}},
        "Green Lane Produce": {"telegram": "greenlane", "items": {
            "tomatoes": {"unit": "kg", "list": 340, "last": 320, "floor": 300, "perishable": True},
            "courgettes": {"unit": "kg", "list": 290, "last": 280, "floor": 260, "perishable": True},
            "salad": {"unit": "kg", "list": 620, "last": 600, "floor": 560, "perishable": True},
            "herbs": {"unit": "bunch", "list": 130, "last": 120, "floor": 110, "perishable": True},
            "lemons": {"unit": "each", "list": 38, "last": 35, "floor": 32, "perishable": False}}},
        "Meadow Dairy": {"telegram": "meadow", "items": {
            "burrata": {"unit": "each", "list": 260, "last": 250, "floor": 235, "perishable": True},
            "butter": {"unit": "kg", "list": 880, "last": 850, "floor": 800, "perishable": False},
            "cream": {"unit": "l", "list": 430, "last": 420, "floor": 395, "perishable": True}}},
    })
    _dump("stock.json", {"sea_bass": 0, "tomatoes": 2, "courgettes": 1, "salad": 0.5, "herbs": 2,
                         "lemons": 10, "burrata": 4, "butter": 1, "cream": 1})
    # What Marco orders on a Friday by habit, whatever the weather.
    _dump("habit_orders.json", {"sea_bass": 12, "tomatoes": 11, "courgettes": 8, "salad": 6, "herbs": 7, "lemons": 35, "burrata": 30, "butter": 2, "cream": 3})

    trig = config.TRIGGERS_DIR
    for old in trig.glob("*.*"):
        old.unlink()
    (trig / "01-nightly-order.txt").write_text(
        f"It's 22:45 on {(TOMORROW - timedelta(days=1)).strftime('%A %d %B')}. Service has finished at Marco's, Camden.\n"
        f"Place tomorrow's ({TOMORROW.strftime('%A %d %B')}) orders for fresh food: forecast the covers, plan the order, "
        "negotiate with each supplier, then place each order.", encoding="utf-8")
    print(f"seeded {len(rows)} days ({START}..{END}); tomorrow {TOMORROW}: {temp}C, {rain}mm rain ({src})")


def _dump(name: str, obj) -> None:
    (D / name).write_text(json.dumps(obj, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
