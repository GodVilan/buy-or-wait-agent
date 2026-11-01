"""Score the no-spending-change safe amount against public sample gold values."""

from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

CODE_DIR = Path(__file__).resolve().parents[1]
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from data_layer import DataLayer  # noqa: E402
from forecast import project  # noqa: E402


TOLERANCE = Decimal("0.01")


def predicted_safe_amount(sample, layer: DataLayer) -> Decimal:
    """Compute the safe amount from the baseline forecast, with no changes."""

    balances = project(sample.user_id, sample.request_date, 90, layer)
    profile = layer.profile_for(sample.user_id)
    capacity = min(
        (balance - profile.minimum_balance_to_keep for balance in balances.values()),
        default=Decimal("0"),
    )
    return max(Decimal("0"), min(sample.requested_amount, capacity))


def score(layer: DataLayer | None = None) -> list[tuple[str, Decimal, Decimal, Decimal]]:
    """Return request ID, predicted amount, gold amount, and absolute difference."""

    data = layer or DataLayer()
    results = []
    for sample in data.sample_requests:
        predicted = predicted_safe_amount(sample, data)
        difference = abs(predicted - sample.amount_safe_to_pay)
        results.append((sample.request_id, predicted, sample.amount_safe_to_pay, difference))
    return results


def main() -> None:
    results = score()
    for request_id, predicted, gold, difference in results:
        print(
            f"{request_id}: predicted={predicted} gold={gold} "
            f"absolute_difference={difference}"
        )

    exact = sum(difference == Decimal("0") for _, _, _, difference in results)
    within_tolerance = sum(
        difference <= TOLERANCE for _, _, _, difference in results
    )
    materially_wrong = sum(
        difference > TOLERANCE for _, _, _, difference in results
    )
    print(f"exact matches: {exact}")
    print(f"matches within 0.01: {within_tolerance}")
    print(f"materially wrong rows: {materially_wrong}")


if __name__ == "__main__":
    main()
