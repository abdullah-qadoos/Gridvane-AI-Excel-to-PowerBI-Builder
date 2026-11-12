"""Apply only the fixes the user chose. Every change is counted in the audit log."""

import pandas as pd

from sheetforge.quality.checks import (
    NUMERIC,
    exact_duplicate_mask,
    inspect,
    is_missing,
    is_placeholder,
    limit,
    mismatch_mask,
    missing_mask,
    near_duplicate_groups,
    numbers,
    parsed,
    rule_mask,
)
from sheetforge.quality.models import STRATEGIES, AuditEntry, AuditLog, Decision
from sheetforge.values import dates, number


def validate_decisions(report, decisions):
    decisions = {
        category: value if isinstance(value, Decision) else Decision.model_validate(value)
        for category, value in (decisions or {}).items()
    }
    unknown = set(decisions) - set(STRATEGIES)
    if unknown:
        raise ValueError(f"Unknown quality categories: {', '.join(sorted(unknown))}.")
    needed = [item.category for item in report.categories]
    missing = [category for category in needed if category not in decisions]
    if missing:
        raise ValueError("Choose fix_for_me, i_will_fix or ignore for: " + ", ".join(missing) + ".")
    for category in needed:
        decision = decisions[category]
        if decision.action != "fix_for_me":
            continue
        options = STRATEGIES[category]
        strategy = decision.strategy or (options[0] if len(options) == 1 else None)
        if strategy not in options:
            raise ValueError(f"{category} needs a strategy: {', '.join(options)}.")
        update = {"strategy": strategy}
        if category == "near_duplicates":
            key = decision.key or []
            if not key or not set(key) <= set(report.types):
                raise ValueError("Confirm a duplicate key made of existing columns before merging.")
        decisions[category] = decision.model_copy(update=update)
    return decisions


def changed(before, after):
    def differs(old, new):
        if is_missing(old) and not is_placeholder(old) and is_missing(new):
            return False
        return type(old) is not type(new) or old != new

    return pd.Series(
        [differs(a, b) for a, b in zip(before, after, strict=True)], index=before.index
    )


def fix_formatting(frame, decision, report, log):
    for column in frame:
        kind = report.types.get(column, "string")
        values = frame[column]

        def normalize(value, kind=kind):
            if not isinstance(value, str):
                return value
            if is_placeholder(value):
                return None
            text = " ".join(value.split())
            if kind in NUMERIC and number(text) is not None:
                return number(text)
            return text

        result = values.map(normalize).astype(object)
        if kind == "date":
            text = result.map(lambda value: isinstance(value, str))
            converted = dates(result.where(text, None))
            result = result.where(~(text & converted.notna()), converted.astype(object))
        count = int(changed(values, result).sum())
        if count:
            frame[column] = result
            log.entries.append(
                AuditEntry(
                    category="formatting",
                    rule="normalize",
                    column=column,
                    cells_changed=count,
                    detail="Trimmed spaces, turned placeholders into blanks, parsed text numbers/dates.",
                )
            )
    return frame


def drop_rows(frame, mask, category, rule, log, detail):
    removed = int(mask.sum())
    if removed:
        log.entries.append(
            AuditEntry(category=category, rule=rule, rows_removed=removed, detail=detail)
        )
    return frame[~mask].reset_index(drop=True)


def fix_types(frame, decision, report, log):
    masks = {
        column: mismatch_mask(frame[column], report.types.get(column, "string")) for column in frame
    }
    if decision.strategy == "drop":
        union = pd.concat(masks.values(), axis=1).any(axis=1)
        return drop_rows(
            frame, union, "type_consistency", "drop", log, "Dropped rows with wrongly typed values."
        )
    for column, mask in masks.items():
        if mask.any():
            frame[column] = frame[column].astype(object).where(~mask, None)
            log.entries.append(
                AuditEntry(
                    category="type_consistency",
                    rule="nullify",
                    column=column,
                    cells_changed=int(mask.sum()),
                    detail=f"Cleared values that are not {report.types[column]}.",
                )
            )
    return frame


def fix_impossible(frame, decision, report, log):
    rules = [rule for rule in report.rules if rule.column in frame]
    if decision.strategy == "drop":
        union = pd.Series(False, index=frame.index)
        for rule in rules:
            union |= rule_mask(frame, rule, report.types)
        return drop_rows(
            frame, union, "impossible_values", "drop", log, "Dropped rows with impossible values."
        )
    for rule in rules:
        mask = rule_mask(frame, rule, report.types)
        if not mask.any():
            continue
        kind = report.types[rule.column]
        clamp = decision.strategy == "clamp" and rule.kind != "order"
        if clamp:
            values = parsed(frame[rule.column], kind)
            low, high = limit(rule.min, kind), limit(rule.max, kind)
            bounded = values.astype(object)
            if low is not None:
                bounded = bounded.where(~(values < low).fillna(False).astype(bool), low)
            if high is not None:
                bounded = bounded.where(~(values > high).fillna(False).astype(bool), high)
            frame[rule.column] = (
                frame[rule.column].astype(object).where(~mask, bounded.astype(object))
            )
        else:
            frame[rule.column] = frame[rule.column].astype(object).where(~mask, None)
        log.entries.append(
            AuditEntry(
                category="impossible_values",
                rule=f"{rule.kind}:{'clamp' if clamp else 'nullify'}",
                column=rule.column,
                cells_changed=int(mask.sum()),
                detail=f"{rule.source} rule min={rule.min} max={rule.max} before={rule.before_column}",
            )
        )
    return frame


def fix_missing(frame, decision, report, log):
    strategy = decision.strategy
    masks = {column: missing_mask(frame[column]) for column in frame}
    if strategy == "drop":
        union = pd.concat(masks.values(), axis=1).any(axis=1)
        return drop_rows(frame, union, "missing", "drop", log, "Dropped rows with any empty cell.")
    for column, mask in masks.items():
        if not mask.any():
            continue
        kind = report.types.get(column, "string")
        values = frame[column].astype(object)
        if strategy == "ffill":
            filled = values.where(~mask, None).ffill()
            detail = "Copied the value from the row above."
        elif strategy in {"fill_mean", "fill_median", "fill_zero"} and kind in NUMERIC:
            known = numbers(values).dropna()
            if strategy == "fill_zero":
                value = 0.0
            elif known.empty:
                continue
            else:
                value = float(known.mean() if strategy == "fill_mean" else known.median())
            filled = values.where(~mask, value)
            detail = f"Filled with {strategy.removeprefix('fill_')} {value:g}."
        elif strategy == "fill_unknown" and kind == "string":
            filled = values.where(~mask, "Unknown")
            detail = "Filled with 'Unknown'."
        else:
            log.entries.append(
                AuditEntry(
                    category="missing",
                    rule=f"{strategy}:skipped",
                    column=column,
                    detail=f"{int(mask.sum())} empty {kind} cell(s) left as-is; {strategy} does not apply to {kind}.",
                )
            )
            continue
        count = int((mask & ~missing_mask(filled)).sum())
        frame[column] = filled
        if count:
            log.entries.append(
                AuditEntry(
                    category="missing",
                    rule=strategy,
                    column=column,
                    cells_changed=count,
                    detail=detail,
                )
            )
    return frame


def fix_exact(frame, decision, report, log):
    return drop_rows(
        frame,
        exact_duplicate_mask(frame),
        "exact_duplicates",
        "drop",
        log,
        "Removed exact copies; kept the first.",
    )


def fix_near(frame, decision, report, log):
    groups = near_duplicate_groups(frame, decision.key)
    strategy, keep, cells = decision.strategy, set(frame.index), 0
    date_column = next(
        (
            c
            for c, kind in report.types.items()
            if kind == "date" and c in frame and c not in decision.key
        ),
        None,
    )
    for group in groups:
        survivor = group[0]
        if strategy == "keep_latest":
            survivor = group[-1]
            if date_column:
                stamps = parsed(frame.loc[group, date_column], "date")
                if stamps.notna().any():
                    survivor = stamps.idxmax()
        elif strategy == "keep_max":
            for column, kind in report.types.items():
                if kind in NUMERIC and column in frame:
                    best = numbers(frame.loc[group, column]).max()
                    if pd.notna(best) and number(frame.at[survivor, column]) != best:
                        frame.at[survivor, column] = float(best)
                        cells += 1
        keep -= set(group) - {survivor}
    removed = len(frame) - len(keep)
    if removed:
        log.entries.append(
            AuditEntry(
                category="near_duplicates",
                rule=strategy,
                column=" + ".join(decision.key),
                cells_changed=cells,
                rows_removed=removed,
                detail=f"Merged {len(groups)} group(s) on {' + '.join(decision.key)}.",
            )
        )
    return frame.loc[sorted(keep)].reset_index(drop=True)


FIXERS = {
    "formatting": fix_formatting,
    "type_consistency": fix_types,
    "impossible_values": fix_impossible,
    "missing": fix_missing,
    "exact_duplicates": fix_exact,
    "near_duplicates": fix_near,
}


def apply(frame, decisions, report=None):
    """Return (cleaned copy, AuditLog). The input frame is never modified."""
    report = report or inspect(frame)
    decisions = validate_decisions(report, decisions)
    detected = {item.category for item in report.categories}
    result, log = frame.copy().reset_index(drop=True), AuditLog()
    for category, fixer in FIXERS.items():
        decision = decisions.get(category)
        if category not in detected or decision is None:
            continue
        if decision.action == "i_will_fix":
            log.user_will_fix.append(category)
        elif decision.action == "ignore":
            log.ignored.append(category)
        else:
            result = fixer(result, decision, report, log)
    return result, log
