"""Synthetic Xero HTTP fixtures; never live accounts or credentials.

Shapes follow Xero's public Payments, BankTransfers and BankSummary examples:
https://developer.xero.com/documentation/api/accounting/payments
https://developer.xero.com/documentation/api/accounting/banktransfers
https://github.com/XeroAPI/Xero-OpenAPI/blob/master/xero_accounting.yaml
All identifiers, counterparties and amounts below are synthetic.
"""

from copy import deepcopy
from decimal import Decimal
import json
from urllib.parse import parse_qs
from uuid import UUID

import httpx
import pytest

from vesper_bank_rec.connectors import xero
from vesper_bank_rec.settings import MemorySecretStore, SettingsService

TENANT = "11111111-1111-4111-8111-111111111111"
ACCOUNT = "22222222-2222-4222-8222-222222222222"
OTHER = "33333333-3333-4333-8333-333333333333"
SECRET = "synthetic-old-access-never-sent-to-provider"
REFRESH = "synthetic-old-refresh-never-sent-to-provider"
SCOPES = "offline_access " + " ".join(sorted(xero.READ_SCOPES))


def identity(number):
    return str(UUID(int=number))


def bank(number, amount="75.00", kind="SPEND", **kwargs):
    return {"BankTransactionID": identity(number), "Date": "2026-10-12", "Type": kind,
            "Status": "AUTHORISED", "BankAccount": {"AccountID": ACCOUNT},
            "CurrencyCode": "USD", "Total": amount, "Reference": "Synthetic software", **kwargs}


def payment(number, amount="100.00", kind="ACCPAYPAYMENT", **kwargs):
    return {"PaymentID": identity(number), "Date": "2026-10-12", "PaymentType": kind,
            "Status": "AUTHORISED", "Account": {"AccountID": ACCOUNT},
            "BankAmount": amount, "Amount": amount, "Reference": "Synthetic payment", **kwargs}


def report(received="0.00", spent="0.00", opening="5000.00", *, fx=None):
    closing = Decimal(opening) + Decimal(received) - Decimal(spent)
    headings = ["Bank Accounts", "Opening Balance", "Cash Received", "Cash Spent", "Closing Balance"]
    cells = [{"Value": "Synthetic operating bank", "Attributes": [{"Id": "accountID", "Value": ACCOUNT}]},
             {"Value": opening}, {"Value": received}, {"Value": spent}, {"Value": str(closing)}]
    if fx is not None:
        headings.append("FX Gain")
        cells.append({"Value": fx})
    return {"Status": "OK", "Reports": [{"ReportType": "BankSummary", "ReportName": "Bank Summary",
        "ReportTitles": ["Bank Summary", "Synthetic company", "From 1 October 2026 to 31 October 2026"],
        "Rows": [{"RowType": "Header", "Cells": [{"Value": text} for text in headings]},
                 {"RowType": "Section", "Rows": [{"RowType": "Row", "Cells": cells}]}]}]}


class Fixture:
    def __init__(self, banks=None, payments=None):
        self.banks = banks or []
        self.payments = payments or []
        amounts = [Decimal(row["Total"]) * xero.BANK_SIGNS[row["Type"]] for row in self.banks if row["BankAccount"]["AccountID"] == ACCOUNT]
        amounts += [Decimal(row["BankAmount"]) * xero.PAYMENT_SIGNS[row["PaymentType"]] for row in self.payments if row["Account"]["AccountID"] == ACCOUNT]
        self.summary = report(str(sum((value for value in amounts if value > 0), Decimal(0))), str(-sum((value for value in amounts if value < 0), Decimal(0))))
        self.requests = []
        self.resource_calls = {}
        self.hook = None
        self.currency = "USD"

    def __call__(self, request):
        self.requests.append(request)
        resource = request.url.path.rsplit("/", 1)[-1]
        self.resource_calls[resource] = self.resource_calls.get(resource, 0) + 1
        if self.hook:
            response = self.hook(request, resource, self.resource_calls[resource])
            if response is not None:
                return response
        assert request.url.host in {"api.xero.com", "identity.xero.com"}
        if request.url.host == "identity.xero.com":
            data = parse_qs(request.content.decode())
            assert data["grant_type"] == ["refresh_token"]
            assert data["client_id"] == ["synthetic-client-id"]
            assert "client_secret" not in data
            return httpx.Response(200, json={"access_token": "synthetic-new-access", "refresh_token": "synthetic-new-refresh", "token_type": "Bearer", "expires_in": 1800, "scope": SCOPES})
        assert request.method == "GET"
        assert request.headers["Authorization"].startswith("Bearer ")
        assert "If-Modified-Since" not in request.headers
        if resource == "connections":
            return httpx.Response(200, json=[{"tenantId": TENANT, "tenantName": "Synthetic company", "tenantType": "ORGANISATION"}])
        assert request.headers["xero-tenant-id"] == TENANT
        if resource == "Organisation":
            return httpx.Response(200, json={"Organisations": [{"OrganisationID": TENANT, "Name": "Synthetic company", "BaseCurrency": "USD"}]})
        if resource == "Accounts":
            return httpx.Response(200, json={"Accounts": [{"AccountID": ACCOUNT, "Name": "Synthetic operating bank", "Type": "BANK", "CurrencyCode": self.currency, "Status": "ACTIVE"}]})
        if resource == "BankSummary":
            assert request.url.params["fromDate"] == "2026-10-01"
            assert request.url.params["toDate"] == "2026-10-31"
            return httpx.Response(200, json=self.summary)
        if resource in {"BankTransactions", "Payments"}:
            assert "Status==\"AUTHORISED\"" in request.url.params["where"]
            assert "DateTime(2026,11,1)" in request.url.params["where"]
            population = self.banks if resource == "BankTransactions" else self.payments
            page, size = int(request.url.params["page"]), int(request.url.params["pageSize"])
            return httpx.Response(200, json={resource: population[(page - 1) * size:page * size],
                "pagination": {"page": page, "pageSize": size, "pageCount": (len(population) + size - 1) // size,
                               "itemCount": len(population)}})
        raise AssertionError("Unapproved synthetic endpoint")


def client(tmp_path, fixture, *, bound=True, expiry=10000):
    settings = SettingsService(tmp_path, MemorySecretStore(), testing=True)
    bundle = {"client_id": "synthetic-client-id", "access_token": SECRET, "refresh_token": REFRESH,
              "expires_at": expiry, "scope": SCOPES}
    if bound:
        bundle["bound_company"] = {"id": TENANT, "name": "Synthetic company", "currency": "USD"}
    settings.save("workspace-xero", "xero", bundle, persist=False)
    http = httpx.Client(transport=httpx.MockTransport(fixture), follow_redirects=True)
    return xero.XeroClient(settings, "workspace-xero", client=http, clock=lambda: 1000), settings


def read(connector):
    return connector.bank_ledger(ACCOUNT, "2026-10-01", "2026-10-31")


def test_controlled_composite_preserves_duplicate_ids_and_does_not_double_count_transfers(tmp_path):
    fixture = Fixture([bank(1), bank(2), bank(3, "20.00", "RECEIVE-TRANSFER")],
                      [payment(4, "2500.00", "ACCRECPAYMENT"), payment(5, "1000.00")])
    connector, settings = client(tmp_path, fixture)
    result = read(connector)
    assert result["opening_balance"] == "5000.00"
    assert result["closing_balance"] == "6370.00"
    assert result["currency"] == "USD" and len(result["rows"]) == 5
    duplicates = [row for row in result["rows"] if row["amount"] == "-75.00"]
    assert len(duplicates) == 2 and duplicates[0]["id"] != duplicates[1]["id"]
    assert len({row["id"] for row in result["rows"]}) == 5
    assert not any("BankTransfers" in str(request.url) for request in fixture.requests)
    assert fixture.resource_calls["BankTransactions"] == fixture.resource_calls["Payments"] == 2
    assert fixture.resource_calls["BankSummary"] == 2
    assert result["source"]["controls"]["cash received"] == "2520.00"
    assert result["source"]["controls"]["cash spent"] == "1150.00"
    assert result["source"]["universal_ledger"] is False
    assert SECRET not in json.dumps(result) and REFRESH not in json.dumps(result)
    assert settings.get("workspace-xero", "xero")["access_token"] == SECRET


@pytest.mark.parametrize("kind,sign", sorted(xero.PAYMENT_SIGNS.items()))
def test_all_documented_invoice_bill_and_refund_payment_directions(tmp_path, kind, sign):
    fixture = Fixture(payments=[payment(1, "10.01", kind, Amount="999.99")])
    connector, _ = client(tmp_path, fixture)
    result = read(connector)
    assert result["rows"][0]["amount"] == format(Decimal("10.01") * sign, ".2f")
    assert result["rows"][0]["source_ids"] == [identity(1)]


def test_missing_bank_amount_never_falls_back_to_document_amount(tmp_path):
    fixture = Fixture(payments=[payment(1)])
    del fixture.payments[0]["BankAmount"]
    connector, _ = client(tmp_path, fixture)
    with pytest.raises(xero.XeroIncomplete, match="monetary"):
        read(connector)


def test_pagination_follows_declared_population_and_keeps_equal_occurrences(tmp_path, monkeypatch):
    monkeypatch.setattr(xero, "PAGE_SIZE", 2)
    fixture = Fixture([bank(1), bank(2), bank(3)])
    connector, _ = client(tmp_path, fixture)
    assert len(read(connector)["rows"]) == 3
    assert fixture.resource_calls["BankTransactions"] == 4


@pytest.mark.parametrize("defect", ["missing-metadata", "missing-page-row", "repeated-id", "changed-count"])
def test_paging_control_failures_block_the_entire_import(tmp_path, monkeypatch, defect):
    monkeypatch.setattr(xero, "PAGE_SIZE", 2)
    fixture = Fixture([bank(1), bank(2), bank(3)])

    def mutate(request, resource, count):
        if resource != "BankTransactions":
            return None
        if defect == "missing-metadata":
            return httpx.Response(200, json={"BankTransactions": fixture.banks})
        if int(request.url.params["page"]) == 2:
            row = [] if defect == "missing-page-row" else [fixture.banks[0] if defect == "repeated-id" else fixture.banks[2]]
            return httpx.Response(200, json={"BankTransactions": row, "pagination": {"page": 2, "pageSize": 2,
                "pageCount": 2, "itemCount": 4 if defect == "changed-count" else 3}})
    fixture.hook = mutate
    connector, _ = client(tmp_path, fixture)
    with pytest.raises(xero.XeroIncomplete):
        read(connector)


def test_same_count_but_changed_transaction_content_is_detected_by_second_read(tmp_path):
    fixture = Fixture([bank(1)])

    def mutate(request, resource, count):
        if resource == "BankTransactions" and count == 2:
            fixture.banks[0]["Reference"] = "Changed after first observation"
    fixture.hook = mutate
    connector, _ = client(tmp_path, fixture)
    with pytest.raises(xero.XeroIncomplete, match="changed between"):
        read(connector)


def test_gross_controls_detect_offsetting_omissions_even_when_net_ties(tmp_path):
    fixture = Fixture([bank(1, "20.00", "RECEIVE"), bank(2, "10.00")])
    fixture.summary = report("30.00", "20.00")  # Same net, unsupported +10/-10 omitted.
    connector, _ = client(tmp_path, fixture)
    with pytest.raises(xero.XeroIncomplete, match="gross receipts"):
        read(connector)


def test_changed_report_controls_during_read_block_import(tmp_path):
    fixture = Fixture([bank(1)])

    def mutate(request, resource, count):
        if resource == "BankSummary" and count == 2:
            return httpx.Response(200, json=report("0.00", "76.00"))
    fixture.hook = mutate
    connector, _ = client(tmp_path, fixture)
    with pytest.raises(xero.XeroIncomplete, match="controls changed"):
        read(connector)


@pytest.mark.parametrize("defect", ["unknown-type", "deleted", "outside-period", "foreign-transaction", "conflicting-dates"])
def test_unsupported_selected_movements_fail_closed(tmp_path, defect):
    fixture = Fixture([bank(1)])
    if defect == "unknown-type":
        fixture.banks[0]["Type"] = "EXPPAYMENT"
    elif defect == "deleted":
        fixture.banks[0]["Status"] = "DELETED"
    elif defect == "outside-period":
        fixture.banks[0]["Date"] = "2026-09-30"
    elif defect == "foreign-transaction":
        fixture.banks[0]["CurrencyCode"] = "EUR"
    else:
        fixture.banks[0]["DateString"] = "2026-10-13T00:00:00"
    connector, _ = client(tmp_path, fixture)
    with pytest.raises(xero.XeroIncomplete):
        read(connector)


def test_foreign_currency_bank_is_rejected_before_ledger_read(tmp_path):
    fixture = Fixture()
    fixture.currency = "EUR"
    connector, _ = client(tmp_path, fixture)
    with pytest.raises(xero.XeroIncomplete, match="base currency"):
        read(connector)
    assert "BankSummary" not in fixture.resource_calls


def test_missing_bank_currency_is_never_assumed_to_be_base_currency(tmp_path):
    fixture = Fixture()
    def missing(request, resource, count):
        if resource == "Accounts":
            return httpx.Response(200, json={"Accounts": [{"AccountID": ACCOUNT, "Name": "Unknown currency bank", "Type": "BANK", "Status": "ACTIVE"}]})
    fixture.hook = missing
    connector, _ = client(tmp_path, fixture)
    with pytest.raises(xero.XeroIncomplete, match="currency"):
        read(connector)
    assert "BankSummary" not in fixture.resource_calls


def test_other_account_transactions_do_not_cross_into_selected_ledger(tmp_path):
    fixture = Fixture([bank(1), bank(2, BankAccount={"AccountID": OTHER})])
    connector, _ = client(tmp_path, fixture)
    result = read(connector)
    assert len(result["rows"]) == 1
    assert result["rows"][0]["source_ids"] == [identity(1)]


def test_no_activity_period_still_requires_explicit_balances_and_pagination(tmp_path):
    connector, _ = client(tmp_path, Fixture())
    result = read(connector)
    assert result["rows"] == []
    assert result["opening_balance"] == result["closing_balance"] == "5000.00"


@pytest.mark.parametrize("defect", ["wrong-period", "missing-account", "duplicate-account", "nonzero-fx", "wrong-close"])
def test_report_account_period_and_independent_balance_controls(defect):
    value = report("110.00", "100.00", "0.00", fx="1.00" if defect == "nonzero-fx" else None)
    row = value["Reports"][0]["Rows"][1]["Rows"][0]
    if defect == "wrong-period":
        value["Reports"][0]["ReportTitles"][-1] = "From 1 September 2026 to 30 September 2026"
    elif defect == "missing-account":
        row["Cells"][0]["Attributes"] = []
    elif defect == "duplicate-account":
        value["Reports"][0]["Rows"][1]["Rows"].append(deepcopy(row))
    elif defect == "wrong-close":
        row["Cells"][-1]["Value"] = "11.00"
    with pytest.raises(xero.XeroIncomplete):
        xero.parse_bank_summary(value, account_id=ACCOUNT, start_date="2026-10-01", end_date="2026-10-31")


def test_public_bank_summary_shape_and_zero_optional_fx_are_supported():
    result = xero.parse_bank_summary(report("110.00", "100.00", "0.00", fx="0.00"),
        account_id=ACCOUNT, start_date="2026-10-01", end_date="2026-10-31")
    assert result["opening balance"] == 0 and result["closing balance"] == Decimal("10.00")


def test_expired_token_rotation_is_complete_and_preserves_binding(tmp_path):
    fixture = Fixture()
    connector, settings = client(tmp_path, fixture, expiry=0)
    assert connector.accounts()[0]["id"] == ACCOUNT
    saved = settings.get("workspace-xero", "xero")
    assert saved["refresh_token"] == "synthetic-new-refresh"
    assert saved["access_token"] == "synthetic-new-access"
    assert saved["expires_at"] == 2800
    assert saved["bound_company"]["id"] == TENANT
    assert len([request for request in fixture.requests if request.method == "POST"]) == 1
    assert all(SECRET.encode() not in path.read_bytes() for path in tmp_path.iterdir() if path.is_file())


def test_401_retries_once_with_rotated_saved_token(tmp_path):
    fixture = Fixture()

    def unauthorized(request, resource, count):
        if resource == "Accounts" and count == 1:
            return httpx.Response(401, json={"detail": "not echoed"})
    fixture.hook = unauthorized
    connector, settings = client(tmp_path, fixture)
    assert connector.accounts()
    accounts = [request for request in fixture.requests if request.url.path.endswith("/Accounts")]
    assert accounts[0].headers["Authorization"] == "Bearer " + SECRET
    assert accounts[1].headers["Authorization"] == "Bearer synthetic-new-access"
    assert settings.get("workspace-xero", "xero")["refresh_token"] == "synthetic-new-refresh"


def test_incomplete_refresh_never_replaces_old_bundle_or_echoes_payload(tmp_path):
    fixture = Fixture()
    fixture.hook = lambda request, resource, count: httpx.Response(200, json={"access_token": SECRET, "error_description": REFRESH}) if request.url.host == "identity.xero.com" else None
    connector, settings = client(tmp_path, fixture, expiry=0)
    before = settings.get("workspace-xero", "xero")
    with pytest.raises(xero.XeroError) as caught:
        connector.accounts()
    assert SECRET not in str(caught.value) and REFRESH not in str(caught.value)
    assert settings.get("workspace-xero", "xero") == before


@pytest.mark.parametrize("status", [302, 403, 429, 500])
def test_provider_failures_and_redirects_are_static_and_never_followed(tmp_path, status):
    fixture = Fixture()
    fixture.hook = lambda request, resource, count: httpx.Response(status, headers={"Location": "https://unapproved.invalid/capture"}, json={"error": SECRET})
    connector, _ = client(tmp_path, fixture)
    with pytest.raises(xero.XeroError) as caught:
        connector.accounts()
    assert SECRET not in str(caught.value)
    assert len(fixture.requests) == 1 and fixture.requests[0].url.host == "api.xero.com"


def test_organisation_binding_is_explicit_and_stale_guard_prevents_write(tmp_path):
    fixture = Fixture()
    connector, settings = client(tmp_path, fixture, bound=False)
    assert connector.organizations() == [{"id": TENANT, "name": "Synthetic company"}]
    with pytest.raises(xero.XeroError, match="changed"):
        connector.bind(TENANT, scope_check=lambda: False)
    assert "bound_company" not in settings.get("workspace-xero", "xero")
    assert connector.bind(TENANT) == {"id": TENANT, "name": "Synthetic company", "currency": "USD"}
    with pytest.raises(xero.XeroError, match="returned"):
        connector.bind(OTHER)


def test_binding_scope_lock_spans_check_and_save_after_all_provider_requests(tmp_path, monkeypatch):
    held = False
    events = []

    class ScopeLock:
        def __enter__(self):
            nonlocal held
            held = True
            events.append("lock")
        def __exit__(self, *args):
            nonlocal held
            held = False
            events.append("unlock")

    fixture = Fixture()
    def provider(request, resource, count):
        assert not held, "The finance Store lock must not span provider RPCs"
    fixture.hook = provider
    connector, settings = client(tmp_path, fixture, bound=False)
    original = settings.replace
    def replace(*args):
        assert held
        events.append("save")
        return original(*args)
    monkeypatch.setattr(settings, "replace", replace)
    def check():
        assert held
        events.append("check")
        return True
    connector.bind(TENANT, scope_check=check, scope_lock=ScopeLock())
    assert events == ["lock", "check", "save", "unlock"]


def test_missing_required_scope_and_missing_binding_are_recoverable(tmp_path):
    connector, settings = client(tmp_path, Fixture(), bound=False)
    with pytest.raises(xero.XeroError, match="confirm"):
        connector.accounts()
    value = settings.get("workspace-xero", "xero")
    value["scope"] = "offline_access accounting.settings.read"
    settings.replace("workspace-xero", "xero", value)
    with pytest.raises(xero.XeroError, match="read-only"):
        connector.organizations()


def test_request_and_response_budgets_fail_without_partial_result(tmp_path, monkeypatch):
    connector, _ = client(tmp_path, Fixture([bank(1)]))
    monkeypatch.setattr(xero, "MAX_REQUESTS", 2)
    with pytest.raises(xero.XeroIncomplete, match="bounded"):
        read(connector)
    monkeypatch.setattr(xero, "MAX_RESPONSE", 1)
    with pytest.raises(xero.XeroIncomplete, match="size"):
        connector.accounts()


@pytest.mark.parametrize("bad", [True, None, "NaN", "Infinity", "1.001", "1,2.00", 1.01])
def test_money_rejects_inexact_or_malformed_values(bad):
    with pytest.raises(xero.XeroIncomplete):
        xero._money(bad)


def test_json_numbers_decode_to_exact_decimal_and_dates_preserve_calendar_day():
    value = xero.XeroClient._decode(httpx.Response(200, content=b'{"Amount": 0.01}'))
    assert value["Amount"] == Decimal("0.01")
    assert str(xero._day("/Date(1791763200000+0000)/")) == "2026-10-12"
    with pytest.raises(xero.XeroIncomplete):
        xero.XeroClient._decode(httpx.Response(200, content=b'{"Amount": NaN}'))


def test_duplicate_bank_amount_json_keys_cannot_certify_a_controlled_ledger(tmp_path):
    fixture = Fixture(payments=[payment(1, "10.00")])
    body = json.dumps({"Payments": fixture.payments, "pagination": {
        "page": 1, "pageSize": xero.PAGE_SIZE, "pageCount": 1, "itemCount": 1,
    }}).replace('"BankAmount": "10.00"', '"BankAmount": "999.99", "BankAmount": "10.00"')
    assert body.count('"BankAmount"') == 2
    fixture.hook = lambda request, resource, count: httpx.Response(200, content=body.encode()) if resource == "Payments" else None
    connector, settings = client(tmp_path, fixture)
    before = settings.get("workspace-xero", "xero")
    with pytest.raises(xero.XeroIncomplete, match="invalid JSON"):
        read(connector)
    assert settings.get("workspace-xero", "xero") == before


@pytest.mark.parametrize("body", [
    b'{"AccountID":"first","AccountID":"second"}',
    b'{"nested":{"Total":1,"Total":2}}',
    b'{"nested":' + b'[' * 70 + b'0' + b']' * 70 + b'}',
    b'{"nested":' + b'[' * 1500 + b'0' + b']' * 1500 + b'}',
])
def test_ambiguous_or_overly_nested_json_returns_only_static_errors(body):
    with pytest.raises(xero.XeroIncomplete, match="invalid JSON") as caught:
        xero.XeroClient._decode(httpx.Response(200, content=body))
    assert "first" not in str(caught.value) and "second" not in str(caught.value)


@pytest.mark.parametrize("changes", [
    {"token_type": "Basic"}, {"token_type": None},
    {"access_token": "synthetic\nsecret"}, {"access_token": "synthetic secret"},
    {"access_token": "synthetic\x7fsecret"}, {"access_token": "synthetic-é-secret"},
    {"access_token": "x" * 16385}, {"refresh_token": "x" * 8193},
    {"refresh_token": "synthetic\rsecret"},
    {"scope": " ".join(sorted(xero.READ_SCOPES))},
    {"scope": SCOPES + "\nopenid"}, {"scope": SCOPES + " offline_access"},
    {"scope": "x" * 2049}, {"scope": ["offline_access"]},
])
def test_hostile_refresh_fields_never_replace_saved_bundle_or_leak_values(tmp_path, changes):
    fixture = Fixture()
    payload = {"access_token": "synthetic-new-access", "refresh_token": "synthetic-new-refresh",
               "token_type": "Bearer", "expires_in": 1800, "scope": SCOPES, **changes}
    fixture.hook = lambda request, resource, count: httpx.Response(200, json=payload) if request.url.host == "identity.xero.com" else None
    connector, settings = client(tmp_path, fixture, expiry=0)
    before = settings.get("workspace-xero", "xero")
    with pytest.raises(xero.XeroError) as caught:
        connector.accounts()
    assert "synthetic" not in str(caught.value)
    assert settings.get("workspace-xero", "xero") == before
    assert len(fixture.requests) == 1 and fixture.requests[0].url.host == "identity.xero.com"


@pytest.mark.parametrize("expiry", [float("nan"), float("inf"), float("-inf"), True, "10000"])
def test_nonfinite_or_invalid_saved_expiry_blocks_requests(tmp_path, expiry):
    fixture = Fixture()
    connector, _ = client(tmp_path, fixture, expiry=expiry)
    with pytest.raises(xero.XeroError, match="expiry metadata"):
        connector.accounts()
    assert fixture.requests == []


def test_refresh_may_omit_scope_and_preserves_previously_valid_grant(tmp_path):
    fixture = Fixture()
    fixture.hook = lambda request, resource, count: httpx.Response(200, json={
        "access_token": "synthetic-new-access", "refresh_token": "synthetic-new-refresh",
        "token_type": "bearer", "expires_in": 1800,
    }) if request.url.host == "identity.xero.com" else None
    connector, settings = client(tmp_path, fixture, expiry=0)
    assert connector.accounts()
    assert settings.get("workspace-xero", "xero")["scope"] == SCOPES
