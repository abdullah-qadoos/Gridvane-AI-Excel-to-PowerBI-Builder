from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from zipfile import ZipFile

from openpyxl import load_workbook

from sheetforge.contracts import Bounds
from sheetforge.values import blank, guess_type, number

MAX_ROWS = 100_000
MAX_COLUMNS = 256


def serial(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value[:200] if isinstance(value, str) else value


def read_sheet(path, sheet):
    with ZipFile(path) as archive:
        if sum(item.file_size for item in archive.infolist()) > 128 * 1024 * 1024:
            raise ValueError("Workbook expanded size exceeds 128 MB.")
    book = load_workbook(path, data_only=True, read_only=False, keep_links=False)
    try:
        worksheet = book[sheet]
        if worksheet.max_row > MAX_ROWS or worksheet.max_column > MAX_COLUMNS:
            raise ValueError("Use worksheets with at most 100,000 rows and 256 columns.")
        rows = [list(row) for row in worksheet.iter_rows(values_only=True)]
        merged = [
            {
                "row_start": cell.min_row - 1,
                "row_end": cell.max_row,
                "column_start": cell.min_col - 1,
                "column_end": cell.max_col,
            }
            for cell in worksheet.merged_cells.ranges
        ]
        return rows, merged
    finally:
        book.close()


def bands(indices):
    result = []
    for index in indices:
        if not result or index != result[-1][-1] + 1:
            result.append([index])
        else:
            result[-1].append(index)
    return result


def blocks(rows):
    found = []
    for row_band in bands([i for i, row in enumerate(rows) if any(not blank(v) for v in row)]):
        columns = sorted(
            {j for i in row_band for j, value in enumerate(rows[i]) if not blank(value)}
        )
        for column_band in bands(columns):
            values = [rows[i][j] for i in row_band for j in column_band]
            if len(row_band) >= 2 and any(number(v) is not None for v in values):
                found.append(
                    Bounds(
                        row_start=row_band[0],
                        row_end=row_band[-1] + 1,
                        column_start=column_band[0],
                        column_end=column_band[-1] + 1,
                    )
                )
    return found


@dataclass
class WorkbookProfile:
    source: Path
    sheets: dict

    def to_dict(self):
        return {"source_name": self.source.name, "sheets": self.sheets}

    def llm_context(self, sheet):
        item = self.sheets[sheet]
        return {
            "sheet": sheet,
            "shape": item["shape"],
            "blocks": item["blocks"],
            "merged_cells": item["merged_cells"][:100],
            "columns": item["columns"],
            "row_statistics": item["row_statistics"][:50],
            "sample": item["sample"],
        }


def profile_workbook(path):
    path = Path(path).resolve()
    if path.suffix.lower() != ".xlsx" or not path.is_file():
        raise ValueError("Select an existing .xlsx workbook.")
    with ZipFile(path) as archive:
        if sum(item.file_size for item in archive.infolist()) > 128 * 1024 * 1024:
            raise ValueError("Workbook expanded size exceeds 128 MB.")
    book = load_workbook(path, read_only=True, data_only=True, keep_links=False)
    try:
        names = book.sheetnames
        if len(names) > 64:
            raise ValueError("Use workbooks with at most 64 sheets.")
    finally:
        book.close()
    sheets = {}
    for name in names:
        rows, merged = read_sheet(path, name)
        width = max(map(len, rows), default=0)
        row_statistics = []
        for index, row in enumerate(rows):
            present = [v for v in row if not blank(v)]
            row_statistics.append(
                {
                    "row": index,
                    "nonblank": len(present),
                    "numeric_ratio": sum(number(v) is not None for v in present) / len(present)
                    if present
                    else 0,
                }
            )
        sample_indexes = sorted(
            set([*range(min(20, len(rows))), *range(max(0, len(rows) - 5), len(rows))])
        )
        sheets[name] = {
            "shape": [len(rows), width],
            "blank_rows": [i for i, row in enumerate(rows) if all(blank(v) for v in row)],
            "blank_columns": [j for j in range(width) if all(blank(row[j]) for row in rows)],
            "merged_cells": merged,
            "blocks": [block.model_dump() for block in blocks(rows)],
            "row_statistics": row_statistics,
            "columns": [
                {"index": j, "dtype_guess": guess_type("", [row[j] for row in rows[1:]])}
                for j in range(width)
            ],
            "sample": [
                {"row": i, "values": [serial(value) for value in rows[i]]} for i in sample_indexes
            ],
        }
    return WorkbookProfile(path, sheets)
