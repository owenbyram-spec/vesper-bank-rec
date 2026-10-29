"""Deterministic bank reconciliation proposals and explanations; no confirmation."""

from bisect import bisect_left, bisect_right
from collections import Counter
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from difflib import SequenceMatcher
from .models import DomainError, money, digest

EXACT_WINDOW_DAYS = 4
FUZZY_WINDOW_DAYS = 16
MEMO_SIMILARITY_FLOOR = 0.60
MAX_FUZZY_CANDIDATES = 40
MAX_DIAGNOSTIC_ROWS = 150


@dataclass(frozen=True)
class _Entry:
    row: dict
    day: date
    amount: Decimal
    memo: str

    @property
    def id(self):
        return self.row["id"]


def _entries(source):
    return [_Entry(row, date.fromisoformat(row["date"]), money(row["amount"]), _memo(row.get("description")))
            for row in source]


def _ordered(entries):
    return sorted(entries, key=lambda entry: (entry.day, entry.id))


def _index(entries):
    buckets = {}
    for entry in _ordered(entries):
        buckets.setdefault(entry.amount, []).append(entry)
    return {amount: (bucket, [entry.day.toordinal() for entry in bucket]) for amount, bucket in buckets.items()}


def _bounds(index, amount, day, window):
    bucket, days = index.get(amount, ([], []))
    ordinal = day.toordinal()
    return bucket, bisect_left(days, ordinal - window), bisect_right(days, ordinal + window)


def _window_count(index, amount, day):
    _, start, stop = _bounds(index, amount, day, FUZZY_WINDOW_DAYS)
    return stop - start


def _memo(value):
    # Imported descriptions are limited to 2,000 characters. Use the complete
    # normalized evidence within that bound, rather than a short prefix.
    return " ".join(value.casefold().split())[:2000] if isinstance(value, str) else ""


def _similarity(bank, book):
    left, right = bank.memo, book.memo
    return SequenceMatcher(None, left, right).ratio() if left and right else 0.0


def _proposal(session, bank, book, kind, similarity=1.0):
    distance = abs((bank.day - book.day).days)
    reason = f"Exact signed amount; {distance} day date difference within the 4 day window."
    if kind == "EXACT_GROUP":
        reason = "Duplicate amount group paired by date and source identity. " + reason
    elif kind == "FUZZY_DATE":
        reason = (f"Exact signed amount; {distance} day date difference within the 16 day window; "
                  f"memo similarity {similarity:.0%} meets the 60% floor.")
    return {
        "id": "proposal_" + digest([session["id"], session["input_version"], bank.id, book.id])[:24],
        "bank_id": bank.id, "ledger_id": book.id, "input_version": session["input_version"],
        "match_type": kind, "score": round(similarity, 4), "date_distance": distance,
        "reason": reason + " Human review required.",
    }


def _reason(entry, reason, message, candidate_count=0):
    return {"bank_id": entry.id, "reason": reason, "message": message, "candidate_count": candidate_count}


def _groups(session, bank, book, accepted_bank, accepted_book):
    """Pair duplicate components positionally, retaining every occurrence ID.

    Include confirmed rows when forming components so confirming one twin cannot
    make its leftover twin eligible for a distant fuzzy pairing on the next read.
    Confirmed identities stay reserved; their records are never recalculated.
    """
    buckets = {}
    for side, entries in (("bank", bank), ("book", book)):
        for entry in entries:
            if entry.amount:
                buckets.setdefault(entry.amount, []).append((entry, side))
    proposals, reasons, consumed_bank, consumed_book = [], {}, set(), set()

    def pair_component(component):
        banks = _ordered([entry for entry, side in component if side == "bank"])
        books = _ordered([entry for entry, side in component if side == "book"])
        if not banks or not books or (len(banks) == 1 and len(books) == 1):
            return
        free_bank = [entry for entry in banks if entry.id not in accepted_bank]
        free_book = [entry for entry in books if entry.id not in accepted_book]
        consumed_bank.update(entry.id for entry in free_bank)
        consumed_book.update(entry.id for entry in free_book)
        paired_bank = set()
        i = j = 0
        while i < len(free_bank) and j < len(free_book):
            bank_entry, book_entry = free_bank[i], free_book[j]
            if abs((bank_entry.day - book_entry.day).days) <= EXACT_WINDOW_DAYS:
                proposals.append(_proposal(session, bank_entry, book_entry, "EXACT_GROUP"))
                paired_bank.add(bank_entry.id)
                i += 1
                j += 1
            elif bank_entry.day < book_entry.day:
                i += 1
            else:
                j += 1
        for entry in free_bank:
            if entry.id not in paired_bank:
                reasons[entry.id] = _reason(
                    entry, "duplicate_group_remainder",
                    f"Duplicate amount group has {len(banks)} bank rows and {len(books)} book rows; "
                    "no unused positional counterpart is within 4 days. Review this occurrence separately.",
                    len(books),
                )

    for members in buckets.values():
        members.sort(key=lambda member: (member[0].day, member[0].id, member[1]))
        component = []
        previous = None
        for member in members:
            if previous is not None and (member[0].day - previous).days > EXACT_WINDOW_DAYS:
                pair_component(component)
                component = []
            component.append(member)
            previous = member[0].day
        pair_component(component)
    return proposals, reasons, consumed_bank, consumed_book


def _unproposed_reason(entry, all_book, book_index, accepted_index, all_book_days):
    if not all_book:
        return _reason(entry, "no_book_transactions", "No book rows are loaded for this account and period.")
    same = _window_count(book_index, entry.amount, entry.day)
    if same:
        accepted = _window_count(accepted_index, entry.amount, entry.day)
        if accepted == same:
            return _reason(entry, "already_matched", "Equal signed book amounts in this window are already confirmed.", same)
        return _reason(entry, "candidate_claimed",
                       "Equal signed book amounts in this window are reserved by another proposal or duplicate group.", same)
    opposite = _window_count(book_index, -entry.amount, entry.day)
    if opposite:
        return _reason(entry, "sign_mismatch", "Equal magnitudes in the date window have the opposite money direction.", opposite)
    if entry.amount in book_index:
        return _reason(entry, "outside_window", "Equal signed book amounts exist only outside the 16 day candidate window.")
    ordinal = entry.day.toordinal()
    nearby = bisect_right(all_book_days, ordinal + FUZZY_WINDOW_DAYS) - bisect_left(all_book_days, ordinal - FUZZY_WINDOW_DAYS)
    if nearby:
        return _reason(entry, "amount_mismatch", "Book rows in the date window do not have this exact signed amount.", nearby)
    return _reason(entry, "no_candidate_in_window", "No book row has this exact signed amount within the candidate date windows.")


def _scan(session):
    bank = _entries((session.get("bank") or {}).get("rows", []))
    book = _entries(rows(session))
    identities = [entry.id for entry in bank + book]
    if len(set(identities)) != len(identities):
        raise DomainError("Source transaction identities must be distinct before proposing matches.")
    accepted_bank = {item["bank_id"] for item in session["matches"]}
    accepted_book = {item["ledger_id"] for item in session["matches"]}
    proposals, reasons, grouped_bank, grouped_book = _groups(session, bank, book, accepted_bank, accepted_book)
    free_bank = _ordered([entry for entry in bank if entry.id not in accepted_bank])
    free_book = [entry for entry in book if entry.id not in accepted_book]
    remaining_bank = [entry for entry in free_bank if entry.id not in grouped_bank and entry.amount]
    book_index = _index([entry for entry in free_book if entry.id not in grouped_book and entry.amount])
    claimed_book = set()
    exact_remainder = []
    memo_scores = {}

    def memo_score(left, right):
        # A bounded, request-local cache avoids repeating identical memo work.
        # No client text survives this scan or enters a shared global cache.
        key = (left.memo, right.memo)
        if key not in memo_scores:
            if len(memo_scores) >= 4096:
                memo_scores.clear()
            memo_scores[key] = _similarity(left, right)
        return memo_scores[key]
    # Keep the ordinary passes stable, newest first; duplicate groups above
    # independently use ascending positional order on both sides.
    for entry in reversed(remaining_bank):
        bucket, start, stop = _bounds(book_index, entry.amount, entry.day, EXACT_WINDOW_DAYS)
        chosen = next((bucket[index] for index in range(stop - 1, start - 1, -1)
                       if bucket[index].id not in claimed_book), None)
        if chosen is None:
            exact_remainder.append(entry)
        else:
            claimed_book.add(chosen.id)
            proposals.append(_proposal(session, entry, chosen, "EXACT"))
    for entry in exact_remainder:
        bucket, start, stop = _bounds(book_index, entry.amount, entry.day, FUZZY_WINDOW_DAYS)
        choices = []
        for index in range(stop - 1, start - 1, -1):
            if bucket[index].id not in claimed_book:
                choices.append(bucket[index])
                if len(choices) > MAX_FUZZY_CANDIDATES:
                    break
        if len(choices) > MAX_FUZZY_CANDIDATES:
            reasons[entry.id] = _reason(entry, "candidate_limit",
                                       "More than 40 eligible book rows share this amount and date window. Review manually.",
                                       None)
            reasons[entry.id]["candidate_count_lower_bound"] = MAX_FUZZY_CANDIDATES + 1
            continue
        if choices:
            similarity, chosen = max(((memo_score(entry, candidate), candidate) for candidate in choices),
                                     key=lambda item: item[0])
            if similarity >= MEMO_SIMILARITY_FLOOR:
                claimed_book.add(chosen.id)
                proposals.append(_proposal(session, entry, chosen, "FUZZY_DATE", similarity))
            else:
                reasons[entry.id] = _reason(entry, "memo_below_floor",
                                           "Exact signed amounts are within 16 days, but memo similarity is below 60%.", len(choices))
    bank_days = {entry.id: entry.day for entry in free_bank}
    proposals.sort(key=lambda proposal: (bank_days[proposal["bank_id"]], proposal["bank_id"]))
    proposed_bank = {proposal["bank_id"] for proposal in proposals}
    proposed_book = {proposal["ledger_id"] for proposal in proposals}
    all_book_index = _index(book)
    accepted_index = _index([entry for entry in book if entry.id in accepted_book])
    all_book_days = sorted(entry.day.toordinal() for entry in book)
    unproposed = []
    for entry in free_bank:
        if entry.id in proposed_bank:
            continue
        if not entry.amount:
            reason = _reason(entry, "zero_amount_excluded", "Zero amounts are excluded from candidate matching.")
        else:
            reason = reasons.get(entry.id) or _unproposed_reason(entry, book, all_book_index, accepted_index, all_book_days)
        unproposed.append(reason)
    counts = {
        "bank_rows": len(bank), "book_rows": len(book), "confirmed_matches": len(session["matches"]),
        "proposed_matches": len(proposals), "unmatched_bank": len(free_bank), "unmatched_books": len(free_book),
        "without_proposal_bank": len(unproposed), "without_proposal_books": len(free_book) - len(proposed_book),
    }
    empty_reason = None
    if not bank and not book:
        empty_reason, message = "no_transactions_either_side", "No bank or book rows are loaded for this account and period."
    elif not book:
        empty_reason, message = "no_book_transactions", "No book rows are loaded for this account and period."
    elif not bank:
        empty_reason, message = "no_bank_transactions", "No bank rows are loaded for this account and period."
    else:
        message = (f"{len(proposals)} candidate pairs require human review; "
                   f"{len(unproposed)} unmatched bank rows have no candidate. Existing confirmations are unchanged.")
    return proposals, {
        "message": message, "empty_reason": empty_reason, "counts": counts,
        "reason_counts": dict(Counter(reason["reason"] for reason in unproposed)),
        "rows": unproposed[:MAX_DIAGNOSTIC_ROWS], "total_rows": len(unproposed),
        "truncated": len(unproposed) > MAX_DIAGNOSTIC_ROWS,
    }


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
    return _scan(session)[0]


def diagnostics(session):
    """Explain the current proposal scan without mutating evidence or review."""
    return _scan(session)[1]


def validate_pair(session, bank_id, ledger_id):
    bank, ledger = unmatched(session)
    bank_row = next((r for r in bank if r["id"] == bank_id), None)
    ledger_row = next((r for r in ledger if r["id"] == ledger_id), None)
    if not bank_row or not ledger_row:
        raise DomainError("Match references must be unmatched rows in the selected account/session.")
    if money(bank_row["amount"]) != money(ledger_row["amount"]):
        raise DomainError("A one-to-one match requires the exact same signed amount.")
    return bank_row, ledger_row
