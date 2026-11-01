"""Deterministic decision-explanation templates."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Iterable

try:
    from .schema import format_display_amount
except ImportError:  # pragma: no cover - direct script execution
    from schema import format_display_amount


def _amount(currency: str, value: Decimal) -> str:
    return f"{currency} {format_display_amount(value)}"


def _date(value: date) -> str:
    return f"{value.day} {value.strftime('%B %Y')}"


def _changes_text(changes: Iterable[object], currency: str) -> str:
    clauses = []
    for change in changes:
        if change.kind == "stop":
            clauses.append(f"stop the {change.series.description.lower()}")
        else:
            clauses.append(
                f"reduce the {change.series.description.lower()} to "
                f"{_amount(currency, change.new_amount)}"
            )
    if clauses:
        clauses[0] = clauses[0][0].upper() + clauses[0][1:]
    if len(clauses) == 1:
        return clauses[0]
    if len(clauses) == 2:
        return f"{clauses[0]} and {clauses[1]}"
    return ", ".join(clauses[:-1]) + f", and {clauses[-1]}"


def full_payment_template(
    currency: str,
    amount: Decimal,
    minimum: Decimal,
) -> str:
    return f"Pay {_amount(currency, amount)} today. This leaves at least {_amount(currency, minimum)} available over the next 90 days."


def installments_template(
    currency: str,
    number_of_payments: int,
    payment_amount: Decimal,
    first_payment_date: date,
    minimum: Decimal,
) -> str:
    return (
        f"Use {number_of_payments} installments of {_amount(currency, payment_amount)}, "
        f"starting {_date(first_payment_date)}. This leaves at least "
        f"{_amount(currency, minimum)} available."
    )


def wait_template(
    currency: str,
    requested_amount: Decimal,
    payment_date: date,
    desired_completion_date: date,
    minimum: Decimal,
) -> str:
    if payment_date == desired_completion_date:
        return (
            f"Pay {_amount(currency, requested_amount)} in full on {_date(payment_date)}. "
            f"Paying earlier would take the balance below the {_amount(currency, minimum)} minimum."
        )
    if payment_date < desired_completion_date:
        return (
            f"Wait until {_date(payment_date)}, then pay {_amount(currency, requested_amount)} in full. "
            f"Paying sooner would put the {_amount(currency, minimum)} minimum at risk."
        )
    return (
        f"Pay {_amount(currency, requested_amount)} in full on {_date(payment_date)}. "
        f"Paying earlier would take the balance below the {_amount(currency, minimum)} minimum."
    )


def partial_payment_template(
    currency: str,
    first_amount: Decimal,
    second_amount: Decimal,
    second_date: date,
    minimum: Decimal,
) -> str:
    return (
        f"Pay {_amount(currency, first_amount)} today and the remaining "
        f"{_amount(currency, second_amount)} on {_date(second_date)}. "
        f"This completes the full request and keeps the {_amount(currency, minimum)} minimum protected."
    )


def not_recommended_deadline_template(
    currency: str,
    desired_completion_date: date,
    minimum: Decimal,
) -> str:
    return (
        f"Do not make this payment by {_date(desired_completion_date)}. None of the available "
        f"options keeps the {_amount(currency, minimum)} minimum protected."
    )


def not_recommended_request_template(
    currency: str,
    requested_amount: Decimal,
    amount_safe: Decimal,
) -> str:
    return (
        f"Do not proceed with the {_amount(currency, requested_amount)} request. "
        f"Although {_amount(currency, amount_safe)} is available today, the full amount "
        "cannot be completed safely within 90 days."
    )


def payment_with_changes_template(
    currency: str,
    changes: Iterable[object],
    method: str,
    payments: tuple[tuple[date, Decimal], ...],
    minimum: Decimal,
) -> str:
    change_text = _changes_text(changes, currency)
    if method == "full_payment":
        plain = full_payment_template(currency, payments[0][1], minimum)
    elif method == "installments":
        plain = installments_template(
            currency, len(payments), payments[0][1], payments[0][0], minimum
        )
    elif method == "partial_payment":
        plain = partial_payment_template(
            currency,
            payments[0][1],
            payments[1][1],
            payments[1][0],
            minimum,
        )
    else:
        plain = wait_template(
            currency,
            payments[-1][1],
            payments[-1][0],
            payments[-1][0],
            minimum,
        )
    plain = plain.replace(" available over the next 90 days.", " available.")
    return f"{change_text}, then {plain[0].lower()}{plain[1:]}"


def render_explanation(
    request,
    profile,
    amount_safe: Decimal,
    plan,
    payment_option_methods: Iterable[str] = (),
) -> str:
    currency = profile.home_currency
    minimum = profile.minimum_balance_to_keep
    if plan is None:
        user_methods = {
            method.strip()
            for method in profile.payment_methods_user_will_consider.split("|")
            if method.strip()
        }
        option_methods = set(payment_option_methods)
        if request.allows_partial_payment and not (user_methods & option_methods):
            return not_recommended_request_template(
                currency, request.requested_amount, amount_safe
            )
        return not_recommended_deadline_template(
            currency, request.desired_completion_date, minimum
        )
    if plan.changes:
        return payment_with_changes_template(
            currency, plan.changes, plan.method, plan.payments, minimum
        )
    if plan.method == "full_payment":
        return full_payment_template(
            currency, plan.payments[0][1], minimum
        )
    if plan.method == "installments":
        return installments_template(
            currency,
            len(plan.payments),
            plan.payments[0][1],
            plan.payments[0][0],
            minimum,
        )
    if plan.method == "partial_payment":
        return partial_payment_template(
            currency,
            plan.payments[0][1],
            plan.payments[1][1],
            plan.payments[1][0],
            minimum,
        )
    return wait_template(
        currency,
        request.requested_amount,
        plan.payments[0][0],
        request.desired_completion_date,
        minimum,
    )
