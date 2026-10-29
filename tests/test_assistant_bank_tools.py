from copy import deepcopy
import json

import pytest

from vesper_bank_rec.assistant import AssistantError, run_assistant
from vesper_bank_rec.assistant import bank_tools
from vesper_bank_rec.assistant.bank_tools import build_bank_tools, checklist_views, rebind_checklist, validate_checklist
from vesper_bank_rec.imports import preview_csv
from vesper_bank_rec.matching import candidates, diagnostics
from vesper_bank_rec.models import DomainError, amount
from vesper_bank_rec.reconciliation import commit_import, complete, current, event, new_session, set_anchor, summary
from vesper_bank_rec.store import Store


def populate(store, count=85, *, description="Software", external=None):
    store.create("Synthetic assistant tools", synthetic=True)
    with store.edit() as workspace:
        session = new_session(workspace, {"account_id": "BANK", "account_label": "Synthetic checking",
                              "period_start": "2026-09-01", "period_end": "2026-09-30"})
        # Identical occurrences intentionally retain independent ordinal/row IDs.
        header = "date,description,amount,account,currency" + (",external_id" if external is not None else "") + "\n"
        row = f"2026-09-12,{description},-75.00,BANK,USD" + ("," + external if external is not None else "") + "\n"
        csv = header + row * count
        closing = amount(10000 - 75 * count)
        for side in ("bank", "ledger"):
            commit_import(workspace, session, preview_csv(session, side, {
                "csv": csv, "opening": "10000.00", "closing": closing, "source": "Synthetic independent source",
            }))
        set_anchor(workspace, session, {"kind": "zero_outstanding", "as_of": "2026-08-31",
                                      "outstanding": [], "evidence": "Synthetic reviewed opening evidence"})


def tools_for(store):
    workspace = store.active()
    session = current(workspace)
    captured = {"id": workspace["id"], "session_id": session["id"],
                "input_version": session["input_version"], "version": workspace["version"]}

    def assert_scope(expected):
        selected = store.active()
        active = current(selected)
        actual = {"id": selected["id"], "session_id": active["id"],
                  "input_version": active["input_version"], "version": selected["version"]}
        if actual != expected:
            raise DomainError("The selected workspace or inputs changed.")
        return selected, active

    return {tool.name: tool for tool in build_bank_tools(store=store, captured=captured, assert_scope=assert_scope)}


@pytest.fixture
def store(tmp_path):
    result = Store(tmp_path)
    populate(result)
    return result


def test_registry_keeps_four_read_rehearsals_and_no_financial_authority(store):
    tools = tools_for(store)
    assert len(tools) == 11
    assert {name for name, tool in tools.items() if tool.rehearsal_args is not None} == {
        "reconciliation_summary", "source_controls", "unmatched_records", "match_candidates",
    }
    before = store.active()
    result = run_assistant(message="Explain current evidence", tools=list(tools.values()), rehearsal=True)
    assert result["usage"] == {"provider_calls": 0, "tool_calls": 4}
    assert len({record["id"] for record in result["evidence"]}) == 4
    assert store.active() == before
    assert all(not any(word in name for word in ("confirm", "approve", "complete", "execute", "post_")) for name in tools)


def test_tieout_copies_exact_deterministic_totals_and_retains_completion_blockers(store):
    expected = summary(current(store.active()))
    result = tools_for(store)["get_recon_tieout"].handler({})
    for field in ("adjusted_bank", "adjusted_books", "difference", "outstanding_withdrawals", "pending_book_adjustments"):
        assert result[field] == expected[field] and isinstance(result[field], str)
    assert result["blockers"] == expected["blockers"]
    assert result["completion_authority"] == "human_review_only" and result["ready_to_complete"] is False
    assert result["unclassified_count"] == 170 and "unclassified" not in result
    assert tools_for(store)["reconciliation_summary"].handler({}) == expected


@pytest.mark.parametrize("side", ["bank", "book"])
def test_unmatched_cursor_pages_preserve_duplicate_occurrences_without_skips(store, side):
    handler = tools_for(store)["list_unmatched_items"].handler
    before = store.active()
    cursor, rows = None, []
    for size in (40, 40, 5):
        page = handler({"side": side, "cursor": cursor, "limit": 40})
        assert page["returned"] == size and page["total"] == 85
        rows.extend(page["items"])
        cursor = page["next_cursor"]
        assert page["has_more"] is (cursor is not None)
    assert cursor is None and len({row["id"] for row in rows}) == 85
    assert {row["ordinal"] for row in rows} == set(range(1, 86))
    assert all(row["amount"] == "-75.00" and row["source_hash"] for row in rows)
    assert store.active() == before


def test_candidate_pages_are_bounded_and_all_pairs_remain_distinct(store):
    handler = tools_for(store)["list_match_candidates"].handler
    cursor, pairs = None, []
    while True:
        page = handler({"cursor": cursor, "limit": 40})
        assert page["returned"] <= 40
        pairs.extend(page["items"])
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert len(pairs) == len({pair["bank_id"] for pair in pairs}) == len({pair["ledger_id"] for pair in pairs}) == 85
    assert not current(store.active())["matches"]


@pytest.mark.parametrize("params", [
    {"side": "bank", "cursor": None, "limit": 41},
    {"side": "bank", "cursor": None, "limit": True},
    {"side": "ledger", "cursor": None, "limit": 40},
    {"side": "bank", "cursor": None, "limit": 40, "workspace_id": "foreign"},
])
def test_page_arguments_cannot_widen_scope_or_budget(store, params):
    with pytest.raises(AssistantError):
        tools_for(store)["list_unmatched_items"].handler(params)


def test_cursor_refuses_tamper_other_side_restart_and_foreign_workspace(store, monkeypatch):
    handler = tools_for(store)["list_unmatched_items"].handler
    cursor = handler({"side": "bank", "cursor": None, "limit": 40})["next_cursor"]
    for value, side in ((cursor + "x", "bank"), (cursor, "book")):
        with pytest.raises(AssistantError, match="continuation"):
            handler({"side": side, "cursor": value, "limit": 40})
    monkeypatch.setattr(bank_tools, "_CURSOR_KEY", b"synthetic restarted process key")
    with pytest.raises(AssistantError, match="continuation"):
        handler({"side": "bank", "cursor": cursor, "limit": 40})
    monkeypatch.undo()
    populate(store, 1)
    with pytest.raises(DomainError, match="changed"):
        handler({"side": "bank", "cursor": None, "limit": 40})
    with pytest.raises(AssistantError, match="continuation"):
        tools_for(store)["list_unmatched_items"].handler({"side": "bank", "cursor": cursor, "limit": 40})


def test_cursor_binds_full_evidence_even_when_revision_and_ids_were_preserved(store):
    handler = tools_for(store)["list_unmatched_items"].handler
    cursor = handler({"side": "bank", "cursor": None, "limit": 40})["next_cursor"]
    with store.edit(bump_version=False) as workspace:
        current(workspace)["bank"]["rows"][0]["description"] += " changed synthetic source"
    with pytest.raises(AssistantError, match="continuation"):
        handler({"side": "bank", "cursor": cursor, "limit": 40})


def test_bulk_drafts_one_explicit_page_durably_and_exact_replay_is_idempotent(store):
    handler = tools_for(store)["propose_bulk_matches"].handler
    before = store.active()
    request = {"cursor": None, "max_pairs": 40, "reason": None}
    first = handler(request)
    assert first["created_count"] == len(first["proposals"]) == 40
    assert first["has_more"] and first["next_cursor"] and not first["automatically_continued"]
    assert first["confirmed_count"] == 0 and first["status"] == "awaiting_human_review"
    assert first["continuation_requires_new_request"] is True
    saved = store.active()
    assert saved["version"] == before["version"]
    assert saved["sessions"][0]["input_version"] == before["sessions"][0]["input_version"]
    assert not current(saved)["matches"] and current(saved)["status"] == "draft"
    assert len([entry for entry in current(saved)["history"] if entry["action"] == "assistant_match_drafted"]) == 40
    snapshot = next(entry["detail"]["proposal"] for entry in current(saved)["history"] if entry["action"] == "assistant_match_drafted")
    assert snapshot == first["proposals"][0]
    repeated = handler(request)
    assert repeated["created_count"] == 0 and repeated["proposals"] == first["proposals"]
    assert store.active() == saved
    with pytest.raises(AssistantError, match="one bulk proposal page"):
        handler({**request, "cursor": first["next_cursor"]})
    with pytest.raises(AssistantError, match="one bulk proposal page"):
        handler({**request, "reason": "Changed intent in the same request."})
    assert store.active() == saved
    # Only a new human request creates a new builder/run and may continue.
    second = tools_for(store)["propose_bulk_matches"].handler({**request, "cursor": first["next_cursor"]})
    assert second["created_count"] == 40
    assert not ({p["bank_id"] for p in first["proposals"]} & {p["bank_id"] for p in second["proposals"]})
    reopened = Store(store.path.parent)
    assert len(current(reopened.active())["drafts"]) == 80


def test_bulk_reuses_current_deterministic_intents_without_new_ids_or_events(tmp_path):
    store = Store(tmp_path)
    populate(store, 4)
    with store.edit() as workspace:
        session = current(workspace)
        session["drafts"] = candidates(session)
        event(workspace, session, "deterministic_matches_proposed", {"count": 4})
    before = store.active()
    existing = deepcopy(current(before)["drafts"])
    result = tools_for(store)["propose_bulk_matches"].handler({"cursor": None, "max_pairs": 40, "reason": None})
    assert result["created_count"] == 0 and len(result["proposals"]) == 4
    assert {item["id"] for item in result["proposals"]} == {item["id"] for item in existing}
    assert store.active() == before
    assert len(current(store.active())["drafts"]) == 4


def test_single_draft_idempotency_includes_full_reason_and_preserves_prior_note(store):
    handler = tools_for(store)["draft_match"].handler
    pair = candidates(current(store.active()))[0]
    request = {"bank_id": pair["bank_id"], "ledger_id": pair["ledger_id"], "reason": "Review the duplicate occurrence."}
    first = handler(request)
    assert handler(request)["created_count"] == 0
    second = handler({**request, "reason": "Review the separate source reference."})
    assert first["proposals"][0]["id"] != second["proposals"][0]["id"]
    assert current(store.active())["drafts"][0]["reason"] == request["reason"]


def test_model_loop_cannot_advance_bulk_page_without_a_new_human_request(store):
    class ContinueAdapter:
        calls = 0

        def respond(self, messages, tools, remaining_seconds):
            self.calls += 1
            params = {"cursor": None, "max_pairs": 40, "reason": None}
            if self.calls == 3:
                outputs = [item for item in messages if item.get("type") == "function_call_output"]
                params["cursor"] = json.loads(outputs[0]["output"])["next_cursor"]
            assert self.calls <= 3
            return {"output": [{"type": "function_call", "name": "propose_bulk_matches",
                                "arguments": json.dumps(params), "call_id": f"synthetic-bulk-{self.calls}"}]}

    adapter = ContinueAdapter()
    with pytest.raises(AssistantError, match="new human request"):
        run_assistant(message="Draft a page for review", tools=list(tools_for(store).values()), adapter=adapter)
    session = current(store.active())
    assert adapter.calls == 3 and len(session["drafts"]) == 40 and not session["matches"]
    assert len([entry for entry in session["history"] if entry["action"] == "assistant_match_drafted"]) == 40


def test_invalid_pair_and_failed_source_controls_never_write_proposals(store):
    tools = tools_for(store)
    before = store.active()
    with pytest.raises(DomainError):
        tools["draft_match"].handler({"bank_id": "foreign", "ledger_id": "foreign", "reason": "Invalid IDs"})
    assert store.active() == before
    with store.edit(bump_version=False) as workspace:
        current(workspace)["bank"]["closing"] = "9999.99"
    invalid = store.active()
    with pytest.raises(AssistantError, match="source controls"):
        tools["propose_bulk_matches"].handler({"cursor": None, "max_pairs": 40, "reason": None})
    assert store.active() == invalid


PLAN = {"title": "Review the reconciliation", "goal": "Inspect independent evidence before deciding.",
        "steps": [{"action": "review_source_controls", "note": "Inspect both independent source controls."},
                  {"action": "review_match_proposals", "note": "Review each separate transaction occurrence."}]}


def test_checklist_is_sealed_durable_idempotent_and_never_executes(store):
    tools = tools_for(store)
    before = store.active()
    first = tools["draft_reconciliation_plan"].handler(PLAN)
    plan = first["plan"]
    assert validate_checklist(plan) == plan
    assert plan["execution"] == "manual_review_only" and plan["status"] == "awaiting_human_review"
    assert first["actions_performed"] == 0
    assert tools["draft_reconciliation_plan"].handler(PLAN)["plan"] == plan
    saved = store.active()
    assert len(current(saved)["assistant_plans"]) == 1
    assert current(saved)["drafts"] == current(before)["drafts"] and current(saved)["matches"] == current(before)["matches"]
    assert current(Store(store.path.parent).active())["assistant_plans"][0] == plan
    status = tools["reconciliation_plan_status"].handler({"plan_id": plan["id"]})
    assert status["plans"][0] == plan and status["actions_performed"] == 0
    metadata = tools["reconciliation_plan_status"].handler({"plan_id": None})["plans"][0]
    assert metadata["step_count"] == 2 and "steps" not in metadata


def test_checklist_rejects_execution_steps_and_detects_tampered_history(store):
    tools = tools_for(store)
    before = store.active()
    for action in ("complete_reconciliation", "post_journal_entry", "approve_match"):
        with pytest.raises(AssistantError):
            tools["draft_reconciliation_plan"].handler({**PLAN, "steps": [{"action": action, "note": "Forbidden"}]})
    assert store.active() == before
    plan = tools["draft_reconciliation_plan"].handler(PLAN)["plan"]
    with store.edit(bump_version=False) as workspace:
        current(workspace)["assistant_plans"][0]["steps"][0]["note"] = "Changed after drafting"
    with pytest.raises(AssistantError, match="changed"):
        tools["reconciliation_plan_status"].handler({"plan_id": plan["id"]})


def test_plan_status_marks_changed_financial_evidence_stale_without_rewriting_history(store):
    plan = tools_for(store)["draft_reconciliation_plan"].handler(PLAN)["plan"]
    with store.edit() as workspace:
        session = current(workspace)
        pair = candidates(session)[0]
        session["matches"].append({"id": "synthetic-reviewed", "bank_id": pair["bank_id"],
                                   "ledger_id": pair["ledger_id"], "note": "Synthetic human review"})
        event(workspace, session, "match_confirmed", {"id": "synthetic-reviewed"})
    before = store.active()
    status = tools_for(store)["reconciliation_plan_status"].handler({"plan_id": plan["id"]})
    assert status["plans"][0]["status"] == "stale" and status["plans"][0]["stale_reason"]
    assert current(store.active())["assistant_plans"][0] == plan and store.active() == before


def test_foreign_checklist_cannot_be_read_in_another_workspace(store):
    plan = tools_for(store)["draft_reconciliation_plan"].handler(PLAN)["plan"]
    populate(store, 1)
    with store.edit() as workspace:
        current(workspace)["assistant_plans"] = [deepcopy(plan)]
    with pytest.raises(AssistantError, match="different workspace"):
        tools_for(store)["reconciliation_plan_status"].handler({"plan_id": plan["id"]})


def test_unicode_evidence_is_byte_bounded_through_real_rehearsal_and_pagination(tmp_path):
    store = Store(tmp_path)
    populate(store, 40, description="🙂" * 600, external="🙂" * 512)
    with store.edit(bump_version=False) as workspace:
        session = current(workspace)
        for row in session["ledger"]["rows"]:
            session["classifications"][row["id"]] = {"kind": "outstanding", "evidence": "🙂" * 500}
    tools = tools_for(store)
    result = run_assistant(message="Explain bounded evidence", tools=list(tools.values()), rehearsal=True)
    assert result["usage"]["tool_calls"] == 4
    for item in result["evidence"]:
        assert bank_tools._size(item["data"]) <= bank_tools.MAX_TOOL_BYTES
    rows, cursor = [], None
    while True:
        page = tools["list_unmatched_items"].handler({"side": "book", "cursor": cursor, "limit": 40})
        assert 1 <= page["returned"] <= 40 and bank_tools._size(page) <= bank_tools.MAX_TOOL_BYTES
        rows.extend(page["items"])
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert len(rows) == len({row["id"] for row in rows}) == 40
    assert all(row["description_truncated"] and row["external_id_truncated"] for row in rows)
    assert all(row["amount"] == "-75.00" and row["external_id"] is None and row["external_id_digest"] for row in rows)


def test_oversized_unicode_drafts_are_rejected_before_persistence(store):
    tools = tools_for(store)
    before = store.active()
    with pytest.raises(AssistantError, match="large"):
        tools["propose_bulk_matches"].handler({"cursor": None, "max_pairs": 40, "reason": "🙂" * 500})
    assert store.active() == before
    steps = [{"action": "inspect_tieout", "note": "🙂" * 500} for _ in range(20)]
    with pytest.raises(AssistantError, match="large"):
        tools["draft_reconciliation_plan"].handler({**PLAN, "steps": steps})
    assert store.active() == before


def test_caps_refuse_new_metadata_atomically_but_allow_exact_replay(store, monkeypatch):
    tools = tools_for(store)
    monkeypatch.setattr(bank_tools, "MAX_DRAFTS", 1)
    request = {"cursor": None, "max_pairs": 1, "reason": None}
    page = tools["propose_bulk_matches"].handler(request)
    saved = store.active()
    assert tools["propose_bulk_matches"].handler(request)["created_count"] == 0
    with pytest.raises(AssistantError, match="existing match proposals"):
        tools_for(store)["propose_bulk_matches"].handler({**request, "cursor": page["next_cursor"]})
    assert store.active() == saved
    monkeypatch.setattr(bank_tools, "MAX_PLANS", 1)
    tools["draft_reconciliation_plan"].handler(PLAN)
    saved = store.active()
    with pytest.raises(AssistantError, match="saved checklists"):
        tools["draft_reconciliation_plan"].handler({**PLAN, "goal": "A distinct bounded review goal."})
    assert store.active() == saved


def test_nullable_external_id_is_safe_and_replay_rejects_falsified_draft_status(store):
    with store.edit(bump_version=False) as workspace:
        current(workspace)["bank"]["rows"][0]["external_id"] = None
    tools = tools_for(store)
    assert tools["list_unmatched_items"].handler({"side": "bank", "cursor": None, "limit": 40})["items"]
    pair = candidates(current(store.active()))[0]
    params = {"bank_id": pair["bank_id"], "ledger_id": pair["ledger_id"], "reason": "Review only"}
    tools["draft_match"].handler(params)
    with store.edit(bump_version=False) as workspace:
        current(workspace)["drafts"][0]["status"] = "approved"
    saved = store.active()
    with pytest.raises(AssistantError, match="changed after drafting"):
        tools["draft_match"].handler(params)
    assert store.active() == saved


def test_restored_checklist_rebind_is_pure_and_retains_financial_inputs(store):
    plan = tools_for(store)["draft_reconciliation_plan"].handler(PLAN)["plan"]
    before = deepcopy(plan)
    restored = deepcopy(store.active())
    restored["id"] = "workspace_restored_synthetic"
    rebound = rebind_checklist(plan, restored["id"])
    assert plan == before and rebound["id"] != plan["id"]
    assert rebound["steps"] == plan["steps"] and rebound["input_hash"] == plan["input_hash"]
    current(restored)["assistant_plans"] = [rebound]
    assert checklist_views(restored, current(restored))[0]["status"] == "awaiting_human_review"
    assert validate_checklist(rebound) == rebound


def test_unmatched_bank_pages_copy_deterministic_reasons_and_report_diagnostic_limit(tmp_path):
    store = Store(tmp_path)
    populate(store, 151)
    with store.edit(bump_version=False) as workspace:
        session = current(workspace)
        for row in session["ledger"]["rows"]:
            row["amount"] = "75.00"
        session["ledger"]["closing"] = "21325.00"
    expected = {item["bank_id"]: item for item in diagnostics(current(store.active()))["rows"]}
    assert len(expected) == 150
    handler = tools_for(store)["list_unmatched_items"].handler
    cursor, seen, unavailable = None, set(), 0
    while True:
        result = handler({"side": "bank", "cursor": cursor, "limit": 40})
        assert result["diagnostics"] == {"truncated": True, "available_rows": 150, "total_rows": 151}
        for row in result["items"]:
            seen.add(row["id"])
            if row["id"] in expected:
                assert row["why_not_matched"] == expected[row["id"]]["message"]
                assert row["diagnostic_reason"] == "sign_mismatch"
                assert row["diagnostic_status"] == "available"
            else:
                unavailable += 1
                assert row["why_not_matched"] is None
                assert row["diagnostic_status"] == "not_in_bounded_diagnostics"
        cursor = result["next_cursor"]
        if cursor is None:
            break
    assert len(seen) == 151 and unavailable == 1


def test_completed_reconciliation_remains_readable_but_all_drafting_is_blocked(store):
    with store.edit() as workspace:
        session = current(workspace)
        for index, pair in enumerate(candidates(session)):
            session["matches"].append({"id": "synthetic-human-" + str(index), "bank_id": pair["bank_id"],
                                       "ledger_id": pair["ledger_id"], "note": "Synthetic human review"})
        complete(workspace, session, "Synthetic human acceptance")
    tools = tools_for(store)
    before = store.active()
    assert tools["get_recon_tieout"].handler({})["difference"] == "0.00"
    requests = {"propose_bulk_matches": {"cursor": None, "max_pairs": 40, "reason": None},
                "draft_match": {"bank_id": "bank", "ledger_id": "book", "reason": "Review"},
                "draft_reconciliation_plan": PLAN}
    for name, params in requests.items():
        with pytest.raises(DomainError, match="completed"):
            tools[name].handler(params)
    assert store.active() == before
