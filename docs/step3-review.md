# Step 3: Power BI Desktop review gate

## Open the generated sample

The local review output is `output/desktop-review/SheetForge.pbip`, with its portable project ZIP at `output/desktop-review.zip`. Open the `.pbip` in a current Power BI Desktop release that supports PBIP/PBIR and select **Refresh**. Enable the project preview setting if your Desktop version requires it. If file privacy is requested, use the appropriate local-file privacy setting.

The source is the original `tests/fixtures/header_not_row1.xlsx`, referenced by its absolute path in the M partition. It is not embedded in the ZIP. Keep that source in place for this check, or edit the `File.Contents` path in Power Query if you move it.

Expected results:

- One semantic-model table, one report page, and three native visuals.
- Card: **Total Revenue = 2,800**.
- Bar chart: **North = 1,200**, **South = 1,600**.
- Table: two records, Region/Revenue/Units plus a `_Record` helper that preserves duplicate source records in the visual.
- An edit to a source Revenue value appears after Desktop Refresh. New records appended to the final source table can also refresh. Changes to header/layout structure require regenerating the plan and project.

Please report the exact error message and your Power BI Desktop version if opening, refresh, or a visual fails. Steps 4–6 remain pending this feedback, as requested.

## Validation already performed

Python 3.11.18: 44 pytest checks and ruff pass. All nine messy fixtures clean without AI; fixture-generated projects pass the bundled Microsoft JSON schemas with the network disabled. Ten project/definition/PBIR documents per project are schema-validated. All generated text files are UTF-8 without BOM. No time-intelligence switch is added. ZIPs contain no original workbook, cleaned table rows, API key, or embedded-data M payload.

The exporter emits TMDL with a real `Excel.Workbook(File.Contents(...))` connection and reviewed cleaning operations in M. M replays the reviewed pandas plan for Desktop Refresh; pandas remains the authoritative cleaning implementation. The generated query preserves missing unpivoted cells and uses a helper record index for native table visuals. Measures are explicit SUM calculations for numeric fields and a row count.

TMDL is not JSON and has no JSON schema. The bundled TMDL reference describes the emitted language subset; actual TMDL parsing, M execution, and visual rendering have **not** been verified in Desktop here. The review gate exists to resolve those compatibility details before adding the modeler, FastAPI UI, and packaged executable.

## Current milestone limits

The export intentionally contains one table. A workbook with multiple detected tables requires explicit `--table NAME`; the exporter will not silently drop the others. Multi-table relationships, LLM-proposed DAX, and multi-page reports belong to step 4. FastAPI, SQLAlchemy/Alembic persistence, and the web UI belong to step 5. The one-folder Windows executable, browser/console lifecycle, rotating logs, `scripts/build.ps1`, and executable `/health` smoke test belong to step 6.

Bundled schema provenance is recorded in `sheetforge/schemas/manifest.json`. Generation never downloads schemas. The maintainer-only `scripts/fetch_schemas.py` refreshes the explicitly versioned published resources.
