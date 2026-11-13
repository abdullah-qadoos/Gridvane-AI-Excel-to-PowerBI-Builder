"""One JSON completion interface, two attempts, and no hidden provider retries."""

import json
import os
from typing import Literal

from pydantic import ValidationError

from sheetforge.contracts import Contract
from sheetforge.llm.config import CLI_TOOLS, LLMConfig, dotenv_values, resolve_api_key

_config = LLMConfig()


class CompletionUnavailable(RuntimeError):
    pass


def configure(config):
    global _config
    _config = (
        LLMConfig.model_validate(config.model_dump() | {"api_key": config.api_key})
        if isinstance(config, LLMConfig)
        else LLMConfig.model_validate(config)
    )


def enabled():
    return _config.provider != "none"


def last_json_object(text):
    """The reply with any CLI banners or code fences stripped: the last balanced {...} block."""
    # shortcut: brace counting ignores braces inside JSON strings; the two-attempt retry covers it.
    end = text.rfind("}")
    depth = 0
    for i in range(end, -1, -1):
        depth += {"}": 1, "{": -1}.get(text[i], 0)
        if text[i] == "{" and depth == 0:
            return text[i : end + 1]
    return text


def run_cli(messages):
    import shutil
    import subprocess
    import sys
    import tempfile

    label, args = CLI_TOOLS[_config.model]
    exe = shutil.which(_config.model)
    if not exe:
        raise CompletionUnavailable(
            label + " was not found on this computer. Install it, sign in once, and try again."
        )
    prompt = "\n\n".join(
        {"assistant": "Your previous reply:\n"}.get(m["role"], "") + m["content"] for m in messages
    )
    try:
        done = subprocess.run(
            [exe, *(a.replace("{prompt}", prompt) for a in args)],
            input=None if any("{prompt}" in a for a in args) else prompt,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=240,
            cwd=tempfile.gettempdir(),  # a neutral folder: no project files for the agent to read
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise CompletionUnavailable(label + " did not answer in time.") from None
    if done.returncode != 0 or not done.stdout.strip():
        raise CompletionUnavailable(
            label + " could not answer. Open it once in a terminal to check you are signed in."
        )
    return last_json_object(done.stdout)


def request_completion(messages):
    # Import lazily: offline/no-AI use never imports LiteLLM or attempts model discovery.
    try:
        if _config.provider == "cli":
            return run_cli(messages)
        key = resolve_api_key(_config)
        if _config.provider != "ollama" and not key:
            raise CompletionUnavailable("No provider key is configured.")
        import litellm

        litellm.set_verbose = False
        litellm.suppress_debug_info = True
        litellm.turn_off_message_logging = True
        provider = "ollama_chat" if _config.provider == "ollama" else _config.provider
        model = (
            _config.model
            if _config.model.startswith(provider + "/")
            else provider + "/" + _config.model
        )
        options = {
            "model": model,
            "messages": messages,
            "api_key": key,
            "response_format": {"type": "json_object"},
            "temperature": 0,
            "num_retries": 0,
            "timeout": 30,
            "drop_params": True,
        }
        if _config.base_url or _config.provider == "ollama":
            options["api_base"] = _config.base_url or "http://localhost:11434"
        if _config.api_version:
            options["api_version"] = _config.api_version
        elif _config.provider == "azure":
            version = os.getenv("AZURE_API_VERSION") or dotenv_values().get("AZURE_API_VERSION")
            if version:
                options["api_version"] = version
        response = litellm.completion(**options)
        return response.choices[0].message.content
    except CompletionUnavailable:
        raise
    except Exception:
        # Provider exceptions can embed Authorization headers. Never propagate or log them.
        raise CompletionUnavailable(
            "The provider request failed; deterministic planning is available."
        ) from None


def strict_object(text):
    if not isinstance(text, str) or len(text) > 128_000:
        raise ValueError("Expected a JSON response below 128 KB.")

    def reject(value):
        raise ValueError("JSON cannot contain NaN or Infinity.")

    def distinct(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("JSON contains duplicate keys.")
            result[key] = value
        return result

    result = json.loads(text, parse_constant=reject, object_pairs_hook=distinct)
    if not isinstance(result, dict):
        raise ValueError("The response must be a JSON object.")
    return result


def complete(system, messages, schema) -> dict:
    if not enabled():
        raise CompletionUnavailable("No-AI mode is selected.")
    conversation = [
        {
            "role": "system",
            "content": system
            + "\nReturn only a JSON object satisfying this schema:\n"
            + json.dumps(schema.model_json_schema()),
        },
        *messages,
    ]
    for attempt in range(2):
        text = request_completion(conversation)
        try:
            return schema.model_validate(strict_object(text)).model_dump(mode="json")
        except (ValueError, ValidationError) as error:
            details = (
                json.dumps(
                    error.errors(include_input=False, include_context=False, include_url=False)
                )[:3000]
                if isinstance(error, ValidationError)
                else str(error)[:1000]
            )
            if attempt:
                raise CompletionUnavailable(
                    "The provider returned an invalid plan twice; deterministic planning is available."
                ) from None
            conversation.extend(
                [
                    {"role": "assistant", "content": str(text)[:16_000]},
                    {
                        "role": "user",
                        "content": "Validation failed: "
                        + details
                        + "\nCorrect the JSON; do not add prose.",
                    },
                ]
            )
    raise AssertionError("Unreachable")


class ConnectionReply(Contract):
    ok: Literal[True]


def test_connection():
    if not enabled():
        return {"ok": True, "mode": "no-ai", "message": "Offline deterministic planning is ready."}
    try:
        complete('Check connectivity; return {"ok": true}.', [], ConnectionReply)
        return {"ok": True, "mode": "llm", "message": "Provider returned validated JSON."}
    except CompletionUnavailable:
        return {
            "ok": False,
            "mode": "llm",
            "message": "Connection failed. Check the model, endpoint, and credential manager.",
        }
