"""Portable loopback launcher. No model SDK is imported in offline startup."""

import argparse
import json
import logging
import os
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path

from sheetforge.paths import data_home


class InstanceLock:
    def __init__(self, home):
        self.path = home / "instance.lock"
        self.handle = None

    def acquire(self):
        self.handle = self.path.open("a+b")
        self.handle.seek(0)
        self.handle.write(b"0")
        self.handle.flush()
        self.handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.handle.close()
            self.handle = None
            return False
        return True

    def close(self):
        if self.handle:
            self.handle.close()
            self.handle = None


def main(argv=None):
    parser = argparse.ArgumentParser(prog="sheetforge")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--smoke-test", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--control-file", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--check-runtime", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.check_runtime:
        os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
        import keyring.backends.Windows  # noqa: F401
        import litellm  # noqa: F401
        import pyarrow.parquet  # noqa: F401

        print(json.dumps({"runtime": "ok"}))
        return 0
    if args.control_file and not args.smoke_test:
        parser.error("--control-file is only available with --smoke-test")
    home = data_home().resolve()
    home.mkdir(parents=True, exist_ok=True)
    lock = InstanceLock(home)
    if not lock.acquire():
        try:
            session = json.loads((home / "session.json").read_text(encoding="utf-8"))
            from urllib.request import ProxyHandler, build_opener

            url = session["url"]
            from urllib.parse import urlsplit

            parts = urlsplit(url)
            if parts.hostname != "127.0.0.1" or parts.scheme != "http":
                raise ValueError("Invalid local session")
            with build_opener(ProxyHandler({})).open(url + "/health", timeout=2) as response:
                if json.load(response).get("app") != "sheetforge":
                    raise ValueError("Not a SheetForge server")
            print("SheetForge is already running: " + url)
            if not args.no_browser:
                webbrowser.open(url)
            return 0
        except (OSError, ValueError, KeyError):
            print("SheetForge is starting in another window. Try again shortly.")
            return 1
    from sheetforge.logging_setup import configure_logs

    configure_logs(home)
    session_path = home / "session.json"
    server = None
    listener = None
    try:
        import uvicorn

        from sheetforge.api import create_app
        from sheetforge.api.app import write_json

        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(("127.0.0.1", 0))
        listener.listen(128)
        url = f"http://127.0.0.1:{listener.getsockname()[1]}"

        def shutdown():
            server.should_exit = True

        app = create_app(home, shutdown=shutdown)
        server = uvicorn.Server(
            uvicorn.Config(
                app,
                host="127.0.0.1",
                loop="asyncio",
                http="h11",
                ws="none",
                lifespan="off",
                access_log=False,
                log_config=None,
            )
        )

        def console():
            while not server.should_exit:
                try:
                    command = input().strip().lower()
                except (EOFError, OSError):
                    return
                if command in {"open ui", "open", "o"}:
                    webbrowser.open(url)
                elif command in {"quit", "exit", "q"}:
                    shutdown()

        def ready():
            while not server.started:
                if server.should_exit:
                    return
                time.sleep(0.05)
            write_json(session_path, {"url": url, "pid": os.getpid()})
            if args.control_file:
                write_json(
                    args.control_file,
                    {
                        "url": url,
                        "pid": os.getpid(),
                        "token": app.state.csrf_token,
                    },
                )
            print(
                f"SheetForge is ready: {url}\nType 'open UI' to open the browser or 'quit' to stop.",
                flush=True,
            )
            if not args.no_browser:
                webbrowser.open(url)
            if not args.smoke_test:
                threading.Thread(target=console, daemon=True).start()

        threading.Thread(target=ready, daemon=True).start()
        logging.getLogger(__name__).info("Desktop server starting on loopback.")
        server.run(sockets=[listener])
        app.state.repository.engine.dispose()
        return 0
    except Exception as exc:
        logging.getLogger(__name__).error("Desktop startup failed (%s).", type(exc).__name__)
        print(
            f"SheetForge could not start ({type(exc).__name__}). See {home / 'logs'}.",
            file=sys.stderr,
        )
        return 1
    finally:
        if listener:
            listener.close()
        session_path.unlink(missing_ok=True)
        lock.close()


if __name__ == "__main__":
    raise SystemExit(main())
