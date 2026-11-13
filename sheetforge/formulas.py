"""Excel formula repair: typed totals, broken formulas, and numbers buried inside formulas.

Nothing is written to the user's file. A fixed copy is saved only with the fixes they accept.
"""

import difflib
import json
import math
import re
from typing import Literal

import pandas as pd
from openpyxl import load_workbook
from openpyxl.formula import Tokenizer
from openpyxl.formula.tokenizer import Token
from openpyxl.formula.translate import Translator
from openpyxl.utils import get_column_letter
from openpyxl.workbook.defined_name import DefinedName
from pydantic import Field, model_validator

from sheetforge.contracts import Contract
from sheetforge.llm.client import CompletionUnavailable, complete, enabled

ERRORS = {"#REF!", "#DIV/0!", "#N/A", "#VALUE!", "#NAME?", "#NUM!", "#NULL!"}
TOTAL = re.compile(r"^\s*(?:grand\s+|sub\s*-?\s*)?totals?\b|^\s*sum\b", re.I)
# Numbers that are arguments to these functions are settings (digits, positions), not inputs.
SETTINGS = {
    "ROUND", "ROUNDUP", "ROUNDDOWN", "INDEX", "VLOOKUP", "HLOOKUP", "XLOOKUP", "MATCH", "OFFSET",
    "LEFT", "RIGHT", "MID", "CHOOSE", "LARGE", "SMALL", "RANK", "TEXT", "DATE", "TIME", "WEEKDAY",
    "EOMONTH", "EDATE", "MOD", "FIXED", "REPT", "FIND", "SEARCH", "CEILING", "FLOOR", "MROUND",
    "TRUNC", "DATEDIF", "SUBTOTAL", "AGGREGATE", "WORKDAY", "NETWORKDAYS", "PERCENTILE", "QUARTILE",
}  # fmt: skip
COMMON = [
    "SUM", "AVERAGE", "COUNT", "COUNTA", "IF", "IFS", "VLOOKUP", "XLOOKUP", "HLOOKUP", "INDEX",
    "MATCH", "SUMIF", "SUMIFS", "COUNTIF", "COUNTIFS", "AVERAGEIF", "AVERAGEIFS", "MAX", "MIN",
    "ROUND", "IFERROR", "IFNA", "TEXT", "LEFT", "RIGHT", "MID", "LEN", "TRIM", "CONCAT",
    "CONCATENATE", "DATE", "TODAY", "NOW", "YEAR", "MONTH", "DAY", "ABS", "AND", "OR", "NOT",
    "SUMPRODUCT", "UPPER", "LOWER", "PROPER", "SUBSTITUTE", "MEDIAN", "STDEV", "NETWORKDAYS",
]  # fmt: skip
NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]{0,63}$")
CELL_LIKE = re.compile(r"^(?:[A-Za-z]{1,3}\d+|[Rr]\d*[Cc]\d*|TRUE|FALSE)$", re.I)
# shortcut: very large sheets skip the column-pattern search; chunk it if needed.
MAX_CELLS = 400_000


class Change(Contract):
    cell: str
    old: str
    new: str


class Fix(Contract):
    id: str
    kind: Literal["typed_total", "broken", "hardcoded"]
    sheet: str
    title: str
    detail: str
    changes: list[Change]
    ready: bool = True  # False: we found the problem but have no safe fix to offer
    value: float | None = None
    name: str | None = None
    label: str | None = None


class FormulaScan(Contract):
    formulas: int
    fixes: list[Fix]
    notes: list[str] = Field(default_factory=list)


def formula_text(value):
    return value if isinstance(value, str) and value.startswith("=") else None


def close(a, b):
    return math.isclose(a, b, rel_tol=1e-9, abs_tol=0.005)


def number(value):
    return isinstance(value, int | float) and not isinstance(value, bool)


def show(value):
    return "" if value is None else str(value)


def tokens(formula):
    return Tokenizer(formula).items


def render(items):
    return "=" + "".join(item.value for item in items)


def constants(formula):
    """(index, value, width) for each number typed into a formula that looks like an input."""
    items, stack, found = tokens(formula), [], []
    for i, item in enumerate(items):
        if item.type == Token.FUNC:
            if item.subtype == Token.OPEN:
                stack.append(item.value[:-1].upper().removeprefix("_XLFN."))
            else:
                stack and stack.pop()
        elif item.type == Token.OPERAND and item.subtype == Token.NUMBER:
            value = float(item.value)
            percent = i + 1 < len(items) and items[i + 1].value == "%"
            value = value / 100 if percent else value
            if value in (0, 1) or (stack and stack[-1] in SETTINGS):
                continue
            found.append((i, value, 2 if percent else 1))
    return found


def replace_constants(formula, names):
    """Swap typed numbers for named inputs; a trailing % is folded into the named value."""
    items = tokens(formula)
    for index, value, width in reversed(constants(formula)):
        key = round(value, 12)
        if key in names:
            items[index : index + width] = [Token(names[key], Token.OPERAND, Token.RANGE)]
    return render(items)


def header_for(sheet, row, column):
    """The nearest text cell above a value: usually its column title."""
    for r in range(row - 1, 0, -1):
        value = sheet.cell(r, column).value
        if isinstance(value, str) and not value.startswith("="):
            return value.strip()
    return get_column_letter(column)


def row_label(sheet, row):
    for column in range(1, min(sheet.max_column, 3) + 1):
        value = sheet.cell(row, column).value
        if isinstance(value, str) and value.strip() and not value.startswith("="):
            return value.strip()
    return ""


def safe_name(text, taken):
    name = re.sub(r"[^A-Za-z0-9_]+", "_", text).strip("_")[:40] or "Input"
    if not name[0].isalpha() or CELL_LIKE.match(name):
        name = "In_" + name
    candidate, count = name, 1
    while candidate.casefold() in taken:
        count += 1
        candidate = f"{name}_{count}"
    taken.add(candidate.casefold())
    return candidate


def broken_fix(sheet, cell, formula, error, fix_id):
    expression = formula[1:]
    if "#REF!" in formula:
        # A neighbor with the same pattern tells us what the deleted reference used to be.
        for dr, dc in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
            r, c = cell.row + dr, cell.column + dc
            if r < 1 or c < 1:
                continue
            neighbor = sheet.cell(r, c)
            source = formula_text(neighbor.value)
            if source and "#REF!" not in source:
                new = Translator(source, origin=neighbor.coordinate).translate_formula(
                    cell.coordinate
                )
                return Fix(id=fix_id, kind="broken", sheet=sheet.title,
                           title=f"{cell.coordinate} points to a deleted cell (#REF!)",
                           detail=f"Rebuilt from the same formula in {neighbor.coordinate}.",
                           changes=[Change(cell=cell.coordinate, old=formula, new=new)])  # fmt: skip
        return Fix(id=fix_id, kind="broken", sheet=sheet.title, ready=False,
                   title=f"{cell.coordinate} points to a deleted cell (#REF!)",
                   detail="No neighbor uses the same pattern. Retype the reference, or turn on AI.",
                   changes=[Change(cell=cell.coordinate, old=formula, new=formula)])  # fmt: skip
    if error == "#NAME?":
        items = tokens(formula)
        for item in items:
            if item.type == Token.FUNC and item.subtype == Token.OPEN:
                name = item.value[:-1].upper()
                guess = difflib.get_close_matches(name, COMMON, n=1, cutoff=0.75)
                if name not in COMMON and guess:
                    item.value = guess[0] + "("
                    return Fix(id=fix_id, kind="broken", sheet=sheet.title,
                               title=f"{cell.coordinate} uses an unknown function {name} (#NAME?)",
                               detail=f"Looks like a typo for {guess[0]}.",
                               changes=[Change(cell=cell.coordinate, old=formula, new=render(items))])  # fmt: skip
        return Fix(id=fix_id, kind="broken", sheet=sheet.title, ready=False,
                   title=f"{cell.coordinate} has an unknown name (#NAME?)",
                   detail="Check the spelling of function or range names, or turn on AI.",
                   changes=[Change(cell=cell.coordinate, old=formula, new=formula)])  # fmt: skip
    fallback = "0" if error == "#DIV/0!" else '""'
    reason = {
        "#DIV/0!": "It divides by zero or an empty cell. Shows 0 instead of the error.",
        "#N/A": "A lookup found no match. Shows a blank instead of the error.",
    }.get(error, f"It returns {error}. Shows a blank instead; check the cells it uses.")
    return Fix(id=fix_id, kind="broken", sheet=sheet.title,
               title=f"{cell.coordinate} shows {error}", detail=reason,
               changes=[Change(cell=cell.coordinate, old=formula,
                               new=f"=IFERROR({expression},{fallback})")])  # fmt: skip


def typed_totals(sheet, values, next_id):
    """Total rows/columns typed as numbers that equal the sum of the cells next to them."""
    fixes = []

    def numeric(r, c):
        cached = values.cell(r, c).value
        return cached if number(cached) else None

    def is_constant(r, c):
        return number(sheet.cell(r, c).value)

    for r in range(2, sheet.max_row + 1):
        if not TOTAL.search(row_label(sheet, r)):
            continue
        changes = []
        for c in range(1, sheet.max_column + 1):
            if not is_constant(r, c):
                continue
            top, total = r - 1, 0.0
            while (
                top >= 1 and numeric(top, c) is not None and not TOTAL.search(row_label(sheet, top))
            ):
                total += numeric(top, c)
                top -= 1
            if r - 1 - top >= 2 and close(total, sheet.cell(r, c).value):
                letter = get_column_letter(c)
                changes.append(Change(cell=f"{letter}{r}", old=show(sheet.cell(r, c).value),
                                      new=f"=SUM({letter}{top + 1}:{letter}{r - 1})"))  # fmt: skip
        if changes:
            fixes.append(Fix(id=next_id(), kind="typed_total", sheet=sheet.title,
                             title=f"Row {r} “{row_label(sheet, r)}” is typed, not calculated",
                             detail="These totals match the cells above. A SUM keeps them right when the numbers change.",
                             changes=changes))  # fmt: skip
    for c in range(2, sheet.max_column + 1):
        changes, header = [], None
        for r in range(1, sheet.max_row + 1):
            if not is_constant(r, c):
                continue
            header = header or header_for(sheet, r, c)
            if not TOTAL.search(header):
                break
            left, total = c - 1, 0.0
            while left >= 1 and numeric(r, left) is not None:
                total += numeric(r, left)
                left -= 1
            if c - 1 - left >= 2 and close(total, sheet.cell(r, c).value):
                first, last = get_column_letter(left + 1), get_column_letter(c - 1)
                changes.append(Change(cell=f"{get_column_letter(c)}{r}",
                                      old=show(sheet.cell(r, c).value), new=f"=SUM({first}{r}:{last}{r})"))  # fmt: skip
        if changes:
            fixes.append(Fix(id=next_id(), kind="typed_total", sheet=sheet.title,
                             title=f"Column “{header}” is typed, not calculated",
                             detail="Each value matches the sum of the numbers to its left.",
                             changes=changes))  # fmt: skip
    if sheet.max_row * sheet.max_column > MAX_CELLS:
        return fixes
    # Product columns: Amount typed as a number when it is always Price x Quantity.
    grid = pd.DataFrame(
        [[numeric(r, c) for c in range(1, sheet.max_column + 1)] for r in range(1, sheet.max_row + 1)],
        dtype="float64",
    )  # fmt: skip
    constant = pd.DataFrame(
        [[is_constant(r, c) for c in range(1, sheet.max_column + 1)] for r in range(1, sheet.max_row + 1)]
    )  # fmt: skip
    columns = [c for c in grid if grid[c].notna().sum() >= 3][:20]
    claimed = set()
    for target in columns:
        for i, a in enumerate(columns):
            for b in columns[i + 1 :]:
                if target in (a, b) or target in claimed:
                    continue
                rows = grid[[a, b, target]].notna().all(axis=1) & constant[target]
                if rows.sum() < 3:
                    continue
                product = grid.loc[rows, a] * grid.loc[rows, b]
                match = (product - grid.loc[rows, target]).abs() <= 0.005 + 1e-9 * product.abs()
                ones = (grid.loc[rows, a] == 1).all() or (grid.loc[rows, b] == 1).all()
                if match.sum() >= 3 and match.mean() >= 0.9 and not ones:
                    claimed.add(target)
                    la, lb, lt = (get_column_letter(x + 1) for x in (a, b, target))
                    hits = [r + 1 for r in match.index[match]]
                    header = header_for(sheet, hits[0], target + 1)
                    fixes.append(Fix(id=next_id(), kind="typed_total", sheet=sheet.title,
                                     title=f"“{header}” is typed, but it is always {header_for(sheet, hits[0], a + 1)} × {header_for(sheet, hits[0], b + 1)}",
                                     detail=f"{len(hits)} rows match {la} × {lb}. A formula keeps them right when the inputs change.",
                                     changes=[Change(cell=f"{lt}{r}", old=show(sheet.cell(r, target + 1).value),
                                                     new=f"={la}{r}*{lb}{r}") for r in hits]))  # fmt: skip
    return fixes


def scan(path):
    book = load_workbook(path)
    values = load_workbook(path, data_only=True)
    fixes, groups, count = [], {}, 0
    counter = iter(range(1, 1_000_000))

    def next_id():
        return f"F{next(counter):04d}"

    for sheet in book.worksheets:
        cached = values[sheet.title]
        for row in sheet.iter_rows():
            for cell in row:
                formula = formula_text(cell.value)
                if not formula:
                    continue
                count += 1
                error = cached[cell.coordinate].value
                error = error if error in ERRORS else "#REF!" if "#REF!" in formula else None
                if error:
                    fixes.append(broken_fix(sheet, cell, formula, error, next_id()))
                try:
                    found = constants(formula)
                except Exception:  # the tokenizer rejects a few exotic formulas; leave them alone
                    continue
                for _, value, _ in found:
                    groups.setdefault(round(value, 12), []).append((sheet, cell, formula))
        fixes.extend(typed_totals(sheet, cached, next_id))
    taken = {name.casefold() for name in book.defined_names}
    for value, uses in groups.items():
        sheet, cell, _ = uses[0]
        header = header_for(sheet, cell.row, cell.column)
        seen, changes = set(), []
        for owner, used, formula in uses:
            key = (owner.title, used.coordinate)
            if key not in seen:
                seen.add(key)
                changes.append(Change(cell=f"{owner.title}!{used.coordinate}", old=formula, new=""))
        name = safe_name(f"{header} rate" if abs(value) < 1 else f"{header} factor", taken)
        changes = [
            c.model_copy(update={"new": replace_constants(c.old, {value: name})}) for c in changes
        ]
        fixes.append(Fix(id=next_id(), kind="hardcoded", sheet=sheet.title, value=value, name=name,
                         label=f"Number used in {header}",
                         title=f"{value:g} is typed inside {len(changes)} formula(s)",
                         detail="Moving it to one named input cell means you change it once, everywhere.",
                         changes=changes))  # fmt: skip
    notes = [] if count else ["This workbook has no formulas yet; we only looked for typed totals."]
    return FormulaScan(formulas=count, fixes=fixes, notes=notes)


SYSTEM = """You repair Excel formulas. You see formulas and column titles, never data rows.
For each item in "broken", return {id, formula}: a corrected formula starting with "=", or omit it if unsure.
For each item in "inputs", return {id, name, label}: an Excel defined name (letters, digits, underscores; not a cell address) and a short human label for that number, e.g. Tax_Rate / "Sales tax rate"."""


class Repair(Contract):
    id: str
    formula: str


class InputName(Contract):
    id: str
    name: str
    label: str = Field(max_length=80)


class Review(Contract):
    broken: list[Repair] = Field(default_factory=list, max_length=500)
    inputs: list[InputName] = Field(default_factory=list, max_length=500)


def improve(result, *, use_ai=True):
    """Ask the AI to name inputs and repair formulas we could not fix ourselves. Optional."""
    if not use_ai or not enabled():
        return result
    stuck = {f.id: f for f in result.fixes if f.kind == "broken" and not f.ready}
    inputs = {f.id: f for f in result.fixes if f.kind == "hardcoded"}
    if not stuck and not inputs:
        return result

    class Checked(Review):
        @model_validator(mode="after")
        def valid(self):
            for item in self.broken:
                if (
                    item.id not in stuck
                    or not item.formula.startswith("=")
                    or "#REF!" in item.formula
                ):
                    raise ValueError("Unknown fix or invalid formula.")
                Tokenizer(item.formula)
            names = [item.name.casefold() for item in self.inputs]
            if len(set(names)) != len(names):
                raise ValueError("Input names must be unique.")
            for item in self.inputs:
                if item.id not in inputs or not NAME.match(item.name) or CELL_LIKE.match(item.name):
                    raise ValueError("Invalid input name.")
            return self

    question = {
        "broken": [{"id": i, "cell": f.changes[0].cell, "formula": f.changes[0].old,
                    "problem": f.title} for i, f in stuck.items()],
        "inputs": [{"id": i, "number": f.value, "used_in": [c.old for c in f.changes[:3]],
                    "column": f.label} for i, f in inputs.items()],
    }  # fmt: skip
    try:
        reply = Checked.model_validate(
            complete(SYSTEM, [{"role": "user", "content": json.dumps(question)}], Checked)
        )
    except (CompletionUnavailable, ValueError):
        return result.model_copy(update={"notes": [*result.notes, "AI review was unavailable; used our own fixes."]})  # fmt: skip
    repairs = {item.id: item.formula for item in reply.broken}
    names = {item.id: item for item in reply.inputs}
    fixes = []
    for fix in result.fixes:
        if fix.id in repairs:
            change = fix.changes[0].model_copy(update={"new": repairs[fix.id]})
            fix = fix.model_copy(update={"changes": [change], "ready": True,
                                         "detail": "Suggested by AI. Check it before you accept."})  # fmt: skip
        elif fix.id in names:
            item = names[fix.id]
            changes = [c.model_copy(update={"new": replace_constants(c.old, {round(fix.value, 12): item.name})}) for c in fix.changes]  # fmt: skip
            fix = fix.model_copy(
                update={"name": item.name, "label": item.label, "changes": changes}
            )
        fixes.append(fix)
    return result.model_copy(update={"fixes": fixes, "notes": [*result.notes, "AI named the inputs and suggested repairs."]})  # fmt: skip


def apply_fixes(path, result, accepted, target):
    """accepted: fix id -> optional new input name. Writes a new workbook; the source is untouched."""
    book = load_workbook(path)
    chosen = [f for f in result.fixes if f.id in accepted and f.ready]
    taken = {name.casefold() for name in book.defined_names}
    for fix in chosen:
        if fix.kind != "hardcoded":
            for change in fix.changes:
                book[fix.sheet][change.cell].value = change.new
    inputs = [f for f in chosen if f.kind == "hardcoded"]
    if inputs:
        if "Inputs" in book.sheetnames:
            sheet = book["Inputs"]
        else:
            sheet = book.create_sheet("Inputs")
            sheet.append(["Input", "Value", "Name"])
        names = {}
        for fix in inputs:
            wanted = (accepted.get(fix.id) or fix.name or "Input").strip()
            if not NAME.match(wanted) or CELL_LIKE.match(wanted):
                raise ValueError(f"“{wanted}” is not a valid Excel name.")
            if wanted.casefold() in taken:
                raise ValueError(f"The name “{wanted}” is already used in this workbook.")
            taken.add(wanted.casefold())
            sheet.append([fix.label or wanted, fix.value, wanted])
            book.defined_names[wanted] = DefinedName(
                wanted, attr_text=f"'Inputs'!$B${sheet.max_row}"
            )
            names[round(fix.value, 12)] = wanted
        cells = {c.cell for f in inputs for c in f.changes}
        for address in cells:
            owner, coordinate = address.rsplit("!", 1)
            cell = book[owner][coordinate]
            if formula_text(cell.value):
                cell.value = replace_constants(cell.value, names)
    book.calculation.fullCalcOnLoad = True  # Excel recalculates everything when it opens the file
    book.save(target)
    return target, len(chosen)
