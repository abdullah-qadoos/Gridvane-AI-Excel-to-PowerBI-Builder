"""Loopback desktop API. SQLite holds metadata; all workbook data stays in files."""

import csv
import hashlib
import io
import json
import logging
import secrets
from pathlib import Path
from threading import RLock
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID, uuid4
from xml.etree.ElementTree import ParseError
from zipfile import BadZipFile

import pandas as pd
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from openpyxl import Workbook
from openpyxl.utils.exceptions import InvalidFileException
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError
from starlette.concurrency import run_in_threadpool
from starlette.middleware.trustedhost import TrustedHostMiddleware

from sheetforge.contracts import AppliedStep, CleaningPlan, Contract
from sheetforge.llm import LLMConfig, configure, discover_ollama, public_config, store_api_key
from sheetforge.llm import test_connection as check_connection
from sheetforge.llm.config import resolve_api_key
from sheetforge.logging_setup import register_secret
from sheetforge.modeler import plan_model, validate_model
from sheetforge.paths import data_home, resource_path
from sheetforge.pbip import build_semantic_project
from sheetforge.planner.llm_planner import plan_cleaning
from sheetforge.presentation import clean_excel, kpis
from sheetforge.profiler import profile_workbook
from sheetforge.storage import Repository
from sheetforge.transformer import TransformResult, apply_plan

MAX_UPLOAD = 8 * 1024 * 1024
LOG = logging.getLogger("sheetforge.api")


class PlanRequest(Contract):
    project_id: str
    run_id: str | None = None
    sheet: str | None = None
    cleaning_plan: dict | None = None
    privacy: Literal["local", "masked", "sample"] = "local"


class RunRequest(Contract):
    project_id: str
    run_id: str


class ModelRequest(RunRequest):
    model_plan: dict | None = None
    privacy: Literal["local", "masked", "sample"] = "local"


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8"
    )
    temporary.replace(path)


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def preview(frame, limit=30):
    return {
        "columns": list(frame.columns),
        "rows": json.loads(frame.head(limit).to_json(orient="records", date_format="iso")),
        "row_count": len(frame),
    }


def create_app(home=None, shutdown=None):
    home = Path(home or data_home()).resolve()
    repo = Repository(home)
    app = FastAPI(title="Pivotra", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.repository = repo
    app.state.csrf_token = secrets.token_urlsafe(32)
    app.state.pipeline_lock = RLock()
    selected = LLMConfig.model_validate(json.loads(repo.setting("llm_config", "{}")))
    app.state.llm_config = selected
    configure(selected)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "[::1]"])
    web = resource_path("web")
    app.mount("/static", StaticFiles(directory=web), name="static")

    @app.middleware("http")
    async def local_access(request, call_next):
        protected = request.url.path not in {"/", "/health"} and not request.url.path.startswith(
            "/static/"
        )
        origin = request.headers.get("origin")
        if origin:
            parts = urlsplit(origin)
            if parts.netloc != request.headers.get("host") or parts.scheme != "http":
                return JSONResponse(
                    {"detail": "Cross-origin access is not allowed."}, status_code=403
                )
        if protected and not secrets.compare_digest(
            request.headers.get("x-sheetforge-token", ""), app.state.csrf_token
        ):
            return JSONResponse({"detail": "Reload Pivotra before continuing."}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; object-src 'none'; frame-ancestors 'none'; base-uri 'self'"
        )
        return response

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, exc):
        return JSONResponse(
            {
                "detail": "Invalid request.",
                "errors": [{"loc": error["loc"], "msg": error["msg"]} for error in exc.errors()],
            },
            status_code=422,
        )

    @app.exception_handler(ValidationError)
    async def invalid_plan(request, exc):
        return JSONResponse(
            {
                "detail": "The plan did not pass validation.",
                "errors": exc.errors(include_input=False, include_context=False, include_url=False),
            },
            status_code=422,
        )

    @app.exception_handler(ValueError)
    async def invalid_value(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=400)

    @app.exception_handler(SQLAlchemyError)
    async def storage_error(request, exc):
        LOG.error("Storage operation failed (%s).", type(exc).__name__)
        return JSONResponse(
            {"detail": "Local storage is unavailable. Check disk space and folder permissions."},
            status_code=503,
        )

    @app.exception_handler(OSError)
    async def file_error(request, exc):
        LOG.error("File operation failed (%s).", type(exc).__name__)
        return JSONResponse(
            {
                "detail": "A working file could not be read or written. Check the source path and disk space."
            },
            status_code=400,
        )

    @app.exception_handler(BadZipFile)
    @app.exception_handler(InvalidFileException)
    @app.exception_handler(ParseError)
    async def invalid_workbook(request, exc):
        return JSONResponse(
            {"detail": "Cannot read this workbook. Select a valid, unencrypted .xlsx file."},
            status_code=400,
        )

    @app.exception_handler(KeyError)
    async def missing_column(request, exc):
        return JSONResponse(
            {"detail": "A selected worksheet or column is no longer available. Review the plan."},
            status_code=400,
        )

    @app.get("/health")
    def health():
        return {"status": "ok", "app": "sheetforge", "version": "0.1.0"}

    @app.get("/", response_class=HTMLResponse)
    def index():
        return (
            web.joinpath("index.html")
            .read_text(encoding="utf-8")
            .replace("__SHEETFORGE_TOKEN__", app.state.csrf_token)
        )

    def project(project_id):
        item = repo.project(project_id)
        if not item:
            raise HTTPException(404, "Project not found.")
        UUID(item["id"])
        return item

    def run_for(body):
        item = repo.run(body.run_id)
        if not item or item["project_id"] != body.project_id:
            raise HTTPException(404, "Run not found for this project.")
        UUID(item["id"])
        return item

    def run_dir(item):
        directory = home / "projects" / item["project_id"] / "runs" / item["id"]
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def load_result(item):
        location = run_dir(item)
        manifest = read_json(location / "cleaned.json")
        tables = {
            record["name"]: pd.read_parquet(location / record["file"])
            for record in manifest["tables"]
        }
        steps = [AppliedStep.model_validate(value) for value in json.loads(item["steps_json"])]
        return TransformResult(
            tables=tables,
            steps=steps,
            warnings=manifest["warnings"],
            plan=CleaningPlan.model_validate_json(item["cleaning_plan_json"]),
            source=Path(project(item["project_id"])["source_path"]),
        )

    def source_digest(path):
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()

    def require_same_source(item, path):
        saved = read_json(run_dir(item) / "source.json")["sha256"]
        if saved != source_digest(path):
            raise HTTPException(
                409, "The source workbook changed. Create a new plan before continuing."
            )

    @app.get("/projects")
    def projects():
        return {"projects": repo.projects()}

    @app.get("/runs")
    def runs(project_id: str):
        project(project_id)
        return {"runs": repo.runs(project_id)}

    def import_source(name, content=None, original=None):
        with app.state.pipeline_lock:
            project_id = str(uuid4())
            directory = home / "projects" / project_id
            directory.mkdir(parents=True)
            source = original or directory / "source.xlsx"
            try:
                if content is not None:
                    if Path(name).suffix.lower() == ".csv":
                        text = content.decode("utf-8-sig")
                        try:
                            dialect = csv.Sniffer().sniff(text[:8192], delimiters=",;\t|")
                        except csv.Error:
                            dialect = csv.excel
                        book = Workbook()
                        try:
                            for index, row in enumerate(csv.reader(io.StringIO(text), dialect)):
                                if index >= 100_000 or len(row) > 256:
                                    raise ValueError(
                                        "Use CSV files with at most 100,000 rows and 256 columns."
                                    )
                                book.active.append(row)
                                for cell in book.active[book.active.max_row]:
                                    cell.data_type = "s"
                            book.save(source)
                        finally:
                            book.close()
                    else:
                        source.write_bytes(content)
                profile = profile_workbook(source)
                if not any(sheet["blocks"] for sheet in profile.sheets.values()):
                    raise ValueError(
                        "No numeric data blocks were found. Choose a worksheet with headers and data."
                    )
            except Exception:
                if content is not None:
                    source.unlink(missing_ok=True)
                directory.rmdir()
                raise
            write_json(directory / "profile.json", profile.to_dict())
            repo.add_project(project_id, name, source)
            item = repo.add_run(project_id)
            LOG.info("Workbook imported; project=%s", project_id)
            return {"project": repo.project(project_id), "run": item, "profile": profile.to_dict()}

    @app.post("/upload")
    async def upload(request: Request, filename: str = "workbook.xlsx"):
        if request.headers.get("content-type", "").startswith("application/json"):
            body = await request.json()
            if (
                not isinstance(body, dict)
                or set(body) != {"source_path"}
                or not isinstance(body["source_path"], str)
            ):
                raise ValueError("Provide a source_path string.")
            original = Path(body["source_path"]).expanduser().resolve()
            if (
                original.suffix.lower() != ".xlsx"
                or not original.is_file()
                or original.stat().st_size > MAX_UPLOAD
            ):
                raise ValueError("Select an existing .xlsx file under 8 MB.")
            return await run_in_threadpool(import_source, original.name, None, original)
        if Path(filename).suffix.lower() not in {".xlsx", ".csv"} or len(filename) > 255:
            raise ValueError("Upload an .xlsx workbook or UTF-8 CSV.")
        chunks, size = [], 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > MAX_UPLOAD:
                raise HTTPException(413, "Upload an .xlsx file under 8 MB.")
            chunks.append(chunk)
        return await run_in_threadpool(import_source, Path(filename).name, b"".join(chunks))

    @app.get("/profile")
    def get_profile(project_id: str):
        item = project(project_id)
        return read_json(home / "projects" / item["id"] / "profile.json")

    @app.post("/plan")
    def plan(body: PlanRequest):
        with app.state.pipeline_lock:
            owner = project(body.project_id)
            previous = run_for(body) if body.run_id else None
            item = (
                previous
                if previous and previous["status"] in {"uploaded", "planned"}
                else repo.add_run(body.project_id)
            )
            repo.update_run(item["id"], status="planning")
            try:
                profile = profile_workbook(owner["source_path"])
                write_json(home / "projects" / body.project_id / "profile.json", profile.to_dict())
                if body.cleaning_plan is not None:
                    cleaning = CleaningPlan.model_validate(body.cleaning_plan)
                    notes = ["Used the explicitly reviewed cleaning plan."]
                else:
                    register_secret(resolve_api_key(app.state.llm_config))
                    cleaning, notes = plan_cleaning(
                        profile,
                        body.sheet,
                        use_ai=body.privacy != "local" or app.state.llm_config.provider == "ollama",
                        privacy=body.privacy,
                    )
                write_json(
                    run_dir(item) / "source.json", {"sha256": source_digest(owner["source_path"])}
                )
                write_json(run_dir(item) / "planning_notes.json", notes)
                item = repo.update_run(
                    item["id"],
                    status="planned",
                    cleaning_plan_json=cleaning.model_dump_json(),
                    model_plan_json=None,
                    output_path=None,
                )
                return {"run": item, "plan": cleaning.model_dump(), "notes": notes}
            except Exception:
                repo.update_run(item["id"], status="failed")
                raise

    @app.post("/apply")
    def apply(body: RunRequest):
        with app.state.pipeline_lock:
            item, owner = run_for(body), project(body.project_id)
            if item["status"] not in {"planned", "applied"} or not item["cleaning_plan_json"]:
                raise HTTPException(409, "Create or review a cleaning plan first.")
            require_same_source(item, owner["source_path"])
            repo.update_run(item["id"], status="applying")
            try:
                result = apply_plan(
                    owner["source_path"],
                    CleaningPlan.model_validate_json(item["cleaning_plan_json"]),
                )
                location = run_dir(item)
                manifest = []
                for index, (name, frame) in enumerate(result.tables.items(), 1):
                    file = f"cleaned_{index:03d}.parquet"
                    frame.to_parquet(location / file, index=False, engine="pyarrow")
                    manifest.append({"name": name, "file": file})
                write_json(
                    location / "cleaned.json", {"tables": manifest, "warnings": result.warnings}
                )
                item = repo.update_run(
                    item["id"],
                    status="applied",
                    steps_json=json.dumps([step.model_dump() for step in result.steps]),
                    model_plan_json=None,
                    output_path=None,
                )
                return {
                    "run": item,
                    "tables": {name: preview(frame) for name, frame in result.tables.items()},
                    "steps": [step.model_dump() for step in result.steps],
                    "warnings": result.warnings,
                    "kpis": kpis(result.tables),
                }
            except Exception:
                repo.update_run(item["id"], status="failed")
                raise

    @app.get("/preview")
    def get_preview(run_id: str):
        item = repo.run(run_id)
        if not item:
            raise HTTPException(404, "Run not found.")
        if item["status"] not in {"applied", "modeled", "generated"}:
            return {"tables": {}, "steps": [], "warnings": []}
        result = load_result(item)
        return {
            "tables": {name: preview(frame) for name, frame in result.tables.items()},
            "steps": [step.model_dump() for step in result.steps],
            "warnings": result.warnings,
            "kpis": kpis(result.tables),
        }

    @app.post("/model")
    def model(body: ModelRequest):
        with app.state.pipeline_lock:
            item = run_for(body)
            if item["status"] not in {"applied", "modeled"}:
                raise HTTPException(409, "Apply a cleaning plan before modeling.")
            result = load_result(item)
            require_same_source(item, result.source)
            repo.update_run(item["id"], status="modeling")
            try:
                if body.model_plan is not None:
                    semantic, notes = (
                        validate_model(body.model_plan, result.tables),
                        ["Used the explicitly reviewed model plan."],
                    )
                else:
                    register_secret(resolve_api_key(app.state.llm_config))
                    semantic, notes = plan_model(
                        result.tables,
                        use_ai=body.privacy != "local" or app.state.llm_config.provider == "ollama",
                    )
                write_json(run_dir(item) / "model_notes.json", notes)
                item = repo.update_run(
                    item["id"], status="modeled", model_plan_json=semantic.model_dump_json()
                )
                return {"run": item, "model_plan": semantic.model_dump(), "notes": notes}
            except Exception:
                repo.update_run(item["id"], status="failed")
                raise

    @app.post("/generate")
    def generate(body: RunRequest):
        with app.state.pipeline_lock:
            item = run_for(body)
            if item["status"] != "modeled" or not item["model_plan_json"]:
                raise HTTPException(409, "Review a semantic model before generating the project.")
            result = load_result(item)
            require_same_source(item, result.source)
            repo.update_run(item["id"], status="generating")
            try:
                output = run_dir(item) / ("powerbi-" + uuid4().hex[:8])
                _, zipped = build_semantic_project(
                    result, output, json.loads(item["model_plan_json"])
                )
                item = repo.update_run(item["id"], status="generated", output_path=str(zipped))
                LOG.info("PBIP generated; run=%s", item["id"])
                return {"run": item, "download": "/download?run_id=" + item["id"]}
            except Exception:
                repo.update_run(item["id"], status="failed")
                raise

    @app.get("/download")
    def download(run_id: str):
        item = repo.run(run_id)
        if not item or item["status"] != "generated" or not item["output_path"]:
            raise HTTPException(404, "Generate a project before downloading.")
        path = Path(item["output_path"]).resolve()
        if (
            not path.is_relative_to((home / "projects" / item["project_id"]).resolve())
            or not path.is_file()
        ):
            raise HTTPException(404, "Generated project file is unavailable.")
        return FileResponse(path, filename="Pivotra-powerbi.zip", media_type="application/zip")

    @app.get("/download/excel")
    def download_excel(run_id: str):
        with app.state.pipeline_lock:
            item = repo.run(run_id)
            if not item or item["status"] not in {"applied", "modeled", "generated"}:
                raise HTTPException(409, "Clean a workbook first.")
            target = clean_excel(load_result(item), run_dir(item) / "Pivotra-cleaned.xlsx")
            return FileResponse(
                target,
                filename="Pivotra-cleaned.xlsx",
                media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )

    @app.get("/samples/{name}")
    def sample(name: str):
        samples = {
            "transposed": "transposed.xlsx",
            "monthly": "wide_months.xlsx",
            "multiple": "two_tables.xlsx",
            "merged": "merged_headers.xlsx",
        }
        if name not in samples:
            raise HTTPException(404, "Sample not found.")
        return FileResponse(resource_path("samples", samples[name]), filename=samples[name])

    @app.get("/settings")
    def get_settings():
        return public_config(app.state.llm_config) | {
            "has_key": bool(resolve_api_key(app.state.llm_config))
        }

    @app.post("/settings")
    def save_settings(body: LLMConfig):
        with app.state.pipeline_lock:
            if body.api_key and body.api_key.get_secret_value():
                key = body.api_key.get_secret_value()
                register_secret(key)
                store_api_key(body, key)
            repo.set_setting("llm_config", json.dumps(body.model_dump(exclude={"api_key"})))
            configure(body)
            app.state.llm_config = body
            return get_settings()

    @app.post("/settings/test")
    def test_settings():
        with app.state.pipeline_lock:
            register_secret(resolve_api_key(app.state.llm_config))
            return check_connection()

    @app.get("/settings/ollama")
    def ollama():
        return discover_ollama()

    @app.post("/shutdown")
    def stop():
        if shutdown is None:
            raise HTTPException(409, "This server is not controlled by the desktop launcher.")
        shutdown()
        return {"status": "stopping"}

    return app
