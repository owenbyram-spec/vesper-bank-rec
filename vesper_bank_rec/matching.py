"""Bounded deterministic one-to-one candidate proposals; no automatic confirmation."""

from datetime import date
from difflib import SequenceMatcher
from .models import DomainError, money, digest


def rows(session):
    return (session.get("ledger") or {}).get("rows", []) + session.get("carry", [])


def unmatched(session):
    used_bank = {item["bank_id"] for item in session["matches"]}
    used_ledger = {item["ledger_id"] for item in session["matches"]}
    return (
        [r for r in (session.get("bank") or {}).get("rows", []) if r["id"] not in used_bank],
        [r for r in rows(session) if r["id"] not in used_ledger],
    )


def candidates(session):
    # Amount buckets and a bounded date-neighborhood keep large duplicate groups
    # practical. Suggestions are greedy review aids, never a claim of optimality.
    from bisect import bisect_left

    bank, ledger = unmatched(session)
    pools = {}
    for row in ledger:
        pools.setdefault(row["amount"], []).append(row)
    for pool in pools.values():
        pool.sort(key=lambda row: (row["date"], row["id"]))
    dates = {key: [row["date"] for row in pool] for key, pool in pools.items()}
    proposed = []
    for bank_row in sorted(bank, key=lambda row: (row["date"], row["id"])):
        pool = pools.get(bank_row["amount"], [])
        if not pool:
            continue
        pivot = bisect_left(dates[bank_row["amount"]], bank_row["date"])
        choices = []
        for index in range(max(0, pivot - 20), min(len(pool), pivot + 20)):
            ledger_row = pool[index]
            delta = abs((date.fromisoformat(bank_row["date"]) - date.fromisoformat(ledger_row["date"])).days)
            if delta > 120:
                continue
            # Memo comparison is bounded as well; full descriptions remain evidence.
            memo = SequenceMatcher(
                None, bank_row["description"][:300].casefold(), ledger_row["description"][:300].casefold()
            ).ratio()
            score = round((1 if delta == 0 else max(0, 1 - delta / 120)) * 0.65 + memo * 0.35, 4)
            choices.append((score, -delta, ledger_row["id"], index, memo))
        if not choices:
            continue
        score, negative_delta, _, index, memo = max(choices)
        ledger_row = pool.pop(index)
        dates[bank_row["amount"]].pop(index)
        proposal = {
            "bank_id": bank_row["id"],
            "ledger_id": ledger_row["id"],
            "score": score,
            "date_distance": -negative_delta,
            "reason": f"Exact signed amount; {-negative_delta} day date difference; memo similarity {round(memo * 100)}%.",
        }
        proposal["id"] = (
            "proposal_"
            + digest([session["id"], session["input_version"], bank_row["id"], ledger_row["id"]])[:24]
        )
        proposal["input_version"] = session["input_version"]
        proposed.append(proposal)
    return proposed


def validate_pair(session, bank_id, ledger_id):
    bank, ledger = unmatched(session)
    bank_row = next((r for r in bank if r["id"] == bank_id), None)
    ledger_row = next((r for r in ledger if r["id"] == ledger_id), None)
    if not bank_row or not ledger_row:
        raise DomainError("Match references must be unmatched rows in the selected account/session.")
    if money(bank_row["amount"]) != money(ledger_row["amount"]):
        raise DomainError("A one-to-one match requires the exact same signed amount.")
    return bank_row, ledger_row
