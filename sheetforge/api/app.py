"""Loopback desktop API. SQLite holds metadata; all workbook data stays in files."""

import csv
import hashlib
import io
import json
import logging
import os
import re
import secrets
from pathlib import Path
from threading import RLock
from typing import Literal
from urllib.parse import quote, urlsplit
from uuid import UUID, uuid4
from xml.etree.ElementTree import ParseError
from zipfile import BadZipFile, ZipFile

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

from sheetforge import formulas, quality
from sheetforge.contracts import AppliedStep, CleaningPlan, Contract
from sheetforge.llm import (
    LLMConfig,
    configure,
    detect_clis,
    discover_ollama,
    public_config,
    store_api_key,
)
from sheetforge.llm import test_connection as check_connection
from sheetforge.llm.config import resolve_api_key
from sheetforge.logging_setup import register_secret
from sheetforge.modeler import plan_model, validate_model
from sheetforge.modeler.design import suggest_design
from sheetforge.modeler.planning import IMAGE
from sheetforge.modeler.visuals import catalogue
from sheetforge.paths import data_home, output_stem, resource_path
from sheetforge.pbip import build_semantic_project
from sheetforge.planner.llm_planner import plan_cleaning
from sheetforge.presentation import clean_excel, kpis
from sheetforge.profiler import profile_workbook
from sheetforge.storage import Repository
from sheetforge.transformer import TransformResult, apply_plan, cast_table

MAX_UPLOAD = 8 * 1024 * 1024
MAX_IMAGE = 5 * 1024 * 1024
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


class QualityInspectRequest(RunRequest):
    privacy: Literal["local", "masked", "sample"] = "local"


class QualityRequest(RunRequest):
    decisions: dict[str, dict[str, dict]] = {}


QUALITY_READY = {"applied", "checked", "needs_fix", "modeled", "generated"}
CLEANED = QUALITY_READY


class FormulaScanRequest(Contract):
    project_id: str
    privacy: Literal["local", "masked", "sample"] = "local"


class FormulaApplyRequest(Contract):
    project_id: str
    # Accepted fix ids; the value renames a hardcoded input (None keeps the suggested name).
    accepted: dict[str, str | None]


class DesignRequest(RunRequest):
    model_plan: dict | None = None
    privacy: Literal["local", "masked", "sample"] = "local"


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

    def source_name(item):
        return project(item["project_id"])["name"]

    def download_name(item, suffix):
        return output_stem(source_name(item)) + suffix

    def save_tables(item, result):
        location = run_dir(item)
        manifest = []
        for index, (name, frame) in enumerate(result.tables.items(), 1):
            file = f"cleaned_{index:03d}.parquet"
            frame.to_parquet(location / file, index=False, engine="pyarrow")
            manifest.append({"name": name, "file": file})
        write_json(location / "cleaned.json", {"tables": manifest, "warnings": result.warnings})

    def result_payload(item, result):
        return {
            "run": item,
            "tables": {name: preview(frame) for name, frame in result.tables.items()},
            "steps": [step.model_dump() for step in result.steps],
            "warnings": result.warnings,
            "kpis": kpis(result.tables),
        }

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
                        title=owner["name"],
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
                save_tables(item, result)
                for stale in ["quality_report.json", "quality.json", "audit.json", "cleaned.xlsx"]:
                    (run_dir(item) / stale).unlink(missing_ok=True)
                item = repo.update_run(
                    item["id"],
                    status="applied",
                    steps_json=json.dumps([step.model_dump() for step in result.steps]),
                    model_plan_json=None,
                    output_path=None,
                )
                return result_payload(item, result)
            except Exception:
                repo.update_run(item["id"], status="failed")
                raise

    @app.get("/preview")
    def get_preview(run_id: str):
        item = repo.run(run_id)
        if not item:
            raise HTTPException(404, "Run not found.")
        if item["status"] not in CLEANED:
            return {"tables": {}, "steps": [], "warnings": []}
        result = load_result(item)
        return {
            "tables": {name: preview(frame) for name, frame in result.tables.items()},
            "steps": [step.model_dump() for step in result.steps],
            "warnings": result.warnings,
            "kpis": kpis(result.tables),
        }

    def raw_tables(item, owner):
        require_same_source(item, owner["source_path"])
        return apply_plan(
            owner["source_path"],
            CleaningPlan.model_validate_json(item["cleaning_plan_json"]),
            cast=False,
        )

    @app.post("/quality/inspect")
    def quality_inspect(body: QualityInspectRequest):
        with app.state.pipeline_lock:
            item, owner = run_for(body), project(body.project_id)
            if item["status"] not in QUALITY_READY:
                raise HTTPException(409, "Apply a cleaning plan before the quality check.")
            raw = raw_tables(item, owner)
            use_ai = body.privacy != "local" or app.state.llm_config.provider == "ollama"
            if use_ai:
                register_secret(resolve_api_key(app.state.llm_config))
            reports, notes = {}, []
            for name, frame in raw.tables.items():
                rules, note = quality.propose_rules(frame, raw.types[name], use_ai=use_ai)
                report = quality.inspect(frame, raw.types[name], rules)
                reports[name] = report.model_dump(mode="json")
                notes.append(f"{name}: {note}")
            write_json(run_dir(item) / "quality_report.json", {"report": reports, "notes": notes})
            (run_dir(item) / "quality.json").unlink(missing_ok=True)
            return {"run": item, "report": reports, "notes": notes}

    @app.post("/quality")
    def quality_apply(body: QualityRequest):
        with app.state.pipeline_lock:
            item, owner = run_for(body), project(body.project_id)
            location = run_dir(item)
            if (
                item["status"] not in QUALITY_READY
                or not (location / "quality_report.json").exists()
            ):
                raise HTTPException(409, "Run the quality check first.")
            saved = read_json(location / "quality_report.json")
            raw = raw_tables(item, owner)
            if set(saved["report"]) != set(raw.tables):
                raise HTTPException(409, "The tables changed. Run the quality check again.")
            unknown = set(body.decisions) - set(raw.tables)
            if unknown:
                raise ValueError(
                    "Decisions reference unknown tables: " + ", ".join(sorted(unknown))
                )
            # Validate every choice before touching run state: a missing choice is not a failed run.
            result = TransformResult(
                steps=list(raw.steps),
                warnings=list(raw.warnings),
                plan=raw.plan,
                source=raw.source,
                types=raw.types,
            )
            audits = {}
            for name, frame in raw.tables.items():
                report = quality.QualityReport.model_validate(saved["report"][name])
                fixed, log = quality.apply(frame, body.decisions.get(name, {}), report)
                result.tables[name] = cast_table(fixed, raw.types[name], name, result)
                audits[name] = log.model_dump() | {"stopped": log.stopped}
            stopped = any(audit["stopped"] for audit in audits.values())
            repo.update_run(item["id"], status="checking")
            try:
                save_tables(item, result)
                write_json(location / "audit.json", audits)
                write_json(
                    location / "quality.json",
                    saved | {"decisions": body.decisions, "audit": audits},
                )
                clean_excel(result, location / "cleaned.xlsx", audits, owner["name"])
                item = repo.update_run(
                    item["id"],
                    status="needs_fix" if stopped else "checked",
                    steps_json=json.dumps([step.model_dump() for step in result.steps]),
                    model_plan_json=None,
                    output_path=None,
                )
                return result_payload(item, result) | {"stopped": stopped, "audit": audits}
            except Exception:
                repo.update_run(item["id"], status="failed")
                raise

    def saved_quality(run_id):
        item = repo.run(run_id)
        if not item:
            raise HTTPException(404, "Run not found.")
        for name in ["quality.json", "quality_report.json"]:
            path = run_dir(item) / name
            if path.exists():
                return read_json(path)
        return None

    @app.get("/quality")
    def get_quality(run_id: str):
        return saved_quality(run_id) or {}

    @app.get("/download/quality")
    def download_quality(run_id: str):
        saved = saved_quality(run_id)
        if not saved:
            raise HTTPException(404, "Run the quality check first.")
        filename = download_name(repo.run(run_id), "-quality-report.json")
        ascii_name = filename.encode("ascii", "replace").decode().replace("?", "_")
        disposition = f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename)}"
        return JSONResponse(saved, headers={"Content-Disposition": disposition})

    @app.post("/model")
    def model(body: ModelRequest):
        with app.state.pipeline_lock:
            item = run_for(body)
            if item["status"] not in {"checked", "modeled", "generated"}:
                raise HTTPException(409, "Finish the quality check before modeling.")
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
                    result,
                    output,
                    json.loads(item["model_plan_json"]),
                    name=source_name(item),
                    images=image_dir(item["project_id"]),
                )
                item = repo.update_run(item["id"], status="generated", output_path=str(zipped))
                LOG.info("PBIP generated; run=%s", item["id"])
                return {"run": item, "download": "/download?run_id=" + item["id"]}
            except Exception:
                repo.update_run(item["id"], status="failed")
                raise

    def generated_zip(item):
        if not item or item["status"] != "generated" or not item["output_path"]:
            raise HTTPException(404, "Generate a project before downloading.")
        path = Path(item["output_path"]).resolve()
        if (
            not path.is_relative_to((home / "projects" / item["project_id"]).resolve())
            or not path.is_file()
        ):
            raise HTTPException(404, "Generated project file is unavailable.")
        return path

    @app.post("/open/powerbi")
    def open_powerbi(body: RunRequest):
        """Unpack the project into Documents and open it, so nobody opens a .pbip inside a ZIP."""
        item = run_for(body)
        zipped = generated_zip(item)
        stem = output_stem(source_name(item))
        root = Path(os.getenv("PIVOTRA_EXPORT_DIR") or Path.home() / "Documents" / "Pivotra")
        target, count = root / stem, 1
        while target.exists():
            count += 1
            target = root / f"{stem} ({count})"
        # Extended paths keep deep PBIR folders writable past the 260-character limit.
        writable = Path("\\\\?\\" + str(target.resolve())) if os.name == "nt" else target
        with ZipFile(zipped) as archive:
            for member in archive.infolist():
                parts = Path(member.filename).parts[1:]  # drop the ZIP's top folder
                if member.is_dir() or not parts or ".." in parts:
                    continue
                destination = writable.joinpath(*parts)
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(archive.read(member))
        project_file = next(target.glob("*.pbip"), None)
        if project_file is None:
            raise HTTPException(500, "The generated project has no .pbip file.")
        opened = False
        if hasattr(os, "startfile"):
            try:
                os.startfile(project_file)
                opened = True
            except OSError:
                LOG.warning("Power BI Desktop could not be launched for %s", project_file)
        return {"folder": str(target), "project": str(project_file), "opened": opened}

    @app.get("/download")
    def download(run_id: str):
        path = generated_zip(repo.run(run_id))
        item = repo.run(run_id)
        return FileResponse(
            path, filename=download_name(item, "-powerbi.zip"), media_type="application/zip"
        )

    @app.get("/download/excel")
    def download_excel(run_id: str):
        with app.state.pipeline_lock:
            item = repo.run(run_id)
            if not item or item["status"] not in CLEANED:
                raise HTTPException(409, "Clean a workbook first.")
            audit_file = run_dir(item) / "audit.json"
            target = clean_excel(
                load_result(item),
                run_dir(item) / "cleaned-download.xlsx",
                read_json(audit_file) if audit_file.exists() else None,
                source_name(item),
            )
            return FileResponse(
                target,
                filename=download_name(item, "-cleaned.xlsx"),
                media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )

    def image_dir(project_id):
        return home / "projects" / project(project_id)["id"] / "images"

    @app.post("/design/image")
    async def upload_image(request: Request, project_id: str):
        content, size = [], 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > MAX_IMAGE:
                raise HTTPException(413, "Choose a picture under 5 MB.")
            content.append(chunk)
        data = b"".join(content)
        # Trust the bytes, not the file name: only real PNG and JPEG pictures are kept.
        if data.startswith(b"\x89PNG\r\n\x1a\n"):
            suffix = ".png"
        elif data.startswith(b"\xff\xd8\xff"):
            suffix = ".jpg"
        else:
            raise ValueError("Choose a PNG or JPG picture.")
        folder = image_dir(project_id)
        folder.mkdir(parents=True, exist_ok=True)
        name = hashlib.sha256(data).hexdigest()[:16] + suffix
        (folder / name).write_bytes(data)
        return {"name": name}

    @app.get("/design/image")
    def get_image(project_id: str, name: str):
        if not re.fullmatch(IMAGE, name) or not (image_dir(project_id) / name).is_file():
            raise HTTPException(404, "Picture not found.")
        media = "image/png" if name.endswith(".png") else "image/jpeg"
        return FileResponse(image_dir(project_id) / name, media_type=media)

    @app.post("/design/suggest")
    def design_suggest(body: DesignRequest):
        item = run_for(body)
        if item["status"] not in {"checked", "modeled", "generated"} or not item["model_plan_json"]:
            raise HTTPException(409, "Build the report before asking for a design.")
        result = load_result(item)
        register_secret(resolve_api_key(app.state.llm_config))
        plan, notes = suggest_design(
            body.model_plan or json.loads(item["model_plan_json"]),
            result.tables,
            use_ai=body.privacy != "local" or app.state.llm_config.provider == "ollama",
        )
        return {"model_plan": plan.model_dump(), "notes": notes}

    @app.get("/visuals")
    def visuals():
        return catalogue()

    def formula_files(project_id):
        item = project(project_id)
        folder = home / "projects" / item["id"]
        return item, folder / "formulas.json", folder / "formulas-fixed.xlsx"

    @app.post("/formulas/scan")
    def formulas_scan(body: FormulaScanRequest):
        item, saved, _ = formula_files(body.project_id)
        register_secret(resolve_api_key(app.state.llm_config))
        result = formulas.improve(
            formulas.scan(item["source_path"]),
            use_ai=body.privacy != "local" or app.state.llm_config.provider == "ollama",
        )
        write_json(saved, result.model_dump())
        return result.model_dump()

    @app.post("/formulas/apply")
    def formulas_apply(body: FormulaApplyRequest):
        item, saved, target = formula_files(body.project_id)
        if not saved.exists():
            raise HTTPException(409, "Scan the formulas first.")
        result = formulas.FormulaScan.model_validate(read_json(saved))
        _, count = formulas.apply_fixes(item["source_path"], result, body.accepted, target)
        return {"applied": count, "download": "/download/formulas?project_id=" + item["id"]}

    @app.get("/download/formulas")
    def download_formulas(project_id: str):
        item, _, target = formula_files(project_id)
        if not target.exists():
            raise HTTPException(404, "Apply some formula fixes first.")
        return FileResponse(
            target,
            filename=output_stem(item["name"]) + "-formulas-fixed.xlsx",
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

    @app.get("/settings/clis")
    def clis():
        return {"tools": detect_clis()}

    @app.get("/settings/update")
    def update_check():
        import urllib.request

        from sheetforge import __version__

        request = urllib.request.Request(
            "https://api.github.com/repos/abdullah-qadoos/"
            "Gridvane-AI-Excel-to-PowerBI-Builder/releases/latest",
            headers={"User-Agent": "pivotra-update-check", "Accept": "application/vnd.github+json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=6) as response:
                latest = json.loads(response.read(65536))
        except Exception:
            raise HTTPException(502, "Could not reach GitHub to check for updates.") from None
        tag = str(latest.get("tag_name", "")).lstrip("v")
        return {
            "current": __version__,
            "latest": tag,
            "url": str(latest.get("html_url", "")),
            "newer": tag != __version__ and bool(tag),
        }

    @app.post("/shutdown")
    def stop():
        if shutdown is None:
            raise HTTPException(409, "This server is not controlled by the desktop launcher.")
        shutdown()
        return {"status": "stopping"}

    return app
