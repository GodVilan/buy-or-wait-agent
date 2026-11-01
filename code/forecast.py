"""Deterministic cash-balance projection utilities.

This module projects balances only. It does not choose payment methods, make
purchase decisions, write explanations, or call models.
"""

from __future__ import annotations

from calendar import monthrange
from collections import Counter, defaultdict
from datetime import date, timedelta
from decimal import Decimal
from typing import Optional

try:  # Supports both ``python code/forecast.py`` and package-style callers.
    from .data_layer import DataLayer, FinancialEventRecord
    from .message_deltas import MessageDelta, MessageDeltaExtractor
    from .recurrence import RecurringSeries, recurring_series
except ImportError:  # pragma: no cover - exercised by direct script execution.
    from data_layer import DataLayer, FinancialEventRecord
    from message_deltas import MessageDelta, MessageDeltaExtractor
    from recurrence import RecurringSeries, recurring_series


EXCLUDED_STATUSES = frozenset({"failed", "cancelled"})
EXCLUDED_EVENT_TYPES = frozenset({"investment_valuation"})
_MESSAGE_DELTA_EXTRACTOR = MessageDeltaExtractor()


def _message_deltas_for_user(
    user_id: str, layer: DataLayer
) -> tuple[MessageDelta, ...]:
    """Load only the forecast-supported delta kinds for one user."""

    deltas: list[MessageDelta] = []
    for message in layer.messages:
        if message.user_id != user_id or message.related_event_id is not None:
            continue
        for delta in _MESSAGE_DELTA_EXTRACTOR.extract(message):
            if delta.kind in {
                "salary_amount_change",
                "income_ended",
                "expense_amount_change",
                "salary_date_change",
                "confirmed_income",
            }:
                if delta.kind == "confirmed_income" and message.source_type == "employer":
                    continue
                deltas.append(delta)
    return tuple(deltas)


def _delta_start(delta: MessageDelta, as_of: date) -> date:
    return delta.effective_date or as_of


def _delta_amount_in_home_currency(
    delta: MessageDelta,
    on_date: date,
    home_currency: str,
    layer: DataLayer,
) -> Optional[Decimal]:
    if delta.amount is None or delta.currency is None:
        return None
    return delta.amount * layer.rate(delta.currency, home_currency, on_date)


def _salary_delta_amount(
    deltas: tuple[MessageDelta, ...],
    occurrence_date: date,
    as_of: date,
    home_currency: str,
    layer: DataLayer,
    conversion_date: Optional[date] = None,
) -> Optional[Decimal]:
    applicable = [
        delta
        for delta in deltas
        if delta.kind == "salary_amount_change"
        and _delta_start(delta, as_of) <= occurrence_date
    ]
    if not applicable:
        return None
    # The last effective change wins when multiple salary amendments apply.
    selected = max(
        applicable,
        key=lambda delta: delta.effective_date or as_of,
    )
    return _delta_amount_in_home_currency(
        selected,
        conversion_date or occurrence_date,
        home_currency,
        layer,
    )


def _salary_target_day(
    deltas: tuple[MessageDelta, ...],
    occurrence_date: date,
    as_of: date,
) -> Optional[int]:
    applicable = [
        delta
        for delta in deltas
        if delta.kind == "salary_date_change"
        and delta.amount is None
        and delta.effective_date is not None
        and delta.effective_date <= occurrence_date
    ]
    if not applicable:
        return None
    selected = max(applicable, key=lambda delta: delta.effective_date)
    return selected.effective_date.day


def _confirmed_income_amount(
    delta: MessageDelta,
    as_of: date,
    end_date: date,
    home_currency: str,
    layer: DataLayer,
) -> Optional[tuple[date, Decimal]]:
    if (
        delta.kind != "confirmed_income"
        or delta.amount is None
        or delta.currency is None
        or delta.effective_date is None
        or not (as_of <= delta.effective_date < end_date)
    ):
        return None
    return (
        delta.effective_date,
        _delta_amount_in_home_currency(
            delta, delta.effective_date, home_currency, layer
        ),
    )


def _expense_multiplier(
    deltas: tuple[MessageDelta, ...],
    category: str,
    occurrence_date: date,
    as_of: date,
) -> Optional[Decimal]:
    applicable = [
        delta
        for delta in deltas
        if delta.kind == "expense_amount_change"
        and delta.target_category == category
        and delta.percent_change is not None
        and _delta_start(delta, as_of) <= occurrence_date
    ]
    if not applicable:
        return None
    selected = max(
        applicable,
        key=lambda delta: delta.effective_date or as_of,
    )
    return Decimal("1") + selected.percent_change / Decimal("100")


def _salary_ended(
    deltas: tuple[MessageDelta, ...], occurrence_date: date, as_of: date
) -> bool:
    return any(
        delta.kind == "income_ended"
        and _delta_start(delta, as_of) <= occurrence_date
        for delta in deltas
    )


def _advance_month(value: date) -> date:
    if value.month == 12:
        year, month = value.year + 1, 1
    else:
        year, month = value.year, value.month + 1
    return date(year, month, min(value.day, monthrange(year, month)[1]))


def _synthetic_salary_occurrences(
    deltas: tuple[MessageDelta, ...],
    as_of: date,
    end_date: date,
    home_currency: str,
    layer: DataLayer,
) -> list[tuple[date, Decimal]]:
    amount_deltas = [
        delta for delta in deltas if delta.kind == "salary_amount_change"
    ]
    if not amount_deltas:
        return []
    effective_dates = [
        delta.effective_date for delta in amount_deltas if delta.effective_date is not None
    ]
    if effective_dates:
        occurrence = min(effective_dates)
        while occurrence < as_of:
            occurrence = _advance_month(occurrence)
    else:
        occurrence = date(as_of.year, as_of.month, 15)
        if occurrence < as_of:
            occurrence = _advance_month(occurrence)

    projected: list[tuple[date, Decimal]] = []
    while occurrence < end_date:
        if not _salary_ended(deltas, occurrence, as_of):
            amount = _salary_delta_amount(
                deltas, occurrence, as_of, home_currency, layer
            )
            if amount is not None:
                projected.append((occurrence, amount))
        occurrence = _advance_month(occurrence)
    return projected


def event_distributions(
    data_layer: Optional[DataLayer] = None,
) -> tuple[Counter, Counter]:
    """Return status and event-type counts from the loaded event table."""

    layer = data_layer or _DEFAULT_DATA_LAYER
    return (
        Counter(event.status for event in layer.events),
        Counter(event.event_type for event in layer.events),
    )


def _is_exact_duplicate(
    event: FinancialEventRecord,
    event_by_id: dict[str, FinancialEventRecord],
) -> bool:
    """Identify an exact linked economic duplicate without collapsing lifecycles."""

    if event.linked_event_id is None:
        return False
    parent = event_by_id.get(event.linked_event_id)
    if parent is None:
        return False
    # linked_event_id alone is not enough: refunds, retries, amendments, sales,
    # and valuations are separate lifecycle records. Only an identical record
    # with the same cash semantics is a duplicate.
    return (
        event.user_id,
        event.event_type,
        event.description,
        event.category,
        event.direction,
        event.amount,
        event.currency,
        event.event_date,
        event.settlement_date,
        event.status,
    ) == (
        parent.user_id,
        parent.event_type,
        parent.description,
        parent.category,
        parent.direction,
        parent.amount,
        parent.currency,
        parent.event_date,
        parent.settlement_date,
        parent.status,
    )


def _included_for_cashflow(
    event: FinancialEventRecord,
    event_by_id: dict[str, FinancialEventRecord],
) -> bool:
    # Exclude failed/cancelled rows because no cash settles; exclude pending
    # credits because they are not available; exclude investment valuations and
    # all unrealized rows because they are non-cash; exclude exact linked
    # duplicates while retaining distinct refunds, retries, and amendments.
    if event.status in EXCLUDED_STATUSES:
        return False
    if event.status == "pending" and event.direction == "credit":
        return False
    if event.event_type in EXCLUDED_EVENT_TYPES or event.status == "unrealized":
        return False
    if _is_exact_duplicate(event, event_by_id):
        return False
    return event.direction in {"debit", "credit"}


def _convert_amount(
    event: FinancialEventRecord,
    amount: Decimal,
    home_currency: str,
    on_date: date,
    layer: DataLayer,
) -> Decimal:
    return amount * layer.rate(event.currency, home_currency, on_date)


def _signed_amount(
    event: FinancialEventRecord,
    home_currency: str,
    on_date: date,
    layer: DataLayer,
) -> Optional[Decimal]:
    if event.amount is None:
        return None
    amount = _convert_amount(event, event.amount, home_currency, on_date, layer)
    return amount if event.direction == "credit" else -amount


def _series_occurrences(
    series: RecurringSeries,
    events_by_id: dict[str, FinancialEventRecord],
    as_of: date,
    end_date: date,
    layer: DataLayer,
    home_currency: str,
    message_deltas: tuple[MessageDelta, ...] = (),
) -> list[tuple[date, Decimal]]:
    members = [events_by_id[event_id] for event_id in series.event_ids]
    valid_members = [
        event
        for event in members
        if event.settlement_date is not None
        and event.amount is not None
        and _included_for_cashflow(event, events_by_id)
    ]
    if not valid_members or series.direction not in {"debit", "credit"}:
        return []

    historical = [
        event for event in valid_members if event.settlement_date is not None and event.settlement_date <= as_of
    ]
    anchor = max(
        historical or valid_members,
        key=lambda event: (event.settlement_date, event.event_id),
    )
    amount_candidates = [event for event in historical if event.amount is not None]
    amount_event = max(
        amount_candidates or valid_members,
        key=lambda event: (event.settlement_date, event.event_id),
    )
    amount = amount_event.amount
    if amount is None:
        return []

    # Project the known home-currency equivalent from the representative
    # occurrence's exact supplied rate; no unsupported future/nearest rate is
    # selected for a synthetic recurrence date.
    projected_amount = _convert_amount(
        amount_event,
        amount,
        home_currency,
        amount_event.settlement_date,
        layer,
    )

    cadence = float(series.cadence_days)
    # Projection occurrences use the half-open window as_of <= occurrence < as_of + 90 days.
    if 28 <= cadence <= 31:
        target_day = anchor.settlement_date.day
        current = anchor.settlement_date
        if current.month == 12:
            year, month = current.year + 1, 1
        else:
            year, month = current.year, current.month + 1
        occurrence = date(year, month, min(target_day, monthrange(year, month)[1]))
        while occurrence < as_of:
            if occurrence.month == 12:
                year, month = occurrence.year + 1, 1
            else:
                year, month = occurrence.year, occurrence.month + 1
            occurrence = date(year, month, min(target_day, monthrange(year, month)[1]))
    else:
        occurrence = anchor.settlement_date
        next_due = occurrence + timedelta(days=cadence)
        rolled = False
        while next_due < as_of:
            occurrence = next_due
            next_due = occurrence + timedelta(days=cadence)
            rolled = True
        if rolled:
            # The next due date is the rolled date to project; do not advance it again.
            occurrence = next_due
        else:
            while occurrence < as_of:
                occurrence += timedelta(days=cadence)

    projected: list[tuple[date, Decimal]] = []
    while occurrence < end_date:
        occurrence_amount = projected_amount
        output_date = occurrence
        if message_deltas:
            if series.direction == "credit" and series.category == "salary":
                if _salary_ended(message_deltas, occurrence, as_of):
                    occurrence += timedelta(days=cadence)
                    continue
                delta_amount = _salary_delta_amount(
                    message_deltas,
                    occurrence,
                    as_of,
                    home_currency,
                    layer,
                    amount_event.settlement_date,
                )
                if delta_amount is not None:
                    occurrence_amount = delta_amount
                target_day = _salary_target_day(
                    message_deltas, occurrence, as_of
                )
                if target_day is not None:
                    output_date = date(
                        occurrence.year,
                        occurrence.month,
                        min(target_day, monthrange(occurrence.year, occurrence.month)[1]),
                    )
            elif series.direction == "debit":
                multiplier = _expense_multiplier(
                    message_deltas, series.category, occurrence, as_of
                )
                if multiplier is not None:
                    occurrence_amount *= multiplier
        projected.append(
            (
                output_date,
                occurrence_amount
                if series.direction == "credit"
                else -occurrence_amount,
            )
        )
        occurrence += timedelta(days=cadence)
    return projected


def project(
    user_id: str,
    as_of: date,
    horizon_days: int = 90,
    data_layer: Optional[DataLayer] = None,
) -> dict[date, Decimal]:
    """Return an inclusive day-by-day projected balance series.

    The returned mapping starts at ``as_of`` with the profile's current
    available balance and contains one entry for each following calendar day.
    Future recurring series and eligible non-recurring settlement events are
    applied in the user's home currency.
    """

    if horizon_days < 0:
        raise ValueError("horizon_days must be non-negative")

    layer = data_layer or _DEFAULT_DATA_LAYER
    # Resolve image-backed blank amounts before recurrence detection and cash
    # flow projection. DataLayer performs this lazily and caches the resolved
    # records for the lifetime of the layer.
    layer.resolve_blank_amounts()
    profile = layer.profile_for(user_id)
    events = list(layer.events_for(user_id))
    event_by_id = {event.event_id: event for event in events}
    end_date = as_of + timedelta(days=horizon_days)

    series = recurring_series(user_id, layer, as_of)
    message_deltas = _message_deltas_for_user(user_id, layer)
    salary_series = tuple(
        item for item in series if item.direction == "credit" and item.category == "salary"
    )
    recurring_ids = {event_id for item in series for event_id in item.event_ids}
    changes: dict[date, Decimal] = defaultdict(Decimal)

    for item in series:
        for occurrence_date, amount in _series_occurrences(
            item,
            event_by_id,
            as_of,
            end_date,
            layer,
            profile.home_currency,
            message_deltas,
        ):
            changes[occurrence_date] += amount

    if not salary_series:
        for occurrence_date, amount in _synthetic_salary_occurrences(
            message_deltas,
            as_of,
            end_date,
            profile.home_currency,
            layer,
        ):
            changes[occurrence_date] += amount

    for delta in message_deltas:
        confirmed = _confirmed_income_amount(
            delta, as_of, end_date, profile.home_currency, layer
        )
        if confirmed is not None:
            occurrence_date, amount = confirmed
            if amount is not None:
                changes[occurrence_date] += amount

    for event in events:
        settlement_date = event.settlement_date
        if event.event_id in recurring_ids:
            continue
        if settlement_date is None or not (as_of < settlement_date < end_date):
            continue
        if not _included_for_cashflow(event, event_by_id):
            continue
        signed = _signed_amount(
            event,
            profile.home_currency,
            settlement_date,
            layer,
        )
        if signed is not None:
            changes[settlement_date] += signed

    balances: dict[date, Decimal] = {as_of: profile.current_available_balance}
    balance = profile.current_available_balance
    for offset in range(1, horizon_days + 1):
        current_date = as_of + timedelta(days=offset)
        balance += changes.get(current_date, Decimal("0"))
        balances[current_date] = balance
    return balances


_DEFAULT_DATA_LAYER = DataLayer()


if __name__ == "__main__":
    status_counts, event_type_counts = event_distributions()
    print(f"status distribution: {dict(sorted(status_counts.items()))}")
    print(f"event_type distribution: {dict(sorted(event_type_counts.items()))}")
