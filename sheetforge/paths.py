"""Writable user data is separate from the portable application's resources."""

import os
from pathlib import Path


def data_home():
    override = os.getenv("SHEETFORGE_HOME")
    if override:
        return Path(override).resolve()
    return Path(os.getenv("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))) / "SheetForge"


def resource_path(*parts):
    return Path(__file__).resolve().parent.joinpath(*parts)
