"""Numbers and spreadsheet presentation are computed locally from complete clean tables."""

import re

import pandas as pd
from openpyxl import Workbook
from openpyxl.chart import BarChart, LineChart, Reference
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter


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


def clean_excel(result, target):
    book = Workbook()
    book.remove(book.active)
    used = set()
    for name, frame in result.tables.items():
        title = re.sub(r"[\\/*?:\[\]]", "_", name).strip("'")[:31] or "Table"
        original, count = title, 1
        while title.casefold() in used or title.casefold() == "transformation audit":
            count += 1
            title = original[:26] + f"_{count}"
        used.add(title.casefold())
        sheet = book.create_sheet(title)
        for row in [
            list(frame.columns),
            *frame.astype(object).where(pd.notna(frame), None).values.tolist(),
        ]:
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
        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = sheet.dimensions
        for cell in sheet[1]:
            cell.font = Font(name="Aptos", bold=True, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor="6D5DF6")
            cell.alignment = Alignment(vertical="center")
        sheet.row_dimensions[1].height = 28
        for index, column in enumerate(frame, 1):
            sheet.column_dimensions[get_column_letter(index)].width = min(
                42, max(14, len(column) + 3)
            )
            for row in range(2, sheet.max_row + 1):
                cell = sheet.cell(row, index)
                cell.font = Font(name="Aptos", size=11, color="303044")
                if row % 2 == 0:
                    cell.fill = PatternFill("solid", fgColor="F5F3FF")
                if pd.api.types.is_datetime64_any_dtype(frame[column]):
                    cell.number_format = "yyyy-mm-dd"
                elif pd.api.types.is_numeric_dtype(frame[column]):
                    cell.number_format = "#,##0.##"
        if len(frame):
            numeric = [
                i
                for i, c in enumerate(frame, 1)
                if pd.api.types.is_numeric_dtype(frame[c])
                and not pd.api.types.is_bool_dtype(frame[c])
            ]
            category = next((i for i, c in enumerate(frame, 1) if i not in numeric), 1)
            for chart_index, column in enumerate(numeric[:3]):
                chart = (
                    LineChart()
                    if pd.api.types.is_datetime64_any_dtype(frame[frame.columns[category - 1]])
                    else BarChart()
                )
                chart.title = str(frame.columns[column - 1])
                chart.style = 13
                chart.legend = None
                chart.add_data(
                    Reference(sheet, min_col=column, min_row=1, max_row=min(31, len(frame) + 1)),
                    titles_from_data=True,
                )
                chart.set_categories(
                    Reference(sheet, min_col=category, min_row=2, max_row=min(31, len(frame) + 1))
                )
                chart.series[0].graphicalProperties.solidFill = "6D5DF6"
                chart.series[0].graphicalProperties.line.solidFill = "6D5DF6"
                sheet.add_chart(
                    chart, f"{get_column_letter(len(frame.columns) + 2)}{2 + chart_index * 16}"
                )
    audit = book.create_sheet("Transformation audit")
    audit.append(["Table", "Transformation", "Rows before", "Rows after"])
    for step in result.steps:
        audit.append([step.table, step.description, step.rows_before, step.rows_after])
        for cell in audit[audit.max_row]:
            if isinstance(cell.value, str):
                cell.data_type = "s"
    audit.freeze_panes = "A2"
    audit.column_dimensions["A"].width = 28
    audit.column_dimensions["B"].width = 85
    book.save(target)
    book.close()
    return target
