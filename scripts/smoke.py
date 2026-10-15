"""Risky-integration check. Run it first: proves keys work and shows which Stripe rails this account has.
   .\\dev.ps1 smoke            probe only (creates nothing)
   .\\dev.ps1 smoke --write    also create a test invoice + test charge to prove money moves"""

import sys

from app import config

config.assert_test_mode()

from app import stripe_ops as so  # noqa: E402
from app.policy import policy  # noqa: E402


def main():
    print("Stripe key set:", bool(config.STRIPE_SECRET_KEY), "| Anthropic key set:", bool(config.ANTHROPIC_API_KEY))
    for k, v in so.probe().items():
        print(f"  stripe {k:15} {v}")

    if config.ANTHROPIC_API_KEY:
        from app.agent import client

        r = client.messages.create(
            model=config.AGENT_MODEL, max_tokens=50, output_config={"effort": "low"},
            messages=[{"role": "user", "content": "Reply with just: ok"}],
        )
        print("  anthropic", config.AGENT_MODEL, "->", "".join(b.text for b in r.content if b.type == "text"))

    d = policy.evaluate({"kind": "spend", "amount": 18000, "counterparty": "Figma", "evidence": ["x"]})
    print("  policy  Figma £180 ->", d.outcome, d.clauses)

    if "--write" in sys.argv:
        inv = so.create_invoice("Smoke Test Ltd", [{"description": "Smoke test line", "amount": 1234}])
        print("  invoice", inv["stripe_id"], inv["result"]["hosted_invoice_url"])
        pi = so.charge_customer_test_card("Smoke Test Ltd", 1500, "Smoke test charge")
        print("  charge ", pi["stripe_id"], pi["result"]["status"], pi["stripe_url"])


if __name__ == "__main__":
    main()
