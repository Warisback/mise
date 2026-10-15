"""Supplier chat over Telegram (stand-in for the WhatsApp thread a restaurant already uses).

Long-polls the Bot API in a background thread: no webhook, no tunnel. A teammate plays the supplier:
  /start harbour      link this chat to Harbour Fish Co (keys are `telegram` in data/suppliers.json)
  any text            read as a reply to the agent (prices like "£19.50" are parsed by negotiate.py)
  /charge 207         the supplier charges the order's card at delivery (£207)
"""

import json
import os
import queue
import re
import threading

import httpx

from . import config

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
API = f"https://api.telegram.org/bot{TOKEN}"
CHATS_FILE = config.DATA_DIR / "telegram_chats.json"

_chats: dict[str, int] = json.loads(CHATS_FILE.read_text()) if CHATS_FILE.exists() else {}
_inbox: dict[int, "queue.Queue[str]"] = {}
_started = False


def _suppliers() -> dict:
    return json.loads((config.DATA_DIR / "suppliers.json").read_text(encoding="utf-8"))


OWNER = "Marco (owner)"


def _key_to_name(key: str) -> str | None:
    if key.lower() in ("marco", "owner"):
        return OWNER
    for name, s in _suppliers().items():
        if s.get("telegram", "").lower() == key.lower() or name.lower().startswith(key.lower()):
            return name
    return None


def enabled() -> bool:
    return bool(TOKEN)


def linked(supplier: str) -> bool:
    return enabled() and supplier in _chats


def status() -> dict:
    return {"enabled": enabled(), "linked": sorted(_chats)}


def send(supplier: str, text: str) -> bool:
    chat = _chats.get(supplier)
    if not (enabled() and chat):
        return False
    try:
        httpx.post(f"{API}/sendMessage", json={"chat_id": chat, "text": text}, timeout=10)
        return True
    except httpx.HTTPError as e:
        print(f"[telegram] send failed: {e!r}")
        return False


def drain(supplier: str) -> None:
    q = _inbox.get(_chats.get(supplier, -1))
    while q and not q.empty():
        q.get_nowait()


def wait_reply(supplier: str, timeout: float) -> str | None:
    chat = _chats.get(supplier)
    if chat is None:
        return None
    try:
        return _inbox.setdefault(chat, queue.Queue()).get(timeout=timeout)
    except queue.Empty:
        return None


def _reply(chat: int, text: str) -> None:
    try:
        httpx.post(f"{API}/sendMessage", json={"chat_id": chat, "text": text}, timeout=10)
    except httpx.HTTPError:
        pass


def ask_owner(action_id: str, text: str) -> bool:
    """Real-time approval: Marco gets Approve/Reject buttons on his phone."""
    chat = _chats.get(OWNER)
    if not (enabled() and chat):
        return False
    kb = {"inline_keyboard": [[{"text": "✅ Approve", "callback_data": f"approve:{action_id}"},
                               {"text": "Reject", "callback_data": f"reject:{action_id}"}]]}
    try:
        httpx.post(f"{API}/sendMessage", json={"chat_id": chat, "text": text, "reply_markup": kb}, timeout=10)
        return True
    except httpx.HTTPError:
        return False


def _callback(cq: dict) -> None:
    verb, _, aid = (cq.get("data") or "").partition(":")
    from . import tools  # late import: tools imports telegram

    res = tools.approve(aid, "Marco (Telegram)") if verb == "approve" else tools.reject(aid, "Marco (Telegram)")
    done = res.get("status")
    note = {"executed": "Approved. Card capped at the agreed total is issued and the supplier is confirmed.",
            "rejected": "Rejected. Nothing ordered from this supplier."}.get(done, f"Couldn't do that: {res.get('error') or done}")
    try:
        httpx.post(f"{API}/answerCallbackQuery", json={"callback_query_id": cq["id"], "text": note[:190]}, timeout=10)
        m = cq.get("message") or {}
        httpx.post(f"{API}/editMessageText", json={"chat_id": m.get("chat", {}).get("id"), "message_id": m.get("message_id"),
                                                    "text": (m.get("text") or "") + f"\n\n{note}"}, timeout=10)
    except httpx.HTTPError:
        pass


def _handle(msg: dict) -> None:
    chat, text = msg["chat"]["id"], (msg.get("text") or "").strip()
    if text.startswith("/start"):
        arg = text.partition(" ")[2].strip() or "harbour"
        name = _key_to_name(arg)
        if not name:
            _reply(chat, f"Unknown supplier {arg!r}. Try /start harbour, /start greenlane or /start meadow.")
            return
        _chats[name] = chat
        CHATS_FILE.write_text(json.dumps(_chats))
        _reply(chat, "Linked as Marco. Orders over your limit will ask you here, with Approve and Reject buttons."
               if name == OWNER else
               f"Linked as {name}. Marco's orders will come through here. At delivery, charge with /charge <pounds>.")
        return
    if text.startswith("/charge"):
        name = next((n for n, c in _chats.items() if c == chat and n != OWNER), None)
        m = re.search(r"(\d+(?:\.\d{1,2})?)", text)
        if not (name and m):
            _reply(chat, "Usage: /charge 162 (after /start <supplier>)")
            return
        from . import delivery  # late import: delivery imports tools/stripe

        try:
            delivery.charge(name, round(float(m.group(1)) * 100), via="telegram")
        except Exception as e:  # noqa: BLE001
            _reply(chat, f"Charge failed: {e}")
        return
    _inbox.setdefault(chat, queue.Queue()).put(text)


def _poll() -> None:
    offset = 0
    while True:
        try:
            r = httpx.get(f"{API}/getUpdates", params={"timeout": 25, "offset": offset}, timeout=35).json()
            for u in r.get("result", []):
                offset = u["update_id"] + 1
                if "message" in u:
                    _handle(u["message"])
                elif "callback_query" in u:
                    threading.Thread(target=_callback, args=(u["callback_query"],), daemon=True).start()
        except Exception as e:  # noqa: BLE001 - keep polling through venue wifi drops
            print(f"[telegram] poll error: {e!r}")
            threading.Event().wait(3)


def start() -> None:
    global _started
    if enabled() and not _started:
        _started = True
        threading.Thread(target=_poll, daemon=True).start()
        print(f"[telegram] polling; linked suppliers: {sorted(_chats) or 'none yet'}")
