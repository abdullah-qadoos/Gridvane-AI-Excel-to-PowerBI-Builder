"""Writable user data is separate from the portable application's resources."""

import os
import re
from pathlib import Path

RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def data_home():
    override = os.getenv("SHEETFORGE_HOME")
    if override:
        return Path(override).resolve()
    return Path(os.getenv("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))) / "SheetForge"


def output_stem(name):
    """Safe file/folder name from the uploaded file name, e.g. 'Sales Q3.xlsx' -> 'Sales Q3'."""
    stem = Path(str(name or "")).stem
    stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", stem).strip(" .")[:60].strip(" .")
    if not stem or stem.upper().split(".")[0] in RESERVED:
        return "Workbook"
    return stem


def resource_path(*parts):
    return Path(__file__).resolve().parent.joinpath(*parts)
