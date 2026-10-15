"""Thin Stripe test-mode helpers for both tracks. Every function returns
{stripe_id, stripe_type, stripe_url, result} so the audit trail can link straight to the dashboard.
`metadata` carries the audit action id and clause ids, so the Stripe dashboard itself shows why."""

import stripe

from . import config

stripe.api_key = config.STRIPE_SECRET_KEY
CUR = config.CURRENCY
DASH = "https://dashboard.stripe.com/test"

_customers: dict[str, str] = {}  # name/email -> cus_ id, avoids search-index lag in test mode


def _out(obj, type_: str, path: str, **result) -> dict:
    return {"stripe_id": obj.id, "stripe_type": type_, "stripe_url": f"{DASH}/{path}/{obj.id}", "result": result}


# ---------- shared ----------


def find_or_create_customer(name: str, email: str | None = None, metadata: dict | None = None) -> str:
    key = (email or name).lower()
    if key in _customers:
        return _customers[key]
    if email:
        found = stripe.Customer.list(email=email, limit=1).data
        if found:
            _customers[key] = found[0].id
            return found[0].id
    c = stripe.Customer.create(name=name, email=email, metadata=metadata or {})
    _customers[key] = c.id
    return c.id


# ---------- Track 2: collect ----------


def create_invoice(customer_name, line_items, customer_email=None, days_until_due=14, send=False, metadata=None):
    """line_items: [{description, amount}] in minor units. Returns finalized invoice with hosted URL."""
    cus = find_or_create_customer(customer_name, customer_email)
    inv = stripe.Invoice.create(
        customer=cus,
        collection_method="send_invoice",
        days_until_due=days_until_due,
        currency=CUR,
        metadata=metadata or {},
    )
    for li in line_items:
        stripe.InvoiceItem.create(
            customer=cus, invoice=inv.id, amount=int(li["amount"]), currency=CUR, description=li["description"]
        )
    inv = stripe.Invoice.finalize_invoice(inv.id)
    if send and customer_email:
        inv = stripe.Invoice.send_invoice(inv.id)
    return _out(
        inv, "invoice", "invoices", number=inv.number, total=inv.total, hosted_invoice_url=inv.hosted_invoice_url
    )


def create_payment_link(product_name, amount, quantity=1, metadata=None):
    price = stripe.Price.create(currency=CUR, unit_amount=int(amount), product_data={"name": product_name})
    link = stripe.PaymentLink.create(line_items=[{"price": price.id, "quantity": quantity}], metadata=metadata or {})
    return {
        "stripe_id": link.id,
        "stripe_type": "payment_link",
        "stripe_url": f"{DASH}/payment-links/{link.id}",
        "result": {"url": link.url, "amount": int(amount) * quantity},
    }


def ensure_meter(event_name: str, display_name: str | None = None) -> str:
    for m in stripe.billing.Meter.list(limit=100).auto_paging_iter():
        if m.event_name == event_name:
            return m.id
    m = stripe.billing.Meter.create(
        display_name=display_name or event_name,
        event_name=event_name,
        default_aggregation={"formula": "sum"},
        customer_mapping={"event_payload_key": "stripe_customer_id", "type": "by_id"},
        value_settings={"event_payload_key": "value"},
    )
    return m.id


def record_meter_event(customer_name, event_name, value, customer_email=None, identifier=None):
    cus = find_or_create_customer(customer_name, customer_email)
    meter_id = ensure_meter(event_name)
    ev = stripe.billing.MeterEvent.create(
        event_name=event_name,
        payload={"stripe_customer_id": cus, "value": str(int(value))},
        **({"identifier": identifier} if identifier else {}),
    )
    return {
        "stripe_id": ev.identifier,
        "stripe_type": "meter_event",
        "stripe_url": f"{DASH}/meters/{meter_id}",
        "result": {"meter": meter_id, "customer": cus, "value": int(value)},
    }


def charge_customer_test_card(customer_name, amount, description, customer_email=None, metadata=None):
    """Collect immediately with Stripe's test Visa. Also creates something for a refunds agent to refund."""
    cus = find_or_create_customer(customer_name, customer_email)
    pi = stripe.PaymentIntent.create(
        amount=int(amount),
        currency=CUR,
        customer=cus,
        description=description,
        payment_method="pm_card_visa",
        confirm=True,
        automatic_payment_methods={"enabled": True, "allow_redirects": "never"},
        metadata=metadata or {},
    )
    return _out(pi, "payment_intent", "payments", status=pi.status)


# ---------- Track 1: spend ----------


def refund_payment(payment_intent_id, amount=None, metadata=None):
    params = {"payment_intent": payment_intent_id, "reason": "requested_by_customer", "metadata": metadata or {}}
    if amount:
        params["amount"] = int(amount)
    r = stripe.Refund.create(**params)
    return {
        "stripe_id": r.id,
        "stripe_type": "refund",
        "stripe_url": f"{DASH}/payments/{payment_intent_id}",
        "result": {"status": r.status, "amount": r.amount},
    }


def transfer_to_connected_account(destination, amount, description, metadata=None):
    """Contractor payouts via Connect. Needs Connect enabled and a connected acct_ id (see policy.yaml)."""
    t = stripe.Transfer.create(
        amount=int(amount), currency=CUR, destination=destination, description=description, metadata=metadata or {}
    )
    return _out(t, "transfer", "connect/transfers", destination=destination)


def issue_virtual_card(holder_name, spending_limit, email=None, metadata=None):
    """Capped single-purpose virtual card via Issuing. Needs Issuing enabled in test mode."""
    first, _, last = holder_name.partition(" ")
    ch = stripe.issuing.Cardholder.create(
        type="individual",
        name=holder_name,
        email=email or "orders@marcos-camden.example",
        phone_number="+447700900123",  # UK/EU Issuing needs a phone on file for 3D Secure (Ofcom drama range)
        individual={
            "first_name": first or holder_name, "last_name": last or "Agent",
            # UK/EU: above the low-value threshold Stripe wants a verified cardholder (DOB + accepted card terms).
            "dob": {"day": 1, "month": 6, "year": 1985},
            "card_issuing": {"user_terms_acceptance": {"date": int(__import__("time").time()), "ip": "127.0.0.1"}},
        },
        billing={"address": {"line1": "1 Finsbury Avenue", "city": "London", "postal_code": "EC2M 2PP", "country": "GB"}},
    )
    card = stripe.issuing.Card.create(
        cardholder=ch.id,
        currency=CUR,
        type="virtual",
        status="active",
        spending_controls={"spending_limits": [{"amount": int(spending_limit), "interval": "all_time"}]},
        metadata=metadata or {},
    )
    return _out(card, "issuing_card", "issuing/cards", last4=card.last4, limit=int(spending_limit))


def simulate_card_purchase(card_id, amount, merchant_name, settle_if_verification_blocked=False):
    """Test helper: a merchant charges the card. Capture so money actually moves."""
    auth = stripe.issuing.Authorization.TestHelpers.create(
        card=card_id, amount=int(amount), currency=CUR, merchant_data={"name": merchant_name},
        # UK/EU strong customer authentication: a card-not-present charge needs 3DS + CVC + expiry checks.
        verification_data={"three_d_secure": {"result": "authenticated"}, "cvc_check": "match", "expiry_check": "match"},
    )
    if auth.approved:
        auth = stripe.issuing.Authorization.TestHelpers.capture(auth.id)
        return _out(auth, "issuing_authorization", "issuing/authorizations", approved=True, status=auth.status)
    reason = auth.request_history[-1].reason if auth.request_history else "declined"
    if settle_if_verification_blocked and reason == "cardholder_verification_required":
        # UK sandbox: card-not-present auths over a threshold need a fully verified cardholder, which test mode
        # can't provide. Settle the (already agreed, within-cap) amount as a captured Issuing transaction instead.
        tx = stripe.issuing.Transaction.TestHelpers.create_force_capture(
            card=card_id, amount=int(amount), currency=CUR, merchant_data={"name": merchant_name}
        )
        return {"stripe_id": tx.id, "stripe_type": "issuing_transaction", "stripe_url": f"{DASH}/issuing/transactions/{tx.id}",
                "result": {"approved": True, "status": "captured", "settled_via": "force_capture", "declined_auth": auth.id}}
    return _out(auth, "issuing_authorization", "issuing/authorizations", approved=False, status=auth.status, reason=reason)


def top_up_test_balance(amount):
    """Puts funds straight into available balance (test card that bypasses pending). Needed before transfers."""
    pi = stripe.PaymentIntent.create(
        amount=int(amount),
        currency=CUR,
        payment_method="pm_card_bypassPending",
        confirm=True,
        automatic_payment_methods={"enabled": True, "allow_redirects": "never"},
        description="Test balance top-up",
    )
    return _out(pi, "payment_intent", "payments", status=pi.status)


# ---------- capability probe (run in the first 30 minutes) ----------


def probe() -> dict:
    out = {"test_mode": config.STRIPE_SECRET_KEY.startswith(("sk_test_", "rk_test_"))}
    checks = {
        "balance": lambda: stripe.Balance.retrieve(),
        "invoices": lambda: stripe.Invoice.list(limit=1),
        "billing_meters": lambda: stripe.billing.Meter.list(limit=1),
        "issuing": lambda: stripe.issuing.Cardholder.list(limit=1),
        "connect": lambda: stripe.Account.list(limit=1),
    }
    for name, fn in checks.items():
        try:
            fn()
            out[name] = "ok"
        except stripe.StripeError as e:
            out[name] = f"unavailable: {getattr(e, 'user_message', None) or str(e)[:160]}"
    return out
