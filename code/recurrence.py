"""Deterministic recurring-event detection.

This module identifies cadence-supported event series only. It does not make
payment decisions, forecast balances, generate explanations, or call models.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from statistics import median
from typing import Optional

try:  # Supports both ``python code/recurrence.py`` and package-style callers.
    from .data_layer import DataLayer, FinancialEventRecord
except ImportError:  # pragma: no cover - exercised by direct script execution.
    from data_layer import DataLayer, FinancialEventRecord


SeriesKey = tuple[str, str, str, str]


@dataclass(frozen=True)
class RecurringSeries:
    """A cadence-supported group of a user's financial events."""

    user_id: str
    event_type: str
    description: str
    category: str
    direction: str
    event_ids: tuple[str, ...]
    cadence_days: int | float
    latest_occurrence_date: date
    representative_amount: Optional[Decimal]



def _group_key(event: FinancialEventRecord) -> SeriesKey:
    return (event.event_type, event.description, event.category, event.direction)


def _cadence(gaps: list[int]) -> int | float | None:
    """Return the cadence accepted by variant E."""

    if not gaps:
        return None
    gcd_gap = math.gcd(*gaps)
    if gcd_gap > 0 and gcd_gap % 7 == 0 and gcd_gap <= 91:
        return 7
    middle = median(gaps)
    if not 25 <= middle <= 35:
        return None
    return int(round(middle))


def _latest_before(
    series: RecurringSeries,
    events: list[FinancialEventRecord],
    as_of: date,
) -> Optional[date]:
    event_ids = set(series.event_ids)
    dates = [
        event.settlement_date
        for event in events
        if event.event_id in event_ids
        and event.settlement_date is not None
        and event.settlement_date <= as_of
    ]
    return max(dates) if dates else None


def _live_at_request(
    series: RecurringSeries,
    events: list[FinancialEventRecord],
    as_of: date,
) -> bool:
    latest_before = _latest_before(series, events, as_of)
    return (
        latest_before is not None
        and (as_of - latest_before).days <= 1.5 * float(series.cadence_days)
    )


def _series_for_events(
    user_id: str,
    events: list[FinancialEventRecord],
    as_of: Optional[date] = None,
) -> tuple[RecurringSeries, ...]:
    grouped: dict[SeriesKey, list[FinancialEventRecord]] = defaultdict(list)
    for event in events:
        if event.settlement_date is not None:
            grouped[_group_key(event)].append(event)

    detected: list[RecurringSeries] = []
    for key, members in grouped.items():
        if len(members) < 2:
            continue
        ordered = sorted(
            members,
            key=lambda event: (event.settlement_date, event.event_id),
        )
        dates = [event.settlement_date for event in ordered]
        gaps = [(right - left).days for left, right in zip(dates, dates[1:])]
        cadence_days = _cadence(gaps)
        if cadence_days is None:
            continue

        representative = next(
            (event.amount for event in reversed(ordered) if event.amount is not None),
            None,
        )
        series = RecurringSeries(
            user_id=user_id,
            event_type=key[0],
            description=key[1],
            category=key[2],
            direction=key[3],
            event_ids=tuple(event.event_id for event in ordered),
            cadence_days=cadence_days,
            latest_occurrence_date=ordered[-1].settlement_date,
            representative_amount=representative,
        )
        if as_of is None or _live_at_request(series, events, as_of):
            detected.append(series)

    return tuple(
        sorted(
            detected,
            key=lambda series: (
                series.latest_occurrence_date,
                series.event_type,
                series.description,
            ),
        )
    )


def recurring_series(
    user_id: str,
    data_layer: Optional[DataLayer] = None,
    as_of: Optional[date] = None,
) -> tuple[RecurringSeries, ...]:
    """Return variant-E recurrence series, optionally live at ``as_of``."""

    layer = data_layer or _DEFAULT_DATA_LAYER
    return _series_for_events(user_id, list(layer.events_for(user_id)), as_of)


def classify(
    user_id: str, data_layer: Optional[DataLayer] = None
) -> dict[str, str]:
    """Classify every event for a user as ``recurring`` or ``one_time``."""

    layer = data_layer or _DEFAULT_DATA_LAYER
    recurring_ids = {
        event_id
        for series in recurring_series(user_id, layer)
        for event_id in series.event_ids
    }
    return {
        event.event_id: ("recurring" if event.event_id in recurring_ids else "one_time")
        for event in layer.events_for(user_id)
    }


def rejection_diagnostics(
    data_layer: Optional[DataLayer] = None,
) -> tuple[Counter, list[dict[str, object]]]:
    """Count mutually exclusive rejected candidate-group reasons.

    Groups are evaluated in priority order: insufficient occurrences, median
    outside the weekly/monthly bands, then an excessive individual gap.
    """

    layer = data_layer or _DEFAULT_DATA_LAYER
    reasons = Counter(
        {
            "fewer_than_3_occurrences": 0,
            "median_gap_outside_allowed_bands": 0,
            "gap_exceeds_1_5x_median": 0,
        }
    )
    long_gap_examples: list[dict[str, object]] = []

    grouped_by_user: dict[str, dict[SeriesKey, list[FinancialEventRecord]]] = {}
    for event in layer.events:
        if event.settlement_date is None:
            continue
        grouped_by_user.setdefault(event.user_id, {}).setdefault(
            _group_key(event), []
        ).append(event)

    for user_id in sorted(grouped_by_user):
        for key, members in sorted(grouped_by_user[user_id].items()):
            ordered = sorted(
                members,
                key=lambda event: (event.settlement_date, event.event_id),
            )
            if len(ordered) < 3:
                reasons["fewer_than_3_occurrences"] += 1
                continue

            gaps = [
                (right.settlement_date - left.settlement_date).days
                for left, right in zip(ordered, ordered[1:])
            ]
            middle = median(gaps)
            if not (25 <= middle <= 35 or 6 <= middle <= 8):
                reasons["median_gap_outside_allowed_bands"] += 1
                continue
            if any(gap > 1.5 * middle for gap in gaps):
                reasons["gap_exceeds_1_5x_median"] += 1
                long_gap_examples.append(
                    {
                        "user_id": user_id,
                        "group": key,
                        "gaps": gaps,
                        "median_gap": middle,
                    }
                )

    long_gap_examples.sort(
        key=lambda example: (
            str(example["user_id"]),
            str(example["group"]),
            str(example["gaps"]),
        )
    )
    return reasons, long_gap_examples


def diagnostic(data_layer: Optional[DataLayer] = None) -> tuple[int, Counter, int]:
    """Return the requested dataset-wide recurrence diagnostics."""

    layer = data_layer or _DEFAULT_DATA_LAYER
    all_series: list[RecurringSeries] = []
    recurring_event_ids: set[str] = set()
    for user_id in sorted({event.user_id for event in layer.events}):
        series = recurring_series(user_id, layer)
        all_series.extend(series)
        recurring_event_ids.update(
            event_id for item in series for event_id in item.event_ids
        )

    cadence_distribution = Counter(series.cadence_days for series in all_series)
    sample_event_ids = {"event_476", "event_989", "event_1815", "event_1816"}
    sample_recurring_count = len(sample_event_ids & recurring_event_ids)
    return len(recurring_event_ids), cadence_distribution, sample_recurring_count


_DEFAULT_DATA_LAYER = DataLayer()


if __name__ == "__main__":
    recurring_count, cadence_distribution, sample_count = diagnostic()
    print(f"events in recurring series: {recurring_count} of {len(_DEFAULT_DATA_LAYER.events)}")
    print(f"detected cadence distribution: {dict(sorted(cadence_distribution.items(), key=lambda item: str(item[0])))}")
    print(
        "sample spending-change events detected as recurring: "
        f"{sample_count} of 4"
    )
    rejection_counts, long_gap_examples = rejection_diagnostics()
    print(f"rejection counts: {dict(rejection_counts)}")
    print("gap-exceeds-1.5x examples (up to 10):")
    for example in long_gap_examples[:10]:
        print(example)
