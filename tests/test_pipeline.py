from pathlib import Path

import pandas as pd
import pytest
from pydantic import ValidationError

from sheetforge.contracts import CleaningPlan
from sheetforge.planner import heuristic_plan
from sheetforge.profiler import profile_workbook
from sheetforge.transformer import apply_plan
from sheetforge.values import number

FIXTURES = Path(__file__).parent / "fixtures"
EXPECTED = {
    "header_not_row1.xlsx": [((2, 3), ["Region", "Revenue", "Units"])],
    "transposed.xlsx": [((2, 3), ["Metric", "Revenue", "Units"])],
    "wide_months.xlsx": [((6, 3), ["Region", "Period", "Value"])],
    "merged_headers.xlsx": [((2, 3), ["Region", "Sales_2025", "Sales_2026"])],
    "totals.xlsx": [((2, 2), ["Region", "Revenue"])],
    "two_tables.xlsx": [((2, 2), ["Region", "Revenue"]), ((2, 2), ["Product", "Units"])],
    "currency_units.xlsx": [((2, 3), ["Region", "Revenue", "Weight"])],
    "text_dates.xlsx": [((2, 2), ["Date", "Revenue"])],
    "horizontal_tables.xlsx": [((2, 2), ["Region", "Revenue"]), ((2, 2), ["Product", "Units"])],
}


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def reject(*args, **kwargs):
        raise AssertionError("The no-AI pipeline must not use the network.")

    monkeypatch.setattr("socket.socket.connect", reject)


@pytest.mark.parametrize("name", EXPECTED)
def test_every_messy_fixture_without_ai(name):
    source = FIXTURES / name
    profile = profile_workbook(source)
    plan = heuristic_plan(profile)
    result = apply_plan(source, plan)
    assert len(result.tables) == len(EXPECTED[name])
    for frame, (shape, columns) in zip(result.tables.values(), EXPECTED[name], strict=True):
        assert frame.shape == shape
        assert list(frame.columns) == columns
        assert not frame.empty
    assert result.steps
    assert all(step.description and step.rows_before >= 0 for step in result.steps)
    assert result.warnings == []


def transformed(name):
    path = FIXTURES / name
    return apply_plan(path, heuristic_plan(profile_workbook(path)))


def test_values_and_types_are_correct():
    currency = next(iter(transformed("currency_units.xlsx").tables.values()))
    assert currency["Revenue"].tolist() == [1200.5, 1234.5]
    assert currency["Weight"].tolist() == [12.0, 7.5]
    dates = next(iter(transformed("text_dates.xlsx").tables.values()))
    assert pd.api.types.is_datetime64_any_dtype(dates["Date"])
    assert dates["Date"].dt.strftime("%Y-%m-%d").tolist() == ["2026-01-13", "2026-01-14"]
    totals = transformed("totals.xlsx")
    assert next(iter(totals.tables.values()))["Revenue"].sum() == 300
    assert any(step.action == "drop_totals" for step in totals.steps)
    wide = next(iter(transformed("wide_months.xlsx").tables.values()))
    assert wide["Value"].sum() == 210


def test_profile_contains_layout_statistics_and_bounded_samples():
    profile = profile_workbook(FIXTURES / "two_tables.xlsx")
    sheet = profile.sheets["Data"]
    assert len(sheet["blocks"]) == 2
    assert sheet["blank_rows"] == [3]
    assert sheet["row_statistics"][1]["numeric_ratio"] == 0.5
    assert len(sheet["sample"]) <= 25
    assert "source_name" in profile.to_dict()
    assert profile_workbook(FIXTURES / "merged_headers.xlsx").sheets["Data"]["merged_cells"]


def test_strict_plan_and_user_override_are_validated():
    source = FIXTURES / "totals.xlsx"
    plan = heuristic_plan(profile_workbook(source)).model_dump()
    plan["totals_rows_to_drop"] = []
    result = apply_plan(source, CleaningPlan.model_validate(plan))
    assert len(next(iter(result.tables.values()))) == 4
    with pytest.raises(ValidationError):
        CleaningPlan.model_validate(plan | {"execute_python": "print('not allowed')"})
    with pytest.raises(ValidationError):
        CleaningPlan.model_validate(plan | {"data_end_row": 999999})


def test_numeric_parsing_does_not_turn_identifiers_into_measurements():
    assert number("R1") is None
    assert number("SKU123") is None
    assert number("USD 2,400") == 2400
    assert number("(£1,200.25)") == -1200.25
    assert number("75%") == 0.75
