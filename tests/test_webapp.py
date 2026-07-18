"""Tests for the local web UI: catalog adapters, run-config validation, and the
SSE streaming endpoint (driven by a fake graph so no LLM is needed)."""

import pytest
from langchain_core.messages import AIMessage

# The web UI is an optional extra (pip install ".[web]"). Skip the whole module
# when FastAPI/Starlette aren't installed so the core test suite still passes.
pytest.importorskip("fastapi")
pytest.importorskip("starlette")

from starlette.testclient import TestClient  # noqa: E402

from webapp import catalog, run_config, server  # noqa: E402


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


# --------------------------------------------------------------------------- #
# Report archive (run.json sidecar + list/detail endpoints)
# --------------------------------------------------------------------------- #
import json as _json  # noqa: E402

from tradingagents.default_config import DEFAULT_CONFIG  # noqa: E402

# NOTE: results_dir is resolved from TRADINGAGENTS_RESULTS_DIR at import time,
# so setenv is a no-op here — patch the DEFAULT_CONFIG dict entry instead
# (build_run copies it per request, and the endpoints read it directly).


def _run_payload():
    return {
        "ticker": "AAPL",
        "analysis_date": "2026-01-15",
        "llm_provider": "ollama",
        "shallow_thinker": "qwen3:latest",
        "deep_thinker": "qwen3:latest",
        "research_depth": 1,
        "analysts": ["market"],
    }


def _seed_sidecar(root, run_id, ticker, created_at, body="Bericht"):
    d = root / "reports" / run_id
    d.mkdir(parents=True)
    (d / "run.json").write_text(
        _json.dumps({
            "schema_version": 1,
            "id": run_id,
            "ticker": ticker,
            "analysis_date": "2026-01-15",
            "asset_type": "stock",
            "provider": "ollama",
            "decision": "Buy",
            "created_at": created_at,
            "reports": {"market_report": body},
        }, ensure_ascii=False),
        encoding="utf-8",
    )


@pytest.mark.unit
def test_run_writes_sidecar_archive(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "TradingAgentsGraph", _FakeGraph)
    monkeypatch.setitem(DEFAULT_CONFIG, "results_dir", str(tmp_path))

    client = TestClient(server.app)
    resp = client.post("/api/run", json=_run_payload())
    assert resp.status_code == 200

    sidecars = list((tmp_path / "reports").glob("AAPL_*/run.json"))
    assert len(sidecars) == 1
    data = _json.loads(sidecars[0].read_text(encoding="utf-8"))
    assert data["schema_version"] == 1
    assert data["id"] == sidecars[0].parent.name
    assert data["ticker"] == "AAPL"
    assert data["analysis_date"] == "2026-01-15"
    assert data["decision"] == "Buy"
    # previously-missing final sections must be archived too
    assert data["reports"]["bull_history"] == "BULL"
    assert data["reports"]["bear_history"] == "BEAR"
    assert "A" in data["reports"]["risk_analysis"]

    events = _parse_sse(resp.text)
    final = _json.loads(next(d for n, d in events if n == "final"))
    assert final["run_id"] == data["id"]
    assert final["reports"]["bull_history"] == "BULL"
    assert final["reports"]["risk_analysis"]


@pytest.mark.unit
def test_archive_write_failure_does_not_fail_run(monkeypatch, tmp_path):
    def broken_writer(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(server, "TradingAgentsGraph", _FakeGraph)
    monkeypatch.setattr(server, "write_report_tree", broken_writer)
    monkeypatch.setitem(DEFAULT_CONFIG, "results_dir", str(tmp_path))

    client = TestClient(server.app)
    resp = client.post("/api/run", json=_run_payload())
    events = _parse_sse(resp.text)
    names = [e[0] for e in events]
    assert "error" not in names
    final = _json.loads(next(d for n, d in events if n == "final"))
    assert final["decision"] == "Buy"
    assert final["run_id"] is None
    assert final["report_path"] is None


@pytest.mark.unit
def test_reports_list_endpoint(monkeypatch, tmp_path):
    monkeypatch.setitem(DEFAULT_CONFIG, "results_dir", str(tmp_path))
    _seed_sidecar(tmp_path, "AAPL_20260115_090000", "AAPL",
                  "2026-01-15T09:00:00+01:00")
    _seed_sidecar(tmp_path, "NVDA_20260116_100000", "NVDA",
                  "2026-01-16T10:00:00+01:00", body="Kursziel erhöht — Prognose übertroffen")
    (tmp_path / "reports" / "OLD_20250101_000000").mkdir()  # legacy: no sidecar
    corrupt = tmp_path / "reports" / "BAD_20260101_000000"
    corrupt.mkdir()
    (corrupt / "run.json").write_text("{not json", encoding="utf-8")

    client = TestClient(server.app)
    runs = client.get("/api/reports").json()["runs"]
    assert [r["id"] for r in runs] == ["NVDA_20260116_100000", "AAPL_20260115_090000"]
    assert runs[0]["ticker"] == "NVDA" and runs[0]["decision"] == "Buy"
    assert "reports" not in runs[0]  # list carries summaries only

    detail = client.get("/api/reports/NVDA_20260116_100000").json()
    assert detail["reports"]["market_report"] == "Kursziel erhöht — Prognose übertroffen"


@pytest.mark.unit
def test_report_detail_traversal_guard_and_empty_root(monkeypatch, tmp_path):
    monkeypatch.setitem(DEFAULT_CONFIG, "results_dir", str(tmp_path))

    client = TestClient(server.app)
    # fresh install: no reports dir at all
    assert client.get("/api/reports").json() == {"runs": []}

    _seed_sidecar(tmp_path, "AAPL_20260115_090000", "AAPL", "2026-01-15T09:00:00+01:00")
    assert client.get("/api/reports/AAPL_20260115_090000").json()["ticker"] == "AAPL"

    # Over HTTP: hostile ids must never resolve. ("." and ".." are collapsed
    # away by path normalization before routing, so they are asserted at the
    # handler level below instead.)
    for bad in ("...", "..%2f..%2fetc", "a/b", "AAPL_x!", "NOPE_20990101_000000"):
        r = client.get(f"/api/reports/{bad}")
        assert r.status_code == 404, bad

    # Handler level: the dot-only guard itself (defense in depth — the charset
    # regex alone would admit "." and "..").
    for bad in (".", "..", "..."):
        assert server.api_report(bad).status_code == 404, bad


@pytest.mark.unit
def test_collect_reports_derivations():
    state = {
        "market_report": "MKT",
        "investment_debate_state": {"bull_history": "B+", "bear_history": "B-",
                                    "judge_decision": "JUDGE"},
        "risk_debate_state": {"aggressive_history": "AGG",
                              "conservative_history": "CON",
                              "neutral_history": "NEU"},
    }
    out = server._collect_reports(state)
    assert out["risk_analysis"] == "AGG\n\nCON\n\nNEU"  # fixed join order
    assert out["investment_plan"] == "JUDGE"  # fallback to judge_decision
    assert out["bull_history"] == "B+" and out["bear_history"] == "B-"
    empty = server._collect_reports({})
    assert all(v is None for v in empty.values())
