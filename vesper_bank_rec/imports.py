"""One preview/certification path for bank CSV, ledger CSV and normalized QBO reads."""

import csv
import hashlib
import io
from decimal import Decimal
from .models import DomainError, amount, day, digest, money, label, now, uid

FIELDS = ("date", "description", "amount", "account", "currency")
MAX_ROWS = 10000


def preview_csv(session, side, payload):
    if side not in ("bank", "ledger"):
        raise DomainError("Choose bank or ledger.")
    raw = payload.get("csv", "")
    if not isinstance(raw, str) or not raw.strip() or len(raw.encode()) > 2_000_000:
        raise DomainError("Provide a UTF-8 CSV up to 2 MB.")
    source = label(payload.get("source", ""), "Source description", 500)
    opening, closing = amount(payload.get("opening")), amount(payload.get("closing"))
    mapping = payload.get("mapping") or {key: key for key in FIELDS}
    if (
        not isinstance(mapping, dict)
        or set(mapping) != set(FIELDS)
        or not all(isinstance(x, str) for x in mapping.values())
    ):
        raise DomainError("Mapping must name date, description, amount, account and currency columns.")
    try:
        records = list(csv.reader(io.StringIO(raw.lstrip("\ufeff")), strict=True))
        headers = records[0] if records else []
        if len(headers) != len(set(headers)):
            raise DomainError("Duplicate CSV column names are not supported.")
        missing = [column for column in mapping.values() if column not in headers]
        if missing:
            raise DomainError("Missing CSV columns: " + ", ".join(missing))
        parsed = [
            dict(zip(headers, values)) if len(values) == len(headers) else {None: values}
            for values in records[1:]
        ]
    except (csv.Error, UnicodeError) as exc:
        raise DomainError(f"CSV could not be read: {exc}") from exc
    if len(parsed) > MAX_ROWS:
        raise DomainError("The CSV exceeds 10,000 records. Narrow the period.")
    source_hash = hashlib.sha256(raw.encode()).hexdigest()
    rows, errors, external = [], [], {}
    for ordinal, record in enumerate(parsed, 1):
        try:
            if None in record or any(v is None for v in record.values()):
                raise DomainError("Unexpected column count.")
            normalized = {key: record[column] for key, column in mapping.items()}
            row_date = day(normalized["date"].strip())
            if not session["period_start"] <= row_date <= session["period_end"]:
                raise DomainError("Date falls outside the selected period.")
            if normalized["account"].strip() != session["account_id"]:
                raise DomainError("Account does not equal the selected account ID.")
            if normalized["currency"].strip().upper() != session["currency"]:
                raise DomainError("Currency does not equal the selected currency.")
            description = label(normalized["description"], "Description", 2000)
            signed = amount(normalized["amount"])
            if money(signed) == 0:
                raise DomainError(
                    "Zero-value transactions are not supported; remove them with a documented source correction."
                )
            external_id = (record.get("external_id") or "").strip()
            identity = (row_date, description, signed)
            if external_id and external_id in external and external[external_id] != identity:
                raise DomainError("A source ID has conflicting transaction values.")
            if external_id:
                external[external_id] = identity
            rows.append(
                {
                    "id": f"{side}_{digest([source_hash, ordinal, identity])[:24]}",
                    "date": row_date,
                    "description": description,
                    "amount": signed,
                    "ordinal": ordinal,
                    "source_hash": source_hash,
                    "external_id": external_id,
                    "origin_side": side,
                    "account_id": session["account_id"],
                    "currency": session["currency"],
                }
            )
        except DomainError as exc:
            errors.append({"ordinal": ordinal, "message": str(exc)})
    activity = sum((money(row["amount"]) for row in rows), Decimal("0"))
    computed = money(opening) + activity
    if computed != money(closing):
        errors.append(
            {
                "ordinal": None,
                "message": f"Opening {opening} + activity {amount(activity)} = {amount(computed)}, which differs from closing {closing}.",
            }
        )
    return {
        "id": uid("preview"),
        "side": side,
        "source": source,
        "source_hash": source_hash,
        "opening": opening,
        "closing": closing,
        "activity": amount(activity),
        "rows": rows,
        "total_rows": len(parsed),
        "valid_rows": len(rows),
        "invalid_rows": len(errors),
        "errors": errors,
        "ready": not errors,
        "observed_at": now(),
        "account_id": session["account_id"],
        "currency": session["currency"],
        "period_start": session["period_start"],
        "period_end": session["period_end"],
        "input_version": session["input_version"],
    }


def qbo_preview(session, result):
    # All normalized vendor rows pass the exact same account/date/money/control path.
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow([*FIELDS, "external_id"])
    for row in result["rows"]:
        writer.writerow(
            [
                row["date"],
                row["description"],
                row["amount"],
                session["account_id"],
                result["currency"],
                row.get("id", ""),
            ]
        )
    preview = preview_csv(
        session,
        "ledger",
        {
            "csv": stream.getvalue(),
            "opening": result["opening_balance"],
            "closing": result["closing_balance"],
            "source": "QuickBooks GeneralLedger read",
        },
    )
    preview["provider_provenance"] = result.get("source", {})
    for row, provider_row in zip(preview["rows"], result["rows"]):
        row["provider_source_ids"] = provider_row.get("source_ids", [])
        row["provider_ordinal"] = provider_row.get("source_ordinal", row["ordinal"])
    return preview
