"""Shared output constants and formatting/validation grammars."""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext
from typing import Any


OUTPUT_COLUMNS = [
    "request_id",
    "amount_safe_to_pay",
    "affordability_status",
    "recommended_payment_method",
    "payment_plan",
    "earliest_date_for_full_payment",
    "spending_changes_needed",
    "decision_explanation",
]

AFFORDABILITY_STATUSES = frozenset(
    {
        "affordable_now",
        "affordable_with_plan",
        "affordable_later",
        "not_affordable",
    }
)

RECOMMENDED_PAYMENT_METHODS = frozenset(
    {
        "full_payment",
        "partial_payment",
        "installments",
        "wait",
        "not_recommended",
    }
)

# Compatibility aliases make the allowed-value names explicit at call sites.
ALLOWED_AFFORDABILITY_STATUSES = AFFORDABILITY_STATUSES
ALLOWED_RECOMMENDED_PAYMENT_METHODS = RECOMMENDED_PAYMENT_METHODS

FLEXIBILITY_STOPPABLE = frozenset({"stoppable", "reducible_or_stoppable"})
FLEXIBILITY_REDUCIBLE = frozenset({"reducible", "reducible_or_stoppable"})

# Amounts in plans and spending changes accept either plain digits or comma
# thousands separators, with an optional decimal fraction.
_AMOUNT_TOKEN = r"(?:0|[1-9]\d{0,2}(?:,\d{3})+|[1-9]\d*)(?:\.\d+)?"
_EVENT_ID_TOKEN = r"event_[A-Za-z0-9_-]+"
_DATE_TOKEN = r"\d{4}-\d{2}-\d{2}"

PAYMENT_PLAN_GRAMMAR = (
    f"none | {_DATE_TOKEN}:{_AMOUNT_TOKEN}"
    f"(?:|{_DATE_TOKEN}:{_AMOUNT_TOKEN})*"
)
SPENDING_CHANGES_GRAMMAR = (
    f"none | stop:{_EVENT_ID_TOKEN}"
    f"(?:|stop:{_EVENT_ID_TOKEN}|reduce_to:{_EVENT_ID_TOKEN}:{_AMOUNT_TOKEN})"
)

PAYMENT_PLAN_PATTERN = re.compile(
    rf"^(?:none|{_DATE_TOKEN}:{_AMOUNT_TOKEN}(?:\|{_DATE_TOKEN}:{_AMOUNT_TOKEN})*)$"
)
SPENDING_CHANGES_PATTERN = re.compile(
    rf"^(?:none|(?:stop:{_EVENT_ID_TOKEN}|reduce_to:{_EVENT_ID_TOKEN}:{_AMOUNT_TOKEN})"
    rf"(?:\|(?:stop:{_EVENT_ID_TOKEN}|reduce_to:{_EVENT_ID_TOKEN}:{_AMOUNT_TOKEN})){{0,2}})$"
)

# Short aliases for callers that prefer regex terminology.
PAYMENT_PLAN_RE = PAYMENT_PLAN_PATTERN
SPENDING_CHANGES_RE = SPENDING_CHANGES_PATTERN


def _as_decimal(value: Any) -> Decimal:
    if isinstance(value, Decimal):
        amount = value
    else:
        try:
            amount = Decimal(str(value))
        except (InvalidOperation, ValueError, TypeError) as exc:
            raise ValueError(f"Invalid amount: {value!r}") from exc

    if not amount.is_finite():
        raise ValueError(f"Amount must be finite: {value!r}")
    return amount


def _round_to_two_decimals(amount: Decimal) -> Decimal:
    with localcontext() as context:
        context.prec = max(28, len(amount.as_tuple().digits) + 8)
        return amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def format_csv_amount(value: Any) -> str:
    """Return a plain, unpadded decimal string for CSV output.

    This matches the ``sample_requests.csv`` representation of
    ``amount_safe_to_pay``: ``17229139.2``, ``603.3``, and ``28820``.
    Thousands separators and unnecessary trailing zeroes are omitted.
    """

    amount = _as_decimal(value)
    text = format(amount, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in {"", "-0"} else text


def format_plan_amount(value: Any) -> str:
    """Return the ungrouped amount format used by ``payment_plan``.

    A fractional value is rounded to and displayed with exactly two decimal
    places; an integral value has no decimal point. This matches plan entries
    such as ``620.40``, ``996.60``, ``15952906.67``, and ``25256``.
    """

    amount = _as_decimal(value)
    if amount == amount.to_integral_value():
        return format(amount, "f").split(".", 1)[0]
    return format(_round_to_two_decimals(amount), ".2f")


def format_display_amount(value: Any) -> str:
    """Return the grouped amount format used in explanation prose.

    Fractional values have exactly two decimal places and integral values have
    none. Examples from ``decision_explanation`` include ``25,256`` and
    ``15,952,906.67``.
    """

    amount = _as_decimal(value)
    if amount == amount.to_integral_value():
        return f"{int(amount):,}"
    return f"{_round_to_two_decimals(amount):,.2f}"
