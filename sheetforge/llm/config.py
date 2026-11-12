"""Credentials stay in keyring or process memory; .env is a read-only development fallback."""

import os
import sys
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, model_validator

from sheetforge.contracts import Contract


class LLMConfig(Contract):
    provider: Literal["none", "openai", "anthropic", "gemini", "azure", "openrouter", "ollama"] = (
        "none"
    )
    model: str = Field(default="", max_length=200)
    api_key: SecretStr | None = Field(default=None, exclude=True)
    base_url: str | None = None
    api_version: str | None = None

    @model_validator(mode="after")
    def valid_endpoint(self):
        if self.provider != "none" and not self.model.strip():
            raise ValueError("Choose a model ID when enabling a provider.")
        if self.base_url:
            url = urlsplit(self.base_url)
            if (
                url.scheme not in {"http", "https"}
                or not url.hostname
                or url.username
                or url.password
                or url.query
                or url.fragment
            ):
                raise ValueError(
                    "Base URLs must be HTTP(S) endpoints without credentials, queries, or fragments."
                )
        return self


def public_config(config):
    return config.model_dump(exclude={"api_key"}) | {"has_key": config.api_key is not None}


def dotenv_values(path=None):
    path = Path(path or ".env")
    if not path.is_file():
        return {}
    result = {}
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.removeprefix("export ").split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        result[key.strip()] = value
    return result


def credential_id(config):
    return config.provider + ":" + (config.base_url or "default")


def vault():
    if sys.platform == "win32":
        from keyring.backends.Windows import WinVaultKeyring

        return WinVaultKeyring()
    import keyring

    return keyring.get_keyring()


def store_api_key(config, key):
    if config.provider in {"none", "ollama"}:
        raise ValueError("No key is needed for no-AI mode or local Ollama.")
    try:
        vault().set_password("SheetForge", credential_id(config), key)
    except Exception:
        raise RuntimeError("Could not store the API key in the OS credential manager.") from None


def resolve_api_key(config):
    if config.api_key:
        return config.api_key.get_secret_value()
    if config.provider in {"none", "ollama"}:
        return None
    try:
        saved = vault().get_password("SheetForge", credential_id(config))
    except Exception:
        saved = None
    aliases = {
        "openai": "OPENAI_API_KEY",
        "anthropic": "ANTHROPIC_API_KEY",
        "gemini": "GEMINI_API_KEY",
        "azure": "AZURE_API_KEY",
        "openrouter": "OPENROUTER_API_KEY",
    }
    local = dotenv_values()
    return (
        saved
        or os.getenv("SHEETFORGE_API_KEY")
        or os.getenv(aliases.get(config.provider, "SHEETFORGE_API_KEY"))
        or local.get("SHEETFORGE_API_KEY")
        or local.get(aliases.get(config.provider, "SHEETFORGE_API_KEY"))
    )
