import json
from pathlib import Path
from zipfile import ZipFile

import pytest
from openpyxl import Workbook

from sheetforge.pbip import build_project
from sheetforge.pbip.power_query import m_string
from sheetforge.pbip.validation import validate_documents
from sheetforge.planner import heuristic_plan
from sheetforge.profiler import profile_workbook
from sheetforge.transformer import apply_plan

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def reject(*args, **kwargs):
        raise AssertionError("PBIP generation/validation must work offline.")

    monkeypatch.setattr("socket.socket.connect", reject)


def result_for(path):
    return apply_plan(path, heuristic_plan(profile_workbook(path)))


@pytest.mark.parametrize("source", sorted(FIXTURES.glob("*.xlsx")), ids=lambda path: path.stem)
def test_each_fixture_can_generate_a_schema_valid_minimal_project(source, tmp_path):
    result = result_for(source)
    selected = next(iter(result.tables))
    project, archive = build_project(result, tmp_path / "Project", selected)
    assert project.is_file()
    root = project.parent
    model = root / "SheetForge.SemanticModel" / "definition"
    assert (model / "model.tmdl").is_file()
    assert (model / "relationships.tmdl").is_file()
    assert len(list((model / "tables").glob("*.tmdl"))) == 1
    table = (model / "tables" / "Table001.tmdl").read_text(encoding="utf-8")
    assert "Excel.Workbook(File.Contents(" in table
    assert m_string(str(source.resolve())) in table
    assert "Binary.FromText" not in table and "#table(" not in table
    assert "__PBI_TimeIntelligenceEnabled" not in table
    validation = json.loads((root / "validation.json").read_text(encoding="utf-8"))
    assert validation["pbir_json_schema"] == "passed"
    assert len(validation["validated_documents"]) == 10
    visuals = list((root / "SheetForge.Report" / "definition" / "pages").rglob("visual.json"))
    assert len(visuals) == 3
    assert {
        json.loads(path.read_text(encoding="utf-8"))["visual"]["visualType"] for path in visuals
    } == {"card", "clusteredBarChart", "tableEx"}
    for path in root.rglob("*"):
        if path.is_file():
            content = path.read_bytes()
            assert not content.startswith(b"\xef\xbb\xbf")
            content.decode("utf-8")
    with ZipFile(archive) as zipped:
        assert zipped.testzip() is None
        assert all(
            not name.startswith(("/", "\\")) and ".." not in Path(name).parts
            for name in zipped.namelist()
        )
        assert not any(name.endswith(".xlsx") for name in zipped.namelist())


def test_original_data_is_not_embedded_and_numeric_measures_are_explicit(tmp_path):
    source = FIXTURES / "header_not_row1.xlsx"
    project, _ = build_project(result_for(source), tmp_path / "Project")
    table = next(project.parent.rglob("Table001.tmdl")).read_text(encoding="utf-8")
    assert "SUM('Data_Table1'[Revenue])" in table
    assert "SUM('Data_Table1'[Units])" in table
    assert "North" not in table and "South" not in table
    assert "List.Skip(Table.ToRows(Raw)" in table


def test_multiple_tables_require_an_explicit_selection(tmp_path):
    with pytest.raises(ValueError, match="Select --table"):
        build_project(result_for(FIXTURES / "two_tables.xlsx"), tmp_path / "Project")


def test_output_is_not_overwritten(tmp_path):
    out = tmp_path / "Existing"
    out.mkdir()
    sentinel = out / "keep.txt"
    sentinel.write_text("keep", encoding="utf-8")
    with pytest.raises(ValueError, match="already exists"):
        build_project(result_for(FIXTURES / "header_not_row1.xlsx"), out)
    assert sentinel.read_text(encoding="utf-8") == "keep"


def test_pbir_schema_rejects_invalid_visual_geometry():
    with pytest.raises(ValueError, match="not of type"):
        validate_documents(
            {
                "visual.json": {
                    "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/visualContainer/2.4.0/schema.json",
                    "name": "bad",
                    "position": {"x": 0, "y": 0, "z": 0, "width": "wide", "height": 100},
                }
            }
        )


def test_unicode_quotes_and_dax_names_are_escaped(tmp_path):
    source = tmp_path / "café's #1.xlsx"
    book = Workbook()
    book.active.append(['Director\'s "note"', "Revenue]"])
    book.active.append(["A", 10])
    book.active.append(["B", 20])
    book.save(source)
    book.close()
    project, _ = build_project(result_for(source), tmp_path / "Project")
    table = next(project.parent.rglob("Table001.tmdl")).read_text(encoding="utf-8")
    assert "Director''s" in table
    assert '""note""' in table
    assert "[Revenue]]]" in table
    assert "#(#)1.xlsx" in table


def test_wide_refresh_query_preserves_missing_cells_instead_of_native_unpivot(tmp_path):
    source = tmp_path / "wide-missing.xlsx"
    book = Workbook()
    book.active.append(["Region", "Jan", "Feb"])
    book.active.append(["North", 10, None])
    book.active.append(["South", 20, 30])
    book.save(source)
    book.close()
    result = result_for(source)
    frame = next(iter(result.tables.values()))
    assert len(frame) == 4 and frame["Value"].isna().sum() == 1
    project, _ = build_project(result, tmp_path / "Project")
    table = next(project.parent.rglob("Table001.tmdl")).read_text(encoding="utf-8")
    assert "Table.FromRecords(List.Combine" in table
    assert "Table.Unpivot(" not in table
