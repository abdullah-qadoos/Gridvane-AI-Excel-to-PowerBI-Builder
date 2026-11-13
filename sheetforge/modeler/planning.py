"""Validated semantic decisions; DAX is restricted to a reviewable aggregate/ratio subset."""

import json
import re
from typing import Literal

import pandas as pd
from pydantic import Field, model_validator

from sheetforge.contracts import Contract
from sheetforge.llm.client import CompletionUnavailable, complete, enabled
from sheetforge.modeler.visuals import THEMES, VISUALS, required

HEADER, MARGIN, GAP, WIDTH, HEIGHT = 64, 24, 16, 1280, 720
COLOR = r"^#[0-9A-Fa-f]{6}$"
# Uploaded page pictures are stored under content-hash names, never user-chosen paths.
IMAGE = r"^[0-9a-f]{16}\.(?:png|jpg)$"

AVERAGED = re.compile(
    r"percent|pct|%|rate|utili[sz]ation|ratio|score|average|avg|mean|price|unit cost", re.I
)
FILTER_NAME = re.compile(
    r"region|country|state|city|team|department|segment|type|category|plan", re.I
)
ID_COLUMN = re.compile(r"(?:^|[ _-])(?:id|code|key|ref|no|nr|number|zip|postal)$", re.I)
CAMEL_ID = re.compile(r"[a-z](?:ID|Id)$")  # case-sensitive so "Paid" or "Valid" never match


class TableRole(Contract):
    name: str
    role: Literal["fact", "dimension"]


class Relationship(Contract):
    from_table: str
    from_column: str
    to_table: str
    to_column: str


class Measure(Contract):
    table: str
    name: str
    dax: str
    format_string: str = "#,##0.##"


class SuggestedVisual(Contract):
    kind: Literal[tuple(VISUALS)]
    table: str
    title: str
    measure: str | None = None
    measure2: str | None = None
    category: str | None = None
    columns: list[str] = Field(default_factory=list, max_length=256)
    # Position on the 1280x720 page, set by the designer or filled in by the auto layout.
    x: int | None = None
    y: int | None = None
    w: int | None = None
    h: int | None = None
    color: str | None = Field(default=None, pattern=COLOR)
    background: str | None = Field(default=None, pattern=COLOR)


class ReportPage(Contract):
    name: str
    title: str
    visuals: list[SuggestedVisual] = Field(min_length=1, max_length=30)
    background: str | None = Field(default=None, pattern=COLOR)
    image: str | None = Field(default=None, pattern=IMAGE)
    image_fit: Literal["Fit", "Fill", "Normal"] = "Fit"
    # Lets a background picture show through the visual boxes (percent).
    transparency: int = Field(default=0, ge=0, le=90)


class SemanticModelPlan(Contract):
    tables: list[TableRole] = Field(min_length=1, max_length=32)
    relationships: list[Relationship] = Field(default_factory=list, max_length=32)
    # Each of 32 supported tables can have 256 numeric columns plus a row count.
    measures: list[Measure] = Field(min_length=1, max_length=32 * 257)
    pages: list[ReportPage] = Field(min_length=1, max_length=33)
    theme: Literal[tuple(THEMES)] = "pivotra"


TOP = {
    "card",
    "multi_card",
    "kpi",
    "gauge",
    "slicer",
    "button_slicer",
    "list_slicer",
    "text_slicer",
}
TREND = {"line", "area", "area_stacked", "combo", "combo_stacked", "ribbon"}
GRID = {"table", "matrix"}


def layout(specs):
    """Dashboard grid: KPI cards on top, wide trends, side-by-side breakdowns, table last."""
    geometry = [None] * len(specs)
    inner = WIDTH - 2 * MARGIN
    group = {i: "card" if s.kind in TOP else "line" if s.kind in TREND else "table" if s.kind in GRID else "bar" for i, s in enumerate(specs)}  # fmt: skip
    kinds = {
        name: [i for i in group if group[i] == name] for name in ["card", "line", "bar", "table"]
    }
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
    card_rows = range(0, len(kinds["card"]), 6)
    height = (
        110 if rows else (HEIGHT - MARGIN - y - GAP * (len(card_rows) - 1)) / max(1, len(card_rows))
    )
    for start in card_rows:
        place(kinds["card"][start : start + 6], y, height)
        y += height + GAP
    if rows:
        available = HEIGHT - MARGIN - y - GAP * (len(rows) - 1)
        total = sum(weight[kind] for kind, _ in rows)
        for kind, indexes in rows:
            height = max(40, available * weight[kind] / total)
            place(indexes, y, height)
            y += height + GAP
    return geometry


def placed(visual):
    return None not in (visual.x, visual.y, visual.w, visual.h)


def quoted(value):
    return "'" + value.replace("'", "''") + "'"


def numeric_columns(frame):
    return [
        column
        for column in frame
        if pd.api.types.is_numeric_dtype(frame[column])
        and not pd.api.types.is_bool_dtype(frame[column])
    ]


def schema_context(tables):
    return {
        name: {
            "row_count": len(frame),
            "columns": [
                {
                    "name": column,
                    "dtype": str(frame[column].dtype),
                    "unique_count": int(frame[column].nunique()),
                    "null_count": int(frame[column].isna().sum()),
                }
                for column in frame
            ],
        }
        for name, frame in tables.items()
    }


AGG = re.compile(
    r"(SUM|AVERAGE|MIN|MAX|DISTINCTCOUNT)\(\s*'((?:[^']|'')+)'\[((?:[^\]]|\]\])+)\]\s*\)", re.I
)
COUNT = re.compile(r"COUNTROWS\(\s*'((?:[^']|'')+)'\s*\)", re.I)
RATIO = re.compile(
    r"DIVIDE\(\s*\[((?:[^\]]|\]\])+)\]\s*,\s*\[((?:[^\]]|\]\])+)\](?:\s*,\s*0(?:\.0+)?)?\s*\)", re.I
)


def validate_model(plan, tables):
    plan = SemanticModelPlan.model_validate(
        plan.model_dump() if isinstance(plan, SemanticModelPlan) else plan
    )
    names = [item.name for item in plan.tables]
    if len(names) != len(set(names)) or set(names) != set(tables):
        raise ValueError("The model must describe each cleaned table exactly once.")
    measures = {item.name: item for item in plan.measures}
    if len({name.casefold() for name in measures}) != len(plan.measures):
        raise ValueError("Measure names must be globally unique.")
    dependencies = {}
    for item in plan.measures:
        if item.table not in tables or item.name in tables[item.table].columns:
            raise ValueError("A measure references an unavailable table or collides with a column.")
        if any(
            ord(char) < 32 for value in [item.name, item.dax, item.format_string] for char in value
        ):
            raise ValueError(
                "DAX names, formats, and expressions cannot contain control characters."
            )
        expression = item.dax.strip()
        aggregate, count, ratio = (
            AGG.fullmatch(expression),
            COUNT.fullmatch(expression),
            RATIO.fullmatch(expression),
        )
        dependencies[item.name] = []
        if aggregate:
            operation, table, column = aggregate.groups()
            table, column = table.replace("''", "'"), column.replace("]]", "]")
            if table not in tables or column not in tables[table]:
                raise ValueError("DAX references an unavailable column.")
            if operation.upper() != "DISTINCTCOUNT" and column not in numeric_columns(
                tables[table]
            ):
                raise ValueError("Numeric DAX aggregates need a numeric column.")
        elif count:
            if count[1].replace("''", "'") not in tables:
                raise ValueError("COUNTROWS references an unavailable table.")
        elif ratio:
            refs = [value.replace("]]", "]") for value in ratio.groups()]
            if not set(refs) <= set(measures):
                raise ValueError("DIVIDE references an unavailable measure.")
            dependencies[item.name] = refs
        else:
            raise ValueError(
                "Use SUM, AVERAGE, MIN, MAX, DISTINCTCOUNT, COUNTROWS, or DIVIDE of named measures."
            )

    def visit(name, stack):
        if name in stack:
            raise ValueError("Measure dependencies cannot contain a cycle.")
        for child in dependencies[name]:
            visit(child, stack | {name})

    for name in dependencies:
        visit(name, set())
    seen_edges = set()
    graph = {}
    for item in plan.relationships:
        if (
            item.from_table == item.to_table
            or item.from_table not in tables
            or item.to_table not in tables
        ):
            raise ValueError("Relationships must connect two available tables.")
        source, target = tables[item.from_table], tables[item.to_table]
        if item.from_column not in source or item.to_column not in target:
            raise ValueError("Relationship key column is unavailable.")
        left, right = source[item.from_column], target[item.to_column]
        if right.isna().any() or right.duplicated().any():
            raise ValueError("The one-side relationship key must be unique and nonblank.")
        if str(left.dtype) != str(right.dtype) or not set(left.dropna()) <= set(right):
            raise ValueError(
                "Relationship keys must have compatible types and complete parent coverage."
            )
        edge = frozenset([item.from_table, item.to_table])
        if edge in seen_edges:
            raise ValueError("Only one active relationship is allowed per table pair.")
        seen_edges.add(edge)
        graph.setdefault(item.to_table, set()).add(item.from_table)
    # Single-direction filtering must not introduce multiple paths or cycles.
    for root in tables:
        reached = set()
        pending = [root]
        while pending:
            node = pending.pop()
            if node in reached:
                raise ValueError("Relationships create an ambiguous filter path or cycle.")
            reached.add(node)
            pending.extend(graph.get(node, set()))
    if len({page.name.casefold() for page in plan.pages}) != len(plan.pages):
        raise ValueError("Report page names must be unique.")
    for page in plan.pages:
        for visual in page.visuals:
            label = VISUALS[visual.kind][1]
            if visual.table not in tables or not set(visual.columns) <= set(
                tables[visual.table].columns
            ):
                raise ValueError("A visual references an unavailable table or column.")
            needs = required(visual.kind)
            if "category" in needs and visual.category is None:
                raise ValueError(f"The {label} visual “{visual.title}” needs a Group by column.")
            if "measure" in needs and visual.measure is None:
                raise ValueError(f"The {label} visual “{visual.title}” needs a value.")
            used = [m for m in (visual.measure, visual.measure2) if m is not None]
            if any(m not in measures for m in used):
                raise ValueError("Charts/cards need an available measure.")
            if visual.category is not None and visual.category not in tables[visual.table]:
                raise ValueError("Chart category column is unavailable.")
            if visual.category is not None:
                reachable, pending = set(), [visual.table]
                while pending:
                    name = pending.pop()
                    if name not in reachable:
                        reachable.add(name)
                        pending.extend(graph.get(name, set()))
                if any(measures[m].table not in reachable for m in used):
                    raise ValueError("Chart category cannot filter the measure's table.")
            box = (visual.x, visual.y, visual.w, visual.h)
            if any(v is not None for v in box) and not (
                placed(visual)
                and 0 <= visual.x
                and HEADER <= visual.y
                and visual.w >= 40
                and visual.h >= 40
                and visual.x + visual.w <= WIDTH
                and visual.y + visual.h <= HEIGHT
            ):
                raise ValueError(f"“{visual.title}” does not fit on the page.")
    # Pages without a full hand-made arrangement get the automatic dashboard layout.
    pages = []
    for page in plan.pages:
        if not all(placed(v) for v in page.visuals):
            page = page.model_copy(
                update={
                    "visuals": [
                        v.model_copy(update=dict(zip("xywh", box, strict=True)))
                        for v, box in zip(page.visuals, layout(page.visuals), strict=True)
                    ]
                }
            )
        pages.append(page)
    return plan.model_copy(update={"pages": pages})


def heuristic_model(tables):
    roles = {
        name: "fact" if numeric_columns(frame) else "dimension" for name, frame in tables.items()
    }
    relationships = []
    connected = set()
    for fact_name, fact in tables.items():
        for dimension_name, dimension in tables.items():
            edge = frozenset([fact_name, dimension_name])
            if fact_name == dimension_name or edge in connected:
                continue
            for column in set(fact.columns) & set(dimension.columns):
                if not re.search(r"(?:^|[ _])(id|code)(?:$|[ _])", column, re.I):
                    continue
                left, right = fact[column], dimension[column]
                if (
                    left.duplicated().any()
                    and not right.isna().any()
                    and not right.duplicated().any()
                    and str(left.dtype) == str(right.dtype)
                    and set(left.dropna()) <= set(right)
                ):
                    relationships.append(
                        Relationship(
                            from_table=fact_name,
                            from_column=column,
                            to_table=dimension_name,
                            to_column=column,
                        )
                    )
                    roles[dimension_name] = "dimension"
                    connected.add(edge)
                    break
    measures, pages, sections = [], [], []
    used = set()
    for name, frame in tables.items():

        def add_measure(label, dax, owner=name, fmt="#,##0.##"):
            # Plain names ("Total GPUs"); the table is only added when two tables would clash.
            taken = used | {column.casefold() for frame in tables.values() for column in frame}
            if label.casefold() in taken:
                label = f"{label} ({owner})"
            while label.casefold() in taken:
                label += "_"
            used.add(label.casefold())
            measures.append(Measure(table=owner, name=label, dax=dax, format_string=fmt))
            return label

        def aggregate(column, name=name, frame=frame):
            reference = f"{quoted(name)}[{column.replace(']', ']]')}]"
            if not AVERAGED.search(column):
                return add_measure("Total " + column, f"SUM({reference})")
            # Percentages and rates are averaged; adding 50% + 50% would show 100%.
            fraction = (
                frame[column].dropna().abs().max() <= 1.5 if frame[column].notna().any() else False
            )
            return add_measure(
                "Average " + column, f"AVERAGE({reference})", fmt="0.0%" if fraction else "#,##0.0"
            )

        count = add_measure("Record count", f"COUNTROWS({quoted(name)})")
        totals = [
            aggregate(column)
            for column in numeric_columns(frame)
            if not (ID_COLUMN.search(column) or CAMEL_ID.search(column))
        ]
        primary = totals[0] if totals else count
        # Readable groupings only: text columns with 2-50 values, never IDs or one-per-row columns.
        groupings = [
            column
            for column in frame
            if column not in numeric_columns(frame)
            and not pd.api.types.is_datetime64_any_dtype(frame[column])
            and not (ID_COLUMN.search(column) or CAMEL_ID.search(column))
            and 2 <= frame[column].nunique() <= 50
            and (len(frame) < 10 or frame[column].nunique() < 0.9 * frame[column].notna().sum())
        ]
        category = groupings[0] if groupings else None
        small = sorted(
            (c for c in groupings if frame[c].nunique() <= 8), key=lambda c: frame[c].nunique()
        )
        # A share donut reads best with a few slices; prefer a different column than the bar.
        donut = next((c for c in small if c != category), small[0] if small else None)
        filter_by = next(
            (c for c in groupings if FILTER_NAME.search(c) and c not in {category, donut}),
            next((c for c in groupings if c not in {category, donut}), category),
        )
        date = next(
            (column for column in frame if pd.api.types.is_datetime64_any_dtype(frame[column])),
            None,
        )
        charts = []
        if date:
            charts.append(
                SuggestedVisual(
                    kind="line",
                    table=name,
                    title=f"{primary} over time",
                    measure=primary,
                    category=date,
                )
            )
        if category:  # a bar per raw record is unreadable, so no grouping means no bar chart
            charts.append(
                SuggestedVisual(
                    kind="bar",
                    table=name,
                    title=f"{primary} by {category}",
                    measure=primary,
                    category=category,
                )
            )
        if donut:
            charts.append(
                SuggestedVisual(
                    kind="donut",
                    table=name,
                    title=f"Share of {primary} by {donut}",
                    measure=primary,
                    category=donut,
                )
            )
        sections.append(
            {
                "name": name,
                "measures": [count, *totals],
                "charts": charts,
                "slicer": [
                    SuggestedVisual(kind="slicer", table=name, title=filter_by, category=filter_by)
                ]
                if filter_by
                else [],
                "table": SuggestedVisual(
                    kind="table", table=name, title="All records", columns=list(frame.columns)
                ),
            }
        )

    def cards(names):
        owner = {measure.name: measure.table for measure in measures}
        return [SuggestedVisual(kind="card", table=owner[n], title=n, measure=n) for n in names]

    # Overview: KPI row, a filter, then the first table's trend and breakdowns.
    first = sections[0]
    headline = [measure.name for measure in measures[:4]]
    overview = ReportPage(
        name="Overview",
        title="Overview",
        visuals=cards(headline) + first["slicer"] + first["charts"][:3],
    )
    # One page per table: the numbers not on the overview, its own charts, a large table.
    for index, section in enumerate(sections):
        extra = [n for n in section["measures"] if n not in headline][:4] or section["measures"][:1]
        own_charts = [] if index == 0 else section["charts"][:2]
        title = section["name"] if len(sections) > 1 else "Details"
        pages.append(
            ReportPage(
                name="Data · " + section["name"],
                title=title,
                visuals=section["slicer"] + cards(extra) + own_charts + [section["table"]],
            )
        )
    plan = SemanticModelPlan(
        tables=[TableRole(name=name, role=role) for name, role in roles.items()],
        relationships=relationships,
        measures=measures,
        pages=[overview, *pages],
    )
    try:
        return validate_model(plan, tables)
    except ValueError:
        # Ambiguous inferred graphs are omitted rather than guessed.
        return validate_model(plan.model_copy(update={"relationships": []}), tables)


def plan_model(tables, *, use_ai=True):
    fallback = heuristic_model(tables)
    if not use_ai or not enabled():
        return fallback, [
            "No-AI mode: used deterministic measures, verified key relationships, and report pages."
        ]

    class SourceModelPlan(SemanticModelPlan):
        @model_validator(mode="after")
        def source(self):
            validate_model(SemanticModelPlan.model_validate(self.model_dump()), tables)
            return self

    try:
        response = complete(
            "Design a semantic model using only these cleaned schemas/statistics. Return strict JSON. Use only validated aggregate DAX or DIVIDE of named measures. Relationships are many-to-one with unique nonblank parent keys. Do not invent data or units.",
            [
                {
                    "role": "user",
                    "content": json.dumps(
                        {"schemas": schema_context(tables), "heuristic_plan": fallback.model_dump()}
                    ),
                }
            ],
            SourceModelPlan,
        )
        return validate_model(response, tables), ["Used a validated semantic model plan."]
    except (CompletionUnavailable, ValueError):
        return fallback, [
            "Invalid or unavailable model decision; used deterministic semantic planning."
        ]
