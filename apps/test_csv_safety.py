"""The CSV export cell rule: text a center typed is data, not a formula.

Every export writes through `apps.csv_safety.csv_writer`, so the rule is the
same in every file: a cell whose first character after ordinary spaces is
"=", "+", "-", "@", a tab, a carriage return or a newline is prefixed with an
apostrophe, which spreadsheets read as text and do not display. Plain decimal
numbers, including negative amounts, are left intact.
"""

import csv
from decimal import Decimal
import io

from django.test import SimpleTestCase

from apps.csv_safety import csv_cell, csv_writer

# The same values every export test writes into a user-controlled field.
FORMULA_CELLS = (
    "=1+1",
    "+44 7700",
    "-1+1",
    "@SUM(A1)",
    "\tcmd",
    "\r=1",
    "\n=1",
    "  =1+1",
    "\t@SUM(A1)",
)


def exported_rows(response):
    """Parse a CSV response without normalising line breaks inside cells."""
    return list(csv.reader(io.StringIO(response.content.decode(), newline="")))


class CsvCellTests(SimpleTestCase):
    """The neutralisation answers exactly the formula starts, nothing else."""

    def test_plain_text_is_unchanged(self):
        for value in (
            "Alpha Learner",
            "AC-1",
            "0.00",
            "",
            "a=b",
            "cost - fee",
            " -44",
        ):
            self.assertEqual(csv_cell(value), value)

    def test_a_number_is_exported_as_its_text(self):
        for value in (
            7,
            -44,
            Decimal("-12.50"),
            Decimal("21.00"),
            Decimal("-1E+3"),
            "-.5",
        ):
            self.assertEqual(csv_cell(value), str(value))
        self.assertEqual(csv_cell(None), "")

    def test_decimal_lookalikes_are_still_neutralised(self):
        """Decimal() accepts non-numeric tokens; a spreadsheet must not."""
        for value in ("-Infinity", "-NaN", "-1_000", "-1,000", "- 44"):
            self.assertEqual(csv_cell(value), "'" + value)

    def test_a_formula_start_is_prefixed(self):
        for value in (
            "=1+1",
            "+44 7700",
            "-1+1",
            "@SUM(A1)",
            "\tcmd",
            "\r=1",
            "\n=1",
        ):
            self.assertEqual(csv_cell(value), "'" + value)

    def test_leading_whitespace_cannot_hide_the_formula_character(self):
        for value in (
            " =1+1",
            "\t=1+1",
            "\r=1+1",
            "\n=1+1",
            "  @SUM(A1)",
            "\t\t+44",
        ):
            self.assertEqual(csv_cell(value), "'" + value)

    def test_the_writer_neutralises_every_cell_it_writes(self):
        output = io.StringIO()
        csv_writer(output)(
            ["=1+1", "  @SUM(A1)", Decimal("-12.50"), None, "Alpha Learner"]
        )
        rows = list(csv.reader(io.StringIO(output.getvalue())))
        self.assertEqual(
            rows,
            [
                [
                    "'=1+1",
                    "'  @SUM(A1)",
                    "-12.50",
                    "",
                    "Alpha Learner",
                ]
            ],
        )
