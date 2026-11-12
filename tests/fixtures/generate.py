"""Reproducible messy workbooks: run with python tests/fixtures/generate.py."""

from pathlib import Path

from openpyxl import Workbook

ROOT = Path(__file__).parent


def save(name, rows, configure=None):
    book = Workbook()
    sheet = book.active
    sheet.title = "Data"
    for row in rows:
        sheet.append(row)
    if configure:
        configure(sheet)
    book.save(ROOT / name)
    book.close()


def generate():
    save(
        "header_not_row1.xlsx",
        [
            ["Quarterly operating report"],
            [],
            ["Region", "Revenue", "Units"],
            ["North", 1200, 12],
            ["South", 1600, 16],
        ],
    )
    save(
        "transposed.xlsx",
        [["Metric", "North", "South"], ["Revenue", 1200, 1600], ["Units", 12, 16]],
    )
    save(
        "wide_months.xlsx",
        [["Region", "Jan", "Feb", "Mar"], ["North", 10, 20, 30], ["South", 40, 50, 60]],
    )

    def merge(sheet):
        sheet.merge_cells("A1:A2")
        sheet.merge_cells("B1:C1")

    save(
        "merged_headers.xlsx",
        [
            ["Region", "Sales", None],
            [None, "2025", "2026"],
            ["North", 100, 120],
            ["South", 200, 240],
        ],
        merge,
    )
    save(
        "totals.xlsx",
        [
            ["Region", "Revenue"],
            ["North", 100],
            ["Subtotal", 100],
            ["South", 200],
            ["Grand Total", 300],
        ],
    )
    save(
        "two_tables.xlsx",
        [
            ["Region", "Revenue"],
            ["North", 100],
            ["South", 200],
            [],
            ["Product", "Units"],
            ["A", 10],
            ["B", 20],
        ],
    )
    save(
        "currency_units.xlsx",
        [
            ["Region", "Revenue", "Weight"],
            ["North", "$1,200.50", "12 kg"],
            ["South", "€1.234,50", "7.5 kg"],
        ],
    )
    save("text_dates.xlsx", [["Date", "Revenue"], ["13/01/2026", 100], ["14/01/2026", 200]])
    save(
        "horizontal_tables.xlsx",
        [
            ["Region", "Revenue", None, "Product", "Units"],
            ["North", 100, None, "A", 10],
            ["South", 200, None, "B", 20],
        ],
    )


if __name__ == "__main__":
    generate()
