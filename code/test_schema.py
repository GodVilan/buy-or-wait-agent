"""Regression tests for the three output amount formats.

Run from the repository root with:
    python3 code/test_schema.py
"""

from __future__ import annotations

import csv
import sys
import unittest
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CODE = ROOT / "code"
if str(CODE) not in sys.path:
    sys.path.insert(0, str(CODE))

from schema import format_csv_amount, format_display_amount, format_plan_amount


SAMPLE_REQUESTS = (
    Path(__file__).resolve().parents[1] / "dataset" / "sample_requests.csv"
)


class SampleAmountFormattingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        with SAMPLE_REQUESTS.open("r", encoding="utf-8", newline="") as handle:
            cls.rows = list(csv.DictReader(handle))

    def test_csv_amounts_match_all_25_sample_rows(self) -> None:
        """CSV formatting reproduces every amount_safe_to_pay string exactly."""

        self.assertEqual(len(self.rows), 25)
        for row in self.rows:
            with self.subTest(request_id=row["request_id"]):
                self.assertEqual(
                    format_csv_amount(Decimal(row["amount_safe_to_pay"])),
                    row["amount_safe_to_pay"],
                )

    def test_plan_amounts_match_every_sample_plan_entry(self) -> None:
        """Plan formatting reproduces every payment_plan amount exactly."""

        for row in self.rows:
            if row["payment_plan"] == "none":
                continue
            for entry in row["payment_plan"].split("|"):
                plan_date, amount = entry.split(":", 1)
                with self.subTest(request_id=row["request_id"], date=plan_date):
                    self.assertEqual(format_plan_amount(Decimal(amount)), amount)

    def test_display_examples_match_explanation_prose(self) -> None:
        """Display formatting matches the grouped prose examples."""

        self.assertEqual(format_display_amount(Decimal("25256")), "25,256")
        self.assertEqual(
            format_display_amount(Decimal("15952906.67")), "15,952,906.67"
        )
        self.assertEqual(format_display_amount(Decimal("620.4")), "620.40")


if __name__ == "__main__":
    unittest.main()
