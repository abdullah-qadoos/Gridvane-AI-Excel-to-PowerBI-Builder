"""Only deterministic pandas operations transform workbook data."""

from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from sheetforge.contracts import AppliedStep, CleaningPlan
from sheetforge.planner.heuristic import crop, header_names
from sheetforge.profiler import read_sheet
from sheetforge.values import dates, number


@dataclass
class TransformResult:
    tables: dict[str, pd.DataFrame] = field(default_factory=dict)
    steps: list[AppliedStep] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    plan: CleaningPlan | None = None
    source: Path | None = None


def apply_plan(path, plan):
    plan = CleaningPlan.model_validate(
        plan.model_dump() if isinstance(plan, CleaningPlan) else plan
    )
    result = TransformResult(plan=plan, source=Path(path).resolve())
    for table in [plan, *plan.tables_split]:
        rows, merged = read_sheet(path, table.sheet_name)
        if table.bounds.row_end > len(rows) or table.bounds.column_end > max(
            map(len, rows), default=0
        ):
            raise ValueError("Plan bounds exceed the source worksheet.")
        grid = crop(rows, table.bounds)

        def step(action, description, before, after, parameters=None, table_name=table.table_name):
            result.steps.append(
                AppliedStep(
                    table=table_name,
                    action=action,
                    description=description,
                    rows_before=before,
                    rows_after=after,
                    parameters=parameters or {},
                )
            )

        step(
            "select_block",
            f"Selected {table.sheet_name}: rows {table.bounds.row_start + 1}–{table.bounds.row_end}, columns {table.bounds.column_start + 1}–{table.bounds.column_end}.",
            len(rows),
            len(grid),
            table.bounds.model_dump(),
        )
        if table.orientation == "transposed":
            before = len(grid)
            grid = [list(row) for row in zip(*grid, strict=True)]
            step("transpose", "Transposed the table so records run down rows.", before, len(grid))
        headers = table.multi_row_header_rows or [table.header_row_index]
        names = header_names(
            grid, headers, merged, table.bounds, transposed=table.orientation == "transposed"
        )
        step(
            "promote_headers",
            f"Promoted oriented row(s) {', '.join(str(row + 1) for row in headers)} into column names.",
            len(grid),
            table.data_end_row - table.data_start_row,
            {"columns": names},
        )
        if table.multi_row_header_rows:
            step(
                "flatten_headers",
                "Forward-filled merged header labels and flattened distinct header parts with underscores.",
                len(grid),
                len(grid),
                {"header_rows": headers},
            )
        indexes = [
            index
            for index in range(table.data_start_row, table.data_end_row)
            if index not in table.totals_rows_to_drop
        ]
        frame = pd.DataFrame([grid[index] for index in indexes], columns=names)
        if table.totals_rows_to_drop:
            step(
                "drop_totals",
                f"Removed {len(table.totals_rows_to_drop)} total/subtotal row(s) to prevent double counting.",
                table.data_end_row - table.data_start_row,
                len(frame),
                {"rows": table.totals_rows_to_drop},
            )
        before = len(frame)
        frame = frame.replace(r"^\s*$", pd.NA, regex=True).dropna(how="all")
        step("drop_blank_rows", "Removed wholly blank records.", before, len(frame))
        if not set(table.column_renames) <= set(frame.columns):
            raise ValueError("Column renames reference unavailable source columns.")
        frame = frame.rename(columns=table.column_renames)
        if len(set(frame.columns)) != len(frame.columns):
            raise ValueError("Column renames must preserve distinct column names.")
        if table.column_renames:
            step(
                "rename_columns",
                "Applied the reviewed column names.",
                len(frame),
                len(frame),
                table.column_renames,
            )
        types = {
            table.column_renames.get(key, key): kind for key, kind in table.column_types.items()
        }
        if table.unpivot_columns:
            columns = [table.column_renames.get(key, key) for key in table.unpivot_columns]
            if len(columns) != len(set(columns)) or not set(columns) <= set(frame.columns):
                raise ValueError("Unpivot references missing or duplicate columns.")
            if {"Period", "Value"} & set(frame.columns):
                raise ValueError("Rename existing Period/Value columns before unpivoting.")
            before = len(frame)
            frame = frame.melt(
                id_vars=[key for key in frame if key not in columns],
                value_vars=columns,
                var_name="Period",
                value_name="Value",
            )
            types = {key: kind for key, kind in types.items() if key not in columns} | {
                "Period": "string",
                "Value": "number",
            }
            step(
                "unpivot",
                f"Unpivoted {len(columns)} wide columns into Period and Value.",
                before,
                len(frame),
                {"columns": columns},
            )
        if not set(types) <= set(frame.columns):
            raise ValueError("Column types reference unavailable columns.")
        for column in frame:
            kind = types.get(column, "string")
            values = frame[column]
            present = values.notna() & values.astype("string").str.strip().ne("")
            if kind in {"number", "integer"}:
                converted = values.map(number).astype("Float64")
                if kind == "integer":
                    converted = converted.where(converted.mod(1).eq(0)).astype("Int64")
            elif kind == "date":
                converted = dates(values)
            elif kind == "boolean":
                converted = values.map(
                    lambda value: {"true": True, "false": False, "1": True, "0": False}.get(
                        str(value).strip().lower()
                    )
                ).astype("boolean")
            else:
                converted = values.astype("string").str.strip()
            invalid = int((present & converted.isna()).sum())
            if invalid:
                result.warnings.append(
                    f"{table.table_name}.{column}: {invalid} nonblank values could not be parsed as {kind}; retained as missing."
                )
            frame[column] = converted
            step(
                "cast_column",
                f"Parsed {column} as {kind}; {invalid} invalid value(s) marked missing.",
                len(frame),
                len(frame),
                {"column": column, "type": kind, "invalid": invalid},
            )
        frame = frame.reset_index(drop=True)
        result.tables[table.table_name] = frame
    return result
