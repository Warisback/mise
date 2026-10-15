"""The agent loop: a real-world trigger comes in, Claude decides, tools act through the policy gate.
Manual loop (not the tool runner) because every call must hit our gate and the run trace."""

import json
import os
import threading
import uuid

import anthropic

from . import audit, config, tools
from .memory import LocalMemory, memory
from .policy import policy

client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY or None, base_url=config.ANTHROPIC_API_BASE)

SYSTEM = """You are Mise, the buying agent for {company}. Every night after service you place tomorrow's \
fresh-food orders so the owner, Marco, doesn't have to. Money moves in Stripe test mode.

How you work, in this order:
1. forecast_covers: tomorrow's customers and why.
2. plan_order: what each supplier needs to send.
3. recall_memory for each supplier (past prices, promises, short deliveries), then negotiate with each supplier \
in the plan, one at a time.
4. place_order for each agreed deal, using its negotiation_id. If a negotiation ends without a deal, don't order; \
say Marco needs to decide.
Rules:
- Numbers come from the tools. Never invent a quantity or price, and never restructure an order to dodge a clause.
- The policy below is enforced in code. If a tool returns "blocked" or "pending_approval", accept it and say so.
- place_order needs `reason` (one plain sentence) and `evidence`: exact quotes of the forecast drivers, the planned \
quantity against Marco's habit, and the agreed price against the asking and last price.
- Amounts are integers in pence. £162.00 = 16200.
- Finish with a short note Marco would read on his phone: each order, the amount, its status, the clause ids, \
and the money saved against his usual order.

POLICY
{policy}"""

_runs: dict[str, dict] = {}


def _call(messages: list) -> anthropic.types.beta.BetaMessage:
    return client.beta.messages.create(
        model=config.AGENT_MODEL,
        max_tokens=16000,
        system=SYSTEM.format(company=policy.doc.get("company"), policy=policy.as_prompt()),
        tools=tools.schemas(),
        messages=messages,
        output_config={"effort": config.AGENT_EFFORT},
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
    )


def run(trigger: str, source: str, run_id: str) -> None:
    _runs[run_id] = {"status": "running"}
    audit.log_event(run_id, "trigger", {"source": source, "content": trigger})
    messages = [{"role": "user", "content": f"New trigger ({source}):\n\n{trigger}"}]
    try:
        for _ in range(config.AGENT_MAX_TURNS):
            resp = _call(messages)
            for b in resp.content:
                if b.type == "text" and b.text.strip():
                    audit.log_event(run_id, "agent_text", {"text": b.text})

            if resp.stop_reason == "refusal":
                audit.log_event(run_id, "error", {"message": "Model declined this request."})
                break
            if resp.stop_reason != "tool_use":
                break

            messages.append({"role": "assistant", "content": resp.content})
            results = []
            for b in resp.content:
                if b.type != "tool_use":
                    continue
                audit.log_event(run_id, "tool_call", {"tool": b.name, "input": b.input})
                try:
                    out = tools.execute(b.name, b.input, run_id)
                except Exception as e:  # noqa: BLE001 - surface to the model, don't kill the run
                    out = {"status": "failed", "error": repr(e)}
                audit.log_event(run_id, "tool_result", {"tool": b.name, "output": out})
                results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": b.id,
                        "content": tools.as_tool_result(out),
                        "is_error": out.get("status") == "failed",
                    }
                )
            messages.append({"role": "user", "content": results})
        _runs[run_id]["status"] = "done"
    except anthropic.APIError as e:
        audit.log_event(run_id, "error", {"message": f"Anthropic API error: {e}"})
        _runs[run_id]["status"] = "error"
    audit.log_event(run_id, "done", {"status": _runs[run_id]["status"]})


def run_scripted(trigger: str, source: str, run_id: str) -> None:
    """Same tools, same policy gate, same audit trail, fixed order and templated wording.
    Used when there is no model key (or AGENT_MODE=scripted), so the demo never depends on one."""
    from .policy import gbp

    _runs[run_id] = {"status": "running"}
    audit.log_event(run_id, "trigger", {"source": source, "content": trigger})
    say = lambda text: audit.log_event(run_id, "agent_text", {"text": text})  # noqa: E731

    def call(name: str, args: dict) -> dict:
        audit.log_event(run_id, "tool_call", {"tool": name, "input": args})
        out = tools.execute(name, args, run_id)
        audit.log_event(run_id, "tool_result", {"tool": name, "output": out})
        return out

    try:
        f = call("forecast_covers", {})
        say(f"{f['weekday']}: {f['covers']} customers forecast ({f['bookings']} booked + {f['walk_ins']} walk-ins "
            f"- {f['no_shows']} no-shows). " + "; ".join(f["drivers"][1:4]) + ".")
        p = call("plan_order", {})
        suppliers = list(dict.fromkeys(ln["supplier"] for ln in p["lines"]))
        notes = []
        for sup in sorted(suppliers, key=lambda s: s != "Harbour Fish Co"):
            hist = call("recall_memory", {"query": sup})
            n = call("negotiate", {"supplier": sup})
            if n.get("status") != "agreed":
                notes.append(f"{sup}: no deal under the last price, so Marco decides.")
                continue
            key = next(ln for ln in n["lines"] if ln["item"] == max(
                n["lines"], key=lambda ln: ln["qty"] * ln["last_price"])["item"])
            evidence = [
                f"Forecast: {f['covers']} customers; {f['drivers'][1] if len(f['drivers']) > 1 else ''}",
                "; ".join(f"{ln['item'].replace('_', ' ')} {ln['qty']}{ln['unit']} vs usual {ln['habit_qty']}" for ln in n["lines"]),
                f"{key['item'].replace('_', ' ')}: asked {gbp(n['asked_unit_price'] or key['list_price'])}, agreed "
                f"{gbp(n['agreed_unit_price'])}, last paid {gbp(n['last_unit_price'])}",
            ] + (hist.get("memories") or [])[:1]
            o = call("place_order", {
                "negotiation_id": n["negotiation_id"], "counterparty": sup,
                "reason": f"Tomorrow's {f['covers']} covers need this; price agreed at or under the last price paid.",
                "evidence": evidence,
            })
            notes.append(f"{sup}: {gbp(n['total'])}, {o.get('status', '?').replace('_', ' ')} ({' '.join(o.get('clauses') or [])}).")
        say("Tonight's orders:\n" + "\n".join("- " + x for x in notes))
        _runs[run_id]["status"] = "done"
    except Exception as e:  # noqa: BLE001
        audit.log_event(run_id, "error", {"message": f"Scripted run failed: {e!r}"})
        _runs[run_id]["status"] = "error"
    audit.log_event(run_id, "done", {"status": _runs[run_id]["status"]})


OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1")


def provider() -> str:
    if os.getenv("AGENT_MODE", "") == "scripted":
        return "scripted"
    if config.ANTHROPIC_API_KEY:
        return "anthropic"
    return "openai" if OPENAI_API_KEY else "scripted"


def _openai():
    from openai import OpenAI

    return OpenAI(api_key=OPENAI_API_KEY, timeout=90)


def run_openai(trigger: str, source: str, run_id: str) -> None:
    """Same loop as run(), on OpenAI chat completions with function calling."""
    _runs[run_id] = {"status": "running"}
    audit.log_event(run_id, "trigger", {"source": source, "content": trigger})
    fns = [{"type": "function", "function": {"name": s["name"], "description": s["description"],
                                             "parameters": s["input_schema"]}} for s in tools.schemas()]
    messages = [
        {"role": "system", "content": SYSTEM.format(company=policy.doc.get("company"), policy=policy.as_prompt())},
        {"role": "user", "content": f"New trigger ({source}):\n\n{trigger}"},
    ]
    try:
        oai = _openai()
        for _ in range(config.AGENT_MAX_TURNS * 2):
            msg = oai.chat.completions.create(
                model=OPENAI_MODEL, messages=messages, tools=fns, parallel_tool_calls=False
            ).choices[0].message
            if msg.content and msg.content.strip():
                audit.log_event(run_id, "agent_text", {"text": msg.content})
            if not msg.tool_calls:
                break
            messages.append({"role": "assistant", "content": msg.content,
                             "tool_calls": [tc.model_dump() for tc in msg.tool_calls]})
            for tc in msg.tool_calls:
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                audit.log_event(run_id, "tool_call", {"tool": tc.function.name, "input": args})
                try:
                    out = tools.execute(tc.function.name, args, run_id)
                except Exception as e:  # noqa: BLE001
                    out = {"status": "failed", "error": repr(e)}
                audit.log_event(run_id, "tool_result", {"tool": tc.function.name, "output": out})
                messages.append({"role": "tool", "tool_call_id": tc.id, "content": tools.as_tool_result(out)})
        _runs[run_id]["status"] = "done"
    except Exception as e:  # noqa: BLE001 - surface API errors in the trace
        audit.log_event(run_id, "error", {"message": f"OpenAI error: {e}"})
        _runs[run_id]["status"] = "error"
    audit.log_event(run_id, "done", {"status": _runs[run_id]["status"]})


def start(trigger: str, source: str = "manual") -> str:
    # One nightly run at a time: a second L press joins the run in progress instead of ordering twice.
    busy = next((rid for rid, r in _runs.items() if r.get("status") == "running"), None)
    if busy:
        return busy
    run_id = "run_" + uuid.uuid4().hex[:8]
    _runs[run_id] = {"status": "running"}  # claim the slot before the thread starts, so a fast double press can't race
    target ={"anthropic": run, "openai": run_openai, "scripted": run_scripted}[provider()]
    threading.Thread(target=target, args=(trigger, source, run_id), daemon=True).start()
    return run_id


def status(run_id: str) -> str:
    return _runs.get(run_id, {}).get("status", "unknown")


def why(action_id: str) -> dict:
    """'Why did you pay this?' — deterministic answer from the audit row, plus what memory recalls."""
    a = audit.get_action(action_id)
    if not a:
        return {"error": "no such action"}
    clauses = [policy.clause(c) for c in a.get("clauses") or []]
    # GET route: instant local keyword recall only, never a model call. Cognee answers via /api/ask.
    recalled = LocalMemory.recall(memory, f"{a['counterparty']} {a['tool']}", k=3)
    return {"action": a, "clauses": clauses, "memory": recalled}


def ask(question: str) -> str:
    """Free-form questions about what the agent did, answered only from the audit trail and memory."""
    actions = audit.list_actions(50)
    slim = [
        {k: a.get(k) for k in ("id", "tool", "counterparty", "amount", "status", "clauses", "reason", "evidence", "stripe_id")}
        for a in actions
    ]
    if provider() == "openai":
        rows = json.dumps(slim, default=str)
        try:
            r = _openai().chat.completions.create(model=OPENAI_MODEL, messages=[
                {"role": "system", "content": "You answer a restaurant owner's or bookkeeper's questions about what a "
                 "buying agent did. Use only the audit trail, memory and policy given. Cite clause ids and action ids. "
                 "Amounts are in pence; show £. If the record doesn't say, say so.\n\nPOLICY\n" + policy.as_prompt()},
                {"role": "user", "content": f"AUDIT TRAIL\n{rows}\n\nMEMORY\n" + "\n".join(memory.recall(question, k=8))
                 + f"\n\nQUESTION\n{question}"}])
            return r.choices[0].message.content or ""
        except Exception as e:  # noqa: BLE001 - fall through to the record-only answer
            print(f"[ask] openai failed: {e!r}")
    if provider() != "anthropic":  # no model: answer straight from the matching audit row
        from .policy import gbp

        q = question.lower()
        hit = next((a for a in actions if a["counterparty"] and a["counterparty"].split()[0].lower() in q), None)
        if not hit:
            return "The record doesn't name that supplier. Ask about Harbour Fish, Green Lane or Meadow Dairy."
        cl = "; ".join(f"{c} {policy.clause(c)['title']}" for c in hit.get("clauses") or [])
        return (f"{hit['tool'].replace('_', ' ')} {gbp(hit['amount'])} to {hit['counterparty']} ({hit['status']}). "
                f"{hit.get('reason') or ''}\nRules: {cl}\nEvidence:\n- " + "\n- ".join(hit.get("evidence") or []))
    recalled = memory.recall(question, k=8)
    resp = client.messages.create(
        model=config.AGENT_MODEL,
        max_tokens=4000,
        output_config={"effort": "low"},
        system=(
            "You answer a finance manager's questions about what an autonomous finance agent did. Use only the "
            "audit trail, memory and policy given. Cite clause ids and action ids. Amounts are in pence; show £. "
            "If the record doesn't say, say so.\n\nPOLICY\n" + policy.as_prompt()
        ),
        messages=[
            {
                "role": "user",
                "content": f"AUDIT TRAIL\n{json.dumps(slim, default=str)}\n\nMEMORY\n"
                + "\n".join(recalled)
                + f"\n\nQUESTION\n{question}",
            }
        ],
    )
    return "".join(b.text for b in resp.content if b.type == "text")
