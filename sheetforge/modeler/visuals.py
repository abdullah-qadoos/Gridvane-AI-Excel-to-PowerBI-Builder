"""Every visual the designer offers, mapped to Power BI's own data-role names, plus report themes.

Role names come from Power BI Desktop's visual capabilities (Category/Y/Y2/X, Group/Values,
Rows/Values, Indicator/TrendLine/Goal, Analyze/ExplainBy, Target/ExplainBy, Y/TargetValue).
"""

# Our fields: category = a column, measure / measure2 = report numbers.
CHART = {"category": "Category", "measure": "Y"}
EMPTY: dict = {}  # created empty; the user drags fields in Power BI (needs scripts/sign-in/preview)

# key: (Power BI visualType, label, roles, labels for our fields, note shown in the designer)
VISUALS = {
    "bar_stacked": ("barChart", "Stacked bar", CHART, {}, ""),
    "column_stacked": ("columnChart", "Stacked column", CHART, {}, ""),
    "bar": ("clusteredBarChart", "Clustered bar", CHART, {}, ""),
    "column": ("clusteredColumnChart", "Clustered column", CHART, {}, ""),
    "bar_100": ("hundredPercentStackedBarChart", "100% stacked bar", CHART, {}, ""),
    "column_100": ("hundredPercentStackedColumnChart", "100% stacked column", CHART, {}, ""),
    "line": ("lineChart", "Line", CHART, {}, ""),
    "area": ("areaChart", "Area", CHART, {}, ""),
    "area_stacked": ("stackedAreaChart", "Stacked area", CHART, {}, ""),
    "combo_stacked": (
        "lineStackedColumnComboChart",
        "Line and stacked column",
        {"category": "Category", "measure": "Y", "measure2": "Y2"},
        {"measure": "Column value", "measure2": "Line value"},
        "",
    ),
    "combo": (
        "lineClusteredColumnComboChart",
        "Line and clustered column",
        {"category": "Category", "measure": "Y", "measure2": "Y2"},
        {"measure": "Column value", "measure2": "Line value"},
        "",
    ),
    "ribbon": ("ribbonChart", "Ribbon", CHART, {}, ""),
    "waterfall": ("waterfallChart", "Waterfall", CHART, {}, ""),
    "funnel": ("funnel", "Funnel", CHART, {}, ""),
    "scatter": (
        "scatterChart",
        "Scatter",
        {"category": "Category", "measure": "X", "measure2": "Y"},
        {"category": "One dot per", "measure": "Across (X)", "measure2": "Up (Y)"},
        "",
    ),
    "pie": ("pieChart", "Pie", CHART, {}, ""),
    "donut": ("donutChart", "Donut", CHART, {}, ""),
    "treemap": ("treemap", "Treemap", {"category": "Group", "measure": "Values"}, {}, ""),
    "map": (
        "map",
        "Map",
        {"category": "Category", "measure": "Size"},
        {"category": "Location", "measure": "Bubble size"},
        "Maps are off by default: in Power BI open File > Options > Security and allow map visuals.",
    ),
    "filled_map": (
        "filledMap",
        "Filled map",
        {"category": "Category"},
        {"category": "Location"},
        "Maps are off by default: in Power BI open File > Options > Security and allow map visuals.",
    ),
    "shape_map": ("shapeMap", "Shape map", EMPTY, {}, "Add the location field in Power BI."),
    "azure_map": (
        "azureMap",
        "Azure map",
        EMPTY,
        {},
        "Needs Azure Maps allowed in Power BI options (and by your admin).",
    ),
    "gauge": (
        "gauge",
        "Gauge",
        {"measure": "Y", "measure2": "TargetValue"},
        {"measure2": "Target"},
        "",
    ),
    "card": ("card", "Card", {"measure": "Values"}, {}, ""),
    "multi_card": (
        "multiRowCard",
        "Multi-row card",
        {"measure": "Values", "measure2": "Values"},
        {"measure2": "Second value"},
        "",
    ),
    "kpi": (
        "kpi",
        "KPI",
        {"measure": "Indicator", "category": "TrendLine", "measure2": "Goal"},
        {"category": "Trend over", "measure2": "Goal"},
        "",
    ),
    "slicer": ("slicer", "Slicer", {"category": "Values"}, {"category": "Filter by"}, ""),
    "button_slicer": (
        "advancedSlicerVisual",
        "Button slicer",
        {"category": "Values"},
        {"category": "Filter by"},
        "",
    ),
    "list_slicer": ("listSlicer", "List slicer", EMPTY, {}, "Add the filter field in Power BI."),
    "text_slicer": ("textSlicer", "Text slicer", EMPTY, {}, "Add the search field in Power BI."),
    "table": ("tableEx", "Table", {"columns": "Values"}, {}, ""),
    "matrix": (
        "pivotTable",
        "Matrix",
        {"category": "Rows", "measure": "Values"},
        {"category": "Rows"},
        "",
    ),
    "r_script": (
        "scriptVisual",
        "R script",
        EMPTY,
        {},
        "Power BI asks you to enable script visuals; needs R installed.",
    ),
    "python": (
        "pythonVisual",
        "Python",
        EMPTY,
        {},
        "Power BI asks you to enable script visuals; needs Python installed.",
    ),
    "key_influencers": (
        "keyDriversVisual",
        "Key influencers",
        {"measure": "Target", "category": "ExplainBy"},
        {"measure": "Analyze", "category": "Explain by"},
        "",
    ),
    "decomposition": (
        "decompositionTreeVisual",
        "Decomposition tree",
        {"measure": "Analyze", "category": "ExplainBy"},
        {"measure": "Analyze", "category": "Explain by"},
        "",
    ),
    "qna": (
        "qnaVisual",
        "Q&A",
        EMPTY,
        {},
        "Microsoft retires Q&A in February 2027 and suggests Copilot.",
    ),
    "narrative": ("aiNarratives", "Narrative", EMPTY, {}, "Writes a text summary in Power BI."),
    "text": ("textbox", "Text box", EMPTY, {}, "Shows the title as text."),
    # These need a Microsoft sign-in or an online service, so the box marks where to add them.
    "metrics": ("textbox", "Metrics", EMPTY, {}, "Placeholder: add Metrics in Power BI (sign-in)."),
    "paginated": (
        "textbox",
        "Paginated report",
        EMPTY,
        {},
        "Placeholder: add it in Power BI (needs the Power BI service).",
    ),
    "image": ("textbox", "Image", EMPTY, {}, "Placeholder: insert your picture in Power BI."),
    "arcgis": (
        "textbox",
        "ArcGIS map",
        EMPTY,
        {},
        "Placeholder: add ArcGIS in Power BI (sign-in).",
    ),
    "power_apps": ("textbox", "Power Apps", EMPTY, {}, "Placeholder: add Power Apps (sign-in)."),
    "power_automate": (
        "textbox",
        "Power Automate",
        EMPTY,
        {},
        "Placeholder: add Power Automate (sign-in).",
    ),
}
FIELD_LABELS = {"category": "Group by", "measure": "Value", "measure2": "Second value"}


def required(kind):
    """Fields a visual must have; the second value is always optional."""
    return {field for field in VISUALS[kind][2] if field in {"category", "measure"}}


# Themes: page, card, border, ink (titles), muted, then the data colors in order.
THEMES = {
    "pivotra": ("Pivotra cream", "#F3ECDD", "#FFFFFF", "#E2D8C6", "#15130F", "#857E70",
                ["#B5562A", "#2F5A43", "#D9A47E", "#15130F", "#7FA88F", "#E8C9A9"]),
    "midnight": ("Midnight", "#0F1115", "#1A1D24", "#2A2F3A", "#F2EFE8", "#9AA0AA",
                 ["#F28C4B", "#5BC0BE", "#E9C46A", "#9B8CF2", "#F25F5C", "#7BD389"]),
    "ocean": ("Ocean", "#EEF3F8", "#FFFFFF", "#D5E0EC", "#0E2A47", "#5D7288",
              ["#1F6FB2", "#2BA8A0", "#F2A541", "#0E2A47", "#7FB3E0", "#E76F51"]),
    "forest": ("Forest", "#EEF2EA", "#FFFFFF", "#D3DCCB", "#1E2B1F", "#66705F",
               ["#2F5A43", "#8FB339", "#C9A227", "#1E2B1F", "#6B8F71", "#D9822B"]),
    "sunset": ("Sunset", "#FFF4EC", "#FFFFFF", "#F1DCCB", "#2B1B17", "#8A6F64",
               ["#E4572E", "#F3A712", "#A8201A", "#29335C", "#669BBC", "#DB9D47"]),
    "mono": ("Monochrome", "#F5F5F5", "#FFFFFF", "#DDDDDD", "#111111", "#777777",
             ["#111111", "#555555", "#888888", "#BBBBBB", "#333333", "#999999"]),
    "rose": ("Rose", "#FBF0F1", "#FFFFFF", "#F0D9DC", "#3A1F25", "#8C6B71",
             ["#C2416B", "#E58FA6", "#7D2E4F", "#F2B880", "#5C8D89", "#B9A3C9"]),
    "lavender": ("Lavender", "#F3F1FA", "#FFFFFF", "#DEDAF0", "#231D3B", "#706A8C",
                 ["#6C5CE7", "#A29BFE", "#00B894", "#FD79A8", "#2D3561", "#FDCB6E"]),
    "emerald": ("Emerald", "#ECF6F1", "#FFFFFF", "#CFE5DA", "#0F2A20", "#5E7A6D",
                ["#0F9D6B", "#34C38F", "#0B5D41", "#F4B942", "#4A90A4", "#9FD8B9"]),
    "amber": ("Amber", "#FFF8E8", "#FFFFFF", "#F2E2BC", "#2E2210", "#857451",
              ["#E09F1F", "#C4561F", "#6B4F12", "#2F6F8F", "#F2C75C", "#8C9C3B"]),
    "slate": ("Slate night", "#161B22", "#1F2630", "#2E3846", "#E6EDF3", "#8B98A5",
              ["#58A6FF", "#3FB950", "#D29922", "#F778BA", "#A371F7", "#FF7B72"]),
    "espresso": ("Espresso", "#1B1512", "#261E1A", "#3A2E27", "#F3E9DD", "#A8978A",
                 ["#D9A066", "#B5562A", "#8FB996", "#E8C9A9", "#C97C5D", "#7FA3B5"]),
}  # fmt: skip


def catalogue():
    """What the designer page needs to draw its palette, fields and theme previews."""
    return {
        "visuals": [
            {
                "key": key,
                "label": label,
                "fields": [f for f in ["category", "measure", "measure2"] if f in roles],
                "labels": FIELD_LABELS | labels,
                "required": sorted(required(key)),
                "note": note,
            }
            for key, (_, label, roles, labels, note) in VISUALS.items()
        ],
        "themes": {
            key: {"label": t[0], "page": t[1], "card": t[2], "line": t[3], "ink": t[4],
                  "muted": t[5], "colors": t[6]}
            for key, t in THEMES.items()
        },
    }  # fmt: skip


def band(theme):
    """(background, text) for the page title band: ink on light themes, a card tone on dark ones."""
    _, page, card, _, ink, _, _ = THEMES[theme]
    red, green, blue = (int(page[i : i + 2], 16) for i in (1, 3, 5))
    dark = 0.299 * red + 0.587 * green + 0.114 * blue < 128
    return (card, ink) if dark else (ink, page)
