from copy import deepcopy
import pytest
from vesper_bank_rec.models import DomainError, money, uid, now, digest
from vesper_bank_rec.imports import preview_csv
from vesper_bank_rec.reconciliation import new_session, set_anchor, commit_import, summary, complete, reopen
from vesper_bank_rec.matching import candidates, validate_pair
from vesper_bank_rec.reports import backup, validate_backup, csv_bytes, xlsx_bytes, html_report
from vesper_bank_rec.store import Store

HEADER = "date,description,amount,account,currency\n"


def workspace():
    return {
        "id": uid("workspace"),
        "name": "Synthetic client",
        "currency": "USD",
        "synthetic": True,
        "version": 1,
        "created_at": now(),
        "active_session_id": None,
        "sessions": [],
        "events": [],
    }


def period(w, month="09", account="BANK"):
    end = "30" if month == "09" else "31"
    return new_session(
        w,
        {
            "account_id": account,
            "account_label": "Checking " + account,
            "period_start": f"2026-{month}-01",
            "period_end": f"2026-{month}-{end}",
        },
    )


def load(w, s, side, text, opening, closing):
    p = preview_csv(
        s,
        side,
        {
            "csv": text,
            "opening": opening,
            "closing": closing,
            "source": "Synthetic independent source controls",
        },
    )
    commit_import(w, s, p)
    return p


def anchor(w, s):
    set_anchor(
        w,
        s,
        {
            "kind": "zero_outstanding",
            "evidence": "Synthetic prior independent reconciliation has no outstanding items.",
            "as_of": "2026-08-31",
            "outstanding": [],
        },
    )


def confirm_all(s):
    for p in candidates(s):
        validate_pair(s, p["bank_id"], p["ledger_id"])
        s["matches"].append(
            {
                "id": uid("match"),
                "bank_id": p["bank_id"],
                "ledger_id": p["ledger_id"],
                "accepted_at": now(),
                "note": "Reviewed",
            }
        )


@pytest.mark.parametrize("bad", ["NaN", "Infinity", "1.001", "1e2", 1.1, True, "$2.00", "1,000.00", "-0.001"])
def test_money_rejects_noncanonical(bad):
    with pytest.raises(DomainError):
        money(bad)


def test_duplicate_occurrences_remain_distinct_and_first_use_blocks():
    w = workspace()
    s = period(w)
    text = HEADER + "2026-09-12,Software,-75.00,BANK,USD\n" * 2
    bank = load(w, s, "bank", text, "1000.00", "850.00")
    load(w, s, "ledger", text, "1000.00", "850.00")
    assert len(bank["rows"]) == 2 and bank["rows"][0]["id"] != bank["rows"][1]["id"]
    assert bank["rows"][0]["ordinal"] == 1 and bank["rows"][1]["ordinal"] == 2
    confirm_all(s)
    assert summary(s)["difference"] == "0.00"
    assert not summary(s)["ready_to_complete"]
    with pytest.raises(DomainError, match="Completion blocked"):
        complete(w, s, "Reviewed")
    anchor(w, s)
    assert summary(s)["ready_to_complete"]
    complete(w, s, "Human accepted synthetic reconciliation")
    assert s["status"] == "completed" and len(s["matches"]) == 2
    with pytest.raises(DomainError):
        load(w, s, "bank", text, "1000.00", "850.00")
    reopen(w, s, "Rechecking evidence")
    assert s["anchor"]["kind"] == "zero_outstanding"
    assert any(e["action"] == "reopened" for e in s["history"])


@pytest.mark.parametrize(
    "row,closing,expected",
    [
        ("2026-09-01,Bad,1.001,BANK,USD\n", "1000.00", "two decimal"),
        ("2026-08-01,Bad,1.00,BANK,USD\n", "1000.00", "outside"),
        ("2026-09-01,Bad,1.00,OTHER,USD\n", "1000.00", "Account"),
        ("2026-09-01,Bad,1.00,BANK,EUR\n", "1000.00", "Currency"),
        ("2026-09-01,Bad,1.00,BANK,USD\n", "1000.00", "differs"),
        ("2026-09-01,Bad,0.00,BANK,USD\n", "1000.00", "Zero-value"),
    ],
)
def test_import_failure_never_certifies(row, closing, expected):
    w = workspace()
    s = period(w)
    p = preview_csv(
        s,
        "bank",
        {"csv": HEADER + row, "opening": "1000.00", "closing": closing, "source": "Synthetic source"},
    )
    assert not p["ready"] and any(expected in e["message"] for e in p["errors"])
    with pytest.raises(DomainError):
        commit_import(w, s, p)
    assert s["bank"] is None


def test_conflicting_source_ids_reject_but_identical_occurrences_preserved():
    w = workspace()
    s = period(w)
    p = preview_csv(
        s,
        "bank",
        {
            "csv": HEADER.rstrip()
            + ",external_id\n2026-09-01,A,1.00,BANK,USD,s1\n2026-09-01,B,2.00,BANK,USD,s1\n",
            "opening": "0.00",
            "closing": "3.00",
            "source": "Synthetic",
        },
    )
    assert not p["ready"] and any("conflicting" in e["message"] for e in p["errors"])


def test_two_period_outstanding_clears_once_and_never_reappears():
    w = workspace()
    s = period(w)
    load(w, s, "bank", HEADER, "1000.00", "1000.00")
    load(w, s, "ledger", HEADER + "2026-09-29,Check 1042,-100.00,BANK,USD\n", "1000.00", "900.00")
    anchor(w, s)
    row = s["ledger"]["rows"][0]
    s["classifications"][row["id"]] = {
        "kind": "outstanding",
        "evidence": "Synthetic issued-check register",
        "reviewed_at": now(),
    }
    assert summary(s)["outstanding_withdrawals"] == "100.00"
    assert summary(s)["adjusted_bank"] == "900.00"
    complete(w, s, "Accepted September")
    october = period(w, "10")
    assert october["carry"][0]["id"] == row["id"]
    load(w, october, "bank", HEADER + "2026-10-02,Check 1042,-100.00,BANK,USD\n", "1000.00", "900.00")
    load(w, october, "ledger", HEADER, "900.00", "900.00")
    assert not summary(october)["ready_to_complete"]
    proposals = candidates(october)
    assert len(proposals) == 1
    confirm_all(october)
    with pytest.raises(DomainError):
        validate_pair(october, proposals[0]["bank_id"], proposals[0]["ledger_id"])
    assert summary(october)["outstanding"] == []
    assert summary(october)["difference"] == "0.00"
    complete(w, october, "Accepted October")
    november = new_session(
        w, {"account_id": "BANK", "period_start": "2026-11-01", "period_end": "2026-11-30"}
    )
    assert november["carry"] == []
    with pytest.raises(DomainError, match="depends"):
        reopen(w, s, "Attempt historical reset")


def test_pending_book_adjustment_blocks_even_when_adjusted_balances_tie():
    w = workspace()
    s = period(w)
    load(w, s, "bank", HEADER + "2026-09-10,Bank fee,-10.00,BANK,USD\n", "1000.00", "990.00")
    load(w, s, "ledger", HEADER, "1000.00", "1000.00")
    anchor(w, s)
    row = s["bank"]["rows"][0]
    s["classifications"][row["id"]] = {
        "kind": "bank_fee",
        "evidence": "Synthetic bank fee schedule",
        "reviewed_at": now(),
    }
    r = summary(s)
    assert (
        r["difference"] == "0.00" and r["pending_book_adjustments"] == "-10.00" and not r["ready_to_complete"]
    )
    with pytest.raises(DomainError, match="pending"):
        complete(w, s, "Accept anyway")
    load(w, s, "ledger", HEADER + "2026-09-10,Bank fee,-10.00,BANK,USD\n", "1000.00", "990.00")
    confirm_all(s)
    assert summary(s)["ready_to_complete"]


def test_prior_opening_difference_requires_itemized_evidence():
    w = workspace()
    s = period(w)
    load(w, s, "bank", HEADER, "1000.00", "1000.00")
    load(w, s, "ledger", HEADER, "900.00", "900.00")
    with pytest.raises(DomainError, match="opening balances"):
        anchor(w, s)
    set_anchor(
        w,
        s,
        {
            "kind": "prior_reconciled",
            "as_of": "2026-08-31",
            "evidence": "Synthetic approved prior reconciliation",
            "outstanding": [
                {
                    "date": "2026-08-28",
                    "description": "Prior check",
                    "amount": "-100.00",
                    "evidence": "Synthetic prior check schedule",
                }
            ],
        },
    )
    assert summary(s)["ready_to_complete"]


def test_stale_import_and_foreign_pair_rejected():
    w = workspace()
    s = period(w)
    p = preview_csv(
        s, "bank", {"csv": HEADER, "opening": "1000.00", "closing": "1000.00", "source": "Synthetic"}
    )
    load(w, s, "ledger", HEADER, "1000.00", "1000.00")
    with pytest.raises(DomainError, match="stale"):
        commit_import(w, s, p)
    with pytest.raises(DomainError, match="selected"):
        validate_pair(s, "foreign", "invented")


def test_exports_backup_and_formula_neutralization():
    import io
    from openpyxl import load_workbook

    w = workspace()
    s = period(w)
    text = HEADER + '2026-09-01,=HYPERLINK(""evil""),-100.00,BANK,USD\n'
    # Use a CSV-safe hostile memo with a valid decimal amount.
    text = HEADER + "2026-09-01,=1+1,-100.00,BANK,USD\n"
    load(w, s, "bank", text, "1000.00", "900.00")
    load(w, s, "ledger", text, "1000.00", "900.00")
    anchor(w, s)
    confirm_all(s)
    complete(w, s, "Accepted")
    output = csv_bytes(w, s).decode("utf-8-sig")
    assert "'=1+1" in output and "-100.00" in output
    wb = load_workbook(io.BytesIO(xlsx_bytes(w, s)))
    assert any(cell.value == "'=1+1" for row in wb.active for cell in row)
    assert all(cell.data_type != "f" for row in wb.active for cell in row)
    assert s["id"] in html_report(w, s)
    restored = validate_backup(backup(deepcopy(w)))
    assert restored["id"] != w["id"] and restored["sessions"][0]["ledger"]["closing"] == "900.00"
    broken = backup(deepcopy(w))
    broken["workspace"]["sessions"][0]["ledger"]["closing"] = "999.00"
    with pytest.raises(DomainError, match="checksum"):
        validate_backup(broken)


def test_store_expected_scope_is_checked_inside_write_lock(tmp_path):
    store = Store(tmp_path)
    first = store.create("First")
    s = period(first)
    store.save(first)
    captured = {"id": first["id"], "session_id": s["id"], "input_version": s["input_version"]}
    store.create("Second")
    with pytest.raises(DomainError, match="workspace changed"):
        with store.edit(
            expected_workspace_id=captured["id"],
            expected_session_id=captured["session_id"],
            expected_input_version=captured["input_version"],
        ) as w:
            w["name"] = "Cross-scope change"
    assert store.active()["name"] == "Second"
    store.select(first["id"])
    with store.edit() as w:
        w["sessions"][0]["input_version"] += 1
    with pytest.raises(DomainError, match="input version"):
        with store.edit(
            expected_workspace_id=captured["id"],
            expected_session_id=captured["session_id"],
            expected_input_version=captured["input_version"],
        ):
            pass


def test_blank_transaction_record_is_visible_and_blocks_import():
    w = workspace()
    s = period(w)
    p = preview_csv(
        s,
        "bank",
        {
            "csv": HEADER + "\n2026-09-01,A,1.00,BANK,USD\n",
            "opening": "0.00",
            "closing": "1.00",
            "source": "Synthetic",
        },
    )
    assert p["total_rows"] == 2 and not p["ready"] and p["errors"][0]["ordinal"] == 1


def test_large_duplicate_group_preserves_every_one_to_one_candidate():
    w = workspace()
    s = period(w)
    text = HEADER + "2026-09-12,Software,-1.00,BANK,USD\n" * 100
    load(w, s, "bank", text, "1000.00", "900.00")
    load(w, s, "ledger", text, "1000.00", "900.00")
    proposals = candidates(s)
    assert len(proposals) == 100
    assert len({p["bank_id"] for p in proposals}) == 100 and len({p["ledger_id"] for p in proposals}) == 100


def test_backup_rejects_forged_carry_scope_even_with_recomputed_checksum():
    w = workspace()
    s = period(w)
    load(w, s, "bank", HEADER, "1000.00", "1000.00")
    load(w, s, "ledger", HEADER, "900.00", "900.00")
    set_anchor(
        w,
        s,
        {
            "kind": "prior_reconciled",
            "as_of": "2026-08-31",
            "evidence": "Synthetic prior reconciliation",
            "outstanding": [
                {
                    "date": "2026-08-28",
                    "description": "Prior check",
                    "amount": "-100.00",
                    "evidence": "Synthetic schedule",
                }
            ],
        },
    )
    payload = backup(deepcopy(w))
    payload["workspace"]["sessions"][0]["carry"][0]["account_id"] = "FOREIGN"
    payload["checksum"] = digest({key: value for key, value in payload.items() if key != "checksum"})
    with pytest.raises(DomainError, match="different account"):
        validate_backup(payload)
