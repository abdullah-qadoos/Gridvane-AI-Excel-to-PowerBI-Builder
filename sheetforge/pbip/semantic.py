"""Multi-table TMDL, verified relationships, measures, and native report pages."""

import json
import os
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5
from zipfile import ZIP_DEFLATED, ZipFile

from sheetforge.modeler import heuristic_model, validate_model
from sheetforge.paths import output_stem
from sheetforge.pbip.generator import (
    SCHEMA,
    identifier,
    literal,
    projection,
    textbox,
    tmdl_table,
    visual,
)
from sheetforge.pbip.power_query import power_query
from sheetforge.pbip.validation import validate_documents
from sheetforge.planner.heuristic import friendly

HEADER, MARGIN, GAP, WIDTH, HEIGHT = 64, 24, 16, 1280, 720
INK, CREAM, PAPER, LINE, BODY, MUTED = (
    "#15130F",
    "#F3ECDD",
    "#FBF7EE",
    "#E2D8C6",
    "#3E3A32",
    "#857E70",
)


def solid(color):
    return {"solid": {"color": color}}


# Report-wide look (Power BI theme "visualStyles"): cream page, white rounded cards, ink text.
THEME_STYLES = {
    "page": {"*": {"background": [{"color": solid(CREAM), "transparency": 0}]}},
    "*": {
        "*": {
            "background": [{"show": True, "color": solid("#FFFFFF"), "transparency": 0}],
            "border": [{"show": True, "color": solid(LINE), "radius": 12}],
            "dropShadow": [{"show": False}],
            "padding": [{"top": 10, "bottom": 10, "left": 14, "right": 14}],
            "title": [
                {
                    "show": True,
                    "fontColor": solid(INK),
                    "fontSize": 12,
                    "fontFamily": "Segoe UI Semibold",
                }
            ],
        }
    },
    "card": {
        "*": {
            "labels": [{"color": solid(INK), "fontSize": 30, "fontFamily": "Segoe UI Semibold"}],
            "categoryLabels": [{"show": False}],
        }
    },
    "tableEx": {
        "*": {
            "columnHeaders": [
                {
                    "fontColor": solid(CREAM),
                    "backColor": solid(INK),
                    "fontFamily": "Segoe UI Semibold",
                }
            ],
            "values": [
                {
                    "fontColorPrimary": solid(BODY),
                    "backColorPrimary": solid("#FFFFFF"),
                    "fontColorSecondary": solid(BODY),
                    "backColorSecondary": solid(PAPER),
                }
            ],
            "grid": [{"gridHorizontalColor": solid(LINE), "gridVerticalColor": solid(LINE)}],
        }
    },
    "lineChart": {"*": {"lineStyles": [{"strokeWidth": 3}]}},
    "slicer": {"*": {"header": [{"fontColor": solid(MUTED), "fontSize": 10}]}},
    # The header band draws its own ink background and has no border.
    "textbox": {
        "*": {
            "border": [{"show": False}],
            "padding": [{"top": 0, "bottom": 0, "left": 0, "right": 0}],
        }
    },
}


def layout(specs):
    """Dashboard grid: KPI cards on top, wide trends, side-by-side breakdowns, table last."""
    geometry = [None] * len(specs)
    inner = WIDTH - 2 * MARGIN
    # Rows top to bottom: KPI cards + filters, trend, breakdowns (bar/donut), records table.
    groups = {
        "top": ["card", "slicer"],
        "line": ["line"],
        "bar": ["bar", "donut"],
        "table": ["table"],
    }
    kinds = {
        group: [i for i, spec in enumerate(specs) if spec.kind in members]
        for group, members in groups.items()
    }
    kinds["card"] = kinds.pop("top")
    weight = {"line": 1.3, "bar": 1.0, "table": 1.0}
    rows = [
        (kind, kinds[kind][start : start + 3])
        for kind in ["line", "bar", "table"]
        for start in range(0, len(kinds[kind]), 3)
    ]

    def place(indexes, y, height):
        width = (inner - GAP * (len(indexes) - 1)) / len(indexes)
        for slot, index in enumerate(indexes):
            geometry[index] = (
                round(MARGIN + slot * (width + GAP)),
                round(y),
                round(width),
                round(height),
            )

    y = HEADER + MARGIN
    if kinds["card"]:
        height = 110 if rows else HEIGHT - MARGIN - y
        place(kinds["card"], y, height)
        y += height + GAP
    if rows:
        available = HEIGHT - MARGIN - y - GAP * (len(rows) - 1)
        total = sum(weight[kind] for kind, _ in rows)
        for kind, indexes in rows:
            height = available * weight[kind] / total
            place(indexes, y, height)
            y += height + GAP
    return geometry


def build_semantic_project(result, out, model_plan=None, name=None):
    """name: the uploaded file name; the project, report and model are named after it."""
    if result.plan is None or result.source is None:
        raise ValueError("A validated cleaning result is required.")
    plan = validate_model(model_plan or heuristic_model(result.tables), result.tables)
    out = Path(out).resolve()
    zipped = out.with_suffix(".zip")
    if out.exists() or zipped.exists():
        raise ValueError("Output folder or ZIP already exists; choose a new output path.")
    stem = output_stem(name or result.source.name)
    rd, md = stem + ".Report", stem + ".SemanticModel"
    documents = {
        stem + ".pbip": {
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
            "dataColors": ["#B5562A", "#2F5A43", "#D9A47E", "#15130F", "#7FA88F", "#E8C9A9"],
            "background": "#FFFFFF",
            "foreground": "#15130F",
            "tableAccent": "#B5562A",
            "textClasses": {
                "title": {"fontFace": "Segoe UI Semibold", "fontSize": 13, "color": "#15130F"},
                "label": {"fontFace": "Segoe UI", "fontSize": 10, "color": "#857E70"},
                "callout": {"fontFace": "Segoe UI Semibold", "fontSize": 30, "color": "#15130F"},
                "header": {"fontFace": "Segoe UI Semibold", "fontSize": 12, "color": "#15130F"},
            },
            "visualStyles": THEME_STYLES,
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
        # TMDL ref statements are top-level lines; indenting them under "model" is a parse error.
        references.append("ref table " + identifier(name))
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
    if relationships:  # Power BI omits this file when a model has no relationships
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
            "width": WIDTH,
            "height": HEIGHT,
        }
        documents[f"{prefix}/visuals/visual_000/visual.json"] = textbox(
            "visual_000", (friendly(stem) or stem) + "  ·  " + page.title, 0, 0, WIDTH, HEADER
        )
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
                # The helper keeps identical rows apart; show it as a plain row number.
                query["Values"]["projections"][0]["displayName"] = "#"
                kind = "tableEx"
            elif spec.kind == "slicer":
                query = {"Values": {"projections": [projection(spec.table, spec.category)]}}
                kind = "slicer"
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
                kind = {
                    "card": "card",
                    "bar": "clusteredBarChart",
                    "line": "lineChart",
                    "donut": "donutChart",
                }[spec.kind]
            objects = {
                # The title already names the number; hide the duplicate grey label.
                "card": {"categoryLabels": [{"properties": {"show": literal("false")}}]},
                "slicer": {"data": [{"properties": {"mode": literal("'Dropdown'")}}]},
            }.get(spec.kind)
            name = f"visual_{index:03d}"
            documents[f"{prefix}/visuals/{name}/visual.json"] = visual(
                name, kind, query, spec.title, *geometry, objects=objects
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
        "Open "
        + stem
        + ".pbip and select Refresh. The model reads the original XLSX at:\n"
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
                # The extracted folder is named after the uploaded file, not the internal run folder.
                archive.write(path, str(Path(stem) / path.relative_to(out)))
    return public_out / (stem + ".pbip"), public_zip
