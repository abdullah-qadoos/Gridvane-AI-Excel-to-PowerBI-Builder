"""Minimal step-3 TMDL/PBIR export. No embedded spreadsheet rows or credentials."""

import json
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5
from zipfile import ZIP_DEFLATED, ZipFile

import pandas as pd

from sheetforge.pbip.power_query import power_query
from sheetforge.pbip.validation import validate_documents

SCHEMA = "https://developer.microsoft.com/json-schemas/fabric/"


def identifier(value):
    if any(ord(char) < 32 for char in value):
        raise ValueError("Model object names cannot contain control characters; rename the column.")
    return "'" + value.replace("'", "''") + "'"


def property_string(value):
    if any(ord(char) < 32 for char in value):
        raise ValueError("TMDL property values cannot contain control characters.")
    return '"' + value.replace('"', '""') + '"'


def projection(table, column, *, measure=False):
    return {
        "field": {
            "Measure" if measure else "Column": {
                "Expression": {"SourceRef": {"Entity": table}},
                "Property": column,
            }
        },
        "queryRef": table + "." + column,
        "displayName": column,
    }


def visual(name, kind, query, title, x, y, width, height):
    properties = {
        "show": {"expr": {"Literal": {"Value": "true"}}},
        "text": {"expr": {"Literal": {"Value": "'" + title.replace("'", "''") + "'"}}},
    }
    return {
        "$schema": SCHEMA + "item/report/definition/visualContainer/2.4.0/schema.json",
        "name": name,
        "position": {"x": x, "y": y, "z": 0, "width": width, "height": height, "tabOrder": 0},
        "visual": {
            "visualType": kind,
            "query": {"queryState": query},
            "visualContainerObjects": {"title": [{"properties": properties}]},
            "drillFilterOtherVisuals": True,
        },
    }


def tmdl_table(name, frame, query, record_column):
    def lineage(key):
        return str(uuid5(NAMESPACE_URL, "sheetforge/" + name + "/" + key))

    lines = [f"table {identifier(name)}", "\tlineageTag: " + lineage("table"), ""]
    count_name = "Record count"
    while count_name in frame.columns:
        count_name = "Measure " + count_name
    measures = {count_name: f"COUNTROWS({identifier(name)})"}
    numeric = [
        column
        for column in frame
        if pd.api.types.is_numeric_dtype(frame[column])
        and not pd.api.types.is_bool_dtype(frame[column])
    ]
    for column in numeric:
        measure_name = "Total " + column
        if measure_name in frame.columns:
            measure_name = "Measure " + measure_name
        while measure_name in measures or measure_name in frame.columns:
            measure_name = "Measure " + measure_name
        measures[measure_name] = f"SUM({identifier(name)}[{column.replace(']', ']]')}])"
    for measure_name, expression in measures.items():
        lines.extend(
            [
                f"\tmeasure {identifier(measure_name)} = {expression}",
                "\t\tformatString: #,##0.##",
                "\t\tlineageTag: " + lineage("measure/" + measure_name),
                "",
            ]
        )
    for column in [*frame.columns, record_column]:
        dtype = "int64" if column == record_column else "string"
        if column != record_column:
            values = frame[column]
            dtype = (
                "dateTime"
                if pd.api.types.is_datetime64_any_dtype(values)
                else "boolean"
                if pd.api.types.is_bool_dtype(values)
                else "int64"
                if pd.api.types.is_integer_dtype(values)
                else "double"
                if pd.api.types.is_numeric_dtype(values)
                else "string"
            )
        lines.extend(
            [
                f"\tcolumn {identifier(column)}",
                "\t\tdataType: " + dtype,
                "\t\tsourceColumn: " + property_string(column),
                "\t\tsummarizeBy: none",
                "\t\tlineageTag: " + lineage("column/" + column),
                "",
            ]
        )
    lines.extend([f"\tpartition {identifier(name)} = m", "\t\tmode: import", "\t\tsource ="])
    lines.extend("\t\t\t" + line for line in query.splitlines())
    return "\n".join(lines) + "\n", list(measures), numeric


def build_project(result, out, table_name=None):
    if not result.plan or not result.source:
        raise ValueError("Generate a validated cleaning result before building a PBIP.")
    if table_name is None:
        if len(result.tables) != 1:
            raise ValueError(
                "The step-3 milestone exports one table. Select --table explicitly for a multi-table workbook."
            )
        table_name = next(iter(result.tables))
    if table_name not in result.tables:
        raise ValueError("The selected output table does not exist.")
    out = Path(out).resolve()
    zip_path = out.with_suffix(".zip")
    if out.exists() or zip_path.exists():
        raise ValueError("Output folder or ZIP already exists. Choose a new --out path.")
    frame = result.tables[table_name]
    plan = next(
        plan for plan in [result.plan, *result.plan.tables_split] if plan.table_name == table_name
    )
    record_column = "_Record"
    while record_column in frame.columns:
        record_column += "_"
    query = power_query(result.source, plan, frame, record_column)
    table_text, measures, numeric = tmdl_table(table_name, frame, query, record_column)
    metric = measures[1] if len(measures) > 1 else measures[0]
    dimension = next(
        (
            name
            for name in frame
            if name not in numeric and not pd.api.types.is_datetime64_any_dtype(frame[name])
        ),
        record_column,
    )
    report_dir, model_dir = "SheetForge.Report", "SheetForge.SemanticModel"
    documents = {
        "SheetForge.pbip": {
            "$schema": SCHEMA + "pbip/pbipProperties/1.0.0/schema.json",
            "version": "1.0",
            "artifacts": [{"report": {"path": report_dir}}],
            "settings": {"enableAutoRecovery": True},
        },
        model_dir + "/definition.pbism": {
            "$schema": SCHEMA + "item/semanticModel/definitionProperties/1.0.0/schema.json",
            "version": "4.0",
        },
        report_dir + "/definition.pbir": {
            "$schema": SCHEMA + "item/report/definitionProperties/2.0.0/schema.json",
            "version": "4.0",
            "datasetReference": {"byPath": {"path": "../" + model_dir}},
        },
        report_dir + "/definition/version.json": {
            "$schema": SCHEMA + "item/report/definition/versionMetadata/1.0.0/schema.json",
            "version": "2.0.0",
        },
        report_dir + "/definition/report.json": {
            "$schema": SCHEMA + "item/report/definition/report/3.0.0/schema.json",
            "themeCollection": {
                "baseTheme": {
                    "name": "CY24SU06",
                    "type": "SharedResources",
                    "reportVersionAtImport": {
                        "visual": "1.8.0",
                        "page": "1.3.0",
                        "report": "2.0.0",
                    },
                }
            },
        },
        report_dir + "/definition/pages/pages.json": {
            "$schema": SCHEMA + "item/report/definition/pagesMetadata/1.0.0/schema.json",
            "pageOrder": ["overview"],
            "activePageName": "overview",
        },
        report_dir + "/definition/pages/overview/page.json": {
            "$schema": SCHEMA + "item/report/definition/page/2.0.0/schema.json",
            "name": "overview",
            "displayName": "SheetForge overview",
            "displayOption": "FitToPage",
            "width": 1280,
            "height": 720,
        },
    }
    charts = [
        visual(
            "metric_card",
            "card",
            {"Values": {"projections": [projection(table_name, metric, measure=True)]}},
            metric,
            30,
            30,
            1220,
            130,
        ),
        visual(
            "category_bar",
            "clusteredBarChart",
            {
                "Category": {"projections": [projection(table_name, dimension)]},
                "Y": {"projections": [projection(table_name, metric, measure=True)]},
            },
            metric + " by " + dimension,
            30,
            180,
            600,
            510,
        ),
        visual(
            "data_table",
            "tableEx",
            {
                "Values": {
                    "projections": [
                        projection(table_name, column) for column in [record_column, *frame.columns]
                    ]
                }
            },
            "Cleaned records",
            650,
            180,
            600,
            510,
        ),
    ]
    for chart in charts:
        documents[f"{report_dir}/definition/pages/overview/visuals/{chart['name']}/visual.json"] = (
            chart
        )
    validated = validate_documents(documents)
    documents["cleaning_plan.json"] = result.plan.model_dump()
    documents["steps.json"] = {
        "steps": [step.model_dump() for step in result.steps],
        "warnings": result.warnings,
        "selected_table": table_name,
        "helper_column": record_column,
    }
    documents["validation.json"] = {
        "pbir_json_schema": "passed",
        "validated_documents": validated,
        "tmdl_and_m_desktop_validation": "pending user review",
    }
    text_files = {
        model_dir + "/definition/database.tmdl": "database\n\tcompatibilityLevel: 1601\n",
        model_dir
        + "/definition/model.tmdl": "model Model\n\tculture: en-US\n\tdefaultPowerBIDataSourceVersion: powerBI_V3\n\tsourceQueryCulture: en-US\n\n\tref table "
        + identifier(table_name)
        + "\n",
        model_dir + "/definition/tables/Table001.tmdl": table_text,
        model_dir + "/definition/relationships.tmdl": "",
        "OPEN_IN_DESKTOP.txt": "SheetForge - step 3 review\n\nOpen SheetForge.pbip in a current Power BI Desktop version supporting PBIP/PBIR. Enable the project preview if required.\nSelect Refresh to load the source workbook. If prompted for file privacy, choose the appropriate local file privacy level.\n\nSource workbook: "
        + str(result.source)
        + "\n\nThe model reads this ORIGINAL XLSX through Excel.Workbook(File.Contents(...)). No spreadsheet rows are embedded in this project.\nKeep the workbook at the original path; change the File.Contents path in Power Query if it moves. Data-value updates refresh in Desktop. The final table can grow when records are appended; header/layout changes require regenerating the plan/project.\n\nThe _Record helper distinguishes duplicate source records in the table visual. Numeric measures use SUM, without invented units or currency.\n\nPBIR JSON has passed the bundled Microsoft schemas. TMDL/M execution and visual rendering are pending your Desktop feedback. Report any exact error text before steps 4-6 proceed.\n",
    }
    out.mkdir(parents=True)
    for relative, document in documents.items():
        target = out / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(
            (json.dumps(document, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        )
    for relative, content in text_files.items():
        target = out / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content.encode("utf-8"))
    with ZipFile(zip_path, "w", ZIP_DEFLATED) as archive:
        for path in sorted(out.rglob("*")):
            if path.is_file():
                archive.write(path, str(Path(out.name) / path.relative_to(out)))
    return out / "SheetForge.pbip", zip_path
