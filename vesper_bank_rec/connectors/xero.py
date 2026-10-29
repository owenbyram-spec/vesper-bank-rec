"""Read-only, controlled Xero cash-account import; not a universal journal reader.

Primary implementation references (reviewed 2026-10-28):
https://developer.xero.com/documentation/api/accounting/payments
https://developer.xero.com/documentation/api/accounting/banktransfers
https://developer.xero.com/documentation/api/accounting/types/
https://developer.xero.com/documentation/api/accounting/requests-and-responses/
https://github.com/XeroAPI/Xero-OpenAPI/blob/master/xero_accounting.yaml

BankTransactions already include both transfer sides. Payments supply invoice,
bill and refund movements. BankSummary supplies separate opening, gross cash
received/spent and closing controls. Unsupported movement classes are never
invented to make those controls agree. Journals require separate Xero approval.
"""

from __future__ import annotations

from contextlib import nullcontext
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
import re
import threading
import time
from uuid import UUID

import httpx

API_ROOT = "https://api.xero.com/api.xro/2.0/"
CONNECTIONS_URL = "https://api.xero.com/connections"
TOKEN_URL = "https://identity.xero.com/connect/token"
READ_SCOPES = frozenset({"accounting.settings.read", "accounting.banktransactions.read",
                         "accounting.payments.read", "accounting.reports.banksummary.read"})
PAGE_SIZE = 1000
MAX_ROWS = 10000
MAX_RESPONSE = 8_000_000
MAX_REQUESTS = 80
MAX_JSON_DEPTH = 64
PAYMENT_SIGNS = {
    "ACCRECPAYMENT": 1, "ACCPAYPAYMENT": -1,
    "ARCREDITPAYMENT": -1, "APCREDITPAYMENT": 1,
    "AROVERPAYMENTPAYMENT": -1, "ARPREPAYMENTPAYMENT": -1,
    "APPREPAYMENTPAYMENT": 1, "APOVERPAYMENTPAYMENT": 1,
}
BANK_SIGNS = {prefix + suffix: sign for prefix, sign in (("RECEIVE", 1), ("SPEND", -1))
              for suffix in ("", "-PREPAYMENT", "-OVERPAYMENT", "-TRANSFER")}


class XeroError(ValueError):
    pass


class XeroIncomplete(XeroError):
    pass


def _id(value):
    try:
        parsed = str(UUID(value))
    except (ValueError, TypeError, AttributeError):
        raise XeroIncomplete("Xero returned an invalid source identifier.") from None
    if parsed == "00000000-0000-0000-0000-000000000000":
        raise XeroIncomplete("Xero returned an empty source identifier.")
    return parsed


def _currency(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Z]{3}", value):
        raise XeroIncomplete("Xero did not establish the account currency.")
    return value


def _day(value):
    if not isinstance(value, str):
        raise XeroIncomplete("Xero returned an unsupported date.")
    if re.fullmatch(r"/Date\(-?\d+(?:[+-]\d{4})?\)/", value):
        milliseconds = int(re.search(r"-?\d+", value).group())
        try:
            return datetime.fromtimestamp(milliseconds / 1000, timezone.utc).date()
        except (ValueError, OverflowError, OSError):
            raise XeroIncomplete("Xero returned an unsupported date.") from None
    try:
        parsed = date.fromisoformat(value[:10])
    except ValueError:
        raise XeroIncomplete("Xero returned an unsupported date.") from None
    if value != parsed.isoformat() and not re.fullmatch(r"\d{4}-\d\d-\d\dT00:00:00(?:\.0+)?(?:Z)?", value):
        raise XeroIncomplete("Xero returned an ambiguous transaction date.")
    return parsed


def _money(value):
    if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)):
        raise XeroIncomplete("Xero returned an invalid exact monetary amount.")
    text = str(value).strip()
    if text.startswith("(") and text.endswith(")"):
        text = "-" + text[1:-1]
    if len(text) > 32 or not re.fullmatch(r"-?(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d+)?", text):
        raise XeroIncomplete("Xero returned unsupported amount formatting.")
    try:
        value = Decimal(text.replace(",", ""))
        cents = value.quantize(Decimal("0.01"))
    except InvalidOperation:
        raise XeroIncomplete("Xero returned an invalid exact monetary amount.") from None
    if value != cents or abs(value) > Decimal("999999999999.99"):
        raise XeroIncomplete("Xero amounts exceed supported precision or range.")
    return cents


def _transaction_day(row):
    observed = [_day(row[key]) for key in ("Date", "DateString") if key in row]
    if not observed or len(set(observed)) != 1:
        raise XeroIncomplete("Xero transaction date fields are missing or inconsistent.")
    return observed[0]


def _digest(value):
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def _text(value, maximum=2000):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise XeroIncomplete("Xero returned a missing or oversized source label.")
    return value.strip()


def _fault(value):
    return any(value.get(key) for key in ("Error", "Errors", "Warnings", "HasErrors", "Truncated", "ContinuationToken"))


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError()
        result[key] = value
    return result


def _bounded_json(value):
    pending = [(value, 0)]
    while pending:
        current, depth = pending.pop()
        if depth > MAX_JSON_DEPTH:
            raise ValueError()
        children = current.values() if isinstance(current, dict) else current if isinstance(current, list) else ()
        pending.extend((child, depth + 1) for child in children)


def _token(value, maximum):
    return isinstance(value, str) and 1 <= len(value) <= maximum and all(33 <= ord(char) <= 126 for char in value)


def _scopes(value):
    if not isinstance(value, str) or len(value) > 2048 or any(not 32 <= ord(char) <= 126 for char in value):
        raise XeroError("The saved or refreshed Xero permissions are invalid. Reconnect this workspace.")
    parts = value.split()
    if not parts or len(parts) > 32 or len(set(parts)) != len(parts) or any(not re.fullmatch(r"[a-z][a-z0-9._:-]{0,159}", part) for part in parts):
        raise XeroError("The saved or refreshed Xero permissions are invalid. Reconnect this workspace.")
    if not READ_SCOPES | {"offline_access"} <= set(parts):
        raise XeroError("Reconnect Xero with the required read-only account, transaction, payment and Bank Summary permissions and offline access.")
    return " ".join(sorted(parts))


def parse_bank_summary(data, *, account_id, start_date, end_date):
    """Parse the documented BankSummary account UUID and declared columns."""
    account_id = _id(account_id)
    start, end = _day(start_date), _day(end_date)
    reports = data.get("Reports") if isinstance(data, dict) else None
    if not isinstance(data, dict) or _fault(data) or not isinstance(reports, list) or len(reports) != 1:
        raise XeroIncomplete("Xero omitted a complete Bank Summary report.")
    report = reports[0]
    if not isinstance(report, dict) or _fault(report) or report.get("ReportType") != "BankSummary":
        raise XeroIncomplete("Xero returned an unexpected report type.")
    titles = report.get("ReportTitles")
    if not isinstance(titles, list) or any(not isinstance(title, str) for title in titles):
        raise XeroIncomplete("Xero did not establish the report period.")
    # Xero's public example states the requested period in the report titles.
    title_dates = []
    for title in titles:
        for token in re.findall(r"\d{4}-\d\d-\d\d|\d{1,2} [A-Za-z]+ \d{4}", title):
            try:
                parsed = date.fromisoformat(token) if "-" in token else datetime.strptime(token, "%d %B %Y").date()
            except ValueError:
                raise XeroIncomplete("Xero returned an unsupported report period label.") from None
            title_dates.append(parsed)
    if title_dates != [start, end]:
        raise XeroIncomplete("Xero Bank Summary dates do not match the requested period.")
    headings, matched = None, []
    expected = {"bank accounts", "opening balance", "cash received", "cash spent", "closing balance"}
    fx_columns = {"fx", "fx gain", "fx gains", "fx gains/losses", "foreign exchange", "foreign exchange gains/losses"}

    def walk(rows, depth=0):
        nonlocal headings
        if depth > 8 or not isinstance(rows, list):
            raise XeroIncomplete("Xero returned an unsupported report hierarchy.")
        for row in rows:
            if not isinstance(row, dict):
                raise XeroIncomplete("Xero returned a malformed report row.")
            kind = row.get("RowType")
            if kind == "Section":
                walk(row.get("Rows", []), depth + 1)
                continue
            if kind == "SummaryRow":
                continue  # Organisation totals are not selected-account controls.
            cells = row.get("Cells")
            if not isinstance(cells, list) or any(not isinstance(cell, dict) for cell in cells):
                raise XeroIncomplete("Xero returned malformed report columns.")
            if kind == "Header":
                names = [str(cell.get("Value", "")).strip().lower() for cell in cells]
                if headings is not None or len(set(names)) != len(names) or not expected <= set(names) or set(names) - expected - fx_columns:
                    raise XeroIncomplete("Xero Bank Summary columns are ambiguous or unsupported.")
                headings = names
            elif kind == "Row":
                if headings is None or len(cells) != len(headings):
                    raise XeroIncomplete("Xero Bank Summary account controls are incomplete.")
                identifiers = set()
                for cell in cells:
                    attributes = cell.get("Attributes", [])
                    if not isinstance(attributes, list) or any(not isinstance(item, dict) for item in attributes):
                        raise XeroIncomplete("Xero returned ambiguous report account identities.")
                    identifiers.update(_id(item.get("Value")) for item in attributes if str(item.get("Id", "")).lower() in {"account", "accountid"})
                if identifiers == {account_id}:
                    values = dict(zip(headings, (cell.get("Value") for cell in cells)))
                    controls = {key: _money(values[key]) for key in expected - {"bank accounts"}}
                    if any(_money(values[key]) != 0 for key in set(headings) & fx_columns):
                        raise XeroIncomplete("Foreign-exchange bank movements are unsupported.")
                    if controls["cash received"] < 0 or controls["cash spent"] < 0 or controls["opening balance"] + controls["cash received"] - controls["cash spent"] != controls["closing balance"]:
                        raise XeroIncomplete("Xero Bank Summary opening, activity and closing controls disagree.")
                    matched.append(controls)
                elif account_id in identifiers or not identifiers:
                    raise XeroIncomplete("Xero Bank Summary account identities are ambiguous.")
            else:
                raise XeroIncomplete("Xero returned an unsupported report row type.")
    walk(report.get("Rows"))
    if headings is None or len(matched) != 1:
        raise XeroIncomplete("Xero did not provide exactly one selected-account Bank Summary row.")
    return matched[0]


class XeroClient:
    def __init__(self, settings, workspace_id, *, client=None, lock=None, clock=time.time):
        self.settings = settings
        self.workspace_id = workspace_id
        self.client = client
        self.lock = lock or threading.RLock()
        self.clock = clock
        self._begin()

    def _begin(self):
        self.deadline = time.monotonic() + 120
        self.requests = 0

    def _bundle(self):
        bundle = self.settings.get(self.workspace_id, "xero")
        if not isinstance(bundle, dict) or not isinstance(bundle.get("client_id"), str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,199}", bundle["client_id"]) or not _token(bundle.get("access_token"), 16384) or not _token(bundle.get("refresh_token"), 8192):
            raise XeroError("Connect Xero in this workspace first.")
        _scopes(bundle.get("scope"))
        expiry = bundle.get("expires_at")
        if isinstance(expiry, bool) or not isinstance(expiry, (int, float, Decimal)) or not Decimal(str(expiry)).is_finite():
            raise XeroError("The saved Xero connection has invalid expiry metadata. Reconnect it.")
        return bundle

    def _require_bound(self):
        bound = self._bundle().get("bound_company")
        if not isinstance(bound, dict):
            raise XeroError("Select and confirm the Xero organisation for this workspace first.")
        _id(bound.get("id"))
        _currency(bound.get("currency"))
        return bound

    def _http(self, method, url, **kwargs):
        if url not in {TOKEN_URL, CONNECTIONS_URL} and not url.startswith(API_ROOT):
            raise XeroError("This Xero request destination is not allowed.")
        self.requests += 1
        remaining = self.deadline - time.monotonic()
        if remaining <= 0 or self.requests > MAX_REQUESTS:
            raise XeroIncomplete("The Xero read reached its bounded request or time limit. Previous data is unchanged.")
        owned = self.client is None
        client = self.client or httpx.Client(timeout=20, follow_redirects=False, trust_env=False)
        try:
            return client.request(method, url, timeout=min(20, remaining), follow_redirects=False, **kwargs)
        except httpx.HTTPError:
            raise XeroError("Xero could not be reached. Previous financial data is unchanged.") from None
        finally:
            if owned:
                client.close()

    @staticmethod
    def _decode(response, *, connections=False):
        if response.status_code in {401, 403}:
            raise XeroError("Xero authorization was rejected. Reconnect or review organisation permissions.")
        if response.status_code == 429:
            raise XeroError("Xero is limiting requests. Wait before trying again; previous data is unchanged.")
        if not 200 <= response.status_code < 300:
            raise XeroError("Xero could not complete the request. Previous financial data is unchanged.")
        if len(response.content) > MAX_RESPONSE:
            raise XeroIncomplete("Xero returned a response exceeding the supported size.")
        try:
            value = json.loads(response.content, parse_float=Decimal,
                               parse_constant=lambda value: (_ for _ in ()).throw(ValueError()),
                               object_pairs_hook=_unique_pairs)
            _bounded_json(value)
        except (ValueError, TypeError, RecursionError):
            raise XeroIncomplete("Xero returned invalid JSON. Previous data is unchanged.") from None
        if connections:
            if not isinstance(value, list):
                raise XeroIncomplete("Xero returned an incomplete organisation list.")
        elif not isinstance(value, dict) or _fault(value) or value.get("Status", "OK") != "OK":
            raise XeroIncomplete("Xero returned an incomplete or faulted response.")
        return value

    def _refresh(self, force=False):
        bundle = self._bundle()
        expiry = bundle["expires_at"]
        if not force and expiry > self.clock() + 60:
            return bundle
        response = self._http("POST", TOKEN_URL, data={"grant_type": "refresh_token", "refresh_token": bundle["refresh_token"], "client_id": bundle["client_id"]}, headers={"Accept": "application/json"})
        data = self._decode(response)
        lifetime = data.get("expires_in")
        if str(data.get("token_type", "")).lower() != "bearer" or type(lifetime) is not int or not 0 < lifetime <= 86400 or not _token(data.get("access_token"), 16384) or not _token(data.get("refresh_token"), 8192):
            raise XeroError("Xero did not return a complete rotated connection. Reconnect this workspace.")
        replacement = {**bundle, "access_token": data["access_token"], "refresh_token": data["refresh_token"], "expires_at": self.clock() + lifetime}
        if "scope" in data:
            replacement["scope"] = _scopes(data["scope"])
        self.settings.replace(self.workspace_id, "xero", replacement)
        return replacement

    def _get(self, resource, *, tenant_id=None, params=None):
        if resource not in {"connections", "Organisation", "Accounts", "BankTransactions", "Payments", "Reports/BankSummary"}:
            raise XeroError("This Xero resource is not permitted.")
        url = CONNECTIONS_URL if resource == "connections" else API_ROOT + resource
        bundle = self._refresh()
        headers = {"Authorization": "Bearer " + bundle["access_token"], "Accept": "application/json"}
        if resource != "connections":
            headers["xero-tenant-id"] = _id(tenant_id or self._require_bound()["id"])
        response = self._http("GET", url, headers=headers, params=params)
        if response.status_code == 401:
            bundle = self._refresh(force=True)
            headers["Authorization"] = "Bearer " + bundle["access_token"]
            response = self._http("GET", url, headers=headers, params=params)
        return self._decode(response, connections=resource == "connections")

    def _organizations(self):
        data = self._get("connections")
        if len(data) > 100:
            raise XeroIncomplete("The Xero organisation list exceeds supported coverage.")
        found = {}
        for row in data:
            if not isinstance(row, dict):
                raise XeroIncomplete("Xero returned an invalid organisation entry.")
            if row.get("tenantType") != "ORGANISATION":
                continue
            identifier = _id(row.get("tenantId"))
            if identifier in found:
                raise XeroIncomplete("Xero repeated an organisation identity.")
            found[identifier] = {"id": identifier, "name": _text(row.get("tenantName"), 250)}
        return list(found.values())

    def organizations(self):
        with self.lock:
            self._begin()
            return self._organizations()

    def bind(self, tenant_id, *, scope_check=lambda: True, scope_lock=None):
        with self.lock:
            self._begin()
            tenant_id = _id(tenant_id)
            company = next((row for row in self._organizations() if row["id"] == tenant_id), None)
            if company is None:
                raise XeroError("Select a Xero organisation returned by this connection.")
            data = self._get("Organisation", tenant_id=tenant_id).get("Organisations")
            if not isinstance(data, list) or len(data) != 1 or not isinstance(data[0], dict) or _id(data[0].get("OrganisationID")) != tenant_id:
                raise XeroIncomplete("Xero did not establish the selected organisation identity.")
            company = {"id": tenant_id, "name": _text(data[0].get("Name"), 250), "currency": _currency(data[0].get("BaseCurrency"))}
            # Provider RPCs have finished. Keep the captured display scope
            # check and its credential-binding write inside the same Store lock.
            with scope_lock if scope_lock is not None else nullcontext():
                if not scope_check():
                    raise XeroError("The selected workspace changed before Xero organisation binding. Select it again.")
                self.settings.replace(self.workspace_id, "xero", {**self._bundle(), "bound_company": company})
            return company

    def _accounts(self):
        self._require_bound()
        rows = self._get("Accounts").get("Accounts")
        if not isinstance(rows, list) or len(rows) > MAX_ROWS:
            raise XeroIncomplete("Xero returned an incomplete account list.")
        found = {}
        for row in rows:
            if not isinstance(row, dict):
                raise XeroIncomplete("Xero returned a malformed account.")
            identifier = _id(row.get("AccountID"))
            if identifier in found:
                raise XeroIncomplete("Xero repeated an account identity.")
            if row.get("Type") == "BANK" and row.get("Status", "ACTIVE") == "ACTIVE":
                found[identifier] = {"id": identifier, "name": _text(row.get("Name"), 250), "type": "BANK", "currency": _currency(row.get("CurrencyCode"))}
            else:
                found[identifier] = None
        return [row for row in found.values() if row is not None]

    def accounts(self):
        with self.lock:
            self._begin()
            return self._accounts()

    def _paged(self, resource, start, end):
        identifier = "BankTransactionID" if resource == "BankTransactions" else "PaymentID"
        until = end + timedelta(days=1)
        where = f'Date>=DateTime({start.year},{start.month},{start.day}) AND Date<DateTime({until.year},{until.month},{until.day}) AND Status=="AUTHORISED"'
        collected, population, page_count = {}, None, None
        for page in range(1, MAX_ROWS // PAGE_SIZE + 2):
            data = self._get(resource, params={"where": where, "page": page, "pageSize": PAGE_SIZE})
            rows, paging = data.get(resource), data.get("pagination")
            if not isinstance(rows, list) or not isinstance(paging, dict):
                raise XeroIncomplete("Xero omitted required pagination controls.")
            if any(type(paging.get(key)) is not int for key in ("page", "pageSize", "pageCount", "itemCount")):
                raise XeroIncomplete("Xero returned invalid pagination metadata.")
            count, pages = paging["itemCount"], paging["pageCount"]
            if paging["page"] != page or paging["pageSize"] != PAGE_SIZE or not 0 <= count <= MAX_ROWS or pages not in ({0, 1} if count == 0 else {(count + PAGE_SIZE - 1) // PAGE_SIZE}):
                raise XeroIncomplete("Xero pagination controls are inconsistent or exceed supported limits.")
            if population is not None and (population, page_count) != (count, pages):
                raise XeroIncomplete("Xero source counts changed during pagination.")
            population, page_count = count, pages
            if len(rows) != min(PAGE_SIZE, max(0, count - (page - 1) * PAGE_SIZE)):
                raise XeroIncomplete("Xero omitted or repeated a source page.")
            for row in rows:
                if not isinstance(row, dict):
                    raise XeroIncomplete("Xero returned a malformed transaction.")
                key = _id(row.get(identifier))
                if key in collected:
                    raise XeroIncomplete("Xero repeated a transaction identity across pages.")
                if row.get("Status") != "AUTHORISED" or not start <= _transaction_day(row) <= end:
                    raise XeroIncomplete("Xero returned a transaction outside the requested dates or posted status.")
                collected[key] = row
            if page >= pages:
                if len(collected) != count:
                    raise XeroIncomplete("Xero source population is incomplete.")
                return [collected[key] for key in sorted(collected)]
        raise XeroIncomplete("Xero pagination exceeded its bounded page budget.")

    def bank_ledger(self, account_id, start_date, end_date):
        with self.lock:
            self._begin()
            company = self._require_bound()
            account_id, start, end = _id(account_id), _day(start_date), _day(end_date)
            if end < start or (end - start).days > 183:
                raise XeroError("Select an ordered Xero period of at most six months.")
            account = next((row for row in self._accounts() if row["id"] == account_id), None)
            if account is None or account["currency"] != company["currency"]:
                raise XeroIncomplete("Select an active BANK account in the Xero organisation's base currency. Foreign-currency bank accounts are unsupported.")
            began = datetime.now(timezone.utc).isoformat()
            params = {"fromDate": start.isoformat(), "toDate": end.isoformat()}
            before_raw = self._get("Reports/BankSummary", params=params)
            before = parse_bank_summary(before_raw, account_id=account_id, start_date=start.isoformat(), end_date=end.isoformat())
            first = {name: self._paged(name, start, end) for name in ("BankTransactions", "Payments")}
            second = {name: self._paged(name, start, end) for name in ("BankTransactions", "Payments")}
            if _digest(first) != _digest(second):
                raise XeroIncomplete("Xero transactions changed between controlled reads. Retry after the source is stable.")
            after_raw = self._get("Reports/BankSummary", params=params)
            after = parse_bank_summary(after_raw, account_id=account_id, start_date=start.isoformat(), end_date=end.isoformat())
            if before != after:
                raise XeroIncomplete("Xero Bank Summary controls changed during the read.")
            records = []
            for resource, rows in first.items():
                for row in rows:
                    account_ref = row.get("BankAccount" if resource == "BankTransactions" else "Account")
                    if not isinstance(account_ref, dict):
                        raise XeroIncomplete("Xero returned a transaction without a bank-account identity.")
                    if _id(account_ref.get("AccountID")) != account_id:
                        continue
                    is_bank = resource == "BankTransactions"
                    kind = row.get("Type" if is_bank else "PaymentType")
                    signs = BANK_SIGNS if is_bank else PAYMENT_SIGNS
                    if kind not in signs:
                        raise XeroIncomplete("An unsupported Xero bank movement type prevents a controlled import.")
                    if is_bank and _currency(row.get("CurrencyCode")) != company["currency"]:
                        raise XeroIncomplete("Xero bank-transaction currency disagrees with the selected account.")
                    # BankAmount is explicitly in the bank account's currency;
                    # Amount belongs to the paid document and is never a fallback.
                    value = _money(row.get("Total" if is_bank else "BankAmount"))
                    if value <= 0:
                        raise XeroIncomplete("Xero returned a nonpositive bank movement requiring unsupported treatment.")
                    identifier = _id(row.get("BankTransactionID" if is_bank else "PaymentID"))
                    label_parts = [kind]
                    for key in ("Reference", "InvoiceNumber"):
                        if row.get(key):
                            label_parts.append(_text(row[key], 500))
                    records.append({"id": f"xero-{company['id']}-{resource.lower()}-{identifier}",
                                    "date": _transaction_day(row).isoformat(),
                                    "description": " | ".join(label_parts), "amount": format(value * signs[kind], ".2f"),
                                    "source_ids": [identifier], "source_ordinal": 0})
            records.sort(key=lambda row: (row["date"], row["id"]))
            if len(records) > MAX_ROWS:
                raise XeroIncomplete("The Xero account exceeds the supported transaction budget.")
            for ordinal, row in enumerate(records, 1):
                row["source_ordinal"] = ordinal
            received = sum((_money(row["amount"]) for row in records if _money(row["amount"]) > 0), Decimal("0.00"))
            spent = -sum((_money(row["amount"]) for row in records if _money(row["amount"]) < 0), Decimal("0.00"))
            if received != before["cash received"] or spent != before["cash spent"]:
                raise XeroIncomplete("Xero transaction detail does not cover Bank Summary gross receipts and payments. Unsupported expenses, conversion or other adjustments may require another source; previous data is unchanged.")
            if before["opening balance"] + received - spent != before["closing balance"]:
                raise XeroIncomplete("Xero opening plus supported movement does not equal closing.")
            return {
                "rows": records, "opening_balance": format(before["opening balance"], ".2f"),
                "closing_balance": format(before["closing balance"], ".2f"), "currency": company["currency"],
                "source": {"provider": "Xero", "tenant_id": company["id"], "account_id": account_id,
                           "start_date": start.isoformat(), "end_date": end.isoformat(),
                           "report": "BankSummary + BankTransactions + Payments", "complete": True,
                           "universal_ledger": False, "coverage": "Supported base-currency BANK movements only; not the Journals endpoint.",
                           "unsupported": ["classic expense-claim payments", "conversion adjustments", "bank reconciliation adjustments", "foreign-currency bank accounts"],
                           "controls": {key: format(value, ".2f") for key, value in before.items()},
                           "activity": format(received - spent, ".2f"), "row_count": len(records),
                           "population_counts": {name: len(rows) for name, rows in first.items()},
                           "digest": _digest([first, before]), "report_digests": [_digest(before_raw), _digest(after_raw)],
                           "observed_start": began, "observed_end": datetime.now(timezone.utc).isoformat(),
                           "observation": "Repeated stable provider reads and independent report controls; not an atomic vendor snapshot."},
            }
