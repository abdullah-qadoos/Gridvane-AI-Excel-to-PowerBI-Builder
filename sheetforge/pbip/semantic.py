"""Multi-table TMDL, verified relationships, measures, and native report pages."""

import json
import os
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5
from zipfile import ZIP_DEFLATED, ZipFile

from sheetforge.modeler import heuristic_model, validate_model
from sheetforge.modeler.planning import HEADER, HEIGHT, WIDTH, layout  # noqa: F401 (re-exported)
from sheetforge.modeler.visuals import THEMES, VISUALS, band
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


def solid(color):
    return {"solid": {"color": color}}


def colored(color):
    """A per-visual color override (visual objects take expressions, not plain strings)."""
    return {"solid": {"color": literal("'" + color + "'")}}


def theme_styles(theme):
    """Report-wide look (Power BI theme "visualStyles"): page color, rounded cards, ink titles."""
    _, page, card, line, ink, muted, _ = THEMES[theme]
    return {
        "page": {"*": {"background": [{"color": solid(page), "transparency": 0}]}},
        "*": {
            "*": {
                "background": [{"show": True, "color": solid(card), "transparency": 0}],
                "border": [{"show": True, "color": solid(line), "radius": 12}],
                "dropShadow": [{"show": False}],
                "padding": [{"top": 10, "bottom": 10, "left": 14, "right": 14}],
                "title": [
                    {
                        "show": True,
                        "fontColor": solid(ink),
                        "fontSize": 12,
                        "fontFamily": "Segoe UI Semibold",
                    }
                ],
            }
        },
        "card": {
            "*": {
                "labels": [
                    {"color": solid(ink), "fontSize": 30, "fontFamily": "Segoe UI Semibold"}
                ],
                "categoryLabels": [{"show": False}],
            }
        },
        "tableEx": {
            "*": {
                "columnHeaders": [
                    {
                        "fontColor": solid(card),
                        "backColor": solid(ink),
                        "fontFamily": "Segoe UI Semibold",
                    }
                ],
                "values": [
                    {
                        "fontColorPrimary": solid(ink),
                        "backColorPrimary": solid(card),
                        "fontColorSecondary": solid(ink),
                        "backColorSecondary": solid(page),
                    }
                ],
                "grid": [{"gridHorizontalColor": solid(line), "gridVerticalColor": solid(line)}],
            }
        },
        "lineChart": {"*": {"lineStyles": [{"strokeWidth": 3}]}},
        "slicer": {"*": {"header": [{"fontColor": solid(muted), "fontSize": 10}]}},
        # The header band draws its own background and has no border.
        "textbox": {
            "*": {
                "border": [{"show": False}],
                "padding": [{"top": 0, "bottom": 0, "left": 0, "right": 0}],
            }
        },
    }


# Per-box colors: charts color their bars/lines; cards color the number. Multi-color visuals
# (pie, donut, treemap, maps) keep the theme palette so their slices stay distinguishable.
NO_FILL = {"pie", "donut", "treemap", "map", "filled_map", "shape_map", "azure_map", "table",
           "matrix", "slicer", "button_slicer", "list_slicer", "text_slicer"}  # fmt: skip


def build_semantic_project(result, out, model_plan=None, name=None, images=None):
    """name: the uploaded file name; the project, report and model are named after it.
    images: folder holding the page background pictures the plan refers to."""
    if result.plan is None or result.source is None:
        raise ValueError("A validated cleaning result is required.")
    plan = validate_model(model_plan or heuristic_model(result.tables), result.tables)
    out = Path(out).resolve()
    zipped = out.with_suffix(".zip")
    if out.exists() or zipped.exists():
        raise ValueError("Output folder or ZIP already exists; choose a new output path.")
    stem = output_stem(name or result.source.name)
    theme = THEMES[plan.theme]
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
                    "items": [{"name": "Pivotra", "path": "Pivotra.json", "type": "CustomTheme"}]
                    + [
                        {"name": picture, "path": picture, "type": "Image"}
                        for picture in sorted({p.image for p in plan.pages if p.image})
                    ],
                }
            ],
        },
        rd + "/StaticResources/RegisteredResources/Pivotra.json": {
            "name": "Pivotra",
            "dataColors": theme[6],
            "background": theme[2],
            "foreground": theme[4],
            "tableAccent": theme[6][0],
            "textClasses": {
                "title": {"fontFace": "Segoe UI Semibold", "fontSize": 13, "color": theme[4]},
                "label": {"fontFace": "Segoe UI", "fontSize": 10, "color": theme[5]},
                "callout": {"fontFace": "Segoe UI Semibold", "fontSize": 30, "color": theme[4]},
                "header": {"fontFace": "Segoe UI Semibold", "fontSize": 12, "color": theme[4]},
            },
            "visualStyles": theme_styles(plan.theme),
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
    pictures = {}
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
        backdrop = {"transparency": literal("0D")}
        if page.background:
            backdrop["color"] = colored(page.background)
        if page.image:
            source = Path(images or "") / page.image
            if not images or not source.is_file():
                raise ValueError(f"The background picture for \u201c{page.title}\u201d is missing.")
            pictures[page.image] = source.read_bytes()
            backdrop["image"] = {
                "image": {
                    "name": literal("'" + page.image + "'"),
                    "url": {
                        "expr": {
                            "ResourcePackageItem": {
                                "PackageName": "RegisteredResources",
                                "PackageType": 1,
                                "ItemName": page.image,
                            }
                        }
                    },
                    "scaling": literal("'" + page.image_fit + "'"),
                }
            }
        if len(backdrop) > 1:
            documents[prefix + "/page.json"]["objects"] = {"background": [{"properties": backdrop}]}
        documents[f"{prefix}/visuals/visual_000/visual.json"] = textbox(
            "visual_000",
            (friendly(stem) or stem) + "  \u00b7  " + page.title,
            0,
            0,
            WIDTH,
            HEADER,
            color=band(plan.theme)[1],
            background=band(plan.theme)[0],
        )
        for index, spec in enumerate(page.visuals, 1):
            name = f"visual_{index:03d}"
            geometry = (spec.x, spec.y, spec.w, spec.h)
            kind, label, roles, _, note = VISUALS[spec.kind]
            if kind == "textbox":
                # Text boxes and sign-in visuals (Power Apps, ArcGIS...) become a labelled box.
                caption = spec.title if spec.kind == "text" else f"{label}: {note}"
                documents[f"{prefix}/visuals/{name}/visual.json"] = textbox(
                    name, caption, *geometry, color=theme[4], background=spec.background or theme[2],
                    size="12pt",
                )  # fmt: skip
                continue
            query = {}
            for field, role in roles.items():
                if field == "columns":
                    # The helper keeps identical rows apart; show it as a plain row number.
                    columns = spec.columns or list(result.tables[spec.table].columns)
                    items = [projection(spec.table, c) for c in [helpers[spec.table], *columns]]
                    items[0]["displayName"] = "#"
                elif field == "category" and spec.category:
                    items = [projection(spec.table, spec.category)]
                elif field in {"measure", "measure2"} and getattr(spec, field):
                    measure = getattr(spec, field)
                    items = [projection(measures[measure].table, measure, measure=True)]
                else:
                    continue
                query.setdefault(role, {"projections": []})["projections"] += items
            objects = {
                # The title already names the number; hide the duplicate grey label.
                "card": {"categoryLabels": [{"properties": {"show": literal("false")}}]},
                "slicer": {"data": [{"properties": {"mode": literal("'Dropdown'")}}]},
            }.get(spec.kind, {})
            if spec.color and spec.kind not in NO_FILL:
                card = spec.kind in {"card", "multi_card"}
                target, prop = ("labels", "color") if card else ("dataPoint", "fill")
                objects = objects | {target: [{"properties": {prop: colored(spec.color)}}]}
            documents[f"{prefix}/visuals/{name}/visual.json"] = visual(
                name, kind, query, spec.title, *geometry, objects=objects or None,
                background=spec.background or (theme[2] if page.transparency else None),
                transparency=page.transparency,
            )  # fmt: skip
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
    for picture, content in pictures.items():
        path = out / rd / "StaticResources" / "RegisteredResources" / picture
        path.write_bytes(content)
    with ZipFile(zipped, "w", ZIP_DEFLATED) as archive:
        for path in sorted(out.rglob("*")):
            if path.is_file():
                # The extracted folder is named after the uploaded file, not the internal run folder.
                archive.write(path, str(Path(stem) / path.relative_to(out)))
    return public_out / (stem + ".pbip"), public_zip
