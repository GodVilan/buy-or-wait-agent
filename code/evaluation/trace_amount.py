"""Trace four public sample safe-amount calculations."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path

CODE_DIR = Path(__file__).resolve().parents[1]
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from data_layer import DataLayer, FinancialEventRecord  # noqa: E402
from forecast import (  # noqa: E402
    _included_for_cashflow,
    _series_occurrences,
    project,
)
from recurrence import RecurringSeries, classify, recurring_series  # noqa: E402


TRACE_REQUESTS = ("request_19", "request_05", "request_14", "request_02")


@dataclass(frozen=True)
class Contribution:
    event_id: str
    event_type: str
    status: str
    direction: str
    amount: Decimal | None
    settlement_date: date
    recurring: bool
    cadence_days: int | float | None
    anchor_date: date | None
    home_currency_amount: Decimal


def _series_anchor(
    series: RecurringSeries,
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
    anchor = max(
        historical or valid,
        key=lambda event: (event.settlement_date, event.event_id),
    )
    amount_event = max(
        [event for event in historical if event.amount is not None] or valid,
        key=lambda event: (event.settlement_date, event.event_id),
    )
    return anchor, amount_event


def contributions_for(
    user_id: str,
    as_of: date,
    layer: DataLayer,
    horizon_days: int = 90,
) -> tuple[Contribution, ...]:
    """Return every source/projected cash-flow contribution used by ``project``."""

    profile = layer.profile_for(user_id)
    events = list(layer.events_for(user_id))
    event_by_id = {event.event_id: event for event in events}
    end_date = as_of.fromordinal(as_of.toordinal() + horizon_days)
    series = recurring_series(user_id, layer)
    recurring_ids = {event_id for item in series for event_id in item.event_ids}
    rows: list[Contribution] = []

    for item in series:
        anchor_info = _series_anchor(item, event_by_id, as_of)
        if anchor_info is None:
            continue
        anchor, amount_event = anchor_info
        occurrences = _series_occurrences(
            item,
            event_by_id,
            as_of,
            end_date,
            layer,
            profile.home_currency,
        )
        for occurrence_date, signed_home_amount in occurrences:
            rows.append(
                Contribution(
                    event_id=amount_event.event_id,
                    event_type=amount_event.event_type,
                    status=amount_event.status,
                    direction=amount_event.direction,
                    amount=amount_event.amount,
                    settlement_date=occurrence_date,
                    recurring=True,
                    cadence_days=item.cadence_days,
                    anchor_date=anchor.settlement_date,
                    home_currency_amount=signed_home_amount,
                )
            )

    for event in events:
        if event.event_id in recurring_ids:
            continue
        if event.settlement_date is None or not (
            as_of < event.settlement_date <= end_date
        ):
            continue
        if not _included_for_cashflow(event, event_by_id):
            continue
        if event.amount is None:
            continue
        # Reuse project() for conversion behavior by calculating the one-event
        # delta through the same exact-date rate policy.
        from forecast import _signed_amount

        signed_home_amount = _signed_amount(
            event,
            profile.home_currency,
            event.settlement_date,
            layer,
        )
        if signed_home_amount is None:
            continue
        rows.append(
            Contribution(
                event_id=event.event_id,
                event_type=event.event_type,
                status=event.status,
                direction=event.direction,
                amount=event.amount,
                settlement_date=event.settlement_date,
                recurring=False,
                cadence_days=None,
                anchor_date=None,
                home_currency_amount=signed_home_amount,
            )
        )

    return tuple(sorted(rows, key=lambda row: (row.settlement_date, row.event_id)))


def _print_trace(row, layer: DataLayer) -> None:
    profile = layer.profile_for(row.user_id)
    balances = project(row.user_id, row.request_date, 90, layer)
    trough_date, trough_value = min(
        balances.items(), key=lambda item: (item[1], item[0])
    )
    contributions = contributions_for(row.user_id, row.request_date, layer)
    classification = classify(row.user_id, layer)
    flexible_one_time = sum(
        event.flexibility != "fixed" and classification[event.event_id] == "one_time"
        for event in layer.events_for(row.user_id)
    )

    print(f"\n=== {row.request_id} ({row.user_id}) ===")
    print(f"request_date: {row.request_date}")
    print(f"profile_balance: {profile.current_available_balance}")
    print(f"minimum_balance_to_keep: {profile.minimum_balance_to_keep}")
    print("projected balances:")
    print(f"  {row.request_date}: {balances[row.request_date]} (start)")
    for current_date, balance in balances.items():
        if current_date == row.request_date:
            continue
        print(f"  {current_date}: {balance}")
    print(f"trough: {trough_date} -> {trough_value}")

    print("contributing events:")
    for contribution in contributions:
        recurrence = "recurring" if contribution.recurring else "one-off"
        metadata = (
            f" cadence_days={contribution.cadence_days}"
            f" anchor_date={contribution.anchor_date}"
            if contribution.recurring
            else ""
        )
        print(
            f"  event_id={contribution.event_id} "
            f"event_type={contribution.event_type} "
            f"status={contribution.status} "
            f"direction={contribution.direction} "
            f"amount={contribution.amount} "
            f"settlement_date={contribution.settlement_date} "
            f"source={recurrence}{metadata} "
            f"home_currency_change={contribution.home_currency_amount}"
        )

    implied_gold_trough = profile.minimum_balance_to_keep + row.amount_safe_to_pay
    print(f"gold_amount_safe_to_pay: {row.amount_safe_to_pay}")
    print(f"implied_gold_trough: {implied_gold_trough}")
    print(f"computed_trough: {trough_value}")
    print(f"computed_minus_implied_trough: {trough_value - implied_gold_trough}")
    print(f"flexible_one_time_event_count: {flexible_one_time}")


def main() -> None:
    layer = DataLayer()
    samples = {row.request_id: row for row in layer.sample_requests}
    for request_id in TRACE_REQUESTS:
        _print_trace(samples[request_id], layer)


if __name__ == "__main__":
    main()
