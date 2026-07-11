from types import SimpleNamespace

import pytest

from tradingagents.llm_clients import codex_client
from tradingagents.llm_clients.codex_client import (
    CodexChatModel,
    CodexSetupError,
    _available_model_ids,
    _codex_setup_message,
)
from tradingagents.llm_clients.factory import create_llm_client


@pytest.mark.unit
def test_codex_factory_returns_chat_model(monkeypatch):
    monkeypatch.setattr(codex_client, "preflight_codex_runtime", lambda model: ["gpt-5.4"])

    client = create_llm_client("codex", "gpt-5.4")
    llm = client.get_llm()

    assert isinstance(llm, CodexChatModel)
    assert llm.model_name == "gpt-5.4"


@pytest.mark.unit
def test_codex_plain_response(monkeypatch):
    llm = CodexChatModel(model_name="gpt-5.4")
    monkeypatch.setattr(llm, "_run_codex", lambda prompt: "Final analysis")

    result = llm.invoke("Analyze SPY")

    assert result.content == "Final analysis"
    assert result.tool_calls == []


@pytest.mark.unit
def test_codex_json_tool_response(monkeypatch):
    llm = CodexChatModel(model_name="gpt-5.4").bind_tools([
        SimpleNamespace(
            name="get_stock_data",
            description="Fetch stock data",
            args={"ticker": {"type": "string"}},
        )
    ])
    monkeypatch.setattr(
        llm,
        "_run_codex",
        lambda prompt: '{"tool_calls":[{"name":"get_stock_data","args":{"ticker":"SPY"}}]}',
    )

    result = llm.invoke("Analyze SPY")

    assert result.content == ""
    assert result.tool_calls == [
        {
            "name": "get_stock_data",
            "args": {"ticker": "SPY"},
            "id": "codex_tool_0",
            "type": "tool_call",
        }
    ]


@pytest.mark.unit
def test_available_model_ids_extracts_codex_sdk_models():
    codex = SimpleNamespace(
        models=lambda: SimpleNamespace(
            data=[
                SimpleNamespace(id="gpt-5.5"),
                SimpleNamespace(model="gpt-5.4-mini"),
            ]
        )
    )

    assert _available_model_ids(codex) == ["gpt-5.5", "gpt-5.4-mini"]


@pytest.mark.unit
def test_codex_setup_message_includes_recovery_commands():
    message = _codex_setup_message(
        "This Codex model requires a newer Codex app/CLI/SDK.",
        original_error="gpt-5.6-luna requires a newer version of Codex",
        available_models=["gpt-5.5", "gpt-5.4-mini"],
    )

    assert "python -m pip install -U --pre openai-codex" in message
    assert "codex update" in message
    assert "codex login" in message
    assert "c.account()" in message
    assert "gpt-5.5, gpt-5.4-mini" in message


@pytest.mark.unit
def test_preflight_rejects_unavailable_runtime_model(monkeypatch):
    class FakeCodex:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def account(self):
            return object()

        def models(self):
            return SimpleNamespace(data=[SimpleNamespace(id="gpt-5.5")])

    monkeypatch.setitem(__import__("sys").modules, "openai_codex", SimpleNamespace(Codex=FakeCodex))

    with pytest.raises(CodexSetupError) as exc:
        codex_client.preflight_codex_runtime("gpt-5.6-luna")

    assert "not available" in str(exc.value)
    assert "python -m pip install -U --pre openai-codex" in str(exc.value)
