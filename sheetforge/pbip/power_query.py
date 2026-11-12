"""Compile a reviewed CleaningPlan to refreshable, data-free Power Query M."""

import re

from sheetforge.planner.heuristic import crop, header_names
from sheetforge.profiler import read_sheet
from sheetforge.values import TOTAL, blank


def m_string(value):
    return (
        '"'
        + str(value)
        .replace("#", "#(#)")
        .replace('"', '""')
        .replace("\r", "#(cr)")
        .replace("\n", "#(lf)")
        .replace("\t", "#(tab)")
        + '"'
    )


def m_list(values):
    return "{" + ", ".join(m_string(value) for value in values) + "}"


PARSERS = r"""    ParseNumber = (value as any) as nullable number =>
        if value = null then null else if Value.Is(value, type number) then value else
        let
            Raw = Text.Trim(Text.From(value)),
            Negative = Text.StartsWith(Raw, "(") and Text.EndsWith(Raw, ")"),
            Percent = Text.EndsWith(Raw, "%"),
            Unwrapped = Text.Trim(Raw, {"(", ")", " "}),
            Currencies = {"USD", "EUR", "GBP", "PKR", "CAD", "AUD", "JPY", "CNY", "CHF", "INR", "AED", "SAR"},
            WithoutCodes = List.Accumulate(Currencies, Unwrapped, (state, code) =>
                if Text.StartsWith(Text.Upper(state), code) then Text.Trim(Text.Range(state, Text.Length(code)))
                else if Text.EndsWith(Text.Upper(state), code) then Text.Trim(Text.Start(state, Text.Length(state)-Text.Length(code))) else state),
            WithoutSymbols = Text.Remove(WithoutCodes, {"$", "€", "£", "¥", "₹", "₨", " ", "#(00A0)", "#(202F)"}),
            Prefix = Text.Combine(List.FirstN(Text.ToList(WithoutSymbols), each List.Contains({"0".."9", ".", ",", "+", "-", "e", "E"}, _))),
            Suffix = Text.Range(WithoutSymbols, Text.Length(Prefix)),
            Numeric = if Text.EndsWith(Text.Lower(Prefix), "e") then Text.Start(Prefix, Text.Length(Prefix)-1) else Prefix,
            Both = Text.Contains(Numeric, ".") and Text.Contains(Numeric, ","),
            CommaLast = Text.PositionOf(Numeric, ",", Occurrence.Last) > Text.PositionOf(Numeric, ".", Occurrence.Last),
            Parts = Text.Split(Text.TrimStart(Numeric, {"+", "-"}), ","),
            Grouped = List.Count(Parts) > 1 and Text.Length(Parts{0}) <= 3 and List.AllTrue(List.Transform(List.Skip(Parts), each Text.Length(_) = 3)),
            Normalized = if Both then (if CommaLast then Text.Replace(Text.Replace(Numeric, ".", ""), ",", ".") else Text.Replace(Numeric, ",", ""))
                else if Text.Contains(Numeric, ",") then (if Grouped then Text.Replace(Numeric, ",", "") else Text.Replace(Numeric, ",", ".")) else Numeric,
            Parsed = if Text.Length(Text.Select(Suffix, {"0".."9", ".", ",", "+", "-"})) > 0 then null else try Number.FromText(Normalized, "en-US") otherwise null
        in if Parsed = null then null else Parsed * (if Negative then -1 else 1) / (if Percent then 100 else 1),
    ParseBoolean = (value as any) as nullable logical =>
        if value = null then null else if List.Contains({"true", "1"}, Text.Lower(Text.From(value))) then true
        else if List.Contains({"false", "0"}, Text.Lower(Text.From(value))) then false else null,
"""


def power_query(source, plan, frame, record_column):
    rows, merged = read_sheet(source, plan.sheet_name)
    grid = crop(rows, plan.bounds)
    if plan.orientation == "transposed":
        grid = [list(row) for row in zip(*grid, strict=True)]
    names = header_names(
        grid,
        plan.multi_row_header_rows or [plan.header_row_index],
        merged,
        plan.bounds,
        transposed=plan.orientation == "transposed",
    )
    source_height, source_width = len(rows), max(map(len, rows), default=0)
    # A last table expands when records are appended. Earlier blocks stop at the first blank separator.
    dynamic_rows = plan.bounds.row_end == source_height
    dynamic_columns = plan.orientation == "transposed" and plan.bounds.column_end == source_width
    row_slice = (
        f"List.Skip(Table.ToRows(Raw), {plan.bounds.row_start})"
        if dynamic_rows
        else f"List.Range(Table.ToRows(Raw), {plan.bounds.row_start}, {plan.bounds.row_end - plan.bounds.row_start})"
    )
    column_slice = (
        f"List.Skip(_, {plan.bounds.column_start})"
        if dynamic_columns
        else f"List.Range(_, {plan.bounds.column_start}, {plan.bounds.column_end - plan.bounds.column_start})"
    )
    expressions = [
        f"    Source = Excel.Workbook(File.Contents({m_string(str(source.resolve()))}), null, true)",
        f'    Raw = Source{{[Item={m_string(plan.sheet_name)}, Kind="Sheet"]}}[Data]',
        f"    Block = Table.FromRows(List.Transform({row_slice}, each {column_slice}))",
        "    Oriented = Table.Transpose(Block)"
        if plan.orientation == "transposed"
        else "    Oriented = Block",
    ]
    oriented_length = len(grid)
    data = f"List.Skip(Table.ToRows(Oriented), {plan.data_start_row})"
    if plan.data_end_row != oriented_length:
        data = f"List.FirstN({data}, {plan.data_end_row - plan.data_start_row})"
    expressions += [
        f"    DataRows = {data}",
        "    Indexed = List.Transform(List.Positions(DataRows), each [Position = _ + "
        + str(plan.data_start_row)
        + ", Values = DataRows{_}])",
    ]
    labels, static_indexes = [], []
    for index in plan.totals_rows_to_drop:
        found = [
            str(value).strip()
            for value in grid[index][:2]
            if not blank(value) and TOTAL.match(str(value).strip())
        ]
        if found:
            labels.extend(found)
        else:
            static_indexes.append(index)
    conditions = []
    if static_indexes:
        conditions.append(
            "not List.Contains({" + ",".join(map(str, static_indexes)) + "}, [Position])"
        )
    if labels:
        conditions.append(
            "not List.AnyTrue(List.Transform(List.FirstN([Values], 2), (v) => List.Contains("
            + m_list(sorted(set(value.casefold() for value in labels)))
            + ', Text.Lower(Text.Trim(if v = null then "" else Text.From(v))))))'
        )
    expressions.append(
        "    Retained = "
        + (
            "List.Select(Indexed, each " + " and ".join(conditions) + ")"
            if conditions
            else "Indexed"
        )
    )
    expressions.extend(
        [
            "    Data = Table.FromRows(List.Transform(Retained, each [Values]), "
            + m_list(names)
            + ")",
            '    Nonblank = Table.SelectRows(Data, each List.AnyTrue(List.Transform(Record.FieldValues(_), (v) => v <> null and Text.Trim(Text.From(v)) <> "")))',
        ]
    )
    previous = "Nonblank"
    if plan.column_renames:
        pairs = (
            "{"
            + ", ".join(
                "{" + m_string(old) + ", " + m_string(new) + "}"
                for old, new in plan.column_renames.items()
            )
            + "}"
        )
        expressions.append(
            f"    Renamed = Table.RenameColumns({previous}, {pairs}, MissingField.Error)"
        )
        previous = "Renamed"
    if plan.unpivot_columns:
        columns = [plan.column_renames.get(name, name) for name in plan.unpivot_columns]
        id_columns = [
            plan.column_renames.get(name, name)
            for name in names
            if plan.column_renames.get(name, name) not in columns
        ]
        # Native Table.Unpivot drops nulls; explicit records preserve pandas.melt's rows/order.
        expressions.append(
            f"    Long = Table.FromRecords(List.Combine(List.Transform({m_list(columns)}, (c) => List.Transform(Table.ToRecords({previous}), (r) => Record.Combine({{Record.RemoveFields(r, {m_list(columns)}), [Period=c, Value=Record.Field(r, c)]}})))), {m_list([*id_columns, 'Period', 'Value'])}, MissingField.UseNull)"
        )
        previous = "Long"
    types = {plan.column_renames.get(key, key): value for key, value in plan.column_types.items()}
    if plan.unpivot_columns:
        columns = [plan.column_renames.get(name, name) for name in plan.unpivot_columns]
        types = {key: value for key, value in types.items() if key not in columns} | {
            "Period": "string",
            "Value": "number",
        }
    operations = []
    for name in frame.columns:
        kind = types.get(name, "string")
        if kind == "number":
            conversion, m_type = "each ParseNumber(_)", "type nullable number"
        elif kind == "integer":
            conversion, m_type = (
                "each let n = ParseNumber(_) in if n <> null and Number.Mod(n, 1) = 0 then Int64.From(n) else null",
                "Int64.Type",
            )
        elif kind == "date":
            # Explicit day/month evidence used by pandas is compiled into the refresh query.
            raw_values = [
                str(row[j])
                for row in grid[plan.data_start_row : plan.data_end_row]
                for j, col in enumerate(names)
                if plan.column_renames.get(col, col) == name and row[j] is not None
            ]
            culture = (
                "en-GB"
                if any(re.match(r"^(?:1[3-9]|2\d|3[01])/", value) for value in raw_values)
                else "en-US"
            )
            conversion, m_type = (
                f'each if _ = null then null else if Value.Is(_, type datetime) or Value.Is(_, type date) then DateTime.From(_) else try DateTime.FromText(Text.From(_), [Culture="{culture}"]) otherwise null',
                "type nullable datetime",
            )
        elif kind == "boolean":
            conversion, m_type = "each ParseBoolean(_)", "type nullable logical"
        else:
            conversion, m_type = (
                'each if _ = null then null else let s = Text.Trim(Text.From(_)) in if s = "" then null else s',
                "type nullable text",
            )
        operations.append("{" + m_string(name) + ", " + conversion + ", " + m_type + "}")
    expressions.append(
        f"    Typed = Table.TransformColumns({previous}, {{"
        + ", ".join(operations)
        + "}, null, MissingField.Error)"
    )
    expressions.append(
        f"    WithRecord = Table.AddIndexColumn(Typed, {m_string(record_column)}, 1, 1, Int64.Type)"
    )
    return "let\n" + PARSERS + ",\n".join(expressions) + "\nin\n    WithRecord"
