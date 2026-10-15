"""Covers forecast = confirmed bookings + predicted walk-ins - predicted no-shows.

Walk-ins come from a least-squares linear regression (plain Python, no numpy) trained at import on
data/sales_history.csv: weekday, temperature, rain, payday, bank holiday, local event. No-shows use the
restaurant's own no-show rate for that weekday. Deterministic: same seed data, same forecast.
"""

import csv
import json
import math

from . import config

WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
NAMES = ["const", "tue", "wed", "thu", "fri", "sat", "sun", "temp", "rain", "payday", "bank_holiday", "event"]
_override: dict | None = None  # build() forecasts this day instead of data/tomorrow.json (used by build_week)


def _x(r: dict) -> list[float]:
    dow = int(r["weekday"])
    return ([1.0] + [1.0 if dow == i else 0.0 for i in range(1, 7)]
            + [float(r["temp"]), min(float(r["rain"]), 10.0), float(r["payday"]), float(r["bank_holiday"]),
               float(r["event"])])


def _solve(a: list[list[float]], b: list[float]) -> list[float]:
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for c in range(n):
        p = max(range(c, n), key=lambda r: abs(m[r][c]))
        m[c], m[p] = m[p], m[c]
        for r in range(n):
            if r != c and m[c][c]:
                f = m[r][c] / m[c][c]
                m[r] = [x - f * y for x, y in zip(m[r], m[c])]
    return [m[i][n] / m[i][i] if m[i][i] else 0.0 for i in range(n)]


def _fit(rows: list[dict]) -> list[float]:
    xs, ys = [_x(r) for r in rows], [float(r["walk_ins"]) for r in rows]
    k = len(xs[0])
    xtx = [[sum(x[i] * x[j] for x in xs) + (1e-6 if i == j else 0) for j in range(k)] for i in range(k)]
    xty = [sum(x[i] * y for x, y in zip(xs, ys)) for i in range(k)]
    return _solve(xtx, xty)


def _predict(coef: list[float], r: dict) -> float:
    return sum(c * x for c, x in zip(coef, _x(r)))


def _load() -> list[dict]:
    with open(config.DATA_DIR / "sales_history.csv", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def build() -> dict:
    rows = _load()
    t = _override or json.loads((config.DATA_DIR / "tomorrow.json").read_text(encoding="utf-8"))

    # Honest accuracy: train on all but the last 4 weeks, test on those 4 weeks, then retrain on everything.
    held = rows[-28:]
    c0 = _fit(rows[:-28])
    mae = sum(abs(_predict(c0, r) - float(r["walk_ins"])) for r in held) / len(held)
    coef = _fit(rows)
    co = dict(zip(NAMES, coef))

    dow = int(t["weekday"])
    same_day = [r for r in rows if int(r["weekday"]) == dow]
    rate = sum(int(r["no_shows"]) for r in same_day) / max(1, sum(int(r["bookings"]) for r in same_day))
    walk_ins = max(0, round(_predict(coef, t)))
    no_shows = round(t["bookings"] * rate)
    covers = t["bookings"] + walk_ins - no_shows
    last4 = same_day[-4:]
    avg4 = round(sum(int(r["covers"]) for r in last4) / len(last4))
    mean_temp = sum(float(r["temp"]) for r in rows) / len(rows)
    day = WEEKDAYS[dow]

    drivers = [f"{t['bookings']} confirmed bookings"]
    if t["rain"] > 0.5:
        drivers.append(f"{t['rain']}mm of rain forecast: {co['rain'] * min(t['rain'], 10):+.0f} walk-ins")
    drivers.append(f"{t['temp']}°C ({t['temp'] - mean_temp:+.1f}°C vs usual): {co['temp'] * (t['temp'] - mean_temp):+.0f} walk-ins")
    if t.get("event"):
        drivers.append(f"{t.get('event_name', 'Local event')}: {co['event']:+.0f} walk-ins")
    if t.get("payday"):
        drivers.append(f"Payday weekend: {co['payday']:+.0f} walk-ins")
    if t.get("bank_holiday"):
        drivers.append(f"Bank holiday weekend: {co['bank_holiday']:+.0f} walk-ins")
    drivers.append(f"Your usual {day} no-show rate is {rate:.0%}: -{no_shows}")
    drivers.append(f"Last 4 {day}s averaged {avg4} covers")

    return {
        "date": t["date"], "weekday": day,
        "weather": f"{t['temp']}°C, {t['rain']}mm rain ({t.get('weather_source', 'forecast')})",
        "temp": t["temp"], "rain": t["rain"], "event_name": t.get("event_name") if t.get("event") else "",
        "bookings": t["bookings"], "walk_ins": walk_ins, "no_shows": no_shows, "covers": covers,
        "drivers": drivers,
        "model": f"Linear regression on {len(rows)} days of this restaurant's covers; "
                 f"walk-ins within ±{math.ceil(mae)} on average over the last 4 weeks",
        "avg_spend": t.get("avg_spend", 3800),
    }


FORECAST = build()


# ---------- the next 7 days (Forecast page) ----------
# Day 1 is FORECAST (the night's order). Days 2-7 use real Open-Meteo weather; bookings and events are synthetic.
BASE_BOOKINGS = [48, 54, 62, 80, 118, 128, 86]
EVENTS = ["Arsenal home match", "Comedy night at the Roundhouse", "Camden Market late opening", "Gig at Electric Ballroom"]


def _weather_days(n: int) -> dict:
    import httpx

    try:
        d = httpx.get("https://api.open-meteo.com/v1/forecast", timeout=8, params={
            "latitude": 51.539, "longitude": -0.143, "daily": "temperature_2m_max,precipitation_sum",
            "timezone": "Europe/London", "forecast_days": n + 1}).json()["daily"]
        return {t: (tm, rn) for t, tm, rn in zip(d["time"], d["temperature_2m_max"], d["precipitation_sum"])}
    except Exception:  # noqa: BLE001 - offline: fall back to tomorrow's weather
        return {}


def build_week(n: int = 7) -> list[dict]:
    import random
    from datetime import date, timedelta

    t0 = json.loads((config.DATA_DIR / "tomorrow.json").read_text(encoding="utf-8"))
    first = date.fromisoformat(t0["date"])
    wx = _weather_days(n)
    out = [FORECAST]
    for i in range(1, n):
        d = first + timedelta(days=i)
        r = random.Random(d.toordinal())
        temp, rain = wx.get(d.isoformat(), (t0["temp"], 0.0))
        ev = r.random() < 0.3
        payday = int((d + timedelta(days=7)).month != d.month and d.weekday() in (4, 5))
        t = {**t0, "date": d.isoformat(), "weekday": d.weekday(), "temp": temp, "rain": rain,
             "weather_source": "Open-Meteo forecast" if wx else "estimate", "payday": payday, "bank_holiday": 0,
             "event": int(ev), "event_name": f"{r.choice(EVENTS)} (synthetic)" if ev else "",
             "bookings": round(BASE_BOOKINGS[d.weekday()] * 0.97 * r.uniform(0.85, 1.0))}
        out.append(_day(t))
    return out


def _day(t: dict) -> dict:
    """Same maths as build(), for any day dict shaped like data/tomorrow.json."""
    global _override
    _override = t
    try:
        return build()
    finally:
        _override = None


WEEK = build_week()
