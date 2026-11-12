"""Quality report, user decisions, and audit contracts."""

from typing import Literal

from pydantic import Field

from sheetforge.contracts import Contract

Category = Literal[
    "formatting",
    "missing",
    "type_consistency",
    "impossible_values",
    "exact_duplicates",
    "near_duplicates",
]
Action = Literal["fix_for_me", "i_will_fix", "ignore"]
Strategy = Literal[
    "normalize",
    "fill_mean",
    "fill_median",
    "fill_zero",
    "fill_unknown",
    "ffill",
    "drop",
    "nullify",
    "clamp",
    "keep_first",
    "keep_latest",
    "keep_max",
]

# Fix order matters: lossless normalization first, row removal last.
STRATEGIES: dict[str, list[str]] = {
    "formatting": ["normalize"],
    "type_consistency": ["nullify", "drop"],
    "impossible_values": ["nullify", "clamp", "drop"],
    "missing": ["fill_mean", "fill_median", "fill_zero", "fill_unknown", "ffill", "drop"],
    "exact_duplicates": ["drop"],
    "near_duplicates": ["keep_first", "keep_latest", "keep_max"],
}


class ColumnQuality(Contract):
    column: str
    type: str
    null_pct: float
    placeholder_nulls: int
    type_mismatches: int
    formatting_issues: int
    impossible_values: int


class Rule(Contract):
    column: str
    kind: Literal["negative_count", "percent_range", "date_range", "bounds", "order"]
    min: float | str | None = None
    max: float | str | None = None
    before_column: str | None = None
    source: Literal["generic", "llm"] = "generic"
    count: int = 0


class NearDuplicateGroup(Contract):
    key: list[str]
    rows: list[int] = Field(description="One-based row numbers in the cleaned table.")


class CategorySummary(Contract):
    category: Category
    count: int
    detail: str
    strategies: list[Strategy]
    default_action: Action | None = None
    default_strategy: Strategy | None = None


class QualityReport(Contract):
    rows: int
    types: dict[str, str]
    columns: list[ColumnQuality]
    rules: list[Rule]
    exact_duplicates: int
    duplicate_key: list[str]
    near_duplicate_groups: list[NearDuplicateGroup]
    near_duplicate_group_count: int
    categories: list[CategorySummary]


class Decision(Contract):
    action: Action
    strategy: Strategy | None = None
    key: list[str] | None = None


class AuditEntry(Contract):
    category: Category
    rule: str
    column: str | None = None
    cells_changed: int = 0
    rows_removed: int = 0
    detail: str = ""


class AuditLog(Contract):
    entries: list[AuditEntry] = Field(default_factory=list)
    user_will_fix: list[Category] = Field(default_factory=list)
    ignored: list[Category] = Field(default_factory=list)

    @property
    def stopped(self):
        return bool(self.user_will_fix)
