"""Deterministic financial lifecycle, first-use anchor and outstanding continuity."""

from datetime import date, timedelta
from decimal import Decimal
from .models import DomainError, amount, money, day, label, evidence, uid, digest, now
from .matching import unmatched
from .controls import source_controls

SCHEMA_VERSION = 1


def current(workspace):
    selected = workspace.get("active_session_id")
    for session in workspace["sessions"]:
        if session["id"] == selected:
            return session
    raise DomainError("Create or select a reconciliation period first.")


def editable(session):
    if session["status"] == "completed":
        raise DomainError("This period is completed. Reopen it before changing its work.")


def event(workspace, session, action, detail=None):
    entry = {
        "id": uid("event"),
        "at": now(),
        "action": action,
        "detail": detail or {},
        "input_version": session["input_version"],
    }
    session["history"].append(entry)
    workspace["events"].append({"at": entry["at"], "session_id": session["id"], "action": action})


def new_session(workspace, payload):
    account_id = label(payload.get("account_id"), "Account ID")
    account_label = label(payload.get("account_label", account_id), "Account name")
    start, end = day(payload.get("period_start")), day(payload.get("period_end"))
    if start > end or (date.fromisoformat(end) - date.fromisoformat(start)).days > 183:
        raise DomainError("Choose an ordered period of no more than six months.")
    same = [s for s in workspace["sessions"] if s["account_id"] == account_id]
    for existing in same:
        if not (end < existing["period_start"] or start > existing["period_end"]):
            raise DomainError(
                "This account already has a period overlapping these dates. Resume that period."
            )
    prior = max((s for s in same if s["period_end"] < start), key=lambda s: s["period_end"], default=None)
    if same and not prior:
        raise DomainError("Create periods in chronological order for this account.")
    if prior and (
        prior["status"] != "completed"
        or (date.fromisoformat(start) - date.fromisoformat(prior["period_end"])).days != 1
    ):
        raise DomainError(
            "The immediately preceding period must be completed and contiguous before carrying items forward."
        )
    session = {
        "id": uid("session"),
        "account_id": account_id,
        "account_label": account_label,
        "currency": workspace["currency"],
        "period_start": start,
        "period_end": end,
        "created_at": now(),
        "status": "draft",
        "input_version": 1,
        "bank": None,
        "ledger": None,
        "anchor": None,
        "carry": [],
        "matches": [],
        "classifications": {},
        "drafts": [],
        "history": [],
        "completed_at": None,
        "completion": None,
    }
    if prior:
        result = summary(prior)
        session["anchor"] = {
            "kind": "prior_completed",
            "evidence": f"Prior completed reconciliation {prior['id']}",
            "prior_session_id": prior["id"],
            "prior_result_hash": prior["completion"]["result_hash"],
            "bank_opening": prior["bank"]["closing"],
            "book_opening": prior["ledger"]["closing"],
            "as_of": prior["period_end"],
            "accepted_at": now(),
        }
        session["carry"] = [{**r, "carried_from": prior["id"]} for r in result["outstanding"]]
    workspace["sessions"].append(session)
    workspace["active_session_id"] = session["id"]
    event(workspace, session, "period_created")
    return session


def set_anchor(workspace, session, payload):
    editable(session)
    if session["anchor"] and session["anchor"]["kind"] == "prior_completed":
        raise DomainError("The prior completed period is the immutable opening anchor.")
    if not session["bank"] or not session["ledger"]:
        raise DomainError("Import independent bank and book controls before reviewing the opening anchor.")
    kind = payload.get("kind")
    if kind not in ("zero_outstanding", "prior_reconciled"):
        raise DomainError(
            "Select an evidenced zero-outstanding opening or an itemized prior reconciled opening."
        )
    note = evidence(payload.get("evidence"))
    as_of = day(payload.get("as_of"))
    expected = (date.fromisoformat(session["period_start"]) - timedelta(days=1)).isoformat()
    if as_of != expected:
        raise DomainError(f"The opening anchor must be dated {expected}.")
    carry = []
    outstanding = payload.get("outstanding", [])
    if not isinstance(outstanding, list) or len(outstanding) > 1000:
        raise DomainError("Opening outstanding items must be a bounded list.")
    if kind == "zero_outstanding" and outstanding:
        raise DomainError("Zero-outstanding review cannot include opening outstanding items.")
    for item in outstanding:
        item_date = day(item.get("date"))
        if item_date > as_of:
            raise DomainError("Opening outstanding items must be dated on/before the anchor.")
        signed = amount(item.get("amount"))
        if money(signed) == 0:
            raise DomainError("Outstanding amounts must be nonzero.")
        carry.append(
            {
                "id": uid("opening"),
                "date": item_date,
                "description": label(item.get("description"), "Description", 2000),
                "amount": signed,
                "evidence": evidence(item.get("evidence")),
                "origin_side": "opening",
                "account_id": session["account_id"],
                "currency": session["currency"],
                "source_hash": digest(item),
                "ordinal": len(carry) + 1,
            }
        )
    adjusted_opening = money(session["bank"]["opening"]) + sum(
        (money(r["amount"]) for r in carry), Decimal("0")
    )
    if adjusted_opening != money(session["ledger"]["opening"]):
        raise DomainError(
            "The independent opening balances do not reconcile with the itemized opening outstanding. Resolve the prior evidence first."
        )
    session["carry"] = carry
    session["anchor"] = {
        "kind": kind,
        "evidence": note,
        "as_of": as_of,
        "accepted_at": now(),
        "bank_opening": session["bank"]["opening"],
        "book_opening": session["ledger"]["opening"],
    }
    session["input_version"] += 1
    session["drafts"] = []
    event(workspace, session, "opening_anchor_reviewed", {"kind": kind, "outstanding_items": len(carry)})


def summary(session):
    bank_control, book_control = source_controls(session, "bank"), source_controls(session, "ledger")
    blockers = []
    if not bank_control["ready"]:
        blockers.extend(bank_control["errors"])
    if not book_control["ready"]:
        blockers.extend(book_control["errors"])
    anchor = session.get("anchor")
    if not anchor:
        blockers.append(
            "First use requires a reviewed prior reconciled anchor or evidenced zero-outstanding opening."
        )
    elif session["bank"] and session["ledger"]:
        if (
            anchor["bank_opening"] != session["bank"]["opening"]
            or anchor["book_opening"] != session["ledger"]["opening"]
        ):
            blockers.append("Imported opening balances differ from the accepted opening anchor.")
    bank_rows, book_rows = unmatched(session)
    outstanding, pending, corrections, unclassified = [], [], [], []
    for row in book_rows:
        classification = session["classifications"].get(row["id"])
        if (
            row.get("origin_side") == "opening"
            or row.get("carried_from")
            or (classification and classification["kind"] == "outstanding")
        ):
            outstanding.append(
                {
                    **row,
                    "evidence": (classification or {}).get(
                        "evidence", row.get("evidence", "Prior completed period")
                    ),
                }
            )
        else:
            unclassified.append(row["id"])
    for row in bank_rows:
        classification = session["classifications"].get(row["id"])
        if classification and classification["kind"] == "bank_correction":
            corrections.append(
                {**row, "adjustment": amount(-money(row["amount"])), "evidence": classification["evidence"]}
            )
        elif classification and classification["kind"] in (
            "bank_fee",
            "interest",
            "returned_payment",
            "book_adjustment",
        ):
            pending.append(
                {
                    **row,
                    "evidence": classification["evidence"],
                    "classification": classification["kind"],
                    "status": "pending_external_posting",
                }
            )
        else:
            unclassified.append(row["id"])
    if unclassified:
        blockers.append(
            f"{len(unclassified)} unmatched record(s) need explicit classification or a confirmed match."
        )
    if pending:
        blockers.append(
            f"{len(pending)} book adjustment(s) remain pending. Post externally and refresh/import the supported ledger, then match the transaction."
        )
    bank_close = money(session["bank"]["closing"]) if session["bank"] else Decimal("0")
    book_close = money(session["ledger"]["closing"]) if session["ledger"] else Decimal("0")
    deposits = sum((money(r["amount"]) for r in outstanding if money(r["amount"]) > 0), Decimal("0"))
    withdrawals = -sum((money(r["amount"]) for r in outstanding if money(r["amount"]) < 0), Decimal("0"))
    bank_corrections = sum((money(r["adjustment"]) for r in corrections), Decimal("0"))
    book_adjustments = sum((money(r["amount"]) for r in pending), Decimal("0"))
    adjusted_bank = bank_close + deposits - withdrawals + bank_corrections
    adjusted_book = book_close + book_adjustments
    difference = adjusted_bank - adjusted_book
    if session["bank"] and session["ledger"] and difference != 0:
        blockers.append("Adjusted bank and adjusted books do not agree exactly.")
    result = {
        "session_id": session["id"],
        "account_id": session["account_id"],
        "period_start": session["period_start"],
        "period_end": session["period_end"],
        "currency": session["currency"],
        "input_version": session["input_version"],
        "bank_control": bank_control,
        "book_control": book_control,
        "opening_anchor": anchor,
        "bank_closing": amount(bank_close),
        "book_closing": amount(book_close),
        "deposits_in_transit": amount(deposits),
        "outstanding_withdrawals": amount(withdrawals),
        "bank_corrections": amount(bank_corrections),
        "pending_book_adjustments": amount(book_adjustments),
        "adjusted_bank": amount(adjusted_bank),
        "adjusted_books": amount(adjusted_book),
        "difference": amount(difference),
        "outstanding": outstanding,
        "pending_adjustments": pending,
        "corrections": corrections,
        "unclassified": unclassified,
        "matches": len(session["matches"]),
        "unmatched_bank": len(bank_rows),
        "unmatched_book": len(book_rows),
        "blockers": blockers,
        "ready_to_complete": not blockers,
    }
    result["evidence_id"] = "reconciliation_" + digest(result)[:24]
    return result


def commit_import(workspace, session, preview):
    editable(session)
    if not preview["ready"]:
        raise DomainError(
            "Import controls failed. Correct the source and preview again; no rows were imported."
        )
    if preview["input_version"] != session["input_version"]:
        raise DomainError("This import preview is stale. Preview again against the current period.")
    side = preview["side"]
    session[side] = preview
    # Replacing evidence invalidates review decisions rather than carrying stale authority.
    session["matches"] = []
    session["classifications"] = {}
    session["drafts"] = []
    session["input_version"] += 1
    event(
        workspace,
        session,
        "source_imported",
        {"side": side, "source_hash": preview["source_hash"], "rows": len(preview["rows"])},
    )


def complete(workspace, session, note):
    editable(session)
    result = summary(session)
    if result["blockers"]:
        raise DomainError("Completion blocked: " + " ".join(result["blockers"]))
    session["status"] = "completed"
    session["completed_at"] = now()
    session["completion"] = {
        "accepted_at": session["completed_at"],
        "acceptance_note": evidence(note),
        "result_hash": digest(result),
        "input_version": session["input_version"],
        "result": result,
    }
    event(workspace, session, "completed", {"result_hash": session["completion"]["result_hash"]})


def reopen(workspace, session, note):
    if session["status"] != "completed":
        raise DomainError("Only a completed period can be reopened.")
    if any(
        s.get("anchor", {}) and s["anchor"].get("prior_session_id") == session["id"]
        for s in workspace["sessions"]
    ):
        raise DomainError(
            "A later period depends on this opening anchor. Preserve continuity; resolve the latest dependent period first."
        )
    session["status"] = "review"
    session["completed_at"] = None
    event(workspace, session, "reopened", {"note": evidence(note), "prior_completion": session["completion"]})
    session["completion"] = None
