"""Exact values and canonical identities for the local reconciliation workflow."""

from __future__ import annotations
import hashlib
import json
import re
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from uuid import uuid4


class DomainError(ValueError):
    pass


def money(value) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)):
        raise DomainError("Money must be an exact decimal string, with at most two decimal places.")
    text = str(value).strip()
    if not re.fullmatch(r"-?\d+(?:\.\d{1,2})?", text):
        raise DomainError(
            "Use a signed decimal amount with at most two decimal places, without commas or currency symbols."
        )
    try:
        result = Decimal(text)
    except InvalidOperation as exc:
        raise DomainError("Invalid money.") from exc
    if not result.is_finite() or abs(result) > Decimal("999999999999.99"):
        raise DomainError("Amount is outside the supported range.")
    return result.quantize(Decimal(".01"))


def amount(value) -> str:
    return format(money(value), ".2f")


def day(value) -> str:
    if not isinstance(value, str):
        raise DomainError("Dates must use YYYY-MM-DD.")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise DomainError("Dates must use YYYY-MM-DD.") from exc
    if parsed.isoformat() != value:
        raise DomainError("Dates must use YYYY-MM-DD.")
    return value


def label(value, name="Value", limit=160):
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > limit:
        raise DomainError(f"{name} must contain 1–{limit} characters.")
    return value.strip()


def evidence(value):
    return label(value, "Evidence reference / review note", 2000)


def uid(prefix):
    return f"{prefix}_{uuid4().hex}"


def digest(value):
    data = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(data.encode()).hexdigest()


def now():
    return datetime.now(timezone.utc).isoformat()
