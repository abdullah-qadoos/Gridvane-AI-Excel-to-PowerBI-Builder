import pandas as pd
import pytest

from sheetforge.quality import apply, inspect
from sheetforge.quality.models import Rule


def categories(report):
    return {item.category: item for item in report.categories}


def ignore_all(report):
    return {item.category: {"action": "ignore"} for item in report.categories}


@pytest.fixture
def messy():
    return pd.DataFrame(
        {
            "Order ID": ["A1", "A2", "A2", "A3", "A4", "A4"],
            "Customer": [" Ann ", "Bob", "Bob", "N/A", "Dee", "Dee"],
            "Order Date": [
                "2024-01-03",
                "2024-01-04",
                "2024-01-04",
                "1850-01-01",
                "2024-02-01",
                "2024-02-02",
            ],
            "Quantity": [3, "4", "4", -2, "x", 7],
            "Discount %": [5, 10, 10, 250, None, 0],
            "Revenue": ["$1,200", 300.0, 300.0, "-", 99.0, 120.0],
        }
    )


TYPES = {
    "Order ID": "string",
    "Customer": "string",
    "Order Date": "date",
    "Quantity": "number",
    "Discount %": "number",
    "Revenue": "number",
}


def test_detects_every_category(messy):
    report = inspect(messy, TYPES)
    found = categories(report)
    assert set(found) == {
        "formatting",
        "missing",
        "type_consistency",
        "impossible_values",
        "exact_duplicates",
        "near_duplicates",
    }
    columns = {item.column: item for item in report.columns}
    assert columns["Customer"].placeholder_nulls == 1
    assert columns["Revenue"].placeholder_nulls == 1
    assert columns["Quantity"].type_mismatches == 1
    assert columns["Customer"].formatting_issues == 1  # " Ann "
    assert columns["Revenue"].formatting_issues == 1  # "$1,200"
    assert columns["Quantity"].impossible_values == 1  # negative count
    assert columns["Discount %"].impossible_values == 1  # 250%
    assert columns["Order Date"].impossible_values == 1  # 1850
    assert report.exact_duplicates == 1
    assert report.duplicate_key == ["Order ID"]
    assert report.near_duplicate_group_count == 1
    assert report.near_duplicate_groups[0].rows == [5, 6]
    assert found["formatting"].default_action == "fix_for_me"
    assert all(
        item.default_action is None for item in report.categories if item.category != "formatting"
    )


def test_placeholder_list_is_missing():
    frame = pd.DataFrame(
        {"Note": ["", "-", "—", "N/A", "n/a", "NA", "null", "None", "?", ".", "..", "ok"]}
    )
    report = inspect(frame, {"Note": "string"})
    assert report.columns[0].placeholder_nulls == 11
    assert report.columns[0].null_pct == pytest.approx(11 / 12 * 100, abs=0.01)


def test_ignore_everything_returns_input_unchanged(messy):
    original = messy.copy()
    report = inspect(messy, TYPES)
    cleaned, log = apply(messy, ignore_all(report), report)
    pd.testing.assert_frame_equal(cleaned, original)
    pd.testing.assert_frame_equal(messy, original)
    assert not log.entries and not log.stopped


def test_no_fix_without_a_choice(messy):
    report = inspect(messy, TYPES)
    with pytest.raises(ValueError, match="Choose fix_for_me"):
        apply(messy, {"formatting": {"action": "fix_for_me"}}, report)


def test_formatting_is_lossless_and_audited(messy):
    report = inspect(messy, TYPES)
    decisions = ignore_all(report) | {"formatting": {"action": "fix_for_me"}}
    cleaned, log = apply(messy, decisions, report)
    assert cleaned.at[0, "Customer"] == "Ann"
    assert cleaned.at[0, "Revenue"] == 1200.0
    assert pd.isna(cleaned.at[3, "Customer"])
    assert cleaned.at[4, "Quantity"] == "x"  # type issues are a separate choice
    changed = {entry.column: entry.cells_changed for entry in log.entries}
    assert changed["Customer"] == 2 and changed["Revenue"] == 2


def test_missing_strategies():
    frame = pd.DataFrame({"Sales": [10, None, 30, "n/a"], "Region": ["N", None, "S", "W"]})
    types = {"Sales": "number", "Region": "string"}
    report = inspect(frame, types)
    base = ignore_all(report)
    median, _ = apply(
        frame, base | {"missing": {"action": "fix_for_me", "strategy": "fill_median"}}, report
    )
    assert median["Sales"].tolist()[1] == 20.0 and median["Sales"].tolist()[3] == 20.0
    assert pd.isna(median.at[1, "Region"])  # median does not apply to text
    unknown, _ = apply(
        frame, base | {"missing": {"action": "fix_for_me", "strategy": "fill_unknown"}}, report
    )
    assert unknown.at[1, "Region"] == "Unknown"
    dropped, log = apply(
        frame, base | {"missing": {"action": "fix_for_me", "strategy": "drop"}}, report
    )
    assert len(dropped) == 2 and log.entries[0].rows_removed == 2


def test_type_and_impossible_fixes(messy):
    report = inspect(messy, TYPES)
    decisions = ignore_all(report) | {
        "type_consistency": {"action": "fix_for_me", "strategy": "nullify"},
        "impossible_values": {"action": "fix_for_me", "strategy": "clamp"},
    }
    cleaned, _ = apply(messy, decisions, report)
    assert pd.isna(cleaned.at[4, "Quantity"])
    assert cleaned.at[3, "Quantity"] == 0.0
    assert cleaned.at[3, "Discount %"] == 100.0


def test_llm_rules_are_checked_and_applied():
    frame = pd.DataFrame(
        {
            "Start": ["2024-01-05", "2024-02-01"],
            "End": ["2024-01-01", "2024-03-01"],
            "Age": [30, 140],
        }
    )
    types = {"Start": "date", "End": "date", "Age": "number"}
    rules = [
        Rule(column="Start", kind="order", before_column="End", source="llm"),
        Rule(column="Age", kind="bounds", min=0.0, max=120.0, source="llm"),
    ]
    report = inspect(frame, types, extra_rules=rules)
    assert categories(report)["impossible_values"].count == 2
    cleaned, _ = apply(
        frame,
        ignore_all(report) | {"impossible_values": {"action": "fix_for_me", "strategy": "nullify"}},
        report,
    )
    assert pd.isna(cleaned.at[0, "Start"]) and pd.isna(cleaned.at[1, "Age"])


def test_duplicates_need_a_confirmed_key(messy):
    report = inspect(messy, TYPES)
    base = ignore_all(report)
    with pytest.raises(ValueError, match="duplicate key"):
        apply(
            messy,
            base | {"near_duplicates": {"action": "fix_for_me", "strategy": "keep_latest"}},
            report,
        )
    cleaned, log = apply(
        messy,
        base
        | {
            "exact_duplicates": {"action": "fix_for_me"},
            "near_duplicates": {
                "action": "fix_for_me",
                "strategy": "keep_latest",
                "key": ["Order ID"],
            },
        },
        report,
    )
    assert cleaned["Order ID"].tolist() == ["A1", "A2", "A3", "A4"]
    assert cleaned.iloc[-1]["Order Date"] == "2024-02-02"
    assert sum(entry.rows_removed for entry in log.entries) == 2


def test_keep_max_merges_numbers():
    frame = pd.DataFrame({"SKU": ["K1", "K1", "K2"], "Stock": [5, 9, 1]})
    types = {"SKU": "string", "Stock": "number"}
    report = inspect(frame, types)
    cleaned, _ = apply(
        frame,
        {"near_duplicates": {"action": "fix_for_me", "strategy": "keep_max", "key": ["SKU"]}},
        report,
    )
    assert cleaned["Stock"].tolist() == [9.0, 1]


def test_i_will_fix_stops(messy):
    report = inspect(messy, TYPES)
    decisions = ignore_all(report) | {"missing": {"action": "i_will_fix"}}
    _, log = apply(messy, decisions, report)
    assert log.stopped and log.user_will_fix == ["missing"]
