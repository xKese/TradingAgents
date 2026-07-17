"""Tests for the local web UI: catalog adapters, run-config validation, and the
SSE streaming endpoint (driven by a fake graph so no LLM is needed)."""

import pytest
from langchain_core.messages import AIMessage
from starlette.testclient import TestClient

from webapp import catalog, run_config, server


# --------------------------------------------------------------------------- #
# Catalog adapters
# --------------------------------------------------------------------------- #
@pytest.mark.unit
def test_catalog_providers_and_models():
    provs = catalog.providers()
    keys = {p["key"] for p in provs}
    assert {"openai", "anthropic", "ollama"} <= keys
    ollama = next(p for p in provs if p["key"] == "ollama")
    assert ollama["local"] is True and ollama["needs_url"] is True

    anthropic = catalog.models("anthropic")
    assert any(m["id"] == "claude-fable-5" for m in anthropic["deep"])


@pytest.mark.unit
def test_requires_api_key_local_providers_exempt():
    # Local / OpenAI-compatible servers (LM Studio, vLLM) never require a key.
    assert catalog.requires_api_key("ollama") is False
    assert catalog.requires_api_key("openai_compatible") is False
    assert catalog.requires_api_key("bedrock") is False
    assert catalog.requires_api_key("openai") is True


@pytest.mark.unit
def test_preflight_does_not_block_openai_compatible(monkeypatch):
    # An LM Studio-style local server must run even with no key env set.
    monkeypatch.delenv("OPENAI_COMPATIBLE_API_KEY", raising=False)
    assert server._preflight_key_error("openai_compatible") is None
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert server._preflight_key_error("openai") is not None


@pytest.mark.unit
def test_catalog_analysts_crypto_drops_fundamentals():
    stock = [a["value"] for a in catalog.analysts("stock")]
    crypto = [a["value"] for a in catalog.analysts("crypto")]
    assert "fundamentals" in stock
    assert "fundamentals" not in crypto


# --------------------------------------------------------------------------- #
# Run-config validation
# --------------------------------------------------------------------------- #
@pytest.mark.unit
def test_build_run_valid_and_depth_maps_to_rounds():
    spec = run_config.build_run(
        {
            "ticker": "nvda",
            "analysis_date": "2026-01-15",
            "llm_provider": "ollama",
            "shallow_thinker": "qwen3:latest",
            "deep_thinker": "qwen3:latest",
            "research_depth": 5,
            "analysts": ["market", "news"],
        }
    )
    assert spec["ticker"] == "NVDA"
    assert spec["asset_type"] == "stock"
    assert spec["config"]["max_debate_rounds"] == 5
    assert spec["config"]["llm_provider"] == "ollama"


@pytest.mark.unit
@pytest.mark.parametrize(
    "payload",
    [
        {"ticker": "bad!!", "analysis_date": "2026-01-15"},
        {"ticker": "AAPL", "analysis_date": "2099-01-01"},
        {"ticker": "AAPL", "analysis_date": "not-a-date"},
    ],
)
def test_build_run_rejects_bad_input(payload):
    base = {
        "llm_provider": "ollama",
        "shallow_thinker": "x",
        "deep_thinker": "y",
        "research_depth": 1,
    }
    with pytest.raises(run_config.RunRequestError):
        run_config.build_run({**base, **payload})


# --------------------------------------------------------------------------- #
# ProgressTracker
# --------------------------------------------------------------------------- #
@pytest.mark.unit
def test_progress_tracker_status_progression():
    t = server.ProgressTracker(["market"])
    # first chunk: no report yet -> market analyst in_progress
    t.process({"messages": []})
    assert t.status["Market Analyst"] == "in_progress"
    # report arrives -> completed, and a report event is emitted
    events = t.process({"market_report": "MKT"})
    names = [e[0] for e in events]
    assert "report" in names and "status" in names
    assert t.status["Market Analyst"] == "completed"
    # final decision -> portfolio manager completed
    t.process({"final_trade_decision": "**Rating**: Buy"})
    assert t.status["Portfolio Manager"] == "completed"


# --------------------------------------------------------------------------- #
# SSE endpoint with a fake graph (no LLM)
# --------------------------------------------------------------------------- #
class _FakePropagator:
    def create_initial_state(self, ticker, date, **kw):
        return {"messages": []}

    def get_graph_args(self, callbacks=None):
        return {}


class _FakeInner:
    def stream(self, init, **args):
        yield {"messages": [AIMessage(content="Analysiere Markt …", id="m1")],
               "market_report": "# Market\nMKT"}
        yield {"messages": [],
               "investment_debate_state": {
                   "bull_history": "BULL", "bear_history": "BEAR", "judge_decision": "RM"},
               "investment_plan": "RM PLAN"}
        yield {"messages": [], "trader_investment_plan": "TRADE PLAN"}
        yield {"messages": [],
               "risk_debate_state": {
                   "aggressive_history": "A", "neutral_history": "N",
                   "conservative_history": "C", "judge_decision": "PM"},
               "final_trade_decision": "**Rating**: Buy\nGo long."}


class _FakeGraph:
    def __init__(self, selected_analysts=None, debug=False, config=None, callbacks=None):
        self.propagator = _FakePropagator()
        self.graph = _FakeInner()

    def resolve_instrument_context(self, ticker, asset_type):
        return {}

    def process_signal(self, text):
        return "Buy"


def _parse_sse(text):
    events = []
    for block in text.split("\n\n"):
        if not block.strip():
            continue
        ev, data = "message", ""
        for line in block.split("\n"):
            if line.startswith("event:"):
                ev = line[6:].strip()
            elif line.startswith("data:"):
                data += line[5:].strip()
        events.append((ev, data))
    return events


@pytest.mark.unit
def test_sse_run_streams_full_pipeline(monkeypatch, tmp_path):
    written = {}

    def fake_writer(final_state, ticker, save_path):
        written["state"] = final_state
        written["ticker"] = ticker
        return save_path / "complete_report.md"

    monkeypatch.setattr(server, "TradingAgentsGraph", _FakeGraph)
    monkeypatch.setattr(server, "write_report_tree", fake_writer)
    # keep report writing inside the tmp dir
    monkeypatch.setenv("TRADINGAGENTS_RESULTS_DIR", str(tmp_path))

    client = TestClient(server.app)
    resp = client.post(
        "/api/run",
        json={
            "ticker": "AAPL",
            "analysis_date": "2026-01-15",
            "llm_provider": "ollama",
            "shallow_thinker": "qwen3:latest",
            "deep_thinker": "qwen3:latest",
            "research_depth": 1,
            "analysts": ["market"],
        },
    )
    assert resp.status_code == 200
    events = _parse_sse(resp.text)
    names = [e[0] for e in events]

    assert names[0] == "open"
    assert "run" in names
    assert "report" in names
    assert "final" in names
    assert names[-1] == "done"

    import json

    final = json.loads(next(d for n, d in events if n == "final"))
    assert final["decision"] == "Buy"
    assert final["reports"]["final_trade_decision"].startswith("**Rating**: Buy")
    # report writer was called with the merged final state
    assert written["ticker"] == "AAPL"
    assert written["state"]["market_report"].startswith("# Market")


@pytest.mark.unit
def test_sse_run_reports_missing_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    client = TestClient(server.app)
    resp = client.post(
        "/api/run",
        json={
            "ticker": "AAPL",
            "analysis_date": "2026-01-15",
            "llm_provider": "openai",
            "shallow_thinker": "gpt-5.4-mini",
            "deep_thinker": "gpt-5.5",
            "research_depth": 1,
            "analysts": ["market"],
        },
    )
    events = _parse_sse(resp.text)
    err = next((d for n, d in events if n == "error"), None)
    assert err is not None and "OPENAI_API_KEY" in err
