import json
from pathlib import Path

import pandas as pd
import pytest

from sheetforge.llm import LLMConfig, client, configure
from sheetforge.modeler import heuristic_model, plan_model, schema_context, validate_model
from sheetforge.pbip import build_semantic_project
from sheetforge.planner import heuristic_plan
from sheetforge.profiler import profile_workbook
from sheetforge.transformer import apply_plan


@pytest.fixture(autouse=True)
def offline():
    configure(LLMConfig())
    yield
    configure(LLMConfig())


def star():
    return {
        "Sales": pd.DataFrame(
            {
                "Product ID": pd.Series(["01", "01", "02"], dtype="string"),
                "Revenue": [10.0, 20.0, 30.0],
            }
        ),
        "Products": pd.DataFrame(
            {
                "Product ID": pd.Series(["01", "02"], dtype="string"),
                "Product": pd.Series(["SECRET_A", "SECRET_B"], dtype="string"),
            }
        ),
    }


def test_heuristics_create_verified_relationship_and_multiple_pages():
    plan = heuristic_model(star())
    assert len(plan.relationships) == 1
    assert plan.relationships[0].to_table == "Products"
    assert next(item for item in plan.tables if item.name == "Products").role == "dimension"
    assert len(plan.pages) == 3
    assert len({item.name for item in plan.measures}) == len(plan.measures)
    assert "SECRET_A" not in json.dumps(schema_context(star()))


@pytest.mark.parametrize(
    "change",
    ["missing_column", "duplicate_parent", "missing_parent", "unsafe_dax", "cycle", "bad_visual"],
)
def test_model_validation_rejects_invalid_decisions(change):
    tables = star()
    plan = heuristic_model(tables).model_dump()
    if change == "missing_column":
        plan["relationships"][0]["to_column"] = "invented"
    elif change == "duplicate_parent":
        tables["Products"] = pd.concat(
            [tables["Products"], tables["Products"].iloc[:1]], ignore_index=True
        )
    elif change == "missing_parent":
        tables["Products"] = tables["Products"].iloc[:1]
    elif change == "unsafe_dax":
        plan["measures"][0]["dax"] = "COUNTROWS('Sales')\nannotation injected = value"
    elif change == "cycle":
        name = plan["measures"][0]["name"]
        plan["measures"][0]["dax"] = f"DIVIDE([{name}], [{name}], 0)"
    else:
        plan["pages"][0]["visuals"][0]["measure"] = "invented"
    with pytest.raises(ValueError):
        validate_model(plan, tables)


def test_llm_modeler_retries_then_falls_back_with_no_cell_values(monkeypatch):
    calls = []

    def request(messages):
        calls.append(json.dumps(messages))
        return '{"bad":true}'

    monkeypatch.setattr(client, "request_completion", request)
    configure(LLMConfig(provider="ollama", model="local"))
    plan, notes = plan_model(star())
    assert len(calls) == 2 and "SECRET_A" not in calls[0]
    assert plan == heuristic_model(star())
    assert "deterministic" in notes[0]


def test_multi_table_export_has_native_pages_and_unique_measures(tmp_path):
    source = Path(__file__).parent / "fixtures" / "two_tables.xlsx"
    result = apply_plan(source, heuristic_plan(profile_workbook(source)))
    project, zipped = build_semantic_project(result, tmp_path / "multi")
    assert zipped.is_file()
    assert len(list(project.parent.rglob("Table*.tmdl"))) == 2
    pages = json.loads(next(project.parent.rglob("pages.json")).read_text())
    assert len(pages["pageOrder"]) == 3
    model = json.loads((project.parent / "model_plan.json").read_text())
    assert len({item["name"] for item in model["measures"]}) == len(model["measures"])
    assert (
        json.loads((project.parent / "validation.json").read_text())["pbir_json_schema"] == "passed"
    )
