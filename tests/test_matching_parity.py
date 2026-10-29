"""Synthetic bank-only proposal rules, occurrence identity and review boundaries."""
from copy import deepcopy
from datetime import date, timedelta

import pytest

from vesper_bank_rec import matching
from vesper_bank_rec.models import DomainError, digest


def row(identifier, offset=0, description="Synthetic merchant", amount="-75.00"):
    return {"id": identifier, "date": (date(2026, 9, 1) + timedelta(days=offset)).isoformat(),
            "description": description, "amount": amount}


def session(bank=(), books=(), *, matches=(), carry=()):
    return {"id": "session_synthetic", "input_version": 4,
            "bank": {"rows": list(bank)}, "ledger": {"rows": list(books)}, "carry": list(carry),
            "matches": list(matches), "drafts": [], "status": "review",
            "history": [{"action": "synthetic_review_record", "note": "Preserve this history"}]}


def pairs(selected):
    return [(proposal["bank_id"], proposal["ledger_id"]) for proposal in matching.candidates(selected)]


def reason(selected):
    result = matching.diagnostics(selected)
    assert result["total_rows"] == 1
    return result["rows"][0]


@pytest.mark.parametrize("distance", [-4, 0, 4])
def test_exact_window_includes_four_days_without_a_memo_requirement(distance):
    selected = session([row("bank", 8, "ABCDE")], [row("book", 8 + distance, "VWXYZ")])
    proposal, = matching.candidates(selected)
    assert proposal["match_type"] == "EXACT" and proposal["score"] == 1
    assert proposal["date_distance"] == abs(distance)


@pytest.mark.parametrize("distance", [-16, -5, 5, 16])
def test_fuzzy_window_is_distinct_and_normalizes_complete_memos(distance):
    selected = session([row("bank", 20, "  Synthetic  MERCHANT\t")],
                       [row("book", 20 + distance, "synthetic merchant")])
    proposal, = matching.candidates(selected)
    assert proposal["match_type"] == "FUZZY_DATE" and proposal["score"] == 1
    assert proposal["date_distance"] == abs(distance)


def test_similarity_floor_is_inclusive_and_applies_before_rounding():
    selected = session([row("bank", description="abcde")], [row("book", 5, "abcxy")])
    proposal, = matching.candidates(selected)
    assert proposal["score"] == 0.6 and proposal["match_type"] == "FUZZY_DATE"
    selected["ledger"]["rows"][0]["description"] = "abxyz"
    assert pairs(selected) == []
    assert reason(selected)["reason"] == "memo_below_floor"


@pytest.mark.parametrize("bank_memo,book_memo", [("", ""), (" \t", " "), ("merchant", "")])
def test_empty_memos_cannot_justify_a_fuzzy_pair(bank_memo, book_memo):
    selected = session([row("bank", description=bank_memo)], [row("book", 5, book_memo)])
    assert pairs(selected) == []
    assert reason(selected)["reason"] == "memo_below_floor"


def test_full_description_can_distinguish_fuzzy_candidates_after_the_old_prefix_limit():
    shared = "a" * 300
    selected = session([row("bank", description=shared + "b" * 1700)],
                       [row("book", 5, shared + "c" * 1700)])
    assert pairs(selected) == []
    assert reason(selected)["reason"] == "memo_below_floor"


@pytest.mark.parametrize("distance", [17, 90, -17])
def test_distant_identical_memos_do_not_generate_candidates(distance):
    selected = session([row("bank")], [row("book", distance)])
    assert pairs(selected) == []
    assert reason(selected)["reason"] == "outside_window"
    # A separate explicit review may still confirm a genuine older item.
    assert matching.validate_pair(selected, "bank", "book") == (
        selected["bank"]["rows"][0], selected["ledger"]["rows"][0])


@pytest.mark.parametrize("book_amount,expected", [("75.00", "sign_mismatch"), ("-75.01", "amount_mismatch")])
def test_exact_signed_money_is_never_relaxed(book_amount, expected):
    selected = session([row("bank")], [row("book", amount=book_amount)])
    assert pairs(selected) == []
    assert reason(selected)["reason"] == expected
    with pytest.raises(DomainError, match="exact same signed amount"):
        matching.validate_pair(selected, "bank", "book")


def test_decimal_buckets_use_exact_value_and_keep_opposite_directions_separate():
    selected = session([row("debit"), row("deposit", amount="75.00")],
                       [row("book", amount="-75.0")])
    assert pairs(selected) == [("debit", "book")]
    assert reason(selected)["bank_id"] == "deposit"
    assert reason(selected)["reason"] == "sign_mismatch"


def test_zero_rows_are_not_suggested():
    selected = session([row("bank", amount="0.00")], [row("book", amount="0.00")])
    assert pairs(selected) == []
    assert reason(selected)["reason"] == "zero_amount_excluded"


def test_duplicate_groups_pair_positionally_instead_of_cross_wiring_memos():
    selected = session([row("bank1", 1, "Alpha"), row("bank2", 2, "Beta")],
                       [row("book1", 1, "Beta"), row("book2", 2, "Alpha")])
    assert pairs(selected) == [("bank1", "book1"), ("bank2", "book2")]
    assert {proposal["match_type"] for proposal in matching.candidates(selected)} == {"EXACT_GROUP"}
    assert matching.diagnostics(selected)["reason_counts"] == {}


def test_same_day_duplicates_use_stable_id_order_and_input_reordering_does_not_change_them():
    selected = session([row("bank2"), row("bank1")], [row("book2"), row("book1")])
    original = matching.candidates(selected)
    assert pairs(selected) == [("bank1", "book1"), ("bank2", "book2")]
    selected["bank"]["rows"].reverse()
    selected["ledger"]["rows"].reverse()
    assert matching.candidates(selected) == original


def test_transitive_cluster_does_not_expand_the_four_day_pair_bound_or_fuzzy_reuse_remainder():
    selected = session([row("bank0", 0), row("bank4", 4), row("bank8", 8)],
                       [row("book0", 0), row("book12", 12), row("book20", 20)])
    assert pairs(selected) == [("bank0", "book0"), ("bank8", "book12")]
    assert all(proposal["date_distance"] <= 4 for proposal in matching.candidates(selected))
    assert reason(selected)["bank_id"] == "bank4"
    assert reason(selected)["reason"] == "duplicate_group_remainder"


def test_book_group_remainders_cannot_be_fuzzy_cross_wired_to_a_later_bank_row():
    selected = session([row("bank0", 0), row("bank16", 16)],
                       [row("book0", 0), row("book4", 4), row("book8", 8)])
    assert pairs(selected) == [("bank0", "book0")]
    assert reason(selected)["reason"] == "candidate_claimed"
    assert matching.diagnostics(selected)["counts"]["without_proposal_books"] == 2


def test_confirmed_twin_still_defines_duplicate_group_on_recomputation():
    accepted = {"id": "saved_match", "bank_id": "bank0", "ledger_id": "book0",
                "accepted_at": "2026-09-02T00:00:00Z", "note": "Existing synthetic reviewer note"}
    selected = session([row("bank0", 0), row("bank1", 1)], [row("book0", 0), row("book9", 9)],
                       matches=[accepted])
    before = deepcopy(selected)
    assert pairs(selected) == []
    assert reason(selected)["reason"] == "duplicate_group_remainder"
    assert selected == before


def test_accepted_far_date_history_is_reserved_without_reinterpretation():
    accepted = {"id": "saved_match", "bank_id": "bank0", "ledger_id": "book50",
                "accepted_at": "2026-10-21T00:00:00Z", "note": "Existing explicit synthetic review"}
    selected = session([row("bank0", 0), row("bank1", 1)], [row("book50", 50), row("book1", 1)],
                       matches=[accepted])
    before = deepcopy(selected)
    assert pairs(selected) == [("bank1", "book1")]
    matching.diagnostics(selected)
    assert selected == before
    with pytest.raises(DomainError, match="unmatched rows"):
        matching.validate_pair(selected, "bank0", "book50")


def test_confirming_one_proposal_does_not_reassign_the_other_duplicate_identity():
    selected = session([row("bank1", 1), row("bank2", 2)], [row("book1", 1), row("book2", 2)])
    original = matching.candidates(selected)
    first, second = original
    selected["matches"].append({"id": "saved", "bank_id": first["bank_id"], "ledger_id": first["ledger_id"],
                                "note": "Synthetic pair reviewed"})
    assert matching.candidates(selected) == [second]


def test_exact_pass_has_priority_over_a_fuzzy_competitor():
    selected = session([row("fuzzy", 0, "Exact memo"), row("exact", 8, "Different memo")],
                       [row("book", 8, "Exact memo")])
    assert pairs(selected) == [("exact", "book")]
    assert matching.candidates(selected)[0]["match_type"] == "EXACT"
    assert reason(selected)["reason"] == "candidate_claimed"


def test_fuzzy_pass_uses_best_memo_and_newest_stable_tie_order():
    selected = session([row("bank", description="abcdef")],
                       [row("older", 5, "abcdef"), row("newer", 6, "abcdef"), row("poor", 7, "zzzzzz")])
    assert pairs(selected) == [("bank", "newer")]


def test_fuzzy_comparison_limit_refuses_instead_of_silently_truncating(monkeypatch):
    selected = session([row("bank")], [row(f"book{index:03}", 8) for index in range(41)])
    monkeypatch.setattr(matching, "_similarity", lambda *args: pytest.fail("Limit must be checked before memo comparisons"))
    assert pairs(selected) == []
    explanation = reason(selected)
    assert explanation["reason"] == "candidate_limit"
    assert explanation["candidate_count"] is None and explanation["candidate_count_lower_bound"] == 41


def test_all_forty_eligible_fuzzy_rows_are_compared():
    selected = session([row("bank", description="unique merchant")],
                       [row(f"book{index:03}", 8, "unrelated") for index in range(40)])
    selected["ledger"]["rows"][0]["description"] = "unique merchant"
    assert pairs(selected) == [("bank", "book000")]


def test_ten_thousand_duplicate_occurrences_remain_distinct():
    selected = session([row(f"bank{index:05}") for index in range(10_000)],
                       [row(f"book{index:05}") for index in range(10_000)])
    proposals = matching.candidates(selected)
    assert len(proposals) == len({proposal["bank_id"] for proposal in proposals}) == 10_000
    assert len({proposal["ledger_id"] for proposal in proposals}) == 10_000
    assert proposals[0]["ledger_id"] == "book00000" and proposals[-1]["ledger_id"] == "book09999"


@pytest.mark.parametrize("banks,books", [
    ([row("bank"), row("bank")], [row("book1"), row("book2")]),
    ([row("bank1"), row("bank2")], [row("book"), row("book")]),
    ([row("same_id")], [row("same_id")]),
])
def test_repeated_source_identity_is_refused_without_losing_occurrences(banks, books):
    selected = session(banks, books)
    before = deepcopy(selected)
    with pytest.raises(DomainError, match="identities must be distinct"):
        matching.candidates(selected)
    assert selected == before


def test_repeated_memo_work_is_cached_only_within_one_scan(monkeypatch):
    selected = session([row(f"bank{index:03}", description="aaaaa") for index in range(300)],
                       [row(f"book{index:03}", 8, "zzzzz") for index in range(40)])
    original = matching._similarity
    calls = []

    def observed(*args):
        calls.append(1)
        return original(*args)

    monkeypatch.setattr(matching, "_similarity", observed)
    assert pairs(selected) == [] and len(calls) == 1
    assert pairs(selected) == [] and len(calls) == 2


def test_diagnostics_bound_rows_but_keep_complete_counts():
    selected = session([row(f"bank{index:03}", amount=f"-{index + 1}.00") for index in range(200)])
    result = matching.diagnostics(selected)
    assert result["empty_reason"] == "no_book_transactions"
    assert result["counts"]["bank_rows"] == result["counts"]["unmatched_bank"] == 200
    assert result["total_rows"] == result["reason_counts"]["no_book_transactions"] == 200
    assert len(result["rows"]) == 150 and result["truncated"] is True


@pytest.mark.parametrize("banks,books,expected", [
    ([], [], "no_transactions_either_side"), ([], [row("book")], "no_bank_transactions"),
])
def test_empty_side_explanations_are_explicit(banks, books, expected):
    assert matching.diagnostics(session(banks, books))["empty_reason"] == expected


def test_fully_confirmed_sources_are_not_described_as_missing_books():
    selected = session([row("bank")], [row("book")],
                       matches=[{"bank_id": "bank", "ledger_id": "book", "note": "Existing review"}])
    selected["status"] = "completed"
    before = deepcopy(selected)
    result = matching.diagnostics(selected)
    assert result["empty_reason"] is None
    assert result["counts"]["book_rows"] == result["counts"]["confirmed_matches"] == 1
    assert result["counts"]["unmatched_books"] == result["counts"]["proposed_matches"] == 0
    assert selected == before


def test_proposals_keep_legacy_identity_shape_and_never_become_confirmations():
    selected = session([row("bank")], [row("book")])
    before = deepcopy(selected)
    first = matching.candidates(selected)
    assert matching.candidates(selected) == first
    assert first[0]["id"] == "proposal_" + digest([selected["id"], 4, "bank", "book"])[:24]
    assert matching.diagnostics(selected)["counts"] == {
        "bank_rows": 1, "book_rows": 1, "confirmed_matches": 0, "proposed_matches": 1,
        "unmatched_bank": 1, "unmatched_books": 1, "without_proposal_bank": 0, "without_proposal_books": 0,
    }
    assert matching.unmatched(selected) == (selected["bank"]["rows"], selected["ledger"]["rows"])
    assert selected == before
    with pytest.raises(DomainError, match="selected account/session"):
        matching.validate_pair(selected, "foreign_bank", "foreign_book")


def test_carried_outstanding_row_keeps_its_identity():
    selected = session([row("bank", 1)], carry=[row("prior_check", -2)])
    assert pairs(selected) == [("bank", "prior_check")]


def test_candidates_do_not_bypass_source_anchor_or_completion_review():
    from vesper_bank_rec.controls import source_controls
    from vesper_bank_rec.imports import preview_csv
    from vesper_bank_rec.reconciliation import commit_import, complete, new_session, set_anchor, summary

    workspace = {"id": "workspace_synthetic", "name": "Synthetic client", "currency": "USD",
                 "sessions": [], "active_session_id": None, "events": []}
    selected = new_session(workspace, {"account_id": "BANK", "period_start": "2026-09-01",
                                       "period_end": "2026-09-30"})
    text = "date,description,amount,account,currency\n2026-09-12,Software,-75.00,BANK,USD\n"
    for side in ("bank", "ledger"):
        preview = preview_csv(selected, side, {"csv": text, "opening": "1000.00", "closing": "925.00",
                                               "source": "Synthetic independent source controls"})
        commit_import(workspace, selected, preview)
    assert source_controls(selected, "bank")["ready"] and source_controls(selected, "ledger")["ready"]
    assert len(matching.candidates(selected)) == 1
    assert not summary(selected)["ready_to_complete"]
    set_anchor(workspace, selected, {"kind": "zero_outstanding", "as_of": "2026-08-31", "outstanding": [],
                                     "evidence": "Reviewed synthetic prior opening with no outstanding items"})
    before = deepcopy(workspace)
    assert len(matching.candidates(selected)) == 1
    matching.diagnostics(selected)
    assert workspace == before and selected["matches"] == []
    assert not summary(selected)["ready_to_complete"]
    with pytest.raises(DomainError, match="Completion blocked"):
        complete(workspace, selected, "Synthetic review cannot replace matching")
