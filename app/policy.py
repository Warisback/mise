"""Deterministic policy gate. The model proposes; this code disposes.

Order of evaluation (first match wins):
  1. any deny-type rule trips          -> deny
  2. any approval-type rule trips      -> needs_approval (or allow if an approver signed off)
  3. an authorise-type rule covers it  -> allow
  4. nothing authorises it             -> deny (default-deny)
"""

from dataclasses import asdict, dataclass, field

import yaml

from . import config

DENY_TYPES = {"allowlist", "hard_cap", "daily_cap", "evidence_required", "no_duplicates", "max_unit_price_last"}


@dataclass
class Decision:
    outcome: str  # allow | needs_approval | deny
    clauses: list[str] = field(default_factory=list)  # clause ids that decided it
    reasons: list[str] = field(default_factory=list)  # one human sentence per clause

    def to_dict(self) -> dict:
        return asdict(self)


def gbp(minor: int) -> str:
    return f"£{minor / 100:,.2f}"


class Policy:
    def __init__(self, path=config.POLICY_FILE):
        self.path = path
        self.reload()

    def reload(self) -> None:
        with open(self.path, encoding="utf-8") as f:
            self.doc = yaml.safe_load(f)
        self.clauses = self.doc.get("clauses", [])
        self.counterparties = self.doc.get("counterparties", {}) or {}
        self.by_id = {c["id"]: c for c in self.clauses}

    def clause(self, cid: str) -> dict:
        return self.by_id.get(cid, {"id": cid, "title": "?", "text": "(unknown clause)"})

    def counterparty(self, name: str) -> tuple[str, dict] | None:
        for k, v in self.counterparties.items():
            if k.lower() == (name or "").strip().lower():
                return k, (v or {})
        return None

    def as_prompt(self) -> str:
        lines = [f"Company: {self.doc.get('company')}", f"Currency: {self.doc.get('currency', 'gbp').upper()}", ""]
        for c in self.clauses:
            lines.append(f"[{c['id']}] {c['title']}: {c['text']}")
        if self.counterparties:
            lines += ["", "Approved counterparties: " + ", ".join(self.counterparties.keys())]
        return "\n".join(lines)

    def evaluate(self, action: dict, spent_today: int = 0, approved_by: str | None = None) -> Decision:
        """action = {kind: spend|collect, amount: int (minor units), counterparty: str, evidence: [str]}"""
        kind, amount = action["kind"], int(action.get("amount") or 0)
        rules = [c for c in self.clauses if c.get("rule", {}).get("applies_to") in (kind, "all")]

        deny, approve, authorise = Decision("deny"), Decision("needs_approval"), None
        for c in rules:
            r, cid = c["rule"], c["id"]
            t = r["type"]
            if t == "allowlist" and not self.counterparty(action.get("counterparty", "")):
                deny.clauses.append(cid)
                deny.reasons.append(f"{action.get('counterparty')!r} is not on the approved list.")
            elif t == "hard_cap" and amount > r["amount"]:
                deny.clauses.append(cid)
                deny.reasons.append(f"{gbp(amount)} is over the {gbp(r['amount'])} hard ceiling.")
            elif t == "daily_cap" and spent_today + amount > r["amount"]:
                deny.clauses.append(cid)
                deny.reasons.append(
                    f"{gbp(spent_today)} already spent today; {gbp(amount)} more breaks the {gbp(r['amount'])} daily ceiling."
                )
            elif t == "no_duplicates" and action.get("duplicate"):
                deny.clauses.append(cid)
                deny.reasons.append(
                    f"Same amount to {action.get('counterparty')!r} already went through in the last 24 hours."
                )
            elif t == "evidence_required" and not [e for e in action.get("evidence") or [] if str(e).strip()]:
                deny.clauses.append(cid)
                deny.reasons.append("No evidence was quoted for this charge.")
            elif t == "max_unit_price_last" and [
                ln for ln in action.get("lines") or [] if ln.get("unit_price") and ln["unit_price"] > ln["last_price"]
            ]:
                for ln in [ln for ln in action["lines"] if ln.get("unit_price") and ln["unit_price"] > ln["last_price"]]:
                    deny.clauses.append(cid)
                    deny.reasons.append(
                        f"{ln['item']} at {gbp(ln['unit_price'])} is above the last price paid ({gbp(ln['last_price'])})."
                    )
            elif t == "require_approval_over" and amount > r["amount"]:
                approve.clauses.append(cid)
                approve.reasons.append(f"{gbp(amount)} is over {gbp(r['amount'])}, so a human signs off first.")
            elif t == "auto_approve_upto" and amount <= r["amount"] and authorise is None:
                authorise = Decision("allow", [cid], [f"{gbp(amount)} is within the {gbp(r['amount'])} limit of {cid}."])

        if deny.clauses:
            return deny
        if approve.clauses:
            if approved_by:
                return Decision(
                    "allow", approve.clauses, [f"Approved by {approved_by} as {', '.join(approve.clauses)} requires."]
                )
            return approve
        if authorise:
            # Cite the guard clauses that were checked and passed, too.
            passed = [c["id"] for c in rules if c["rule"]["type"] in DENY_TYPES]
            authorise.clauses += passed
            return authorise
        return Decision("deny", [], ["No clause in the policy authorises this action (default-deny)."])


policy = Policy()
