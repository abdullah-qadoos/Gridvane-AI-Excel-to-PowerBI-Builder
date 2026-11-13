import json
import sys
from copy import deepcopy
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from openpyxl import Workbook
from pydantic import SecretStr

from sheetforge.llm import (
    CompletionUnavailable,
    LLMConfig,
    client,
    complete,
    config,
    configure,
    public_config,
)
from sheetforge.llm.ollama import discover_ollama
from sheetforge.planner import heuristic_plan
from sheetforge.planner.llm_planner import plan_cleaning
from sheetforge.profiler import profile_workbook

SOURCE = Path(__file__).parent / "fixtures" / "header_not_row1.xlsx"


@pytest.fixture(autouse=True)
def restore_no_ai():
    configure(LLMConfig())
    yield
    configure(LLMConfig())


def test_no_ai_never_loads_or_calls_a_provider(monkeypatch):
    monkeypatch.setattr(
        client, "request_completion", lambda *_: pytest.fail("No-AI called a provider")
    )
    plan, notes = plan_cleaning(profile_workbook(SOURCE))
    assert plan == heuristic_plan(profile_workbook(SOURCE))
    assert "No-AI" in notes[0]
    assert client.test_connection()["mode"] == "no-ai"


def test_invalid_json_is_retried_once_with_validation_error(monkeypatch):
    profile = profile_workbook(SOURCE)
    valid = heuristic_plan(profile).model_dump()
    invalid = valid | {"header_row_index": "not-an-integer"}
    calls = []

    def request(messages):
        calls.append(deepcopy(messages))
        return json.dumps(invalid if len(calls) == 1 else valid)

    monkeypatch.setattr(client, "request_completion", request)
    configure(LLMConfig(provider="ollama", model="user-chosen-model"))
    plan, notes = plan_cleaning(profile)
    assert plan.model_dump() == valid
    assert len(calls) == 2
    assert "Validation failed" in calls[1][-1]["content"]
    assert "header_row_index" in calls[1][-1]["content"]
    assert "LLM plan" in notes[0]


def test_two_invalid_responses_fall_back_without_logging_provider_secrets(monkeypatch, caplog):
    calls = []

    def request(messages):
        calls.append(messages)
        return '{"unexpected": "test-secret-not-a-real-key"}'

    monkeypatch.setattr(client, "request_completion", request)
    configure(LLMConfig(provider="ollama", model="local"))
    profile = profile_workbook(SOURCE)
    plan, notes = plan_cleaning(profile)
    assert plan == heuristic_plan(profile)
    assert len(calls) == 2
    assert "deterministic" in notes[0]
    assert "test-secret" not in caplog.text


def test_source_bounds_validation_also_gets_one_retry(monkeypatch):
    profile = profile_workbook(SOURCE)
    invalid = heuristic_plan(profile).model_dump()
    invalid["bounds"]["row_end"] = 500
    calls = []

    def request(messages):
        calls.append(deepcopy(messages))
        return json.dumps(invalid)

    monkeypatch.setattr(client, "request_completion", request)
    configure(LLMConfig(provider="ollama", model="local"))
    plan, _ = plan_cleaning(profile)
    assert plan == heuristic_plan(profile)
    assert len(calls) == 2
    assert "source shape" in calls[1][-1]["content"]


def test_invented_columns_are_rejected_before_execution(monkeypatch):
    profile = profile_workbook(SOURCE)
    invalid = heuristic_plan(profile).model_dump()
    invalid["unpivot_columns"] = ["Invented column"]
    calls = []

    def request(messages):
        calls.append(messages)
        return json.dumps(invalid)

    monkeypatch.setattr(client, "request_completion", request)
    configure(LLMConfig(provider="ollama", model="local"))
    plan, notes = plan_cleaning(profile)
    assert len(calls) == 2
    assert plan == heuristic_plan(profile)
    assert "deterministic" in notes[0]


def test_only_first_twenty_and_last_five_rows_are_sent(tmp_path, monkeypatch):
    book = Workbook()
    book.active.append(["Region", "Revenue"])
    for index in range(100):
        book.active.append(["DO_NOT_SEND_MIDDLE" if index == 50 else "Region", index + 1])
    path = tmp_path / "large.xlsx"
    book.save(path)
    book.close()
    profile = profile_workbook(path)
    captured = []

    def request(messages):
        captured.append(json.dumps(messages))
        return heuristic_plan(profile).model_dump_json()

    monkeypatch.setattr(client, "request_completion", request)
    configure(LLMConfig(provider="ollama", model="local"))
    plan_cleaning(profile)
    assert "DO_NOT_SEND_MIDDLE" not in captured[0]
    assert str(path) not in captured[0]
    assert len(profile.sheets["Sheet"]["sample"]) == 25


@pytest.mark.parametrize(
    "provider,prefix",
    [
        ("openai", "openai"),
        ("anthropic", "anthropic"),
        ("gemini", "gemini"),
        ("azure", "azure"),
        ("openrouter", "openrouter"),
        ("ollama", "ollama_chat"),
    ],
)
def test_litellm_adapter_routes_user_selected_provider_and_model(monkeypatch, provider, prefix):
    calls = []

    def completion(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content='{"ok":true}'))]
        )

    monkeypatch.setitem(sys.modules, "litellm", SimpleNamespace(completion=completion))
    configure(LLMConfig(provider=provider, model="chosen-model", api_key=SecretStr("fake-key")))
    assert client.test_connection()["ok"]
    assert calls[0]["model"] == prefix + "/chosen-model"
    assert calls[0]["num_retries"] == 0


def test_credentials_use_vault_and_are_excluded_from_public_config(tmp_path, monkeypatch):
    writes = []
    vault = SimpleNamespace(
        set_password=lambda *args: writes.append(args), get_password=lambda *_: "vault-test-key"
    )
    monkeypatch.setattr(config, "vault", lambda: vault)
    selected = LLMConfig(provider="openai", model="chosen")
    config.store_api_key(selected, "vault-test-key")
    assert writes[0][0] == "SheetForge"
    assert config.resolve_api_key(selected) == "vault-test-key"
    selected = LLMConfig(provider="openai", model="chosen", api_key=SecretStr("fake-secret"))
    assert "fake-secret" not in json.dumps(public_config(selected))
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text('SHEETFORGE_API_KEY="dotenv-test-key"\n', encoding="utf-8")
    monkeypatch.setattr(config, "vault", lambda: SimpleNamespace(get_password=lambda *_: None))
    monkeypatch.delenv("SHEETFORGE_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert config.resolve_api_key(LLMConfig(provider="openai", model="chosen")) == "dotenv-test-key"


def test_json_rejects_duplicate_keys_and_nonfinite_numbers():
    for text in ['{"ok":true,"ok":false}', '{"number":NaN}', "[]"]:
        with pytest.raises(ValueError):
            client.strict_object(text)
    with pytest.raises(CompletionUnavailable):
        complete("none", [], client.ConnectionReply)


def test_ollama_discovery_only_uses_the_local_model_list(monkeypatch):
    calls = []

    def request(url, timeout):
        calls.append(url)
        return BytesIO(b'{"models":[{"name":"local-b"},{"name":"local-a"},{"name":"local-a"}]}')

    monkeypatch.setattr("urllib.request.build_opener", lambda *_: SimpleNamespace(open=request))
    assert discover_ollama() == {"available": True, "models": ["local-a", "local-b"]}
    assert calls == ["http://localhost:11434/api/tags"]


def test_absent_ollama_is_a_normal_unavailable_result(monkeypatch):
    def request(*args, **kwargs):
        raise OSError("not running")

    monkeypatch.setattr("urllib.request.build_opener", lambda *_: SimpleNamespace(open=request))
    assert discover_ollama() == {"available": False, "models": []}


def test_cli_provider_needs_a_known_tool_and_no_key():
    with pytest.raises(ValueError, match="supported AI apps"):
        LLMConfig(provider="cli", model="evil.exe")
    config_cli = LLMConfig(provider="cli", model="claude")
    assert config.resolve_api_key(config_cli) is None
    with pytest.raises(ValueError, match="No key is needed"):
        config.store_api_key(config_cli, "sk-x")


def test_last_json_object_strips_banners_and_fences():
    noisy = 'Signed in as x\n```json\n{"a": {"b": 1}}\n```\nDone'
    assert client.last_json_object(noisy) == '{"a": {"b": 1}}'
    assert client.last_json_object("no braces here") == "no braces here"


def test_cli_provider_runs_the_tool_headless(monkeypatch):
    import shutil
    import subprocess

    configure(LLMConfig(provider="cli", model="claude"))
    calls = {}

    def fake_run(argv, **options):
        calls["argv"], calls["input"] = argv, options["input"]
        return SimpleNamespace(returncode=0, stdout='Banner line\n{"ok": true}\n', stderr="")

    monkeypatch.setattr(shutil, "which", lambda name: r"C:\fake\claude.exe")
    monkeypatch.setattr(subprocess, "run", fake_run)
    assert client.test_connection()["ok"] is True
    assert calls["argv"][0] == r"C:\fake\claude.exe"
    assert "--output-format" in calls["argv"] and "connectivity" in calls["input"]


def test_cli_provider_missing_tool_is_a_normal_unavailable(monkeypatch):
    import shutil

    configure(LLMConfig(provider="cli", model="codex"))
    monkeypatch.setattr(shutil, "which", lambda name: None)
    with pytest.raises(CompletionUnavailable, match="not found"):
        client.request_completion([{"role": "user", "content": "hi"}])
