"""Independent source control checks; evidence is never inferred from a balanced result."""

from decimal import Decimal
from .models import money, amount


def source_controls(session, side):
    source = session.get(side)
    if not source:
        return {
            "ready": False,
            "errors": [f"Import an independent {side} source and its opening/closing balances."],
        }
    activity = sum((money(r["amount"]) for r in source["rows"]), Decimal("0"))
    errors = list(source.get("errors", []))
    if money(source["opening"]) + activity != money(source["closing"]):
        errors.append("Opening + activity does not equal closing.")
    if source["account_id"] != session["account_id"] or source["currency"] != session["currency"]:
        errors.append("Source account/currency does not equal selected session.")
    if source["period_start"] != session["period_start"] or source["period_end"] != session["period_end"]:
        errors.append("Source dates do not equal selected session.")
    return {
        "ready": not errors,
        "opening": source["opening"],
        "activity": amount(activity),
        "closing": source["closing"],
        "rows": len(source["rows"]),
        "source_hash": source["source_hash"],
        "observed_at": source["observed_at"],
        "errors": errors,
    }
