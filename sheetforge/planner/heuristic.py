"""Explainable layout decisions; this module never imports or contacts an LLM."""

import re

from sheetforge.contracts import Bounds, CleaningPlan, TablePlan
from sheetforge.profiler import read_sheet
from sheetforge.values import MONTH, TOTAL, blank, date_like, guess_type, number


def crop(rows, bounds):
    return [
        row[bounds.column_start : bounds.column_end]
        for row in rows[bounds.row_start : bounds.row_end]
    ]


def header_names(grid, indexes, merged, bounds, *, transposed=False):
    rows = [list(grid[index]) for index in indexes]
    if not transposed:
        for merged_range in merged:
            anchor_row = merged_range["row_start"] - bounds.row_start
            anchor_col = merged_range["column_start"] - bounds.column_start
            if not (0 <= anchor_row < len(grid) and 0 <= anchor_col < len(grid[0])):
                continue
            anchor = grid[anchor_row][anchor_col]
            for output_row, index in enumerate(indexes):
                if merged_range["row_start"] <= index + bounds.row_start < merged_range["row_end"]:
                    for col in range(
                        max(0, anchor_col),
                        min(len(grid[0]), merged_range["column_end"] - bounds.column_start),
                    ):
                        if blank(rows[output_row][col]):
                            rows[output_row][col] = anchor
    names, seen = [], {}
    for col in range(len(grid[0])):
        parts = list(
            dict.fromkeys(
                re.sub(r"\s+", " ", str(row[col]).strip()) for row in rows if not blank(row[col])
            )
        )
        base = "_".join(parts) or f"Column_{col + 1}"
        count = seen.get(base, 0) + 1
        seen[base] = count
        names.append(base if count == 1 else f"{base}_{count}")
    # Existing source names can already contain suffixes; keep every name unique.
    used = set()
    for index, name in enumerate(names):
        while name in used:
            name += "_2"
        names[index] = name
        used.add(name)
    return names


def one_table(rows, merged, bounds, sheet, name):
    grid = crop(rows, bounds)
    transposed = (
        str(grid[0][0]).strip().lower() in {"metric", "field", "attribute", "measure"}
        and len(grid[0]) >= 3
    )
    if transposed:
        grid = [list(row) for row in zip(*grid, strict=True)]
    best, score = 0, -1.0
    for index, row in enumerate(grid[:-1][:20]):
        present = [value for value in row if not blank(value)]
        text = sum(
            isinstance(value, str) and number(value) is None and not date_like(value)
            for value in present
        )
        if not present or not text:
            continue
        following = [v for v in grid[index + 1] if not blank(v)]
        candidate = (
            len(present) / len(row)
            + text / len(present)
            + 2 * sum(number(v) is not None for v in following) / max(1, len(following))
        )
        if candidate > score:
            best, score = index, candidate
    if score < 0:
        raise ValueError(
            f"No credible text header in {sheet}/{name}; supply an explicit CleaningPlan."
        )
    header_rows = [best]
    if not transposed and best + 2 < len(grid):
        horizontal = any(
            item["row_start"] == best + bounds.row_start
            and item["column_end"] - item["column_start"] > 1
            for item in merged
        )
        if horizontal and any(
            isinstance(value, str) for value in grid[best + 1] if not blank(value)
        ):
            header_rows.append(best + 1)
    names = header_names(grid, header_rows, merged, bounds, transposed=transposed)
    start = max(header_rows) + 1
    totals = [
        index
        for index in range(start, len(grid))
        if any(TOTAL.match(str(value).strip()) for value in grid[index][:2] if not blank(value))
    ]
    types = {
        column: guess_type(
            column, [grid[index][j] for index in range(start, len(grid)) if index not in totals]
        )
        for j, column in enumerate(names)
    }
    months = [column for column in names if MONTH.fullmatch(column)]
    return TablePlan(
        table_name=name,
        sheet_name=sheet,
        bounds=bounds,
        header_row_index=best,
        data_start_row=start,
        data_end_row=len(grid),
        orientation="transposed" if transposed else "normal",
        multi_row_header_rows=header_rows if len(header_rows) > 1 else [],
        unpivot_columns=months if len(months) >= 2 and len(months) < len(names) else [],
        totals_rows_to_drop=totals,
        column_types=types,
    )


def heuristic_plan(profile, sheet_name=None):
    sheets = [sheet_name] if sheet_name else list(profile.sheets)
    plans = []
    for sheet in sheets:
        if sheet not in profile.sheets:
            raise ValueError("Worksheet is not present in the profile.")
        rows, merged = read_sheet(profile.source, sheet)
        for index, raw_bounds in enumerate(profile.sheets[sheet]["blocks"], 1):
            bounds = Bounds.model_validate(raw_bounds)
            plans.append(one_table(rows, merged, bounds, sheet, f"{sheet}_Table{index}"))
    if not plans:
        raise ValueError(
            "No nonempty numeric tables were found. Select a sheet with headers and data."
        )
    if len(plans) > 32:
        raise ValueError("Use workbooks with at most 32 separate tables.")
    return CleaningPlan(**plans[0].model_dump(), tables_split=plans[1:])
