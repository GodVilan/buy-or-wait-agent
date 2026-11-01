"""Compare recurrence/forecast variants against public sample amounts.

Variants are evaluation-only configurations; none makes a payment decision,
writes an explanation, or calls a model.
"""

from __future__ import annotations

import math
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, replace
from calendar import monthrange
from datetime import date, timedelta
from decimal import Decimal
from functools import reduce
from pathlib import Path
from statistics import median
from typing import Callable, Optional

CODE_DIR = Path(__file__).resolve().parents[1]
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from data_layer import DataLayer, FinancialEventRecord  # noqa: E402
from forecast import (  # noqa: E402
    _included_for_cashflow,
    _signed_amount,
)


SAMPLE_EVENT_IDS = {"event_476", "event_989", "event_1815", "event_1816"}


@dataclass(frozen=True)
class VariantSeries:
    user_id: str
    event_type: str
    description: str
    category: str
    direction: str
    event_ids: tuple[str, ...]
    cadence_days: int | float | None
    latest_occurrence_date: date
    representative_amount: Decimal | None
    daily_rate: Decimal | None = None
    calendar_monthly: bool = False


@dataclass(frozen=True)
class Variant:
    name: str
    description: str
    detect: Callable[[str, DataLayer, Optional[date]], tuple[VariantSeries, ...]]
    weekly_mean_amount: bool = False
    mean_last_three_amounts: bool = False
    median_last_three_amounts: bool = False
    e_projection_fix: bool = False


def _group_events(
    user_id: str,
    layer: DataLayer,
    grouping: str = "key",
) -> dict[tuple[str, str, str, str], list[FinancialEventRecord]]:
    groups: dict[tuple[str, str, str, str], list[FinancialEventRecord]] = defaultdict(list)
    for event in layer.events_for(user_id):
        if event.settlement_date is None:
            continue
        if grouping == "category_direction":
            key = (
                f"category:{event.category}",
                f"{event.category}/{event.direction}",
                event.category,
                event.direction,
            )
        elif grouping == "event_type_category_direction":
            key = (
                f"event_type:{event.event_type}",
                f"{event.event_type}/{event.category}/{event.direction}",
                event.category,
                event.direction,
            )
        else:
            key = (event.event_type, event.description, event.category, event.direction)
        groups[key].append(event)
    return groups


def _series_from_members(
    user_id: str,
    key: tuple[str, str, str, str],
    members: list[FinancialEventRecord],
    cadence_days: int | float | None,
    daily_rate: Decimal | None = None,
    calendar_monthly: bool = False,
) -> VariantSeries:
    ordered = sorted(members, key=lambda event: (event.settlement_date, event.event_id))
    representative = next(
        (event.amount for event in reversed(ordered) if event.amount is not None),
        None,
    )
    return VariantSeries(
        user_id=user_id,
        event_type=key[0],
        description=key[1],
        category=key[2],
        direction=key[3],
        event_ids=tuple(event.event_id for event in ordered),
        cadence_days=cadence_days,
        latest_occurrence_date=ordered[-1].settlement_date,
        representative_amount=representative,
        daily_rate=daily_rate,
        calendar_monthly=calendar_monthly,
    )


def _variant_a(
    user_id: str, layer: DataLayer, as_of: Optional[date] = None
) -> tuple[VariantSeries, ...]:
    """Standalone original detector retained as comparison Variant A.

    A predates the promoted E implementation: it uses the original four-field
    key, requires at least three occurrences, accepts median weekly (6..8 day)
    or monthly (25..35 day) gaps, and rejects a group when any gap exceeds
    1.5 times the median. It has no request-date liveness filter.
    """

    accepted: list[VariantSeries] = []
    for key, members in _group_events(user_id, layer).items():
        if len(members) < 3:
            continue
        ordered = sorted(
            members, key=lambda event: (event.settlement_date, event.event_id)
        )
        gaps = [
            (right.settlement_date - left.settlement_date).days
            for left, right in zip(ordered, ordered[1:])
        ]
        median_gap = median(gaps)
        if not (6 <= median_gap <= 8 or 25 <= median_gap <= 35):
            continue
        if any(gap > 1.5 * median_gap for gap in gaps):
            continue
        cadence = 7 if 6 <= median_gap <= 8 else round(median_gap)
        accepted.append(_series_from_members(user_id, key, ordered, cadence))
    return tuple(
        sorted(
            accepted,
            key=lambda item: (item.latest_occurrence_date, item.description),
        )
    )


def _gcd_variant(
    user_id: str,
    layer: DataLayer,
    grouping: str = "key",
) -> tuple[VariantSeries, ...]:
    """Variant B/D acceptance: GCD weekly test, then median monthly test."""

    accepted: list[VariantSeries] = []
    for key, members in _group_events(user_id, layer, grouping).items():
        if len(members) < 2:
            continue
        ordered = sorted(members, key=lambda event: (event.settlement_date, event.event_id))
        gaps = [
            (right.settlement_date - left.settlement_date).days
            for left, right in zip(ordered, ordered[1:])
        ]
        gcd_gap = reduce(math.gcd, gaps)
        if gcd_gap > 0 and gcd_gap % 7 == 0 and gcd_gap <= 91:
            cadence = 7
        else:
            median_gap = median(gaps)
            if not (25 <= median_gap <= 35):
                continue
            cadence = round(median_gap)
        accepted.append(_series_from_members(user_id, key, ordered, cadence))
    return tuple(sorted(accepted, key=lambda item: (item.latest_occurrence_date, item.description)))


def _variant_b(
    user_id: str, layer: DataLayer, as_of: Optional[date] = None
) -> tuple[VariantSeries, ...]:
    return _gcd_variant(user_id, layer)


def _variant_d(
    user_id: str, layer: DataLayer, as_of: Optional[date] = None
) -> tuple[VariantSeries, ...]:
    return _gcd_variant(user_id, layer)


def _latest_before(
    series: VariantSeries,
    layer: DataLayer,
    as_of: date,
) -> Optional[date]:
    dates = [
        event.settlement_date
        for event_id in series.event_ids
        for event in layer.events
        if event.event_id == event_id and event.settlement_date <= as_of
    ]
    return max(dates) if dates else None


def _live_at_request(
    series: VariantSeries,
    layer: DataLayer,
    as_of: date,
) -> bool:
    latest_before = _latest_before(series, layer, as_of)
    return (
        latest_before is not None
        and (as_of - latest_before).days <= 1.5 * float(series.cadence_days)
    )


def _variant_e(
    user_id: str,
    layer: DataLayer,
    as_of: Optional[date] = None,
    grouping: str = "key",
) -> tuple[VariantSeries, ...]:
    accepted = _gcd_variant(user_id, layer, grouping)
    if as_of is None:
        return accepted
    return tuple(series for series in accepted if _live_at_request(series, layer, as_of))


def _gcd_range_variant(
    user_id: str,
    layer: DataLayer,
    as_of: Optional[date],
    grouping: str,
) -> tuple[VariantSeries, ...]:
    accepted: list[VariantSeries] = []
    for key, members in _group_events(user_id, layer, grouping).items():
        if len(members) < 3:
            continue
        ordered = sorted(members, key=lambda event: (event.settlement_date, event.event_id))
        gaps = [
            (right.settlement_date - left.settlement_date).days
            for left, right in zip(ordered, ordered[1:])
        ]
        cadence = reduce(math.gcd, gaps)
        if not 5 <= cadence <= 45:
            continue
        series = _series_from_members(user_id, key, ordered, cadence)
        if as_of is None or _live_at_request(series, layer, as_of):
            accepted.append(series)
    return tuple(sorted(accepted, key=lambda item: (item.latest_occurrence_date, item.description)))


def _variant_f(
    user_id: str, layer: DataLayer, as_of: Optional[date] = None
) -> tuple[VariantSeries, ...]:
    return _gcd_range_variant(user_id, layer, as_of, "category_direction")


def _variant_g(
    user_id: str, layer: DataLayer, as_of: Optional[date] = None
) -> tuple[VariantSeries, ...]:
    return _gcd_range_variant(user_id, layer, as_of, "event_type_category_direction")


def _advance_calendar_month(current: date) -> date:
    """Advance one month while preserving the day when the month permits it."""

    if current.month == 12:
        year, month = current.year + 1, 1
    else:
        year, month = current.year, current.month + 1
    day = min(current.day, monthrange(year, month)[1])
    return date(year, month, day)


def _variant_h(
    user_id: str, layer: DataLayer, as_of: Optional[date] = None
) -> tuple[VariantSeries, ...]:
    accepted: list[VariantSeries] = []
    for key, members in _group_events(
        user_id, layer, "event_type_category_direction"
    ).items():
        if len(members) < 3:
            continue
        ordered = sorted(members, key=lambda event: (event.settlement_date, event.event_id))
        gaps = [
            (right.settlement_date - left.settlement_date).days
            for left, right in zip(ordered, ordered[1:])
        ]
        gcd_gap = reduce(math.gcd, gaps)
        calendar_monthly = False
        if 5 <= gcd_gap <= 45:
            cadence = gcd_gap
        else:
            median_gap = median(gaps)
            if not (
                28 <= median_gap <= 31
                and all(27 <= gap <= 32 for gap in gaps)
            ):
                continue
            cadence = round(median_gap)
            calendar_monthly = True
        series = _series_from_members(
            user_id,
            key,
            ordered,
            cadence,
            calendar_monthly=calendar_monthly,
        )
        if as_of is None or _live_at_request(series, layer, as_of):
            accepted.append(series)
    return tuple(sorted(accepted, key=lambda item: (item.latest_occurrence_date, item.description)))


def _variant_h2(
    user_id: str, layer: DataLayer, as_of: Optional[date] = None
) -> tuple[VariantSeries, ...]:
    accepted = _variant_h(user_id, layer, as_of)
    if as_of is None:
        return accepted
    window_start = as_of - timedelta(days=180)
    events_by_id = {event.event_id: event for event in layer.events}
    return tuple(
        series
        for series in accepted
        if sum(
            event.settlement_date is not None
            and window_start <= event.settlement_date <= as_of
            for event_id in series.event_ids
            for event in [events_by_id[event_id]]
        )
        >= 3
    )


def _variant_h3(
    user_id: str, layer: DataLayer, as_of: Optional[date] = None
) -> tuple[VariantSeries, ...]:
    return _variant_h2(user_id, layer, as_of)


def _variant_e_key(
    user_id: str, layer: DataLayer, as_of: Optional[date] = None
) -> tuple[VariantSeries, ...]:
    return _variant_e(user_id, layer, as_of, "key")


def _variant_e_category(
    user_id: str, layer: DataLayer, as_of: Optional[date] = None
) -> tuple[VariantSeries, ...]:
    return _variant_e(user_id, layer, as_of, "category_direction")


def _variant_c(
    user_id: str, layer: DataLayer, as_of: Optional[date] = None
) -> tuple[VariantSeries, ...]:
    accepted: list[VariantSeries] = []
    for key, members in _group_events(user_id, layer).items():
        if len(members) < 2:
            continue
        ordered = sorted(members, key=lambda event: (event.settlement_date, event.event_id))
        span_days = max(1, (ordered[-1].settlement_date - ordered[0].settlement_date).days)
        # C has no cadence. It uses the group's trailing absolute flow rate;
        # direction supplies the sign when that rate is projected.
        total = sum(
            (event.amount for event in ordered if event.amount is not None),
            Decimal("0"),
        )
        daily_rate = total / Decimal(span_days)
        accepted.append(
            _series_from_members(
                user_id,
                key,
                ordered,
                cadence_days=None,
                daily_rate=daily_rate,
            )
        )
    return tuple(sorted(accepted, key=lambda item: (item.latest_occurrence_date, item.description)))


VARIANTS = (
    Variant("A", "current median-gap detector", _variant_a),
    Variant("B", "fixed GCD/median cadence detector", _variant_b),
    Variant("C", "trailing daily-rate detector", _variant_c),
    Variant("D", "fixed-B with weekly mean amounts", _variant_d, weekly_mean_amount=True),
    Variant(
        "E",
        "fixed-B with request-date liveness, current key",
        _variant_e_key,
        e_projection_fix=True,
    ),
    Variant(
        "E-category",
        "fixed-B with request-date liveness, category/direction key",
        _variant_e_category,
    ),
    Variant(
        "F",
        "category/direction GCD 5..45 with liveness and trailing-three mean",
        _variant_f,
        mean_last_three_amounts=True,
    ),
    Variant(
        "G",
        "event-type/category/direction GCD 5..45 with liveness and trailing-three mean",
        _variant_g,
        mean_last_three_amounts=True,
    ),
    Variant(
        "H",
        "event-type/category/direction GCD or calendar-monthly with liveness",
        _variant_h,
        mean_last_three_amounts=True,
    ),
    Variant(
        "H2",
        "H plus three occurrences in the prior 180 days",
        _variant_h2,
        mean_last_three_amounts=True,
    ),
    Variant(
        "H3",
        "H2 with median last-three amounts",
        _variant_h3,
        median_last_three_amounts=True,
    ),
)

REPORT_VARIANT_NAMES = ("A", "B", "C", "D", "E", "F", "G", "H")
REPORT_VARIANTS = tuple(
    variant for variant in VARIANTS if variant.name in REPORT_VARIANT_NAMES
)


def _anchor_and_amount(
    series: VariantSeries,
    events_by_id: dict[str, FinancialEventRecord],
    as_of: date,
) -> tuple[FinancialEventRecord, FinancialEventRecord] | None:
    members = [events_by_id[event_id] for event_id in series.event_ids]
    valid = [
        event
        for event in members
        if event.settlement_date is not None
        and event.amount is not None
        and _included_for_cashflow(event, events_by_id)
    ]
    if not valid:
        return None
    historical = [event for event in valid if event.settlement_date <= as_of]
    anchor = max(historical or valid, key=lambda event: (event.settlement_date, event.event_id))
    amount_candidates = [event for event in historical if event.amount is not None]
    amount_event = max(
        amount_candidates or valid,
        key=lambda event: (event.settlement_date, event.event_id),
    )
    return anchor, amount_event


def _convert_at_known_date(
    event: FinancialEventRecord,
    amount: Decimal,
    home_currency: str,
    layer: DataLayer,
) -> Decimal:
    return amount * layer.rate(event.currency, home_currency, event.settlement_date)


def _project_variant(
    variant: Variant,
    user_id: str,
    as_of: date,
    layer: DataLayer,
    horizon_days: int = 90,
) -> dict[date, Decimal]:
    profile = layer.profile_for(user_id)
    events = list(layer.events_for(user_id))
    events_by_id = {event.event_id: event for event in events}
    series = variant.detect(user_id, layer, as_of)
    recurring_ids = {event_id for item in series for event_id in item.event_ids}
    end_date = as_of + timedelta(days=horizon_days)
    changes: dict[date, Decimal] = defaultdict(Decimal)

    for item in series:
        anchor_info = _anchor_and_amount(item, events_by_id, as_of)
        if anchor_info is None or item.direction not in {"debit", "credit"}:
            continue
        anchor, amount_event = anchor_info
        sign = Decimal("1") if item.direction == "credit" else Decimal("-1")

        if variant.name == "C":
            valid = [
                event
                for event in (events_by_id[event_id] for event_id in item.event_ids)
                if event.amount is not None
                and event.settlement_date is not None
                and _included_for_cashflow(event, events_by_id)
            ]
            home_total = sum(
                (
                    _convert_at_known_date(
                        event,
                        event.amount,
                        profile.home_currency,
                        layer,
                    )
                    for event in valid
                ),
                Decimal("0"),
            )
            first_date = min(event.settlement_date for event in valid)
            last_date = max(event.settlement_date for event in valid)
            span_days = max(1, (last_date - first_date).days)
            daily_home_amount = home_total / Decimal(span_days)
            current_date = as_of + timedelta(days=1)
            while current_date <= end_date:
                changes[current_date] += sign * daily_home_amount
                current_date += timedelta(days=1)
            continue

        if variant.mean_last_three_amounts or variant.median_last_three_amounts:
            valid_amounts = [
                event
                for event in (events_by_id[event_id] for event_id in item.event_ids)
                if event.amount is not None
                and event.settlement_date is not None
                and _included_for_cashflow(event, events_by_id)
            ]
            historical_amounts = [
                event for event in valid_amounts if event.settlement_date <= as_of
            ]
            last_three = (historical_amounts or valid_amounts)[-3:]
            last_three_home = [
                _convert_at_known_date(
                    event,
                    event.amount,
                    profile.home_currency,
                    layer,
                )
                for event in last_three
            ]
            if variant.median_last_three_amounts:
                projected_amount = sorted(last_three_home)[len(last_three_home) // 2]
            else:
                projected_amount = sum(last_three_home, Decimal("0")) / Decimal(len(last_three_home))
        elif variant.weekly_mean_amount and item.cadence_days == 7:
            valid_amounts = [
                event
                for event in (events_by_id[event_id] for event_id in item.event_ids)
                if event.amount is not None
                and event.settlement_date is not None
                and _included_for_cashflow(event, events_by_id)
            ]
            projected_amount = sum(
                (
                    _convert_at_known_date(
                        event,
                        event.amount,
                        profile.home_currency,
                        layer,
                    )
                    for event in valid_amounts
                ),
                Decimal("0"),
            ) / Decimal(len(valid_amounts))
        else:
            projected_amount = _convert_at_known_date(
                amount_event,
                amount_event.amount,
                profile.home_currency,
                layer,
            )
        if item.calendar_monthly:
            occurrence = _advance_calendar_month(anchor.settlement_date)
            while occurrence <= as_of:
                occurrence = _advance_calendar_month(occurrence)
            while occurrence <= end_date:
                changes[occurrence] += sign * projected_amount
                occurrence = _advance_calendar_month(occurrence)
        else:
            cadence = float(item.cadence_days)
            occurrence = anchor.settlement_date
            if variant.e_projection_fix:
                rolled = False
                while occurrence + timedelta(days=cadence) < as_of:
                    occurrence += timedelta(days=cadence)
                    rolled = True
                if rolled:
                    occurrence += timedelta(days=cadence)
                else:
                    while occurrence < as_of:
                        occurrence += timedelta(days=cadence)
            else:
                while occurrence <= as_of:
                    occurrence += timedelta(days=cadence)
            while occurrence <= end_date:
                changes[occurrence] += sign * projected_amount
                occurrence += timedelta(days=cadence)

    for event in events:
        if event.event_id in recurring_ids:
            continue
        if event.settlement_date is None or not (
            as_of < event.settlement_date <= end_date
        ):
            continue
        if not _included_for_cashflow(event, events_by_id):
            continue
        signed = _signed_amount(event, profile.home_currency, event.settlement_date, layer)
        if signed is not None:
            changes[event.settlement_date] += signed

    balances = {as_of: profile.current_available_balance}
    balance = profile.current_available_balance
    for offset in range(1, horizon_days + 1):
        current_date = as_of + timedelta(days=offset)
        balance += changes.get(current_date, Decimal("0"))
        balances[current_date] = balance
    return balances


def _series_count(variant: Variant, layer: DataLayer) -> tuple[int, int, int, int, int]:
    series = [
        item
        for user_id in sorted({event.user_id for event in layer.events})
        for item in variant.detect(user_id, layer, None)
    ]
    covered = len({event_id for item in series for event_id in item.event_ids})
    sample_covered = len(
        SAMPLE_EVENT_IDS
        & {event_id for item in series for event_id in item.event_ids}
    )
    weekly = sum(item.cadence_days == 7 for item in series)
    monthly = sum(
        item.cadence_days is not None and item.cadence_days != 7
        for item in series
    )
    return len(series), covered, sample_covered, weekly, monthly


def _score_variant(variant: Variant, layer: DataLayer) -> dict[str, object]:
    rows = []
    for sample in layer.sample_requests:
        balances = _project_variant(variant, sample.user_id, sample.request_date, layer)
        minimum = layer.profile_for(sample.user_id).minimum_balance_to_keep
        capacity = min(
            (balance - minimum for balance in balances.values()),
            default=Decimal("0"),
        )
        predicted = max(Decimal("0"), min(sample.requested_amount, capacity))
        difference = abs(predicted - sample.amount_safe_to_pay)
        rows.append((sample.request_id, predicted, sample.amount_safe_to_pay, difference))

    exact = sum(difference == 0 for _, _, _, difference in rows)
    within = sum(difference <= Decimal("0.01") for _, _, _, difference in rows)
    mean_difference = sum(
        (difference for _, _, _, difference in rows), Decimal("0")
    ) / Decimal(len(rows))
    above = sum(predicted > gold for _, predicted, gold, _ in rows)
    below = sum(predicted < gold for _, predicted, gold, _ in rows)
    zero = sum(predicted == 0 for _, predicted, _, _ in rows)
    return {
        "rows": rows,
        "exact": exact,
        "within": within,
        "mean_difference": mean_difference,
        "above": above,
        "below": below,
        "zero": zero,
    }


def _print_income_trace(variant: Variant, user_id: str, as_of: date, layer: DataLayer) -> None:
    profile = layer.profile_for(user_id)
    events = list(layer.events_for(user_id))
    events_by_id = {event.event_id: event for event in events}
    series = variant.detect(user_id, layer, as_of)
    end_date = as_of + timedelta(days=90)
    print(f"\n{variant.name} — {variant.description}")
    print("income series:")
    for item in series:
        if item.direction != "credit":
            continue
        anchor_info = _anchor_and_amount(item, events_by_id, as_of)
        if anchor_info is None:
            continue
        anchor, amount_event = anchor_info
        amount = item.daily_rate if variant.name == "C" else item.representative_amount
        print(
            f"  description={item.description!r} event_ids={item.event_ids} "
            f"cadence={item.cadence_days} amount={amount} "
            f"anchor_date={anchor.settlement_date}"
        )

    print("income events in 90-day window:")
    for item in series:
        if item.direction != "credit":
            continue
        anchor_info = _anchor_and_amount(item, events_by_id, as_of)
        if anchor_info is None:
            continue
        anchor, amount_event = anchor_info
        sign = Decimal("1")
        if variant.name == "C":
            valid = [
                event
                for event in (events_by_id[event_id] for event_id in item.event_ids)
                if event.amount is not None
                and event.settlement_date is not None
                and _included_for_cashflow(event, events_by_id)
            ]
            total_home = sum(
                (
                    _convert_at_known_date(event, event.amount, profile.home_currency, layer)
                    for event in valid
                ),
                Decimal("0"),
            )
            span = max(1, (max(e.settlement_date for e in valid) - min(e.settlement_date for e in valid)).days)
            daily_amount = total_home / Decimal(span)
            current = as_of + timedelta(days=1)
            while current <= end_date:
                print(
                    f"  source_event={amount_event.event_id} date={current} "
                    f"amount={daily_amount} status={amount_event.status}"
                )
                current += timedelta(days=1)
        else:
            current = (
                _advance_calendar_month(anchor.settlement_date)
                if item.calendar_monthly
                else anchor.settlement_date
            )
            while current <= as_of:
                current = (
                    _advance_calendar_month(current)
                    if item.calendar_monthly
                    else current + timedelta(days=float(item.cadence_days))
                )
            projected_amount = _convert_at_known_date(
                amount_event,
                amount_event.amount,
                profile.home_currency,
                layer,
            )
            while current <= end_date:
                print(
                    f"  source_event={amount_event.event_id} date={current} "
                    f"amount={projected_amount} status={amount_event.status}"
                )
                current = (
                    _advance_calendar_month(current)
                    if item.calendar_monthly
                    else current + timedelta(days=float(item.cadence_days))
                )

    for event in events:
        if event.direction != "credit" or event.event_id in {
            event_id for item in series for event_id in item.event_ids
        }:
            continue
        if event.settlement_date is None or not (as_of < event.settlement_date <= end_date):
            continue
        if not _included_for_cashflow(event, events_by_id):
            continue
        signed = _signed_amount(event, profile.home_currency, event.settlement_date, layer)
        if signed is not None:
            print(
                f"  source_event={event.event_id} date={event.settlement_date} "
                f"amount={signed} status={event.status} one_off=True"
            )


def _print_best_variant_series_trace(
    variant: Variant,
    request_id: str,
    layer: DataLayer,
) -> None:
    sample = next(row for row in layer.sample_requests if row.request_id == request_id)
    profile = layer.profile_for(sample.user_id)
    events = list(layer.events_for(sample.user_id))
    events_by_id = {event.event_id: event for event in events}
    series = variant.detect(sample.user_id, layer, sample.request_date)
    end_date = sample.request_date + timedelta(days=90)
    balances = _project_variant(variant, sample.user_id, sample.request_date, layer)
    trough_date, trough_value = min(balances.items(), key=lambda item: (item[1], item[0]))
    gold_trough = profile.minimum_balance_to_keep + sample.amount_safe_to_pay

    print(f"\n{variant.name} series trace for {request_id}")
    for item in series:
        if item.direction not in {"debit", "credit"}:
            continue
        anchor_info = _anchor_and_amount(item, events_by_id, sample.request_date)
        if anchor_info is None:
            continue
        anchor, amount_event = anchor_info
        valid_amounts = [
            event
            for event in (events_by_id[event_id] for event_id in item.event_ids)
            if event.amount is not None
            and event.settlement_date is not None
            and _included_for_cashflow(event, events_by_id)
            and event.settlement_date <= sample.request_date
        ]
        last_three = valid_amounts[-3:]
        home_amounts = [
            _convert_at_known_date(
                event,
                event.amount,
                profile.home_currency,
                layer,
            )
            for event in last_three
        ]
        if variant.median_last_three_amounts:
            projected_amount = sorted(home_amounts)[len(home_amounts) // 2]
        else:
            projected_amount = sum(home_amounts, Decimal("0")) / Decimal(len(home_amounts))
        occurrence_dates: list[date] = []
        if item.calendar_monthly:
            occurrence = _advance_calendar_month(anchor.settlement_date)
            while occurrence <= sample.request_date:
                occurrence = _advance_calendar_month(occurrence)
            while occurrence <= end_date:
                occurrence_dates.append(occurrence)
                occurrence = _advance_calendar_month(occurrence)
        else:
            occurrence = anchor.settlement_date
            cadence = float(item.cadence_days)
            while occurrence <= sample.request_date:
                occurrence += timedelta(days=cadence)
            while occurrence <= end_date:
                occurrence_dates.append(occurrence)
                occurrence += timedelta(days=cadence)
        signed_amount = projected_amount if item.direction == "credit" else -projected_amount
        print(
            f"  description={item.description!r} cadence="
            f"{'calendar-monthly' if item.calendar_monthly else item.cadence_days} "
            f"last_occurrence={anchor.settlement_date} projected_amount={signed_amount} "
            f"occurrences_in_window={len(occurrence_dates)}"
        )
    print(f"  trough={trough_date}: {trough_value}")
    print(f"  gold_implied_trough={gold_trough}")


def _print_event_989_groupings(layer: DataLayer) -> None:
    target = next(event for event in layer.events if event.event_id == "event_989")
    user_id = target.user_id
    print("\nevent_989 user dining/debit events under both E groupings")
    for grouping in ("key", "category_direction"):
        groups = _group_events(user_id, layer, grouping)
        matching_groups = [
            (group_key, members)
            for group_key, members in groups.items()
            if group_key[2] == target.category and group_key[3] == target.direction
        ]
        accepted_ids = {
            event_id
            for series in _gcd_variant(user_id, layer, grouping)
            for event_id in series.event_ids
        }
        print(
            f"grouping={grouping} group_count={len(matching_groups)} "
            f"accepted_events={len(accepted_ids & {event.event_id for _, members in matching_groups for event in members})}"
        )
        for group_key, members in sorted(matching_groups, key=lambda item: item[0]):
            print(f"  group={group_key}")
            for event in sorted(
                members, key=lambda item: (item.settlement_date, item.event_id)
            ):
                print(
                    f"    event_id={event.event_id} date={event.settlement_date} "
                    f"amount={event.amount}"
                )


def main() -> None:
    layer = DataLayer()
    metadata = []
    for variant in REPORT_VARIANTS:
        groups, covered, sample_count, weekly, monthly = _series_count(variant, layer)
        share = Decimal(covered) / Decimal(len(layer.events))
        metadata.append((variant, groups, covered, share, sample_count, weekly, monthly))

    summaries = []
    for variant, groups, covered, share, sample_count, weekly, monthly in metadata:
        result = _score_variant(variant, layer)
        summaries.append(
            (variant, groups, covered, share, sample_count, weekly, monthly, result)
        )

    print("variant summaries")
    print("variant | groups | weekly_groups | monthly_groups | events_covered | share | sample_recurring | exact | within_0.01 | mean_abs_diff | predicted>gold | predicted<gold | predicted==0")
    for variant, groups, covered, share, sample_count, weekly, monthly, result in summaries:
        print(
            f"{variant.name} | {groups} | {weekly} | {monthly} | {covered} | {share:.6f} | "
            f"{sample_count} | {result['exact']} | {result['within']} | "
            f"{result['mean_difference']} | {result['above']} | {result['below']} | "
            f"{result['zero']}"
        )

    h_variant = next(item for item in REPORT_VARIANTS if item.name == "H")
    h_series = [
        series
        for user_id in sorted({event.user_id for event in layer.events})
        for series in h_variant.detect(user_id, layer, None)
    ]
    day_multiple = Counter(
        series.cadence_days for series in h_series if not series.calendar_monthly
    )
    calendar_monthly = Counter(
        series.cadence_days for series in h_series if series.calendar_monthly
    )
    h_sample_count = next(item[4] for item in summaries if item[0].name == "H")
    print(
        "variant H cadence distribution: "
        f"day_multiple={dict(sorted(day_multiple.items(), key=lambda item: str(item[0])))} "
        f"calendar_monthly={dict(sorted(calendar_monthly.items(), key=lambda item: str(item[0])))}"
    )
    print(f"variant H sample events recurring: {h_sample_count}/4")

    ranked = sorted(
        summaries,
        key=lambda item: (item[7]["mean_difference"], -item[7]["exact"], item[0].name),
    )
    best_variant, _, _, _, _, _, _, best_result = ranked[0]
    print(f"\nbest variant: {best_variant.name} — {best_variant.description}")
    print("best-variant per-row predicted values:")
    for request_id, predicted, gold, difference in best_result["rows"]:
        print(
            f"  {request_id}: predicted={predicted} gold={gold} "
            f"absolute_difference={difference}"
        )

    print("signed errors for H2 and H3:")
    for variant, _, _, _, _, _, _, result in summaries:
        if variant.name not in {"H2", "H3"}:
            continue
        print(f"{variant.name} — {variant.description}")
        for request_id, predicted, gold, _ in result["rows"]:
            print(
                f"  {request_id}: predicted={predicted} gold={gold} "
                f"signed_error={predicted - gold}"
            )

    for request_id in ("request_19", "request_02"):
        _print_best_variant_series_trace(best_variant, request_id, layer)

    request_14 = next(row for row in layer.sample_requests if row.request_id == "request_14")
    print("\nrequest_14 income traces")
    for variant in REPORT_VARIANTS:
        _print_income_trace(variant, request_14.user_id, request_14.request_date, layer)


if __name__ == "__main__":
    main()
