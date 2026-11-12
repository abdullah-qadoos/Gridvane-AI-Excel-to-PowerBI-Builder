"""The planner sends profiles and bounded samples, never full workbook tables."""

import json

from pydantic import model_validator

from sheetforge.contracts import CleaningPlan
from sheetforge.llm.client import CompletionUnavailable, complete, enabled
from sheetforge.planner.heuristic import crop, header_names, heuristic_plan
from sheetforge.profiler import read_sheet

SYSTEM = """You decide how to clean a spreadsheet. You do not execute transformations.
Return a CleaningPlan as strict JSON, using only the supplied source bounds and sample.
All bounds are zero-based/end-exclusive; row indexes are relative to each cropped block
AFTER any transpose. Never invent source columns or data. Keep separate tables separate.
Use multi-row headers only when supported by merged-cell layout. Totals must not double count.
The supplied deterministic plan is a safe starting point. Do not emit Python, SQL, or rewritten data."""


def plan_cleaning(profile, sheet_name=None):
    fallback = heuristic_plan(profile, sheet_name)
    if not enabled():
        return fallback, ["No-AI mode: used deterministic layout and type heuristics."]
    sheets = [sheet_name] if sheet_name else list(profile.sheets)
    context = {
        "sheets": [profile.llm_context(sheet) for sheet in sheets],
        "heuristic_plan": fallback.model_dump(),
    }

    class SourceCleaningPlan(CleaningPlan):
        @model_validator(mode="after")
        def source_bounds(self):
            for table in [self, *self.tables_split]:
                if table.sheet_name not in sheets:
                    raise ValueError("Plan references an unavailable worksheet.")
                shape = profile.sheets[table.sheet_name]["shape"]
                if table.bounds.row_end > shape[0] or table.bounds.column_end > shape[1]:
                    raise ValueError("Plan bounds exceed the source shape.")
                rows, merged = read_sheet(profile.source, table.sheet_name)
                grid = crop(rows, table.bounds)
                if table.orientation == "transposed":
                    grid = [list(row) for row in zip(*grid, strict=True)]
                names = header_names(
                    grid,
                    table.multi_row_header_rows or [table.header_row_index],
                    merged,
                    table.bounds,
                    transposed=table.orientation == "transposed",
                )
                renamed = [table.column_renames.get(name, name) for name in names]
                if not set(table.column_renames) <= set(names) or len(renamed) != len(set(renamed)):
                    raise ValueError(
                        "Column renames must reference source headers and remain distinct."
                    )
                if not set(table.column_types) <= set(names) | set(renamed):
                    raise ValueError("Column types reference unavailable headers.")
                unpivot = [table.column_renames.get(name, name) for name in table.unpivot_columns]
                if len(unpivot) != len(set(unpivot)) or not set(unpivot) <= set(renamed):
                    raise ValueError("Unpivot references unavailable or duplicate headers.")
                if unpivot and {"Period", "Value"} & set(renamed):
                    raise ValueError("Rename existing Period/Value columns before unpivoting.")
            return self

    try:
        candidate = CleaningPlan.model_validate(
            complete(
                SYSTEM,
                [{"role": "user", "content": json.dumps(context, ensure_ascii=False)}],
                SourceCleaningPlan,
            )
        )
        return candidate, [
            "Used an LLM plan validated by Pydantic and checked against source bounds."
        ]
    except (CompletionUnavailable, ValueError):
        return fallback, ["LLM planning was unavailable or invalid; used deterministic heuristics."]
