import json
import os
import re
import socket
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook, load_workbook
from sqlalchemy import inspect

from sheetforge.api import create_app
from sheetforge.logging_setup import RedactingFormatter, register_secret
from sheetforge.storage import Repository

FIXTURE = Path(__file__).parent / "fixtures" / "header_not_row1.xlsx"


def client_for(home):
    app = create_app(home)
    client = TestClient(app, base_url="http://127.0.0.1")
    token = re.search(r'name="sheetforge-token" content="([^"]+)"', client.get("/").text)[1]
    client.headers["X-SheetForge-Token"] = token
    return app, client


def upload(client, source=FIXTURE):
    response = client.post(
        "/upload?filename=workbook.xlsx",
        content=source.read_bytes(),
        headers={"Content-Type": "application/octet-stream"},
    )
    assert response.status_code == 200, response.text
    data = response.json()
    return {"project_id": data["project"]["id"], "run_id": data["run"]["id"]}, data


def pipeline(client, ids):
    planned = client.post("/plan", json=ids)
    assert planned.status_code == 200, planned.text
    ids["run_id"] = planned.json()["run"]["id"]
    for endpoint in ["/apply", "/model", "/generate"]:
        response = client.post(endpoint, json=ids)
        assert response.status_code == 200, response.text
    return response.json()


def test_full_offline_api_pipeline_and_restart(tmp_path, monkeypatch):
    connect = socket.socket.connect

    def local_only(self, address):
        if isinstance(address, tuple) and address[0] not in {"127.0.0.1", "localhost", "::1"}:
            raise AssertionError("No-AI mode attempted external network access.")
        return connect(self, address)

    monkeypatch.setattr(socket.socket, "connect", local_only)
    app, client = client_for(tmp_path)
    ids, data = upload(client)
    result = pipeline(client, ids)
    assert Path(result["run"]["output_path"]).is_relative_to(
        tmp_path / "projects" / ids["project_id"]
    )
    assert client.get("/download", params={"run_id": ids["run_id"]}).content.startswith(b"PK")
    assert client.get("/profile", params={"project_id": ids["project_id"]}).json()["sheets"]
    client.close()
    app.state.repository.engine.dispose()
    _, reopened = client_for(tmp_path)
    runs = reopened.get("/runs", params={"project_id": ids["project_id"]}).json()["runs"]
    assert runs[0]["status"] == "generated"
    preview = reopened.get("/preview", params={"run_id": ids["run_id"]}).json()
    frame = next(iter(preview["tables"].values()))
    assert frame["row_count"] == 2
    assert reopened.get("/download", params={"run_id": ids["run_id"]}).status_code == 200
    reopened.close()


def test_db_contains_only_contract_metadata_and_never_cells_or_file_blobs(tmp_path):
    source = tmp_path / "input.xlsx"
    book = Workbook()
    book.active.append(["Region", "Revenue"])
    book.active.append(["UNIQUE_CELL_SENTINEL_8A36", 123])
    book.save(source)
    book.close()
    app, client = client_for(tmp_path / "home")
    ids, _ = upload(client, source)
    pipeline(client, ids)
    schema = inspect(app.state.repository.engine)
    assert set(schema.get_table_names()) == {"projects", "runs", "settings", "alembic_version"}
    assert {column["name"] for column in schema.get_columns("projects")} == {
        "id",
        "name",
        "source_path",
        "created_at",
    }
    assert all(
        "BLOB" not in str(column["type"]).upper()
        for name in schema.get_table_names()
        for column in schema.get_columns(name)
    )
    assert b"UNIQUE_CELL_SENTINEL_8A36" not in (tmp_path / "home" / "sheetforge.db").read_bytes()
    working = tmp_path / "home" / "projects"
    if os.name == "nt":
        working = Path("\\\\?\\" + str(working))
    assert list(working.rglob("*.parquet"))
    client.close()


def test_settings_use_keyring_and_never_persist_keys(tmp_path, monkeypatch):
    import importlib

    module = importlib.import_module("sheetforge.api.app")
    saved = []
    monkeypatch.setattr(module, "store_api_key", lambda config, key: saved.append(key))
    _, client = client_for(tmp_path)
    response = client.post(
        "/settings",
        json={"provider": "openai", "model": "chosen", "api_key": "FAKE_KEY_DO_NOT_PERSIST_99"},
    )
    assert response.status_code == 200 and saved == ["FAKE_KEY_DO_NOT_PERSIST_99"]
    assert "FAKE_KEY" not in response.text
    assert b"FAKE_KEY_DO_NOT_PERSIST_99" not in (tmp_path / "sheetforge.db").read_bytes()
    client.close()


def test_request_guard_blocks_cross_origin_missing_nonce_and_bad_hosts(tmp_path):
    _, client = client_for(tmp_path)
    assert client.get("/health").status_code == 200
    assert client.get("/projects", headers={"X-SheetForge-Token": ""}).status_code == 403
    assert (
        client.post(
            "/upload", content=b"bad", headers={"Origin": "https://example.com"}
        ).status_code
        == 403
    )
    assert client.get("/health", headers={"Host": "evil.example"}).status_code == 400
    client.close()


def test_invalid_uploads_and_out_of_order_steps_are_actionable(tmp_path):
    _, client = client_for(tmp_path)
    assert client.post("/upload?filename=bad.xlsx", content=b"bad").status_code == 400
    assert client.post("/upload?filename=bad.txt", content=b"bad").status_code == 400
    ids, _ = upload(client)
    assert client.post("/generate", json=ids).status_code == 409
    assert client.post("/apply", json=ids).status_code == 409
    assert (
        client.post("/plan", json=ids | {"cleaning_plan": {"execute": "code"}}).status_code == 422
    )
    assert client.get("/download", params={"run_id": "missing"}).status_code == 404
    client.close()


def test_explicit_source_path_and_changed_source_detection(tmp_path):
    source = tmp_path / "original.xlsx"
    source.write_bytes(FIXTURE.read_bytes())
    _, client = client_for(tmp_path / "home")
    data = client.post("/upload", json={"source_path": str(source)}).json()
    assert data["project"]["source_path"] == str(source.resolve())
    ids = {"project_id": data["project"]["id"], "run_id": data["run"]["id"]}
    assert client.post("/plan", json=ids).status_code == 200
    book = load_workbook(source)
    book.active["B4"] = 999
    book.save(source)
    book.close()
    assert client.post("/apply", json=ids).status_code == 409
    client.close()


def test_generated_runs_are_not_mutated_when_overriding_a_plan(tmp_path):
    _, client = client_for(tmp_path)
    ids, _ = upload(client)
    pipeline(client, ids)
    previous = ids["run_id"]
    run = client.get("/runs", params={"project_id": ids["project_id"]}).json()["runs"][0]
    response = client.post(
        "/plan", json=ids | {"cleaning_plan": json.loads(run["cleaning_plan_json"])}
    )
    assert response.status_code == 200
    assert response.json()["run"]["id"] != previous
    assert client.get("/download", params={"run_id": previous}).status_code == 200
    client.close()


def test_migrations_and_interrupted_run_recovery(tmp_path):
    repo = Repository(tmp_path)
    project_id = "0b50e477-2e2b-4d7c-a3a0-7a0cc22c878d"
    repo.add_project(project_id, "test.xlsx", tmp_path / "source.xlsx")
    run = repo.add_run(project_id)
    repo.update_run(run["id"], status="generating")
    repo.engine.dispose()
    restored = Repository(tmp_path)
    assert restored.run(run["id"])["status"] == "interrupted"
    with closing(sqlite3.connect(tmp_path / "sheetforge.db")) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "0001"
    with pytest.raises(ValueError):
        restored.set_setting("llm_config", '{"api_key":"not allowed"}')


def test_rotating_log_formatter_redacts_secrets():
    import logging

    register_secret("FAKE_LOG_SECRET_77")
    record = logging.LogRecord(
        "test", logging.ERROR, "", 0, "Authorization: FAKE_LOG_SECRET_77", (), None
    )
    assert "FAKE_LOG_SECRET_77" not in RedactingFormatter("%(message)s").format(record)
