"""'Design it for me': the AI picks the theme, visuals, layout and colors; rules do it offline.

The AI sees column names, types and counts (never rows), the measures, and the visual catalogue.
Its answer must pass the same validation as a hand-made design.
"""

import json

from pydantic import Field, model_validator

from sheetforge.contracts import Contract
from sheetforge.llm.client import CompletionUnavailable, complete, enabled
from sheetforge.modeler.planning import (
    HEADER,
    HEIGHT,
    WIDTH,
    ReportPage,
    schema_context,
    validate_model,
)
from sheetforge.modeler.visuals import THEMES, VISUALS, required

BOX = {"x", "y", "w", "h"}

SYSTEM = f"""You are a senior Power BI report designer. Design a clear, attractive report for this data.
Pages are {WIDTH}x{HEIGHT} pixels. y from 0 to {HEADER} is the title band, so every visual needs y >= {HEADER}.
Keep a 24px margin and 16px gaps; boxes must not overlap and must stay on the page.
Good practice: a row of 3-5 KPI cards on top, one large hero chart, supporting breakdowns, a slicer for the
main filter. Use line/area charts for dates, bar charts for ranked categories with many values, donut/pie only
for 2-6 shares, a matrix or table on detail pages. At most 10 visuals per page and 1-4 pages.
Pick the theme that suits the subject. Optionally give charts a color from the chosen theme's palette and a
subtle page background tint. Titles are short plain English. Use only the given tables, columns, measures,
visual kinds and themes; a kind's required fields must be set."""


class DesignedPage(Contract):
    name: str
    title: str
    visuals: list[dict] = Field(min_length=1, max_length=12)
    background: str | None = None


class Design(Contract):
    theme: str
    pages: list[DesignedPage] = Field(min_length=1, max_length=6)


def fits(page):
    return all(
        None not in (v.get("x"), v.get("y"), v.get("w"), v.get("h"))
        and v["x"] >= 0
        and v["y"] >= HEADER
        and v["w"] >= 40
        and v["h"] >= 40
        and v["x"] + v["w"] <= WIDTH
        and v["y"] + v["h"] <= HEIGHT
        for v in page["visuals"]
    )


def merge(plan, design):
    """Apply a design to the model; boxes that do not fit fall back to the automatic layout."""
    plan = plan.model_dump()
    old = plan["pages"]
    pages = []
    for index, page in enumerate(design["pages"]):
        page = dict(page)
        if not fits(page):
            page["visuals"] = [
                {k: v for k, v in visual.items() if k not in BOX} for visual in page["visuals"]
            ]
        # Pictures and transparency are the user's own choices; keep them on the same page slot.
        if index < len(old):
            for key in ("image", "image_fit", "transparency"):
                if old[index].get(key) is not None:
                    page[key] = old[index][key]
        pages.append(page)
    return plan | {"theme": design["theme"], "pages": pages}


def luminance(hex_color):
    channels = [int(hex_color[i : i + 2], 16) / 255 for i in (1, 3, 5)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast(a, b):
    """WCAG contrast ratio between two hex colors."""
    light, dark = sorted([luminance(a), luminance(b)], reverse=True)
    return (light + 0.05) / (dark + 0.05)


def rule_design(plan, tables):
    """Offline designer: richer chart choices and a distinct palette color for every chart."""
    theme = THEMES[plan.theme]
    # Pale palette colors are unreadable as text or thin lines on white cards; skip them.
    readable = [c for c in theme[6] if contrast(c, theme[2]) >= 2.5]
    colors = readable or theme[6]
    pages = []
    for page in plan.pages:
        visuals, color = [], 0
        for visual in page.visuals:
            visual = visual.model_dump()
            frame = tables.get(visual["table"])
            values = (
                frame[visual["category"]].nunique()
                if frame is not None and visual.get("category") in frame
                else 0
            )
            if visual["kind"] == "bar" and 0 < values <= 8:
                visual["kind"] = "column"  # few categories read better as columns
            elif visual["kind"] == "line":
                visual["kind"] = "area"
            if visual["kind"] not in {"slicer", "table", "donut", "pie"}:
                visual["color"] = colors[color % len(colors)]
                color += 1
            visuals.append({k: v for k, v in visual.items() if k not in BOX})
        pages.append(
            ReportPage.model_validate(page.model_dump() | {"visuals": visuals}).model_dump()
        )
    return validate_model(plan.model_dump() | {"pages": pages}, tables)


def suggest_design(plan, tables, *, use_ai=True):
    plan = validate_model(plan, tables)
    if not use_ai or not enabled():
        return rule_design(plan, tables), [
            "Designed with built-in rules. Turn on an AI provider for a design made for your data."
        ]

    class Checked(Design):
        @model_validator(mode="after")
        def valid(self):
            if self.theme not in THEMES:
                raise ValueError(f"theme must be one of {sorted(THEMES)}")
            validate_model(merge(plan, self.model_dump()), tables)
            return self

    question = {
        "schemas": schema_context(tables),
        "measures": [m.model_dump() for m in plan.measures],
        "visual_kinds": {
            key: {"label": label, "fields": sorted(roles), "required": sorted(required(key))}
            for key, (_, label, roles, _, _) in VISUALS.items()
        },
        "themes": {key: {"label": t[0], "palette": t[6]} for key, t in THEMES.items()},
        "current_design": {"theme": plan.theme, "pages": [p.model_dump() for p in plan.pages]},
    }
    try:
        reply = complete(SYSTEM, [{"role": "user", "content": json.dumps(question)}], Checked)
        return validate_model(merge(plan, reply), tables), ["The AI designed this report."]
    except (CompletionUnavailable, ValueError):
        return rule_design(plan, tables), [
            "The AI design was unavailable or invalid, so built-in rules designed it."
        ]
