"""Stock on hand: from a photo of a handwritten count (OpenAI vision), or a CSV/text file.
Updates plan.STOCK in place, so the order plan and the agent's plan_order tool pick it up immediately.
(Pitch: a restaurant with an inventory system would feed this from its API instead.)"""

import base64
import json
import os
import re
import time

from . import plan

UNITS = {item: p["unit"] for s in plan.SUPPLIERS.values() for item, p in s["items"].items()}
ALIASES = {"seabass": "sea_bass", "bass": "sea_bass", "fish": "sea_bass", "tomato": "tomatoes", "courgette": "courgettes",
           "zucchini": "courgettes", "lettuce": "salad", "leaves": "salad", "herb": "herbs", "basil": "herbs",
           "lemon": "lemons", "burratas": "burrata", "mozzarella": "burrata", "milk": "cream", "double_cream": "cream"}
SOURCE: dict = {"from": "seed data", "file": None, "at": None, "read": {}}


def _key(name: str) -> str | None:
    k = re.sub(r"[^a-z ]", "", name.lower()).strip().replace(" ", "_")
    if k in UNITS:
        return k
    if k in ALIASES:
        return ALIASES[k]
    for part in k.split("_"):
        if part in UNITS or part in ALIASES:
            return UNITS.get(part) and part or ALIASES[part]
    return None


def _from_text(text: str) -> dict:
    out = {}
    for line in text.splitlines():
        m = re.match(r"\s*([A-Za-z][A-Za-z _-]*?)\s*[,:;=\t ]\s*(\d+(?:\.\d+)?)", line)
        if m and (k := _key(m.group(1))):
            out[k] = float(m.group(2))
    return out


def _from_image(b64: str, content_type: str) -> dict:
    from openai import OpenAI

    key = os.getenv("OPENAI_API_KEY", "")
    if not key:
        raise ValueError("Reading a photo needs OPENAI_API_KEY. Upload a CSV instead (item,quantity).")
    items = ", ".join(f"{k} ({u})" for k, u in UNITS.items())
    r = OpenAI(api_key=key, timeout=60).chat.completions.create(
        model=os.getenv("OPENAI_MODEL", "gpt-4.1"),
        response_format={"type": "json_object"},
        messages=[{"role": "user", "content": [
            {"type": "text", "text": "This is a restaurant's stock count, handwritten or printed. Return a JSON object "
             f"mapping these item keys to the quantity on hand, in these units: {items}. Only include items you can "
             "actually read. Convert grams to kg. Numbers only."},
            {"type": "image_url", "image_url": {"url": f"data:{content_type};base64,{b64}"}},
        ]}],
    )
    raw = json.loads(r.choices[0].message.content or "{}")
    return {k2: float(v) for k, v in raw.items() if (k2 := _key(k)) and isinstance(v, (int, float))}


def update(filename: str, content_type: str, b64: str) -> dict:
    if content_type.startswith("image/"):
        read, kind = _from_image(b64, content_type), "photo"
    else:
        read, kind = _from_text(base64.b64decode(b64).decode("utf-8", "ignore")), "file"
    if not read:
        raise ValueError("Couldn't read any stock lines. Try 'sea bass, 2' style lines, one item per line.")
    plan.STOCK.update(read)
    SOURCE.update({"from": kind, "file": filename, "at": time.strftime("%H:%M"), "read": read})
    return {"read": read, "units": {k: UNITS[k] for k in read}, "source": SOURCE, "stock": plan.STOCK, "plan": plan.build()}
