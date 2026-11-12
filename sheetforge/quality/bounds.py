"""LLM-proposed plausibility bounds. The model sees column statistics, never rows."""

import json

import pandas as pd
from pydantic import Field, model_validator

from sheetforge.contracts import Contract
from sheetforge.llm.client import CompletionUnavailable, complete, enabled
from sheetforge.quality.checks import NUMERIC, missing_mask, parsed
from sheetforge.quality.models import Rule

SYSTEM = """You propose plausibility bounds for spreadsheet columns. You only see column statistics.
For each column with a clear real-world limit return {column, min, max, before_column}.
min/max are numbers for numeric columns and YYYY-MM-DD strings for date columns; use null when unbounded.
before_column names another column of the same type whose value must be >= this column (e.g. start before end).
Omit columns without a clear limit. Never invent column names."""


class ProposedBound(Contract):
    column: str
    min: float | str | None = None
    max: float | str | None = None
    before_column: str | None = None


class BoundsProposal(Contract):
    bounds: list[ProposedBound] = Field(default_factory=list, max_length=200)


def column_stats(frame, types):
    stats = []
    for column, kind in types.items():
        values = parsed(frame[column], kind)
        item = {
            "column": column,
            "type": kind,
            "null_pct": round(float(missing_mask(frame[column]).mean() * 100), 1)
            if len(frame)
            else 0,
        }
        if values is not None and values.notna().any():
            if kind == "date":
                item |= {
                    "min": values.min().date().isoformat(),
                    "max": values.max().date().isoformat(),
                }
            else:
                item |= {
                    "min": float(values.min()),
                    "max": float(values.max()),
                    "mean": round(float(values.mean()), 4),
                }
        stats.append(item)
    return stats


def coerce(value, kind):
    if value is None:
        return None
    if kind == "date":
        return pd.Timestamp(value).date().isoformat()
    if isinstance(value, str):
        raise ValueError("Numeric columns need numeric bounds.")
    return float(value)


def propose_rules(frame, types, *, use_ai=True):
    if not use_ai or not enabled():
        return [], "Impossible-value checks use generic rules only (no-AI mode)."
    typed = {column: kind for column, kind in types.items() if kind in NUMERIC | {"date"}}
    if not typed:
        return [], "No numeric or date columns need bounds."

    class CheckedProposal(BoundsProposal):
        @model_validator(mode="after")
        def known_columns(self):
            for bound in self.bounds:
                if bound.column not in typed:
                    raise ValueError(f"{bound.column} is not a numeric or date column.")
                if bound.before_column and (
                    bound.before_column == bound.column
                    or typed.get(bound.before_column) != typed[bound.column]
                ):
                    raise ValueError("before_column must be another column of the same type.")
                coerce(bound.min, typed[bound.column])
                coerce(bound.max, typed[bound.column])
            return self

    try:
        reply = CheckedProposal.model_validate(
            complete(
                SYSTEM,
                [{"role": "user", "content": json.dumps({"columns": column_stats(frame, typed)})}],
                CheckedProposal,
            )
        )
    except (CompletionUnavailable, ValueError):
        return [], "LLM bounds were unavailable or invalid; used generic rules only."
    rules = []
    for bound in reply.bounds:
        kind = typed[bound.column]
        if bound.min is not None or bound.max is not None:
            rules.append(
                Rule(
                    column=bound.column,
                    kind="bounds",
                    min=coerce(bound.min, kind),
                    max=coerce(bound.max, kind),
                    source="llm",
                )
            )
        if bound.before_column:
            rules.append(
                Rule(
                    column=bound.column,
                    kind="order",
                    before_column=bound.before_column,
                    source="llm",
                )
            )
    return rules, f"Added {len(rules)} LLM-proposed rule(s) validated against the columns."
