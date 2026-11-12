"""Strict, executable-free cleaning contracts. All indexes are zero-based."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Bounds(Contract):
    row_start: int = Field(ge=0)
    row_end: int = Field(gt=0)
    column_start: int = Field(ge=0)
    column_end: int = Field(gt=0)

    @model_validator(mode="after")
    def ordered(self):
        if self.row_end <= self.row_start or self.column_end <= self.column_start:
            raise ValueError("Block bounds must have positive area; end indexes are exclusive.")
        return self


ColumnType = Literal["string", "number", "integer", "date", "boolean"]


class TablePlan(Contract):
    table_name: str = Field(min_length=1, max_length=100)
    sheet_name: str = Field(min_length=1, max_length=100)
    bounds: Bounds
    header_row_index: int = Field(default=0, ge=0)
    data_start_row: int = Field(default=1, ge=0)
    data_end_row: int = Field(gt=0)
    orientation: Literal["normal", "transposed"] = "normal"
    multi_row_header_rows: list[int] = Field(default_factory=list, max_length=10)
    unpivot_columns: list[str] = Field(default_factory=list, max_length=256)
    totals_rows_to_drop: list[int] = Field(default_factory=list, max_length=100_000)
    column_renames: dict[str, str] = Field(default_factory=dict)
    column_types: dict[str, ColumnType] = Field(default_factory=dict)

    @model_validator(mode="after")
    def valid_rows(self):
        length = (
            (self.bounds.column_end - self.bounds.column_start)
            if self.orientation == "transposed"
            else (self.bounds.row_end - self.bounds.row_start)
        )
        headers = self.multi_row_header_rows or [self.header_row_index]
        if any(row < 0 or row >= length for row in headers):
            raise ValueError("Header rows must be inside the oriented block.")
        if len(headers) != len(set(headers)) or headers != sorted(headers):
            raise ValueError("Header rows must be distinct and ordered.")
        if not max(headers) < self.data_start_row < self.data_end_row <= length:
            raise ValueError("Data must follow the header and remain inside the oriented block.")
        if any(
            not self.data_start_row <= row < self.data_end_row for row in self.totals_rows_to_drop
        ):
            raise ValueError("Total row indexes must lie inside the data range.")
        if any(not key.strip() or not value.strip() for key, value in self.column_renames.items()):
            raise ValueError("Column renames must contain nonblank names.")
        return self


class CleaningPlan(TablePlan):
    tables_split: list[TablePlan] = Field(default_factory=list, max_length=31)

    @model_validator(mode="after")
    def unique_tables(self):
        names = [self.table_name, *(plan.table_name for plan in self.tables_split)]
        if len(names) != len(set(names)):
            raise ValueError("Each output table must have a unique name.")
        return self


class AppliedStep(Contract):
    table: str
    action: str
    description: str
    rows_before: int
    rows_after: int
    parameters: dict = Field(default_factory=dict)
