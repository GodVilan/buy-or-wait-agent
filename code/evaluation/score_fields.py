"""Score the deterministic pipeline against the 25 public sample rows."""

from __future__ import annotations

import sys
from collections import Counter, defaultdict
from datetime import date
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CODE = ROOT / "code"
if str(CODE) not in sys.path:
    sys.path.insert(0, str(CODE))

from data_layer import DataLayer  # noqa: E402
from main import build_row  # noqa: E402


def _amount(value: str) -> Decimal:
    return Decimal(value.replace(",", ""))


def _field_matches(field: str, predicted: str, gold) -> bool:
    if field == "earliest_date_for_full_payment":
        predicted_date = date.fromisoformat(predicted) if predicted else None
        return predicted_date == gold
    return predicted == gold


def main() -> None:
    layer = DataLayer()
    fields = (
        "affordability_status",
        "recommended_payment_method",
        "payment_plan",
        "earliest_date_for_full_payment",
        "spending_changes_needed",
        "decision_explanation",
    )
    counts = Counter()
    status_matrix: defaultdict[str, Counter[str]] = defaultdict(Counter)
    method_matrix: defaultdict[str, Counter[str]] = defaultdict(Counter)
    explanation_differences: list[tuple[str, str, str]] = []

    for sample in layer.sample_requests:
        predicted = build_row(layer, sample)
        counts["rows"] += 1
        predicted_amount = _amount(predicted["amount_safe_to_pay"])
        gold_amount = sample.amount_safe_to_pay
        counts["amount_exact"] += predicted_amount == gold_amount
        counts["amount_within_0.01"] += abs(predicted_amount - gold_amount) <= Decimal("0.01")
        for field in fields:
            counts[field] += _field_matches(field, predicted[field], getattr(sample, field))
        status_matrix[sample.affordability_status][predicted["affordability_status"]] += 1
        method_matrix[sample.recommended_payment_method][predicted["recommended_payment_method"]] += 1
        if predicted["decision_explanation"] != sample.decision_explanation:
            explanation_differences.append(
                (
                    sample.request_id,
                    sample.decision_explanation,
                    predicted["decision_explanation"],
                )
            )

    print(f"rows={counts['rows']}")
    print(f"amount_safe_to_pay exact={counts['amount_exact']}/{counts['rows']}")
    print(f"amount_safe_to_pay within_0.01={counts['amount_within_0.01']}/{counts['rows']}")
    for field in fields:
        print(f"{field} exact={counts[field]}/{counts['rows']}")

    print("status confusion matrix (gold -> predicted)")
    for gold in sorted(status_matrix):
        print(f"  {gold}: {dict(sorted(status_matrix[gold].items()))}")

    print("method confusion matrix (gold -> predicted)")
    for gold in sorted(method_matrix):
        print(f"  {gold}: {dict(sorted(method_matrix[gold].items()))}")

    print(f"decision_explanation differences={len(explanation_differences)}")
    for request_id, gold, predicted in explanation_differences:
        print(f"{request_id}")
        print(f"  GOLD: {gold}")
        print(f"  PRED: {predicted}")


if __name__ == "__main__":
    main()
