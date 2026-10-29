"""Backup containment, immutable review history and protected checklist exports."""
from contextlib import contextmanager
from copy import deepcopy
import io

from openpyxl import load_workbook
import pytest

from vesper_bank_rec.assistant.bank_tools import MAX_PLANS, build_bank_tools, validate_checklist
from vesper_bank_rec.imports import preview_csv
from vesper_bank_rec.matching import candidates
from vesper_bank_rec.models import DomainError, digest, now
from vesper_bank_rec.reconciliation import commit_import, complete, new_session, set_anchor, summary
from vesper_bank_rec.reports import backup, csv_bytes, export_rows, validate_backup, xlsx_bytes


def fixture():
    workspace = {"id": "workspace_original", "name": "Synthetic", "currency": "USD", "synthetic": True,
                 "version": 1, "sessions": [], "events": [], "active_session_id": None}
    session = new_session(workspace, {"account_id": "BANK", "account_label": "Checking",
                                    "period_start": "2026-09-01", "period_end": "2026-09-30"})
    for side in ("bank", "ledger"):
        commit_import(workspace, session, preview_csv(session, side, {
            "csv": "date,description,amount,account,currency\n2026-09-12,Software,-75.00,BANK,USD\n",
            "opening": "100.00", "closing": "25.00", "source": "Synthetic source controls",
        }))
    set_anchor(workspace, session, {"kind": "zero_outstanding", "as_of": "2026-08-31",
                                   "evidence": "Synthetic prior tie-out", "outstanding": []})

    class LocalStore:
        @contextmanager
        def edit(self, **kwargs):
            yield workspace

    tools = build_bank_tools(store=LocalStore(), captured={"id": workspace["id"], "session_id": session["id"],
                             "input_version": session["input_version"], "version": workspace["version"]},
                             assert_scope=lambda _: (workspace, session))
    draft = next(tool for tool in tools if tool.name == "draft_reconciliation_plan")
    draft.handler({"title": "=Review the evidence", "goal": "+Keep financial approval with the reviewer",
                   "steps": [{"action": "review_source_controls", "note": "@Inspect independent source controls"}]})
    return workspace, session


def checked_payload(workspace):
    return backup(deepcopy(workspace))


def resign(payload):
    payload["checksum"] = digest({key: value for key, value in payload.items() if key != "checksum"})


def reseal(plan):
    plan["steps_hash"] = digest({key: plan[key] for key in ("title", "goal", "scope", "input_hash", "steps", "execution")})
    plan["id"] = "checklist_" + plan["steps_hash"][:24]


def test_restore_rebinds_checklist_and_retains_exact_completed_result_and_history():
    workspace, session = fixture()
    proposal = candidates(session)[0]
    session["matches"] = [{"id": "match_fixture", "bank_id": proposal["bank_id"], "ledger_id": proposal["ledger_id"],
                           "note": "Synthetic review", "accepted_at": now()}]
    complete(workspace, session, "Synthetic explicit acceptance")
    original = deepcopy(workspace)
    restored = validate_backup(checked_payload(workspace))
    saved = restored["sessions"][0]
    old, new = original["sessions"][0]["assistant_plans"][0], saved["assistant_plans"][0]
    assert restored["id"] != original["id"]
    assert validate_checklist(new) == new
    assert new["scope"]["workspace_id"] == restored["id"]
    assert new["id"] != old["id"] and new["steps_hash"] != old["steps_hash"]
    assert new["steps"] == old["steps"] and new["input_hash"] == old["input_hash"]
    assert new["status"] == "awaiting_human_review" and new["execution"] == "manual_review_only"
    assert saved["completion"] == session["completion"]
    assert digest(summary(saved)) == session["completion"]["result_hash"]
    assert saved["history"][:-1] == original["sessions"][0]["history"]
    receipt = saved["history"][-1]
    assert receipt["action"] == "assistant_checklists_restored"
    mapping = receipt["detail"]["checklists"][0]
    assert mapping["old_id"] == old["id"] and mapping["old_scope"] == old["scope"]
    assert mapping["old_steps_hash"] == old["steps_hash"] and mapping["new_steps_hash"] == new["steps_hash"]
    assert mapping["new_id"] == new["id"]
    assert workspace == original  # restore operates on the supplied backup, not the live workspace


@pytest.mark.parametrize("field,value", [("workspace_id", "foreign"), ("session_id", "foreign"),
    ("account_id", "foreign"), ("period_start", "2026-08-01"), ("period_end", "2026-10-01"), ("currency", "GBP")])
def test_backup_rejects_foreign_checklist_even_with_recomputed_seals(field, value):
    workspace, _ = fixture()
    payload = checked_payload(workspace)
    plan = payload["workspace"]["sessions"][0]["assistant_plans"][0]
    plan["scope"][field] = value
    reseal(plan)
    resign(payload)
    with pytest.raises(DomainError, match="checklist"):
        validate_backup(payload)
    assert workspace["id"] == "workspace_original"


@pytest.mark.parametrize("mutation", ["seal", "execution", "action", "duplicate", "oversized", "not_list", "future"])
def test_backup_rejects_invalid_checklist_schema_authority_identity_or_version(mutation):
    workspace, _ = fixture()
    payload = checked_payload(workspace)
    session = payload["workspace"]["sessions"][0]
    plan = session["assistant_plans"][0]
    if mutation == "seal":
        plan["steps"][0]["note"] = "Changed after drafting"
    elif mutation == "execution":
        plan["execution"] = "automatic"
        reseal(plan)
    elif mutation == "action":
        plan["steps"][0]["action"] = "approve_matches"
        reseal(plan)
    elif mutation == "duplicate":
        session["assistant_plans"].append(deepcopy(plan))
    elif mutation == "oversized":
        session["assistant_plans"] = [deepcopy(plan) for _ in range(MAX_PLANS + 1)]
    elif mutation == "not_list":
        session["assistant_plans"] = None
    else:
        plan["scope"]["input_version"] = session["input_version"] + 1
        reseal(plan)
    resign(payload)
    with pytest.raises(DomainError, match="checklist"):
        validate_backup(payload)
    assert workspace["id"] == "workspace_original"


def test_older_checklist_is_preserved_as_stale_and_exports_remain_protected_text():
    workspace, session = fixture()
    session["input_version"] += 1
    original = deepcopy(session["assistant_plans"])
    rows = export_rows(workspace, session)
    assert any(row[0] == "assistant_checklist_status" and row[2] == "stale" for row in rows)
    assert any(row[0] == "assistant_checklist_step" and row[2].startswith("@") for row in rows)
    assert session["assistant_plans"] == original
    output = csv_bytes(workspace, session).decode("utf-8-sig")
    assert "'=Review the evidence" in output and "'@Inspect independent source controls" in output
    sheet = load_workbook(io.BytesIO(xlsx_bytes(workspace, session))).active
    assert all(cell.data_type != "f" for row in sheet for cell in row)
    for row in sheet:
        if str(row[0].value).startswith("assistant_checklist"):
            assert row[3].value is None
    restored = validate_backup(checked_payload(workspace))
    assert restored["sessions"][0]["assistant_plans"][0]["scope"]["input_version"] < session["input_version"]


def test_optional_absent_or_empty_checklists_preserve_existing_export_rows():
    workspace, session = fixture()
    session.pop("assistant_plans")
    baseline = export_rows(workspace, session)
    session["assistant_plans"] = []
    assert export_rows(workspace, session) == baseline
    restored = validate_backup(checked_payload(workspace))
    assert not any(entry["action"] == "assistant_checklists_restored" for entry in restored["sessions"][0]["history"])


@pytest.mark.parametrize("mutation", ["duplicate", "not_list", "not_object", "blank_id", "long_id", "bad_version", "future", "bad_reason", "foreign_pair", "already_matched"])
def test_backup_rejects_invalid_or_ambiguous_match_proposals_without_live_write(mutation):
    workspace, session = fixture()
    session["drafts"] = candidates(session)
    original = deepcopy(workspace)
    payload = checked_payload(workspace)
    target = payload["workspace"]["sessions"][0]
    draft = target["drafts"][0]
    if mutation == "duplicate":
        target["drafts"].append(deepcopy(draft))
    elif mutation == "not_list":
        target["drafts"] = None
    elif mutation == "not_object":
        target["drafts"] = ["bad"]
    elif mutation == "blank_id":
        draft["id"] = " "
    elif mutation == "long_id":
        draft["id"] = "x" * 181
    elif mutation == "bad_version":
        draft["input_version"] = True
    elif mutation == "future":
        draft["input_version"] = target["input_version"] + 1
    elif mutation == "bad_reason":
        draft["reason"] = "x" * 2001
    elif mutation == "foreign_pair":
        draft["bank_id"] = "foreign"
    else:
        target["matches"] = [{"id": "already_matched", "bank_id": draft["bank_id"], "ledger_id": draft["ledger_id"],
                              "note": "Reviewed", "accepted_at": now()}]
    resign(payload)
    with pytest.raises(DomainError):
        validate_backup(payload)
    assert workspace == original


def test_old_proposal_version_is_preserved_but_cannot_exceed_current_version():
    workspace, session = fixture()
    session["drafts"] = candidates(session)
    session["input_version"] += 1
    restored = validate_backup(checked_payload(workspace))
    assert restored["sessions"][0]["drafts"][0]["input_version"] == session["input_version"] - 1
