from types import SimpleNamespace

import pytest

from tradingagents.llm_clients.codex_client import CodexChatModel
from tradingagents.llm_clients.factory import create_llm_client


@pytest.mark.unit
def test_codex_factory_returns_chat_model():
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
