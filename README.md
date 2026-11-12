# SheetForge

Python 3.11 · pandas transforms data · validated JSON plans explain decisions · no-AI mode works offline.

## Order of work

1. Profiler, deterministic planner/transformer, at least eight reproducible messy XLSX fixtures, and offline tests. **Complete: 13 offline tests pass across nine fixtures.**
2. Provider-neutral LiteLLM abstraction, strict LLM planning with one retry and deterministic fallback, credential storage, mocked tests. **Complete: 27 tests pass.**
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

- Portable Windows **one-folder** PyInstaller distribution: one `sheetforge.exe` entry point plus its accompanying runtime folder. Users keep that folder together; there is no installer, admin requirement, external database, or separate Python installation.
- The executable starts a loopback-only server on a free port, opens the browser, and provides console commands **open UI** and **quit** (or a tray equivalent).
- SQLAlchemy + SQLite at `%LOCALAPPDATA%\SheetForge\sheetforge.db`, initialized/upgraded by Alembic. `projects(id, name, source_path, created_at)`, `runs(id, project_id, cleaning_plan_json, model_plan_json, steps_json, output_path, status, created_at)`, and `settings(key, value)` contain paths/plans/settings only. **Spreadsheet values, original workbook bytes, samples, profiles containing values, and cleaned data must never enter SQLite.**
- Working artifacts (cleaned Parquet, generated PBIP, ZIPs) live under `%LOCALAPPDATA%\SheetForge\projects\<project_id>\`.
- Keys live in Windows Credential Manager through keyring; environment/`.env` fallback is for local development. Keys never enter SQLite, logs, exported projects, or Git.
- Rotating logs live in `%LOCALAPPDATA%\SheetForge\logs\`. No-AI mode makes no network calls. Ollama discovery is explicit and local at `localhost:11434`.
- `scripts/build.ps1` will bundle local PBIR/TMDL schema resources. Step 6 includes the executable `/health` smoke test and controlled shutdown.

SQLAlchemy, Alembic, PyInstaller, and ruff were explicitly requested. Additional uvicorn, pyarrow, and jsonschema dependencies were explicitly approved. No other direct application dependency is to be added without approval.
