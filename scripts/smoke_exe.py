"""Exercise the actual portable binary using only the Python standard library."""

import argparse
import io
import json
import os
import sqlite3
import subprocess
import tempfile
import time
import zipfile
from contextlib import closing
from pathlib import Path
from urllib.request import ProxyHandler, Request, build_opener


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("exe", type=Path)
    args = parser.parse_args()
    executable = args.exe.resolve()
    fixture = Path(__file__).resolve().parents[1] / "tests/fixtures/header_not_row1.xlsx"
    opener = build_opener(ProxyHandler({}))
    with tempfile.TemporaryDirectory(prefix="sf-smoke-") as temporary:
        home = Path(temporary)
        env = dict(os.environ, SHEETFORGE_HOME=str(home))
        # A complete binary must include the optional provider SDK and vault backend.
        runtime = subprocess.run(
            [str(executable), "--check-runtime"],
            env=env,
            capture_output=True,
            text=True,
            cwd=home,
            timeout=90,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        assert runtime.returncode == 0, runtime.stderr + runtime.stdout
        assert '"runtime": "ok"' in runtime.stdout, runtime.stdout
        ids = None
        for launch in range(2):
            control = home / f"control-{launch}.json"
            with (home / f"console-{launch}.txt").open("w", encoding="utf-8") as console:
                process = subprocess.Popen(
                    [
                        str(executable),
                        "--no-browser",
                        "--smoke-test",
                        "--control-file",
                        str(control),
                    ],
                    env=env,
                    cwd=home,
                    stdout=console,
                    stderr=subprocess.STDOUT,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
                try:
                    deadline = time.monotonic() + 90
                    while not control.exists():
                        assert process.poll() is None, (home / f"console-{launch}.txt").read_text()
                        if time.monotonic() > deadline:
                            raise TimeoutError("Executable did not become ready.")
                        time.sleep(0.1)
                    session = json.loads(control.read_text())
                    url, token = session["url"], session["token"]
                    assert url.startswith("http://127.0.0.1:")

                    def request(path, body=None, raw=False, token=token, url=url):
                        headers = {"X-SheetForge-Token": token}
                        if isinstance(body, dict):
                            body = json.dumps(body).encode()
                            headers["Content-Type"] = "application/json"
                        elif body is not None:
                            headers["Content-Type"] = "application/octet-stream"
                        with opener.open(
                            Request(url + path, data=body, headers=headers), timeout=45
                        ) as response:
                            content = response.read()
                            return content if raw else json.loads(content)

                    assert request("/health")["status"] == "ok"
                    if launch == 0:
                        duplicate = subprocess.run(
                            [str(executable), "--no-browser"],
                            env=env,
                            cwd=home,
                            capture_output=True,
                            text=True,
                            timeout=20,
                            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                        )
                        assert duplicate.returncode == 0, duplicate.stderr + duplicate.stdout
                        assert "already running" in duplicate.stdout
                    assert b"SheetForge" in request("/", raw=True)
                    if launch == 0:
                        uploaded = request("/upload?filename=review.xlsx", fixture.read_bytes())
                        ids = {
                            "project_id": uploaded["project"]["id"],
                            "run_id": uploaded["run"]["id"],
                        }
                        for endpoint in ["/plan", "/apply", "/model", "/generate"]:
                            result = request(endpoint, ids)
                            assert result["run"]["id"] == ids["run_id"]
                        assert result["run"]["status"] == "generated"
                    else:
                        assert request("/projects")["projects"][0]["id"] == ids["project_id"]
                        assert (
                            request("/runs?project_id=" + ids["project_id"])["runs"][0]["status"]
                            == "generated"
                        )
                    preview = request("/preview?run_id=" + ids["run_id"])
                    assert next(iter(preview["tables"].values()))["row_count"] == 2
                    downloaded = request("/download?run_id=" + ids["run_id"], raw=True)
                    with zipfile.ZipFile(io.BytesIO(downloaded)) as archive:
                        assert any(name.endswith("SheetForge.pbip") for name in archive.namelist())
                        for name in archive.namelist():
                            if name.endswith((".tmdl", ".json", ".pbip", ".pbir", ".pbism")):
                                assert not archive.read(name).startswith(b"\xef\xbb\xbf")
                    request("/shutdown", {})
                    assert process.wait(timeout=20) == 0
                finally:
                    if process.poll() is None:
                        process.terminate()
                        process.wait(timeout=15)
        with closing(sqlite3.connect(home / "sheetforge.db")) as database:
            tables = {
                row[0]
                for row in database.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            assert tables == {"projects", "runs", "settings", "alembic_version"}, tables
        assert (home / "logs/sheetforge.log").is_file()
    print(
        "EXE smoke passed: runtime imports, free loopback port, /health, offline upload-to-PBIP, restart persistence, download and clean shutdown."
    )


if __name__ == "__main__":
    main()
