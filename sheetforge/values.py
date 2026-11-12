"""Deterministic value parsing shared by profiling, planning, and transformation."""

import math
import re
from datetime import date, datetime

import pandas as pd

MONTH = re.compile(
    r"^(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)(?:[ _-]+\d{4})?$",
    re.I,
)
TOTAL = re.compile(r"^(?:(?:grand|sub)[ _-]*)?totals?(?:\b|$)", re.I)


def blank(value):
    return (
        value is None
        or (isinstance(value, str) and not value.strip())
        or (isinstance(value, float) and math.isnan(value))
    )


def number(value):
    if blank(value) or isinstance(value, (bool, date, datetime)):
        return None
    if isinstance(value, (int, float)):
        return float(value) if math.isfinite(value) else None
    text = str(value).strip().replace("\u00a0", "").replace("\u202f", "")
    negative = text.startswith("(") and text.endswith(")")
    text = text.strip("()")
    percent = text.endswith("%")
    text = re.sub(
        r"^(?:[$€£¥₹₨]\s*|(?:USD|EUR|GBP|PKR|CAD|AUD|JPY|CNY|CHF|INR|AED|SAR)\s*)",
        "",
        text,
        flags=re.I,
    )
    text = re.sub(r"[A-Za-zµ°/% $€£¥₹₨]+$", "", text).replace(" ", "")
    if not re.fullmatch(r"[+-]?[\d.,]+(?:[eE][+-]?\d+)?", text):
        return None
    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif "," in text:
        text = (
            text.replace(",", "")
            if re.fullmatch(r"[+-]?\d{1,3}(?:,\d{3})+", text)
            else text.replace(",", ".")
        )
    try:
        result = float(text)
    except ValueError:
        return None
    if not math.isfinite(result):
        return None
    return (-result if negative else result) / (100 if percent else 1)


def date_like(value):
    return isinstance(value, (date, datetime)) or bool(
        re.fullmatch(
            r"\d{4}[-/]\d{1,2}[-/]\d{1,2}(?:[ T].*)?|\d{1,2}[-/]\d{1,2}[-/]\d{4}",
            str(value).strip(),
        )
    )


def guess_type(name, values):
    present = [value for value in values if not blank(value)]
    if not present:
        return "string"
    if re.search(r"(?:^|[ _])(?:id|code|postal|zip)(?:$|[ _])", name, re.I):
        return "string"
    if sum(date_like(value) for value in present) / len(present) >= 0.8:
        return "date"
    if sum(number(value) is not None for value in present) / len(present) >= 0.8:
        return "number"
    if all(isinstance(value, bool) for value in present):
        return "boolean"
    return "string"


def dates(values):
    strings = values.map(lambda value: None if blank(value) else str(value).strip())
    # Slash-date order comes from unambiguous day evidence, not the host locale.
    day_first = any(re.match(r"^(?:1[3-9]|2\d|3[01])/", str(value)) for value in strings)
    return pd.to_datetime(strings, format="mixed", dayfirst=day_first, errors="coerce")
