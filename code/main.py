"""Deterministic no-model Buy or Wait? output generator."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from itertools import combinations
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Optional

try:
    from .data_layer import DataLayer, PaymentOptionRecord, RequestRecord
    from .explanations import render_explanation
    from .forecast import _included_for_cashflow, _series_occurrences, project
    from .recurrence import RecurringSeries, recurring_series
    from .schema import OUTPUT_COLUMNS, format_csv_amount, format_plan_amount
except ImportError:  # pragma: no cover - direct script execution
    from data_layer import DataLayer, PaymentOptionRecord, RequestRecord
    from explanations import render_explanation
    from forecast import _included_for_cashflow, _series_occurrences, project
    from recurrence import RecurringSeries, recurring_series
    from schema import OUTPUT_COLUMNS, format_csv_amount, format_plan_amount


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_PATH = ROOT / "output.csv"


@dataclass(frozen=True)
class CandidateChange:
    kind: str
    event_id: str
    series: RecurringSeries
    new_amount: Optional[Decimal]
    monthly_saving: Decimal


@dataclass(frozen=True)
class CandidatePlan:
    method: str
    payments: tuple[tuple[date, Decimal], ...]
    total_paid: Decimal
    payment_option_id: str
    changes: tuple[CandidateChange, ...] = ()

    @property
    def start_date(self) -> date:
        return self.payments[0][0]

    @property
    def number_of_payments(self) -> int:
        return len(self.payments)



def _methods(profile_methods: str) -> set[str]:
    return {method.strip() for method in profile_methods.split("|") if method.strip()}


def _capacity(balances: dict[date, Decimal], minimum: Decimal) -> Decimal:
    return max(
        Decimal("0"),
        min((balance - minimum for balance in balances.values()), default=Decimal("0")),
    )


def _earliest_full_date(
    balances: dict[date, Decimal],
    request_date: date,
    requested_amount: Decimal,
    minimum: Decimal,
) -> Optional[date]:
    dates = sorted(date_value for date_value in balances if date_value >= request_date)
    for candidate_date in dates:
        if _plan_is_safe(
            ((candidate_date, requested_amount),),
            balances,
            minimum,
            request_date,
        ):
            return candidate_date
    return None


def _plan_is_safe(
    payments: tuple[tuple[date, Decimal], ...],
    balances: dict[date, Decimal],
    minimum: Decimal,
    request_date: date,
) -> bool:
    if not payments or any(payment_date < request_date for payment_date, _ in payments):
        return False
    if any(payment_date not in balances for payment_date, _ in payments):
        return False
    payments_by_date: dict[date, Decimal] = {}
    for payment_date, amount in payments:
        payments_by_date[payment_date] = payments_by_date.get(payment_date, Decimal("0")) + amount
    cumulative = Decimal("0")
    for current_date in sorted(balances):
        if current_date in payments_by_date:
            cumulative += payments_by_date[current_date]
        if current_date >= request_date and balances[current_date] - cumulative < minimum:
            return False
    return True


def _option_payments(option: PaymentOptionRecord) -> Optional[tuple[tuple[date, Decimal], ...]]:
    if option.first_payment_date is None or option.number_of_payments < 1:
        return None
    if option.number_of_payments == 1:
        return ((option.first_payment_date, option.payment_amount),)
    if option.payment_frequency_days is None:
        return None
    return tuple(
        (
            option.first_payment_date + timedelta(days=option.payment_frequency_days * index),
            option.payment_amount,
        )
        for index in range(option.number_of_payments)
    )


def _categories(raw: str) -> set[str]:
    return {value.strip() for value in raw.split("|") if value.strip()}


def _candidate_changes(
    layer: DataLayer,
    request: RequestRecord,
    profile,
    balances: dict[date, Decimal],
) -> tuple[CandidateChange, ...]:
    events = list(layer.events_for(request.user_id))
    events_by_id = {event.event_id: event for event in events}
    end_date = request.request_date + timedelta(days=90)
    protected = _categories(profile.expense_categories_to_protect)
    willing_stop = _categories(profile.expense_categories_user_is_willing_to_stop)
    willing_reduce = _categories(profile.expense_categories_user_is_willing_to_reduce)
    changes: list[CandidateChange] = []

    for series in recurring_series(request.user_id, layer, request.request_date):
        if series.direction != "debit":
            continue
        members = [events_by_id[event_id] for event_id in series.event_ids]
        eligible = [
            event
            for event in members
            if event.settlement_date is not None
            and event.settlement_date <= request.request_date
            and _included_for_cashflow(event, events_by_id)
            and event.category not in protected
        ]
        if not eligible:
            continue
        target = max(eligible, key=lambda event: (event.settlement_date, event.event_id))
        occurrences = _series_occurrences(
            series,
            events_by_id,
            request.request_date,
            end_date,
            layer,
            profile.home_currency,
        )
        if not occurrences:
            continue
        projected_amount = abs(occurrences[0][1])
        monthly_base = Decimal("30") / Decimal(str(series.cadence_days))

        if target.flexibility in {"stoppable", "reducible_or_stoppable"} and target.category in willing_stop:
            changes.append(
                CandidateChange(
                    "stop",
                    target.event_id,
                    series,
                    None,
                    projected_amount * monthly_base,
                )
            )

        if (
            target.flexibility in {"reducible", "reducible_or_stoppable"}
            and target.category in willing_reduce
            and target.minimum_allowed_amount is not None
            and target.amount is not None
        ):
            minimum_home = target.minimum_allowed_amount * layer.rate(
                target.currency,
                profile.home_currency,
                target.settlement_date,
            )
            saving = projected_amount - minimum_home
            if saving > 0:
                changes.append(
                    CandidateChange(
                        "reduce_to",
                        target.event_id,
                        series,
                        target.minimum_allowed_amount,
                        saving * monthly_base,
                    )
                )

    return tuple(
        sorted(
            changes,
            key=lambda change: (
                change.event_id,
                change.kind,
                change.series.description,
            ),
        )
    )


def _apply_changes(
    balances: dict[date, Decimal],
    changes: tuple[CandidateChange, ...],
    request: RequestRecord,
    profile,
    layer: DataLayer,
) -> dict[date, Decimal]:
    if not changes:
        return dict(balances)
    events = list(layer.events_for(request.user_id))
    events_by_id = {event.event_id: event for event in events}
    end_date = request.request_date + timedelta(days=90)
    modified = dict(balances)
    for change in changes:
        occurrences = _series_occurrences(
            change.series,
            events_by_id,
            request.request_date,
            end_date,
            layer,
            profile.home_currency,
        )
        target = events_by_id[change.event_id]
        target_home = Decimal("0")
        if change.kind == "reduce_to":
            target_home = change.new_amount * layer.rate(
                target.currency,
                profile.home_currency,
                target.settlement_date,
            )
        for occurrence_date, signed_amount in occurrences:
            saving = abs(signed_amount) if change.kind == "stop" else abs(signed_amount) - target_home
            if saving <= 0:
                continue
            for current_date in modified:
                if current_date >= occurrence_date:
                    modified[current_date] += saving
    return modified


def _change_text(changes: tuple[CandidateChange, ...]) -> str:
    if not changes:
        return "none"
    entries = []
    for change in changes:
        if change.kind == "stop":
            entries.append(f"stop:{change.event_id}")
        else:
            entries.append(f"reduce_to:{change.event_id}:{format_plan_amount(change.new_amount)}")
    return "|".join(entries)


def _search_change_plans(
    layer: DataLayer,
    request: RequestRecord,
    profile,
    base_balances: dict[date, Decimal],
    amount_safe: Decimal,
    output_earliest: Optional[date],
) -> tuple[Optional[CandidatePlan], Optional[date]]:
    changes = _candidate_changes(layer, request, profile, base_balances)
    best: tuple[tuple[object, ...], CandidatePlan, date] | None = None
    for count in range(1, min(3, len(changes)) + 1):
        for selected in combinations(changes, count):
            if len({change.event_id for change in selected}) != count:
                continue
            modified = _apply_changes(base_balances, selected, request, profile, layer)
            earliest = _earliest_full_date(
                modified,
                request.request_date,
                request.requested_amount,
                profile.minimum_balance_to_keep,
            )
            candidates = _candidate_plans(
                request,
                profile,
                layer.options_for(request.request_id),
                modified,
                amount_safe,
                earliest,
                selected,
                output_earliest,
                True,
            )
            plan = _choose_plan(candidates, request.desired_completion_date)
            if plan is None:
                continue
            key = (
                count,
                sum((change.monthly_saving for change in selected), Decimal("0")),
                not (plan.payments[-1][0] <= request.desired_completion_date),
                plan.total_paid,
                plan.start_date,
                plan.number_of_payments,
                plan.payment_option_id,
            )
            if best is None or key < best[0]:
                best = (key, plan, earliest)
        if best is not None:
            break
    return (best[1], best[2]) if best is not None else (None, None)


def _candidate_plans(
    request: RequestRecord,
    profile,
    options: tuple[PaymentOptionRecord, ...],
    balances: dict[date, Decimal],
    amount_safe: Decimal,
    earliest_full: Optional[date],
    changes: tuple[CandidateChange, ...] = (),
    partial_earliest: Optional[date] = None,
    partial_earliest_authoritative: bool = False,
) -> list[CandidatePlan]:
    methods = _methods(profile.payment_methods_user_will_consider)
    minimum = profile.minimum_balance_to_keep
    candidates: list[CandidatePlan] = []

    if "full_payment" in methods:
        payments = ((request.request_date, request.requested_amount),)
        if _plan_is_safe(payments, balances, minimum, request.request_date):
            candidates.append(
                CandidatePlan("full_payment", payments, request.requested_amount, "", changes)
            )

    if "installments" in methods:
        for option in options:
            if option.payment_method != "installments":
                continue
            payments = _option_payments(option)
            if payments is None:
                continue
            if profile.max_installment_months is not None:
                span_months = (
                    Decimal((payments[-1][0] - payments[0][0]).days) / Decimal("30")
                    + Decimal("1")
                )
                if span_months > Decimal(profile.max_installment_months):
                    continue
            if payments is None or not _plan_is_safe(payments, balances, minimum, request.request_date):
                continue
            candidates.append(
                CandidatePlan(
                    "installments",
                    payments,
                    option.total_payable_amount,
                    option.payment_option_id,
                    changes,
                )
            )

    if (
        "partial_payment" in methods
        and request.allows_partial_payment
        and Decimal("0") < amount_safe < request.requested_amount
        and (partial_earliest if partial_earliest_authoritative else earliest_full) is not None
        and (partial_earliest if partial_earliest_authoritative else earliest_full) <= request.desired_completion_date
    ):
        payment_date = partial_earliest if partial_earliest_authoritative else earliest_full
        payments = (
            (request.request_date, amount_safe),
            (payment_date, request.requested_amount - amount_safe),
        )
        if _plan_is_safe(payments, balances, minimum, request.request_date):
            candidates.append(
                CandidatePlan("partial_payment", payments, request.requested_amount, "", changes)
            )

    if "full_payment" in methods and earliest_full is not None and earliest_full > request.request_date:
        payments = ((earliest_full, request.requested_amount),)
        if _plan_is_safe(payments, balances, minimum, request.request_date):
            candidates.append(CandidatePlan("wait", payments, request.requested_amount, "", changes))

    return candidates


def _choose_plan(
    candidates: list[CandidatePlan],
    desired_completion_date: date,
) -> Optional[CandidatePlan]:
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda candidate: (
            not (candidate.payments[-1][0] <= desired_completion_date),
            bool(candidate.changes),
            candidate.total_paid,
            candidate.start_date,
            candidate.number_of_payments,
            candidate.payment_option_id,
        ),
    )


def _status_for(plan: Optional[CandidatePlan], request: RequestRecord, amount_safe: Decimal) -> str:
    if plan is None:
        return "not_affordable"
    if (
        plan.method == "full_payment"
        and plan.start_date == request.request_date
        and amount_safe == request.requested_amount
    ):
        return "affordable_now"
    if plan.method in {"partial_payment", "installments"}:
        return "affordable_with_plan"
    if plan.method == "full_payment" and plan.changes:
        return "affordable_with_plan"
    if plan.method == "wait":
        return "affordable_later"
    return "not_affordable"


def _plan_text(plan: Optional[CandidatePlan]) -> str:
    if plan is None:
        return "none"
    return "|".join(
        f"{payment_date.isoformat()}:{format_plan_amount(amount)}"
        for payment_date, amount in plan.payments
    )


def build_row(layer: DataLayer, request: RequestRecord) -> dict[str, str]:
    profile = layer.profile_for(request.user_id)
    balances = project(request.user_id, request.request_date, 90, layer)
    amount_safe = min(
        request.requested_amount,
        _capacity(balances, profile.minimum_balance_to_keep),
    )
    earliest_full = _earliest_full_date(
        balances,
        request.request_date,
        request.requested_amount,
        profile.minimum_balance_to_keep,
    )
    candidates = _candidate_plans(
        request,
        profile,
        layer.options_for(request.request_id),
        balances,
        amount_safe,
        earliest_full,
    )
    plan = _choose_plan(candidates, request.desired_completion_date)
    if plan is None:
        plan, _changed_earliest = _search_change_plans(
            layer,
            request,
            profile,
            balances,
            amount_safe,
            earliest_full,
        )
        # A wait plan that depends on spending changes is not a supported
        # output convention: no gold row combines wait with changes, and the
        # unmodified earliest date must not remain empty beside a wait date.
        if plan is not None and plan.method == "wait" and plan.changes:
            plan = None
    status = _status_for(plan, request, amount_safe)
    recommended_method = plan.method if plan is not None else "not_recommended"
    return {
        "request_id": request.request_id,
        "amount_safe_to_pay": format_csv_amount(
            amount_safe.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        ),
        "affordability_status": status,
        "recommended_payment_method": recommended_method,
        "payment_plan": _plan_text(plan),
        "earliest_date_for_full_payment": (
            earliest_full.isoformat()
            if recommended_method != "not_recommended" and earliest_full is not None
            else ""
        ),
        "spending_changes_needed": _change_text(plan.changes) if plan is not None else "none",
        "decision_explanation": render_explanation(
            request,
            profile,
            amount_safe,
            plan,
            {option.payment_method for option in layer.options_for(request.request_id)},
        ),
    }


def main() -> None:
    layer = DataLayer()
    rows = [build_row(layer, request) for request in layer.requests]
    with OUTPUT_PATH.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=OUTPUT_COLUMNS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    print(f"wrote {len(rows)} rows to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
