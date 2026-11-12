"""Workbook profiles retain statistics locally; only bounded samples go to an LLM."""

from .workbook import WorkbookProfile, profile_workbook, read_sheet

__all__ = ["WorkbookProfile", "profile_workbook", "read_sheet"]
