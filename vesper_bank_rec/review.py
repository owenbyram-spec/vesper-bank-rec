"""Explicit, atomic review of a bounded set of proposed one-to-one matches."""

from .matching import validate_pair
from .models import DomainError, evidence, now, uid
from .reconciliation import editable, event

MAX_REVIEW_BATCH = 40


def proposal_index(session):
    drafts = session["drafts"]
    if (not isinstance(drafts, list)
            or any(not isinstance(draft, dict) or not isinstance(draft.get("id"), str) for draft in drafts)):
        raise DomainError("Saved proposals have invalid identities. Generate new candidates before reviewing.")
    by_id = {draft["id"]: draft for draft in drafts}
    if len(by_id) != len(drafts):
        raise DomainError("Saved proposals repeat an identity. Generate new candidates before reviewing.")
    return by_id


def confirm_proposals(workspace, session, proposal_ids, note):
    editable(session)
    review_note = evidence(note)
    if (
        not isinstance(proposal_ids, list)
        or not 1 <= len(proposal_ids) <= MAX_REVIEW_BATCH
        or any(not isinstance(identifier, str) for identifier in proposal_ids)
        or len(set(proposal_ids)) != len(proposal_ids)
    ):
        raise DomainError("Select between one and forty distinct proposals you have reviewed.")
    by_id = proposal_index(session)
    pairs, bank_ids, ledger_ids = [], set(), set()
    # Validate the entire selection before making any change. Conflicting or
    # stale drafts must never leave a partially accepted batch behind.
    for identifier in proposal_ids:
        draft = by_id.get(identifier)
        if not draft or draft["input_version"] != session["input_version"]:
            raise DomainError("A selected proposal is missing or stale. Refresh and review the current drafts.")
        bank_id, ledger_id = draft["bank_id"], draft["ledger_id"]
        validate_pair(session, bank_id, ledger_id)
        if bank_id in bank_ids or ledger_id in ledger_ids:
            raise DomainError("Selected proposals reuse a transaction. Review each distinct pair again.")
        bank_ids.add(bank_id)
        ledger_ids.add(ledger_id)
        pairs.append((bank_id, ledger_id))
    review_id = uid("review")
    accepted_at = now()
    matches = [
        {
            "id": uid("match"),
            "bank_id": bank_id,
            "ledger_id": ledger_id,
            "accepted_at": accepted_at,
            "note": review_note,
            "review_batch_id": review_id,
        }
        for bank_id, ledger_id in pairs
    ]
    session["matches"].extend(matches)
    session["drafts"] = [
        draft for draft in session["drafts"]
        if draft["bank_id"] not in bank_ids and draft["ledger_id"] not in ledger_ids
    ]
    for match in matches:
        event(workspace, session, "match_confirmed", dict(match))
    event(workspace, session, "match_batch_reviewed", {
        "review_batch_id": review_id,
        "proposal_ids": list(proposal_ids),
        "match_ids": [match["id"] for match in matches],
        "note": review_note,
    })
    return {"confirmed": True, "count": len(matches), "review_batch_id": review_id}
