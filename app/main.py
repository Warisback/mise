"""FastAPI app: API under /api, dashboard at /. Routes and shapes are pinned in CONTRACT.md."""

import stripe
from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from fastapi.responses import PlainTextResponse

from . import agent, audit, config, delivery, money, plan, telegram, tools
from . import stripe_ops as so
from .forecast import FORECAST
from .memory import memory
from .policy import policy

config.assert_test_mode()
app = FastAPI(title="Mise: restaurant buying agent")


@app.on_event("startup")
def _start_telegram():
    telegram.start()


class DeliverIn(BaseModel):
    action_id: str | None = None
    supplier: str | None = None
    amount: int | None = None


@app.get("/api/forecast")
def forecast():
    return FORECAST


class StockIn(BaseModel):
    filename: str = ""
    content_type: str = ""
    data: str  # base64 file contents


@app.get("/api/stock")
def get_stock():
    from . import stock

    return {"stock": plan.STOCK, "source": stock.SOURCE}


@app.post("/api/stock")
def upload_stock(body: StockIn):
    from . import stock

    try:
        return stock.update(body.filename, body.content_type, body.data)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/forecast/week")
def forecast_week():
    from .forecast import WEEK

    return WEEK


@app.get("/api/plan")
def get_plan():
    return plan.build()


@app.get("/api/telegram")
def telegram_status():
    return telegram.status()


@app.post("/api/demo/deliver")
def deliver(body: DeliverIn):
    try:
        return delivery.charge(body.supplier, body.amount, body.action_id)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/report.csv")
def report():
    return PlainTextResponse(money.report_csv(), media_type="text/csv",
                             headers={"Content-Disposition": "attachment; filename=mise-bookkeeper-report.csv"})


class RunIn(BaseModel):
    content: str
    source: str = "manual"


class DecideIn(BaseModel):
    approver: str = "Finance lead"


class AskIn(BaseModel):
    question: str


@app.get("/api/health")
def health():
    return {
        "ok": True,
        "stripe_key": bool(config.STRIPE_SECRET_KEY),
        "anthropic_key": agent.provider() != "scripted",
        "model": {"anthropic": config.AGENT_MODEL, "openai": agent.OPENAI_MODEL, "scripted": "scripted (no model)"}[agent.provider()],
        "memory": memory.name,
        "tools": list(tools.TOOLS),
    }


@app.get("/api/capabilities")
def capabilities():
    return so.probe()


@app.get("/api/policy")
def get_policy():
    policy.reload()
    return {"company": policy.doc.get("company"), "clauses": policy.clauses, "counterparties": list(policy.counterparties)}


@app.get("/api/triggers")
def triggers():
    return [
        {"name": p.stem, "source": p.suffix.lstrip("."), "content": p.read_text(encoding="utf-8")}
        for p in sorted(config.TRIGGERS_DIR.glob("*.*"))
    ]


@app.post("/api/runs")
def start_run(body: RunIn):
    return {"run_id": agent.start(body.content, body.source)}


@app.get("/api/runs/{run_id}")
def get_run(run_id: str, after: int = 0):
    return {"run_id": run_id, "status": agent.status(run_id), "events": audit.events(run_id, after)}


@app.get("/api/actions")
def actions():
    return audit.list_actions()


@app.get("/api/actions/{action_id}/why")
def why(action_id: str):
    out = agent.why(action_id)
    if "error" in out:
        raise HTTPException(404, out["error"])
    return out


@app.post("/api/actions/{action_id}/approve")
def approve(action_id: str, body: DecideIn):
    return tools.approve(action_id, body.approver)


@app.post("/api/actions/{action_id}/reject")
def reject(action_id: str, body: DecideIn):
    return tools.reject(action_id, body.approver)


@app.post("/api/ask")
def ask(body: AskIn):
    return {"answer": agent.ask(body.question)}


@app.get("/api/summary")
def summary():
    return {**audit.summary(), **money.savings()}


@app.post("/api/webhooks/stripe")
async def stripe_webhook(request: Request):
    payload = await request.body()
    if config.STRIPE_WEBHOOK_SECRET:
        try:
            event = stripe.Webhook.construct_event(
                payload, request.headers.get("stripe-signature", ""), config.STRIPE_WEBHOOK_SECRET
            )
        except (ValueError, stripe.SignatureVerificationError) as e:
            raise HTTPException(400, str(e))
    else:
        event = stripe.Event.construct_from(await request.json(), stripe.api_key)

    obj = event["data"]["object"]
    audit.log_event("webhooks", event["type"], {"id": obj.get("id")})
    if event["type"] == "invoice.paid":
        # Money actually came in: counts toward "collected" on the money screen.
        audit.new_action(
            run_id="webhook", tool="invoice.paid", kind="inbound", amount=obj.get("amount_paid", 0),
            currency=obj.get("currency"), counterparty=obj.get("customer_name") or obj.get("customer"),
            outcome="allow", status="executed", clauses=[], reason="Customer paid invoice",
            evidence=[obj.get("number") or ""], stripe_id=obj.get("id"), stripe_type="invoice",
            stripe_url=f"{so.DASH}/invoices/{obj.get('id')}",
        )
        memory.remember(f"{obj.get('customer_name')} paid invoice {obj.get('number')} ({obj.get('amount_paid')} pence).")
    return {"received": True}


@app.post("/api/demo/reset")
def reset():
    """R key. Clears our audit trail and memory; Stripe test objects stay (that's fine)."""
    audit.reset()
    memory.reset()
    from . import stock  # back to last night's seed count so every rehearsal starts the same

    plan.STOCK.clear()
    plan.STOCK.update(plan._j("stock.json"))
    stock.SOURCE.update({"from": "seed data", "file": None, "at": None, "read": {}})
    for line in SUPPLIER_HISTORY:
        memory.remember(line)
    return {"ok": True}


SUPPLIER_HISTORY = [
    "Harbour Fish Co: last order 8 Oct, 14kg sea bass at £18.50/kg. On 1 Oct they delivered 2kg short and Marco wasn't refunded.",
    "Harbour Fish Co: asking prices jump on Fridays; they usually settle near last week's price for 8kg or more.",
    "Green Lane Produce: reliable, prices steady; Marco usually over-orders tomatoes and bins about 3kg on quiet nights.",
    "Meadow Dairy: burrata keeps 3 days; Marco binned 12 burrata the Monday after a rainy weekend in September.",
]


app.mount("/", StaticFiles(directory=config.WEB_DIR, html=True), name="web")
