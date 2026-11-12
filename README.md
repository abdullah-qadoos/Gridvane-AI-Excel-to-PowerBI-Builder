# Pivotra

Python 3.11 · pandas transforms data · validated JSON plans explain decisions · no-AI mode works offline.

## Order of work

1. Profiler, deterministic planner/transformer, at least eight reproducible messy XLSX fixtures, and offline tests. **Complete: 13 offline tests pass across nine fixtures.**
2. Provider-neutral LiteLLM abstraction, strict LLM planning with one retry and deterministic fallback, credential storage, mocked tests. **Complete: 27 tests pass.**
3. Minimal refreshable TMDL/PBIR project: one table, one page, card + bar + table. **Generated and JSON-validated; stopped for the user's Power BI Desktop feedback. 44 tests pass.**
4. Semantic modeler, verified relationships, and multiple report pages. **Complete: 53 tests pass.**
5. FastAPI and minimal UI: upload, before/after previews, plan overrides, model, generate, download, and settings. **Complete: 62 tests pass.**
6. Windows packaging and smoke test: launch `pivotra.exe`, hit `/health`, shut it down. **Complete: the built executable passed runtime imports, end-to-end generation, restart persistence, health and shutdown checks.**

Steps 4–6 are implemented. Native Power BI Desktop validation remains pending and is separate from JSON schema validation.

## Development

```powershell
uv sync --python 3.11 --extra dev
uv run sheetforge fix tests/fixtures/header_not_row1.xlsx --out output/cleaned
uv run sheetforge build tests/fixtures/header_not_row1.xlsx --out output/project
uv run pytest
uv run ruff check .
```

With pip, create a Python 3.11 environment and run `pip install -e ".[dev]"`.

## Optional model assistance

```powershell
uv sync --python 3.11 --extra dev --extra llm --extra validation
uv run sheetforge fix file.xlsx --provider ollama --model YOUR_LOCAL_MODEL
uv run sheetforge fix file.xlsx --provider openrouter --model YOUR_MODEL_ID --base-url https://openrouter.ai/api/v1
```

The default provider is `none`. `sheetforge.llm.complete(system, messages, schema)` returns a validated dictionary. OpenAI, Anthropic, Gemini, Azure, OpenRouter, and Ollama route through LiteLLM using the caller's selected model; no vendor or model is chosen implicitly. Configuration accepts provider, model, an optional secret API key, base URL, and optional Azure API version. `test_connection()` sends no spreadsheet data. Ollama's explicit `discover_ollama()` helper queries only `http://localhost:11434/api/tags`; it never runs automatically in no-AI mode.

Use `store_api_key(config, key)` to store a key under the SheetForge service in Windows Credential Manager (keyring). The resolver checks an explicit in-memory key, the OS vault, then environment variables or the development `.env` fallback. Public configuration excludes keys. Provider exceptions are reduced to safe messages and are never logged with their original contents. JSON must be a single object with distinct keys and finite numbers. A validation failure gets one corrected-JSON attempt with the error; two invalid responses or provider unavailability return the deterministic plan. Source bounds, source columns, and unpivot references are checked before execution.

Only bounded samples/statistics and the proposed plan are sent to the LLM. Transformations remain pandas operations. Mocked tests cover all six requested provider routes, invalid output, correction, fallback, credential handling, and absence of middle-workbook data from prompts. Live provider inference has not been tested with credentials.

## Cleaning contracts

`CleaningPlan` is a strict Pydantic schema. Source block bounds use zero-based, end-exclusive worksheet coordinates. Header/data/total row indexes refer to the cropped block **after** any transpose. Data starts after all header rows; its end is exclusive. Multi-row headers fill actual merged regions, flatten distinct labels with underscores, and deduplicate names. Unpivot creates `Period` and `Value`. Invalid conversions are retained as missing values and reported; totals removed by heuristics appear in the audit and can be restored with an overridden plan. Identifiers named ID/code/postal/zip remain strings.

Profiles include dense table bounds, blank rows/columns, merged regions, row numeric ratios, column type guesses, and at most the first 20 and last 5 sampled rows. Complete workbook rows never go to an LLM. `fix --plan reviewed.json` applies an explicit reviewed plan. Generated CSV and JSON audit files use UTF-8 without BOM.

## Deployment and storage contract (steps 5–6)

- Portable Windows **one-folder** PyInstaller distribution: one `pivotra.exe` entry point plus its accompanying runtime folder. Users keep that folder together; there is no installer, admin requirement, external database, or separate Python installation.
- The executable starts a loopback-only server on a free port, opens the browser, and provides console commands **open UI** and **quit** (or a tray equivalent).
- SQLAlchemy + SQLite at `%LOCALAPPDATA%\SheetForge\sheetforge.db`, initialized/upgraded by Alembic. `projects(id, name, source_path, created_at)`, `runs(id, project_id, cleaning_plan_json, model_plan_json, steps_json, output_path, status, created_at)`, and `settings(key, value)` contain paths/plans/settings only. **Spreadsheet values, original workbook bytes, samples, profiles containing values, and cleaned data must never enter SQLite.**
- Working artifacts (cleaned Parquet, generated PBIP, ZIPs) live under `%LOCALAPPDATA%\SheetForge\projects\<project_id>\`.
- Keys live in Windows Credential Manager through keyring; environment/`.env` fallback is for local development. Keys never enter SQLite, logs, exported projects, or Git.
- Rotating logs live in `%LOCALAPPDATA%\SheetForge\logs\`. No-AI mode makes no network calls. Ollama discovery is explicit and local at `localhost:11434`.
- `scripts/build.ps1` bundles local PBIR/TMDL schema resources and Alembic migrations. Step 6 includes the executable `/health` smoke test and controlled shutdown.

SQLAlchemy, Alembic, PyInstaller, and ruff were explicitly requested. Additional uvicorn, pyarrow, and jsonschema dependencies were explicitly approved. No other direct application dependency is to be added without approval.

## Desktop API and web UI (step 5)

The local interface supports raw `.xlsx` uploads or an original local source path, before/after previews, editable cleaning/model JSON, human-readable transformation steps, saved run history, provider settings, and PBIP ZIP downloads. Uploads retain a managed workbook copy as the refresh source; selecting a local path keeps that original workbook as the refresh source. No external assets are used.

SQLAlchemy creates the SQLite database and Alembic applies migrations on first launch. Profile samples, workbook bytes and cleaned Parquet are files beneath the project directory, never database records. Interrupted operations are marked on restart. Earlier generated runs are preserved when a plan changes. Source changes between planning and generation require a new plan. The loopback API uses an instance token and same-origin checks.

Validation: 62 tests pass, including a complete offline upload → plan → apply → model → generate → download cycle, persistence across restart, metadata-only database checks, rejected cross-origin requests, malformed uploads, changed sources, reviewed plan validation, and secret redaction. Windows extended paths handle nested PBIR folders without changing system registry settings.

## Run on Windows

Extract the entire `pivotra-windows-x64.zip` into a writable folder and double-click `pivotra\pivotra.exe`. Keep `pivotra.exe` and `_internal` together. No Python installation, installer, administrator rights, database server, or manual database creation is needed. The browser opens the local UI at the free `http://127.0.0.1:<port>` address printed in the console. Type `open UI` to reopen it or `quit` to stop. Closing a browser tab leaves the server running. A second launch opens the existing instance.

Projects and settings survive app upgrades because they live in `%LOCALAPPDATA%\SheetForge`, outside the portable application folder. Back up that whole data folder together with any original workbooks selected by path. Browser uploads retain their own managed source copy; keep it in place so Power BI Refresh can read it. No-AI mode is selected on first run and needs no network connection. Selecting a cloud provider explicitly enables sample/statistics requests. Ollama model discovery happens only when selected.

From source, Python 3.11 is required:

```powershell
python -m uv sync --python 3.11 --extra app --extra llm --extra dev
.venv\Scripts\sheetforge serve
```

Build and test the portable Windows distribution:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/build.ps1
# Repeat just the executable smoke test:
.venv\Scripts\python scripts/smoke_exe.py dist/pivotra/pivotra.exe
```

The build uses PyInstaller one-folder mode and pinned `uv.lock` dependencies. The smoke test runs the packaged provider imports, launches on a free loopback port, checks `/health`, uploads a fixture, plans/cleans/models/generates/downloads a PBIP, stops gracefully, restarts with the same database, verifies saved previews/downloads, and stops again. Its isolated temporary `SHEETFORGE_HOME` does not touch your real project storage.

Browser verification used the existing Microsoft Edge/Playwright installation: upload, cleaning/modeling, export/download, reload, no-AI settings connection, desktop/mobile layout, and JavaScript error checks all passed. This browser tooling is not an application dependency. The automated suite has 70 passing tests, and ruff passes.

Native Power BI Desktop execution of generated TMDL/M and visual rendering still needs a manual check on a machine with Desktop: extract a generated project, open `Pivotra.pbip`, Refresh, inspect all report pages/measures/relationships, then edit a source numeric value and Refresh again. Local JSON schema validation does not establish native Desktop compatibility. Live cloud-provider requests have not been tested with real API keys.

Executable verification: the Windows x64 PyInstaller one-folder build passed `scripts/smoke_exe.py`, including provider SDK/vault imports, launch, health, full no-AI workflow, generated ZIP checks, restart persistence, and graceful shutdown. Browser checks were also repeated against the packaged executable.

A boundary regression test covers a full-width worksheet with 256 numeric columns: no-AI modeling emits all 256 sum measures plus the record count. The measure contract accommodates all 32 supported tables at this width.

## Pivotra brand

The app, exported Power BI projects, downloads, and portable Windows launcher use the Pivotra name. The vector logo combines a deep violet rounded square, circular frame, white table cells and violet/amber chart bars. The same geometry supplies the multi-resolution Windows icon.

Existing storage and credential identifiers remain compatible: data stays under `%LOCALAPPDATA%\SheetForge`, the Python package/CLI remains `sheetforge`, and saved API keys continue using the existing Windows Credential Manager service.

## Pivotra builder interface

The Power BI builder uses a centered, sidebar-free layout with locally bundled Inter, a floating pill navigation bar, violet/amber branding, light and dark themes, a drop zone, and four real messy-workbook examples. The original NV-A/Pears app is unchanged. The builder opens the selected saved project on refresh; all other projects remain available through Saved projects.

Select a file and click Build Power BI project to run the complete cleaning/model/report pipeline. Before/after previews are followed by the four KPI cards (Usable rows, Total spend, Total usage, Average utilization). KPI values use the complete cleaned data, not preview samples. Missing measures or unavailable prior weeks are shown explicitly. Weekly deltas compare the most recent data week against the preceding Monday-based week; tooltips disclose that the latest week may be partial.

Cleaning steps can be edited and re-run; the advanced JSON editors, model settings, all providers, source-path input, audit trail, saved runs and PBIP export remain available. Generated projects are preserved when re-running. The cleaned Excel download contains styled tables, editable charts for up to the first three numeric columns per table (first 30 records), and a transformation audit. Power BI downloads are complete ZIPs: extract the whole folder and open Pivotra.pbip.

CSV uploads must be UTF-8 and are converted into a managed local XLSX so the existing native Excel-based Power BI refresh pipeline works. Refresh reads that managed workbook; re-import after changing the original CSV. Excel formulas from CSV text are preserved as literal text. Files are limited to 8 MB.

Privacy is enforced by the backend: Local only prevents cloud planning (local Ollama is allowed); Masked sample replaces sample cell values with typed placeholders while sharing headers/statistics; Full sample shares the bounded first 20/last 5 rows and statistics, never a whole workbook. No-AI is the initial provider. Choosing Auto-detect Ollama explicitly requests local discovery. Selecting a cloud provider opens settings for a model ID and key. Model connection tests send no spreadsheet data.

Inter is bundled from the Google Fonts Inter distribution under its SIL Open Font License; the license is included at sheetforge/web/fonts/OFL.txt. No fonts, scripts, or styles are loaded from external services at runtime.
