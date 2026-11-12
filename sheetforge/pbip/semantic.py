"""Multi-table TMDL, verified relationships, measures, and native report pages."""

import json
import os
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5
from zipfile import ZIP_DEFLATED, ZipFile

from sheetforge.modeler import heuristic_model, validate_model
from sheetforge.pbip.generator import SCHEMA, identifier, projection, tmdl_table, visual
from sheetforge.pbip.power_query import power_query
from sheetforge.pbip.validation import validate_documents


def layout(specs):
    if len(specs) == 3 and specs[0].kind == "card":
        return [(30, 30, 1220, 130), (30, 180, 600, 510), (650, 180, 600, 510)]
    rows = (len(specs) + 1) // 2
    height = (660 - (rows - 1) * 20) / rows
    return [(30 + i % 2 * 620, 30 + i // 2 * (height + 20), 600, height) for i in range(len(specs))]


def build_semantic_project(result, out, model_plan=None):
    if result.plan is None or result.source is None:
        raise ValueError("A validated cleaning result is required.")
    plan = validate_model(model_plan or heuristic_model(result.tables), result.tables)
    out = Path(out).resolve()
    zipped = out.with_suffix(".zip")
    if out.exists() or zipped.exists():
        raise ValueError("Output folder or ZIP already exists; choose a new output path.")
    rd, md = "Pivotra.Report", "Pivotra.SemanticModel"
    documents = {
        "Pivotra.pbip": {
            "$schema": SCHEMA + "pbip/pbipProperties/1.0.0/schema.json",
            "version": "1.0",
            "artifacts": [{"report": {"path": rd}}],
            "settings": {"enableAutoRecovery": True},
        },
        md + "/definition.pbism": {
            "$schema": SCHEMA + "item/semanticModel/definitionProperties/1.0.0/schema.json",
            "version": "4.0",
        },
        rd + "/definition.pbir": {
            "$schema": SCHEMA + "item/report/definitionProperties/2.0.0/schema.json",
            "version": "4.0",
            "datasetReference": {"byPath": {"path": "../" + md}},
        },
        rd + "/definition/version.json": {
            "$schema": SCHEMA + "item/report/definition/versionMetadata/1.0.0/schema.json",
            "version": "2.0.0",
        },
        rd + "/definition/report.json": {
            "$schema": SCHEMA + "item/report/definition/report/3.0.0/schema.json",
            "themeCollection": {
                "customTheme": {
                    "name": "Pivotra",
                    "type": "RegisteredResources",
                    "reportVersionAtImport": {
                        "visual": "1.8.0",
                        "page": "1.3.0",
                        "report": "2.0.0",
                    },
                }
            },
            "resourcePackages": [
                {
                    "name": "RegisteredResources",
                    "type": "RegisteredResources",
                    "items": [{"name": "Pivotra", "path": "Pivotra.json", "type": "CustomTheme"}],
                }
            ],
        },
        rd + "/StaticResources/RegisteredResources/Pivotra.json": {
            "name": "Pivotra",
            "dataColors": ["#6D5DF6", "#B8862B", "#39305F", "#B9AEFA"],
            "background": "#FFFFFF",
            "foreground": "#24212F",
            "tableAccent": "#6D5DF6",
            "textClasses": {
                "title": {"fontFace": "Segoe UI Semibold", "fontSize": 14, "color": "#39305F"},
                "label": {"fontFace": "Segoe UI", "fontSize": 11, "color": "#716B80"},
                "callout": {"fontFace": "Segoe UI Semibold", "fontSize": 32, "color": "#39305F"},
                "header": {"fontFace": "Segoe UI Semibold", "fontSize": 12, "color": "#39305F"},
            },
        },
    }
    text = {md + "/definition/database.tmdl": "database\n\tcompatibilityLevel: 1601\n"}
    references, helpers = [], {}
    for index, role in enumerate(plan.tables, 1):
        name, frame = role.name, result.tables[role.name]
        cleaning = next(
            item for item in [result.plan, *result.plan.tables_split] if item.table_name == name
        )
        helper = "_Record"
        while helper in frame.columns:
            helper += "_"
        helpers[name] = helper
        query = power_query(result.source, cleaning, frame, helper)
        content, _, _ = tmdl_table(
            name, frame, query, helper, [item for item in plan.measures if item.table == name]
        )
        text[f"{md}/definition/tables/Table{index:03d}.tmdl"] = (
            content + f"\tannotation Pivotra_Role = {role.role}\n"
        )
        references.append("\tref table " + identifier(name))
    text[md + "/definition/model.tmdl"] = (
        "model Model\n\tculture: en-US\n\tdefaultPowerBIDataSourceVersion: powerBI_V3\n\tsourceQueryCulture: en-US\n\n"
        + "\n".join(references)
        + "\n"
    )
    relationships = []
    for relation in plan.relationships:
        name = str(uuid5(NAMESPACE_URL, json.dumps(relation.model_dump(), sort_keys=True)))
        relationships.extend(
            [
                "relationship " + name,
                "\tfromColumn: "
                + identifier(relation.from_table)
                + "."
                + identifier(relation.from_column),
                "\ttoColumn: "
                + identifier(relation.to_table)
                + "."
                + identifier(relation.to_column),
                "\tfromCardinality: many",
                "\ttoCardinality: one",
                "\tcrossFilteringBehavior: oneDirection",
                "\tisActive: true",
                "",
            ]
        )
    text[md + "/definition/relationships.tmdl"] = "\n".join(relationships)
    page_names = [f"page_{i:03d}" for i in range(1, len(plan.pages) + 1)]
    documents[rd + "/definition/pages/pages.json"] = {
        "$schema": SCHEMA + "item/report/definition/pagesMetadata/1.0.0/schema.json",
        "pageOrder": page_names,
        "activePageName": page_names[0],
    }
    measures = {item.name: item for item in plan.measures}
    for page_id, page in zip(page_names, plan.pages, strict=True):
        prefix = rd + "/definition/pages/" + page_id
        documents[prefix + "/page.json"] = {
            "$schema": SCHEMA + "item/report/definition/page/2.0.0/schema.json",
            "name": page_id,
            "displayName": page.title,
            "displayOption": "FitToPage",
            "width": 1280,
            "height": 720,
        }
        for index, (spec, geometry) in enumerate(
            zip(page.visuals, layout(page.visuals), strict=True), 1
        ):
            if spec.kind == "table":
                query = {
                    "Values": {
                        "projections": [
                            projection(spec.table, name)
                            for name in [
                                helpers[spec.table],
                                *(spec.columns or list(result.tables[spec.table].columns)),
                            ]
                        ]
                    }
                }
                kind = "tableEx"
            else:
                owner = measures[spec.measure].table
                value = projection(owner, spec.measure, measure=True)
                query = (
                    {"Values": {"projections": [value]}}
                    if spec.kind == "card"
                    else {
                        "Category": {
                            "projections": [
                                projection(spec.table, spec.category or helpers[spec.table])
                            ]
                        },
                        "Y": {"projections": [value]},
                    }
                )
                kind = {"card": "card", "bar": "clusteredBarChart", "line": "lineChart"}[spec.kind]
            name = f"visual_{index:03d}"
            documents[f"{prefix}/visuals/{name}/visual.json"] = visual(
                name, kind, query, spec.title, *geometry
            )
    validated = validate_documents(documents)
    documents |= {
        "cleaning_plan.json": result.plan.model_dump(),
        "model_plan.json": plan.model_dump(),
        "steps.json": {
            "steps": [item.model_dump() for item in result.steps],
            "warnings": result.warnings,
        },
        "validation.json": {
            "pbir_json_schema": "passed",
            "validated_documents": validated,
            "desktop_validation": "not yet verified in Power BI Desktop",
        },
    }
    text["OPEN_IN_DESKTOP.txt"] = (
        "Open Pivotra.pbip and select Refresh. The model reads the original XLSX at:\n"
        + str(result.source)
        + "\n\nKeep this file in place. For moved sources, edit File.Contents in Power Query. Source value edits and appended records in the last table can refresh. Structural changes require regenerating the plans.\n\nRelationships are many-to-one, one-direction, with checked unique parent keys and complete coverage at generation time. Inspect model_plan.json and steps.json. Native Desktop/TMDL/M execution remains unverified here.\n"
    )
    # Extended Windows paths keep nested PBIR files usable without admin registry edits.
    public_out, public_zip = out, zipped
    if os.name == "nt" and not str(out).startswith("\\\\?\\"):
        out = Path("\\\\?\\" + str(out))
        zipped = Path("\\\\?\\" + str(zipped))
    out.mkdir(parents=True)
    for relative, document in documents.items():
        path = out / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(
            (json.dumps(document, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
        )
    for relative, content in text.items():
        path = out / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content.encode("utf-8"))
    with ZipFile(zipped, "w", ZIP_DEFLATED) as archive:
        for path in sorted(out.rglob("*")):
            if path.is_file():
                archive.write(path, str(Path(out.name) / path.relative_to(out)))
    return public_out / "Pivotra.pbip", public_zip
