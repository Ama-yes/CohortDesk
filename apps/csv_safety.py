"""CSV export cells are data, never a formula.

A CSV opened in Excel or LibreOffice treats a cell whose text begins with
"=", "+", "-", "@", a tab, a carriage return or a newline as a formula and may
run it (CSV formula injection). Student names, attendance notes, cohort and
assessment titles are free text a center typed, so an export must carry them
as text. Leading ordinary spaces are inspected too, so "  =1+1" cannot hide
the formula character from the writer; a leading tab or line break is itself
dangerous and is never stripped.

Plain decimal numbers, including negative amounts, are data of a different
shape and stay exactly as they were written. Every other formula-looking cell
is prefixed with an apostrophe, which spreadsheets read as "this cell is text"
and do not display as part of the value.

`csv_writer` is the one writer every export uses, so no export can forget the
rule (SEC-001's habit: the safe path is the only path).
"""

import csv
import re

# Characters a spreadsheet reads as the start of a formula (OWASP CSV
# injection guidance), plus the tab and line breaks that also trigger it.
FORMULA_START = ("=", "+", "-", "@", "\t", "\r", "\n")
# Spaces may hide a formula character, but the tab and line breaks below are
# dangerous in their own right and must not be stripped before the check.
LEADING_SPACE = " \u00a0\v\f"
# Decimal() also accepts "-Infinity", "-NaN" and "-1_000"; none is an ordinary
# number cell, so the exemption uses a strict decimal shape instead.
PLAIN_NUMBER = re.compile(
    r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"
)


def _plain_number(text):
    """Is this text an ordinary decimal number, including a negative one?"""
    return PLAIN_NUMBER.fullmatch(text) is not None


def csv_cell(value):
    """One exported cell as spreadsheet text, never as a formula."""
    text = "" if value is None else str(value)
    visible = text.lstrip(LEADING_SPACE)
    if not visible or not visible.startswith(FORMULA_START):
        return text
    if visible.startswith("-") and _plain_number(visible):
        return text
    return "'" + text


def csv_writer(response):
    """A row writer for `response` that neutralises every cell it writes."""
    writer = csv.writer(response)

    def write_row(values):
        writer.writerow([csv_cell(value) for value in values])

    return write_row
