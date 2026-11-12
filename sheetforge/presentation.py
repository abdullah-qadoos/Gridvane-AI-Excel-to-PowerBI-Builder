"""Numbers and spreadsheet presentation are computed locally from complete clean tables."""

import re
from datetime import datetime
from pathlib import Path

import pandas as pd
from openpyxl import Workbook
from openpyxl.chart import BarChart, LineChart, Reference
from openpyxl.formatting.rule import FormulaRule
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.table import Table, TableStyleInfo

from sheetforge.paths import output_stem


def kpis(tables):
    cards = [
        {"label": label, "value": None, "delta": None, "note": "No matching column"}
        for label in ["Usable rows", "Total spend", "Total usage", "Average utilization"]
    ]
    cards[0].update(
        value=sum(len(frame) for frame in tables.values()), note="Across cleaned tables"
    )
    aliases = [
        r"\b(cost|spend|expense|amount spent)\b",
        r"\b(usage|consumption|units|gpu hours)\b",
        r"\b(utilization|utilisation)\b",
    ]
    for index, pattern in enumerate(aliases, 1):
        for name, frame in tables.items():
            column = next(
                (
                    column
                    for column in frame
                    if re.search(pattern, column.replace("_", " "), re.I)
                    and pd.api.types.is_numeric_dtype(frame[column])
                ),
                None,
            )
            if column is None:
                continue
            values = frame[column].dropna()
            if values.empty:
                continue
            reducer = "mean" if index == 3 else "sum"
            scale = 100 if index == 3 and values.between(0, 1).all() else 1
            cards[index].update(
                value=float(getattr(values, reducer)()) * scale,
                note=f"{name} / {column}",
                unit="%" if index == 3 else None,
            )
            date_column = next(
                (c for c in frame if pd.api.types.is_datetime64_any_dtype(frame[c])), None
            )
            if date_column is not None and frame[date_column].notna().any():
                dates = frame[date_column].dt.normalize()
                latest = dates.max()
                start = latest - pd.Timedelta(days=latest.weekday())
                current = frame.loc[dates.ge(start), column].dropna()
                prior = frame.loc[
                    dates.ge(start - pd.Timedelta(days=7)) & dates.lt(start), column
                ].dropna()
                if not prior.empty and not current.empty:
                    previous, now = (
                        float(getattr(prior, reducer)()),
                        float(getattr(current, reducer)()),
                    )
                    cards[index]["delta"] = (
                        (now - previous) / abs(previous) * 100 if previous else None
                    )
                    cards[index]["period_note"] = (
                        "Latest data week vs prior week; the latest week may be partial."
                    )
            break
    # Count comparisons use the first dated table so unlike dimensions are not combined.
    for frame in tables.values():
        date_column = next(
            (c for c in frame if pd.api.types.is_datetime64_any_dtype(frame[c])), None
        )
        if date_column is not None and frame[date_column].notna().any():
            dates = frame[date_column].dt.normalize()
            start = dates.max() - pd.Timedelta(days=dates.max().weekday())
            current = int(dates.ge(start).sum())
            prior = int((dates.ge(start - pd.Timedelta(days=7)) & dates.lt(start)).sum())
            cards[0]["delta"] = (current - prior) / prior * 100 if prior else None
            cards[0]["period_note"] = (
                "Latest data week vs prior week in the first dated table; the latest week may be partial."
            )
            break
    return cards


INK, CREAM, PAPER, BODY, MUTED, ACCENT, LINE = (
    "15130F",
    "F3ECDD",
    "FBF7EE",
    "3E3A32",
    "857E70",
    "B5562A",
    "E2D8C6",
)
MONEY = re.compile(
    r"cost|price|revenue|spend|amount|sales|salary|budget|profit|fee|income|expense|usd|eur|gbp|pkr|\$",
    re.I,
)
PERCENT = re.compile(r"percent|pct|%|rate|utili[sz]ation|share|margin", re.I)
MAX_CHARTS = 12


def number_format(name, series):
    if pd.api.types.is_bool_dtype(series):
        return None
    if pd.api.types.is_datetime64_any_dtype(series):
        values = series.dropna()
        timed = not values.empty and bool((values != values.dt.normalize()).any())
        return "yyyy-mm-dd hh:mm" if timed else "yyyy-mm-dd"
    if not pd.api.types.is_numeric_dtype(series):
        return None
    values = series.dropna().astype(float)
    if PERCENT.search(name):
        return "0.0%" if values.empty or values.abs().max() <= 1.5 else '0.0"%"'
    if MONEY.search(name):
        return "#,##0.00"
    return "#,##0" if values.empty or (values % 1 == 0).all() else "#,##0.00"


def style_header(cells):
    for cell in cells:
        cell.font = Font(name="Aptos", bold=True, color=CREAM)
        cell.fill = PatternFill("solid", fgColor=INK)
        cell.alignment = Alignment(vertical="center")


def band(sheet, ref):
    # Conditional banding keeps stripes correct after the user sorts or filters.
    sheet.conditional_formatting.add(
        ref, FormulaRule(formula=["MOD(ROW(),2)=0"], fill=PatternFill("solid", fgColor=PAPER))
    )


def sheet_title(name, used, reserved):
    title = re.sub(r"[\\/*?:\[\]]", "_", str(name)).strip("'")[:31] or "Table"
    original, count = title, 1
    while title.casefold() in used or title.casefold() in reserved:
        count += 1
        title = original[:26] + f"_{count}"
    used.add(title.casefold())
    return title


def table_name(name, used):
    base = "tbl_" + (re.sub(r"\W", "_", str(name)) or "Table")[:200]
    candidate, count = base, 1
    while candidate.casefold() in used:
        count += 1
        candidate = f"{base}_{count}"
    used.add(candidate.casefold())
    return candidate


def write_data_sheet(book, title, frame, names):
    sheet = book.create_sheet(title)
    sheet.append([str(column) for column in frame.columns])
    for row in frame.astype(object).where(pd.notna(frame), None).values.tolist():
        sheet.append(
            [
                value.to_pydatetime().replace(tzinfo=None)
                if isinstance(value, pd.Timestamp)
                else value
                for value in row
            ]
        )
        for cell in sheet[sheet.max_row]:
            if isinstance(cell.value, str):
                cell.data_type = "s"
    for cell in sheet[1]:
        cell.data_type = "s"
    sheet.freeze_panes = "A2"
    sheet.sheet_view.showGridLines = False
    style_header(sheet[1])
    sheet.row_dimensions[1].height = 28
    last = get_column_letter(max(1, len(frame.columns)))
    if len(frame) and len(frame.columns):
        ref = f"A1:{last}{len(frame) + 1}"
        table = Table(displayName=table_name(title, names), ref=ref)
        table.tableStyleInfo = TableStyleInfo(name="TableStyleLight1", showRowStripes=False)
        sheet.add_table(table)
        band(sheet, f"A2:{last}{len(frame) + 1}")
    for index, column in enumerate(frame.columns, 1):
        letter = get_column_letter(index)
        series = frame[column]
        fmt = number_format(str(column), series)
        sample = [str(value) for value in series.head(200) if pd.notna(value)]
        longest = max([len(text) for text in sample] or [0])
        sheet.column_dimensions[letter].width = min(50, max(10, len(str(column)) + 4, longest + 2))
        wrap = longest > 50
        for (cell,) in sheet.iter_rows(min_row=2, min_col=index, max_col=index):
            cell.font = Font(name="Aptos", size=11, color=BODY)
            if fmt:
                cell.number_format = fmt
            if wrap:
                cell.alignment = Alignment(wrap_text=True, vertical="top")
    return sheet


def write_summary(sheet, result, sheets, quality_audit, source_name):
    sheet.sheet_view.showGridLines = False
    sheet.column_dimensions["A"].width = 30
    for letter in "BCDE":
        sheet.column_dimensions[letter].width = 18
    sheet["A1"] = output_stem(source_name)
    sheet["A1"].data_type = "s"
    sheet["A1"].font = Font(name="Aptos Display", size=26, bold=True, color=INK)
    sheet["A2"] = "Cleaned workbook · made with Pivotra"
    sheet["A2"].font = Font(name="Aptos", size=13, color=ACCENT)
    details = [
        ("Source", source_name),
        ("Created", datetime.now().strftime("%Y-%m-%d %H:%M")),
        ("Tables", len(sheets)),
    ]
    for offset, (label, value) in enumerate(details, 4):
        sheet.cell(offset, 1, label).font = Font(name="Aptos", color=MUTED)
        sheet.cell(offset, 2, value).font = Font(name="Aptos", color=INK)

    def section(row, title, headers):
        sheet.cell(row, 1, title).font = Font(name="Aptos", size=14, bold=True, color=INK)
        for column, header in enumerate(headers, 1):
            sheet.cell(row + 1, column, header)
        style_header(sheet[row + 1][: len(headers)])
        return row + 2

    row = section(8, "Tables", ["Table", "Rows", "Columns", "Open"])
    for name, (title, frame) in sheets.items():
        sheet.cell(row, 1, str(name)).data_type = "s"
        sheet.cell(row, 2, len(frame)).number_format = "#,##0"
        sheet.cell(row, 3, len(frame.columns))
        link = sheet.cell(row, 4, "Go to sheet")
        link.hyperlink = f"#'{title}'!A1"
        link.font = Font(name="Aptos", color=ACCENT, underline="single")
        row += 1
    row = section(
        row + 1, "Quality fixes", ["Table", "Fix", "Column", "Cells changed", "Rows removed"]
    )
    if not quality_audit:
        sheet.cell(row, 1, "The quality check was not run for this file.").font = Font(color=MUTED)
        return
    for name, audit in quality_audit.items():
        for entry in audit.get("entries", []):
            if not entry.get("cells_changed") and not entry.get("rows_removed"):
                continue
            values = [
                name,
                entry["category"].replace("_", " ") + " · " + entry["rule"].replace("_", " "),
                entry.get("column") or "",
                entry.get("cells_changed", 0),
                entry.get("rows_removed", 0),
            ]
            for column, value in enumerate(values, 1):
                cell = sheet.cell(row, column, value)
                if isinstance(value, str):
                    cell.data_type = "s"
            row += 1
        for label, key in [("You chose to fix", "user_will_fix"), ("Ignored", "ignored")]:
            if audit.get(key):
                sheet.cell(row, 1, name)
                sheet.cell(
                    row, 2, f"{label}: " + ", ".join(c.replace("_", " ") for c in audit[key])
                )
                row += 1
        if not audit.get("entries") and not audit.get("user_will_fix") and not audit.get("ignored"):
            sheet.cell(row, 1, name)
            sheet.cell(row, 2, "No quality issues found.")
            row += 1


def write_charts(sheet, sheets):
    sheet.sheet_view.showGridLines = False
    sheet["A1"] = "Charts"
    sheet["A1"].font = Font(name="Aptos Display", size=20, bold=True, color=INK)
    placed = 0
    for name, (title, frame) in sheets.items():
        if not len(frame):
            continue
        numeric = [
            index
            for index, column in enumerate(frame, 1)
            if pd.api.types.is_numeric_dtype(frame[column])
            and not pd.api.types.is_bool_dtype(frame[column])
        ]
        category = next(
            (index for index in range(1, len(frame.columns) + 1) if index not in numeric), 1
        )
        source = frame.columns[category - 1]
        last = min(31, len(frame) + 1)
        for column in numeric[:3]:
            if placed >= MAX_CHARTS:
                return
            trend = pd.api.types.is_datetime64_any_dtype(frame[source])
            chart = LineChart() if trend else BarChart()
            chart.title = f"{name} · {frame.columns[column - 1]}"
            chart.legend = None
            chart.width, chart.height = 16, 7.5
            data_sheet = sheet.parent[title]
            chart.add_data(
                Reference(data_sheet, min_col=column, min_row=1, max_row=last),
                titles_from_data=True,
            )
            chart.set_categories(Reference(data_sheet, min_col=category, min_row=2, max_row=last))
            series = chart.series[0]
            series.graphicalProperties.line.solidFill = ACCENT
            if trend:
                series.graphicalProperties.line.width = 28575
                series.smooth = False
            else:
                series.graphicalProperties.solidFill = ACCENT
            chart.x_axis.delete = False
            chart.y_axis.delete = False
            anchor = f"{'A' if placed % 2 == 0 else 'K'}{3 + placed // 2 * 17}"
            sheet.add_chart(chart, anchor)
            placed += 1
    if not placed:
        sheet["A3"] = "No numeric columns to chart."
        sheet["A3"].font = Font(color=MUTED)


def clean_excel(result, target, quality_audit=None, source_name=None):
    """source_name: the uploaded file name, used for the cover title."""
    source_name = source_name or (Path(result.source).name if result.source else "Workbook")
    book = Workbook()
    summary = book.active
    summary.title = "Summary"
    used, names = {"summary"}, set()
    reserved = {"charts", "transformation audit"}
    sheets = {}
    for name, frame in result.tables.items():
        title = sheet_title(name, used, reserved)
        write_data_sheet(book, title, frame, names)
        sheets[name] = (title, frame)
    write_summary(summary, result, sheets, quality_audit, source_name)
    write_charts(book.create_sheet("Charts"), sheets)
    audit = book.create_sheet("Transformation audit")
    audit.sheet_view.showGridLines = False
    audit.append(["Table", "Transformation", "Rows before", "Rows after"])
    style_header(audit[1])
    for step in result.steps:
        audit.append([step.table, step.description, step.rows_before, step.rows_after])
        for cell in audit[audit.max_row]:
            if isinstance(cell.value, str):
                cell.data_type = "s"
    if audit.max_row > 1:
        band(audit, f"A2:D{audit.max_row}")
    audit.freeze_panes = "A2"
    audit.column_dimensions["A"].width = 28
    audit.column_dimensions["B"].width = 85
    audit.column_dimensions["C"].width = 14
    audit.column_dimensions["D"].width = 14
    book.save(target)
    book.close()
    return target
