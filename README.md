# SheetForge

Python 3.11 · pandas transforms data · validated JSON plans explain decisions · no-AI mode works offline.

## Order of work

1. Profiler, deterministic planner/transformer, at least eight reproducible messy XLSX fixtures, and offline tests. **Complete: 13 offline tests pass across nine fixtures.**
2. Provider-neutral LiteLLM abstraction, strict LLM planning with one retry and deterministic fallback, credential storage, mocked tests.
3. Minimal refreshable TMDL/PBIR project: one table, one page, card + bar + table. **Stop for the user's Power BI Desktop feedback.**
4. Semantic modeler, relationships, and multiple report pages.
5. FastAPI and minimal UI: upload, before/after previews, plan overrides, model, generate, download, and settings.
6. Windows packaging and smoke test: launch `sheetforge.exe`, hit `/health`, shut it down.

Step 3 is an explicit review gate. Steps 4–6 will not be represented as finished before that gate is passed.

## Development

```powershell
uv sync --python 3.11 --extra dev
uv run sheetforge fix tests/fixtures/header_not_row1.xlsx --out output/cleaned
uv run pytest
uv run ruff check .
```

With pip, create a Python 3.11 environment and run `pip install -e ".[dev]"`.

## Cleaning contracts

`CleaningPlan` is a strict Pydantic schema. Source block bounds use zero-based, end-exclusive worksheet coordinates. Header/data/total row indexes refer to the cropped block **after** any transpose. Data starts after all header rows; its end is exclusive. Multi-row headers fill actual merged regions, flatten distinct labels with underscores, and deduplicate names. Unpivot creates `Period` and `Value`. Invalid conversions are retained as missing values and reported; totals removed by heuristics appear in the audit and can be restored with an overridden plan. Identifiers named ID/code/postal/zip remain strings.

Profiles include dense table bounds, blank rows/columns, merged regions, row numeric ratios, column type guesses, and at most the first 20 and last 5 sampled rows. Complete workbook rows never go to an LLM. `fix --plan reviewed.json` applies an explicit reviewed plan. Generated CSV and JSON audit files use UTF-8 without BOM.

## Deployment and storage contract (steps 5–6)

- Portable Windows **one-folder** PyInstaller distribution: one `sheetforge.exe` entry point plus its accompanying runtime folder. Users keep that folder together; there is no installer, admin requirement, external database, or separate Python installation.
- The executable starts a loopback-only server on a free port, opens the browser, and provides console commands **open UI** and **quit** (or a tray equivalent).
- SQLAlchemy + SQLite at `%LOCALAPPDATA%\SheetForge\sheetforge.db`, initialized/upgraded by Alembic. `projects(id, name, source_path, created_at)`, `runs(id, project_id, cleaning_plan_json, model_plan_json, steps_json, output_path, status, created_at)`, and `settings(key, value)` contain paths/plans/settings only. **Spreadsheet values, original workbook bytes, samples, profiles containing values, and cleaned data must never enter SQLite.**
- Working artifacts (cleaned Parquet, generated PBIP, ZIPs) live under `%LOCALAPPDATA%\SheetForge\projects\<project_id>\`.
- Keys live in Windows Credential Manager through keyring; environment/`.env` fallback is for local development. Keys never enter SQLite, logs, exported projects, or Git.
- Rotating logs live in `%LOCALAPPDATA%\SheetForge\logs\`. No-AI mode makes no network calls. Ollama discovery is explicit and local at `localhost:11434`.
- `scripts/build.ps1` will bundle local PBIR/TMDL schema resources. Step 6 includes the executable `/health` smoke test and controlled shutdown.

SQLAlchemy, Alembic, PyInstaller, and ruff were explicitly requested. Additional uvicorn, pyarrow, and jsonschema dependencies were explicitly approved. No other direct application dependency is to be added without approval.
