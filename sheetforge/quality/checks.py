"""Deterministic quality detection over full tables. No LLM is called here."""

import re
from datetime import date, timedelta

import pandas as pd

from sheetforge.quality.models import (
    STRATEGIES,
    CategorySummary,
    ColumnQuality,
    NearDuplicateGroup,
    QualityReport,
    Rule,
)
from sheetforge.values import dates, guess_type, number

PLACEHOLDERS = {"", "-", "—", "n/a", "na", "null", "none", "?", ".", ".."}
NUMERIC = {"number", "integer"}
COUNT_NAME = re.compile(
    r"(?:^|[ _-])(?:count|counts|qty|quantity|units|orders|items|visits|headcount|stock|pieces|pcs)(?:$|[ _-])|^#|number of",
    re.I,
)
PERCENT_NAME = re.compile(r"percent|pct|%|utili[sz]ation", re.I)
ID_NAME = re.compile(r"(?:^|[ _-])(?:id|code|key|ref|sku|no|nr|number)$", re.I)
CAMEL_ID = re.compile(r"[a-z](?:ID|Id)$")
ENTITY_NAME = re.compile(
    r"name|customer|client|product|employee|item|vendor|supplier|account|company", re.I
)
MAX_GROUPS_REPORTED = 50


def is_placeholder(value):
    return isinstance(value, str) and value.strip().casefold() in PLACEHOLDERS


def is_missing(value):
    try:
        if pd.isna(value):
            return True
    except (TypeError, ValueError):
        pass
    return is_placeholder(value)


def missing_mask(values):
    return values.map(is_missing).astype(bool)


def numbers(values):
    return values.map(
        lambda value: None if is_missing(value) or isinstance(value, bool) else number(value)
    ).astype("Float64")


def parsed(values, kind):
    """Typed view of a raw column; unparseable or missing values become NA."""
    if kind in NUMERIC:
        return numbers(values)
    if kind == "date":
        return dates(values.where(~missing_mask(values), None))
    return None


def mismatch_mask(values, kind):
    present = ~missing_mask(values)
    if kind in NUMERIC:
        nums = numbers(values)
        ok = nums.notna()
        if kind == "integer":
            ok &= nums.mod(1).eq(0).fillna(False)
    elif kind == "date":
        ok = parsed(values, kind).notna()
    elif kind == "boolean":
        ok = values.map(
            lambda value: (
                isinstance(value, bool) or str(value).strip().lower() in {"true", "false", "1", "0"}
            )
        )
    else:
        return pd.Series(False, index=values.index)
    return (present & ~ok.astype(bool)).astype(bool)


def formatting_mask(values, kind):
    """Lossless fixes: stray whitespace, numbers or dates stored as text."""

    def issue(value):
        if not isinstance(value, str) or is_placeholder(value):
            return False
        if value != " ".join(value.split()):
            return True
        return kind in NUMERIC and number(value) is not None

    mask = values.map(issue).astype(bool)
    if kind == "date":
        text = values.map(lambda value: isinstance(value, str) and not is_placeholder(value))
        mask |= text & dates(values.where(text, None)).notna()
    return mask


def generic_rules(frame, types):
    rules = []
    for column, kind in types.items():
        if kind in NUMERIC and COUNT_NAME.search(column):
            rules.append(Rule(column=column, kind="negative_count", min=0.0))
        if kind in NUMERIC and PERCENT_NAME.search(column):
            rules.append(Rule(column=column, kind="percent_range", min=0.0, max=100.0))
        if kind == "date":
            latest = date.today() + timedelta(days=365)
            rules.append(
                Rule(column=column, kind="date_range", min="1990-01-01", max=latest.isoformat())
            )
    return rules


def limit(value, kind):
    if value is None:
        return None
    return pd.Timestamp(value) if kind == "date" else float(value)


def rule_mask(frame, rule, types):
    kind = types[rule.column]
    values = parsed(frame[rule.column], kind)
    if values is None:
        return pd.Series(False, index=frame.index)
    if rule.kind == "order":
        other = parsed(frame[rule.before_column], types[rule.before_column])
        return (values > other).fillna(False).astype(bool)
    mask = pd.Series(False, index=frame.index)
    low, high = limit(rule.min, kind), limit(rule.max, kind)
    if low is not None:
        mask |= (values < low).fillna(False).astype(bool)
    if high is not None:
        mask |= (values > high).fillna(False).astype(bool)
    return mask


def normalized_key(frame, key):
    def text(value):
        return None if is_missing(value) else " ".join(str(value).split()).casefold()

    return frame[list(key)].apply(lambda column: column.map(text))


def exact_duplicate_mask(frame):
    return frame.astype("string").duplicated(keep="first")


def propose_key(frame, types):
    candidates = []
    for column in frame:
        if not (ID_NAME.search(column) or CAMEL_ID.search(column)):
            continue
        present = frame[column][~missing_mask(frame[column])]
        if len(present) < max(1, len(frame) // 2):
            continue
        uniqueness = present.astype("string").nunique() / len(present)
        if uniqueness >= 0.5:  # mostly unique; the repeats are the near-duplicates
            candidates.append((uniqueness, column))
    if candidates:
        return [max(candidates)[1]]
    date_columns = [column for column, kind in types.items() if kind == "date"]
    names = [
        column for column, kind in types.items() if kind == "string" and ENTITY_NAME.search(column)
    ]
    return [date_columns[0], names[0]] if date_columns and names else []


def near_duplicate_groups(frame, key):
    """Rows sharing a normalized key that are not exact copies of each other."""
    if not key:
        return []
    distinct = frame[~exact_duplicate_mask(frame)]
    keys = normalized_key(distinct, key)
    keys = keys[keys.notna().all(axis=1)]
    groups = []
    for _, members in keys.groupby(list(keys.columns), sort=False):
        if len(members) > 1:
            groups.append(list(members.index))
    return groups


def inspect(frame, types=None, extra_rules=()):
    frame = frame.reset_index(drop=True)
    types = types or {
        column: guess_type(column, [value for value in frame[column] if not is_placeholder(value)])
        for column in frame
    }
    rules = []
    for rule in [*generic_rules(frame, types), *extra_rules]:
        rules.append(rule.model_copy(update={"count": int(rule_mask(frame, rule, types).sum())}))
    columns, impossible_cells = [], pd.DataFrame(False, index=frame.index, columns=frame.columns)
    for rule in rules:
        impossible_cells[rule.column] |= rule_mask(frame, rule, types)
    total_missing = total_placeholders = total_format = total_type = 0
    for column in frame:
        values, kind = frame[column], types.get(column, "string")
        missing = missing_mask(values)
        placeholders = int(values.map(is_placeholder).sum())
        mismatches = int(mismatch_mask(values, kind).sum())
        formatting = int(formatting_mask(values, kind).sum())
        columns.append(
            ColumnQuality(
                column=column,
                type=kind,
                null_pct=round(float(missing.mean() * 100) if len(values) else 0.0, 2),
                placeholder_nulls=placeholders,
                type_mismatches=mismatches,
                formatting_issues=formatting,
                impossible_values=int(impossible_cells[column].sum()),
            )
        )
        total_missing += int(missing.sum())
        total_placeholders += placeholders
        total_format += formatting
        total_type += mismatches
    exact = int(exact_duplicate_mask(frame).sum())
    key = propose_key(frame, types)
    groups = near_duplicate_groups(frame, key)
    impossible = int(impossible_cells.to_numpy().sum())
    has_numeric_gap = any(
        types.get(item.column) in NUMERIC and item.null_pct > 0 for item in columns
    )
    found = [
        (
            "formatting",
            total_format + total_placeholders,
            f"{total_format} value(s) with stray spaces or numbers/dates stored as text; "
            f"{total_placeholders} placeholder(s) like N/A or '-' to mark as missing.",
            "normalize",
        ),
        (
            "type_consistency",
            total_type,
            f"{total_type} value(s) that do not match their column type.",
            "nullify",
        ),
        (
            "impossible_values",
            impossible,
            f"{impossible} value(s) break a rule: "
            + ", ".join(
                f"{rule.column} {rule.kind.replace('_', ' ')} ({rule.count})"
                for rule in rules
                if rule.count
            )
            + ".",
            "nullify",
        ),
        (
            "missing",
            total_missing,
            f"{total_missing} empty cell(s), including {total_placeholders} placeholder(s).",
            "fill_median" if has_numeric_gap else "fill_unknown",
        ),
        ("exact_duplicates", exact, f"{exact} row(s) are exact copies of an earlier row.", "drop"),
        (
            "near_duplicates",
            len(groups),
            f"{len(groups)} group(s) share the same {' + '.join(key)} but differ elsewhere.",
            "keep_first",
        ),
    ]
    categories = [
        CategorySummary(
            category=category,
            count=count,
            detail=detail,
            strategies=STRATEGIES[category],
            default_action="fix_for_me" if category == "formatting" else None,
            default_strategy=strategy,
        )
        for category, count, detail, strategy in found
        if count
    ]
    return QualityReport(
        rows=len(frame),
        types={column: types.get(column, "string") for column in frame},
        columns=columns,
        rules=rules,
        exact_duplicates=exact,
        duplicate_key=key,
        near_duplicate_groups=[
            NearDuplicateGroup(
                key=[str(frame.at[group[0], column]) for column in key],
                rows=[index + 1 for index in group],
            )
            for group in groups[:MAX_GROUPS_REPORTED]
        ],
        near_duplicate_group_count=len(groups),
        categories=categories,
    )
