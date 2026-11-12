import logging
import os
import threading
from logging.handlers import RotatingFileHandler

_secrets = set()
_lock = threading.Lock()


def register_secret(value):
    if value:
        with _lock:
            _secrets.add(value)


class RedactingFormatter(logging.Formatter):
    def format(self, record):
        text = super().format(record)
        with _lock:
            for value in sorted(_secrets, key=len, reverse=True):
                text = text.replace(value, "[REDACTED]")
        return text


def configure_logs(home):
    directory = home / "logs"
    directory.mkdir(parents=True, exist_ok=True)
    for key, value in os.environ.items():
        if key.endswith("API_KEY"):
            register_secret(value)
    handler = RotatingFileHandler(
        directory / "sheetforge.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8"
    )
    handler.setFormatter(RedactingFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    for previous in list(root.handlers):
        root.removeHandler(previous)
        previous.close()
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    for name in ["httpx", "httpcore", "LiteLLM", "LiteLLM Router", "LiteLLM Proxy"]:
        logging.getLogger(name).setLevel(logging.CRITICAL)
