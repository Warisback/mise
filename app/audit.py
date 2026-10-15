"""Append-mostly audit trail in SQLite. Every money action the agent attempts lands here,
allowed or not, with the clause that decided it and the evidence it quoted."""

import json
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timezone

from . import config

_lock = threading.Lock()
config.DATA_DIR.mkdir(exist_ok=True)
_db = sqlite3.connect(config.DB_PATH, check_same_thread=False)
_db.row_factory = sqlite3.Row

_db.executescript(
    """
create table if not exists actions (
  id text primary key, ts real, run_id text, tool text, kind text,
  amount integer, currency text, counterparty text,
  outcome text, status text, clauses text, decision_reasons text,
  reason text, evidence text, input text,
  stripe_id text, stripe_type text, stripe_url text, result text,
  error text, approved_by text
);
create table if not exists events (
  id integer primary key autoincrement, ts real, run_id text, type text, data text
);
"""
)

JSON_COLS = ("clauses", "decision_reasons", "evidence", "input", "result")


def _row(r: sqlite3.Row) -> dict:
    d = dict(r)
    for k in JSON_COLS:
        if d.get(k):
            d[k] = json.loads(d[k])
    return d


def new_action(**fields) -> str:
    aid = "act_" + uuid.uuid4().hex[:10]
    fields = {"id": aid, "ts": time.time(), **fields}
    for k in JSON_COLS:
        if k in fields:
            fields[k] = json.dumps(fields[k])
    cols = ",".join(fields)
    with _lock:
        _db.execute(f"insert into actions ({cols}) values ({','.join('?' * len(fields))})", list(fields.values()))
        _db.commit()
    return aid


def update_action(aid: str, **fields) -> None:
    for k in JSON_COLS:
        if k in fields:
            fields[k] = json.dumps(fields[k])
    sets = ",".join(f"{k}=?" for k in fields)
    with _lock:
        _db.execute(f"update actions set {sets} where id=?", [*fields.values(), aid])
        _db.commit()


def get_action(aid: str) -> dict | None:
    r = _db.execute("select * from actions where id=?", (aid,)).fetchone()
    return _row(r) if r else None


def list_actions(limit: int = 200) -> list[dict]:
    return [_row(r) for r in _db.execute("select * from actions order by ts desc limit ?", (limit,))]


def spent_today() -> int:
    midnight = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    r = _db.execute(
        "select coalesce(sum(amount),0) from actions where kind='spend' and status='executed' and ts>=?", (midnight,)
    ).fetchone()
    return int(r[0])


def has_recent_duplicate(kind: str, counterparty: str, amount: int, hours: int = 24, exclude_id=None) -> bool:
    since = time.time() - hours * 3600
    r = _db.execute(
        "select 1 from actions where kind=? and lower(counterparty)=lower(?) and amount=? and amount>0"
        " and status in ('executed','pending_approval') and ts>=? and id!=? limit 1",
        (kind, counterparty, amount, since, exclude_id or ""),
    ).fetchone()
    return r is not None


def summary() -> dict:
    """The money screen."""
    q = lambda sql: int(_db.execute(sql).fetchone()[0])  # noqa: E731
    return {
        "spent": q("select coalesce(sum(amount),0) from actions where kind='spend' and status='executed'"),
        "billed": q("select coalesce(sum(amount),0) from actions where kind='collect' and status='executed'"),
        "collected": q("select coalesce(sum(amount),0) from actions where kind='inbound' and status='executed'"),
        "blocked": q("select coalesce(sum(amount),0) from actions where status='blocked'"),
        "pending_approval": q("select coalesce(sum(amount),0) from actions where status='pending_approval'"),
        "actions": q("select count(*) from actions"),
        "currency": config.CURRENCY,
    }


def log_event(run_id: str | None, type_: str, data) -> None:
    with _lock:
        _db.execute(
            "insert into events (ts, run_id, type, data) values (?,?,?,?)",
            (time.time(), run_id, type_, json.dumps(data, default=str)),
        )
        _db.commit()


def events(run_id: str, after_id: int = 0) -> list[dict]:
    rows = _db.execute("select * from events where run_id=? and id>? order by id", (run_id, after_id))
    return [{**dict(r), "data": json.loads(r["data"])} for r in rows]


def reset() -> None:
    with _lock:
        _db.execute("delete from actions")
        _db.execute("delete from events")
        _db.commit()
