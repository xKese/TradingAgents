import inspect
import json
from pathlib import Path

import pytest

import cli.main as main
from tradingagents.ibkr import IBKRPortfolioError


def _snapshot():
    return {
        "base_currency": "AUD",
        "net_liquidation": 10000.0,
        "cash": 2000.0,
        "gross_position_value": 8000.0,
        "position_fetch_complete": True,
        "positions": [{"symbol": "OUST", "quantity": 10}],
    }


def test_batch_command_exposes_live_tws_options():
    signature = inspect.signature(main.batch_analyze)
    assert signature.parameters["ibkr_context"].default.default is False
    assert signature.parameters["ibkr_host"].default.default == "127.0.0.1"
    assert signature.parameters["ibkr_port"].default.default == 7496
    assert signature.parameters["ibkr_client_id"].default.default == 71


def test_ibkr_preflight_loads_once_and_saves_sanitized_snapshot(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(
        main,
        "load_portfolio_snapshot",
        lambda host, port, client_id: calls.append((host, port, client_id)) or _snapshot(),
    )

    snapshot = main._prepare_ibkr_context(
        enabled=True,
        host="127.0.0.1",
        port=7496,
        client_id=71,
        batch_dir=tmp_path,
    )

    assert calls == [("127.0.0.1", 7496, 71)]
    assert snapshot is not None
    saved = json.loads((tmp_path / "portfolio_snapshot.json").read_text(encoding="utf-8"))
    assert saved["positions"][0]["symbol"] == "OUST"
    assert "account_id" not in saved


def test_ibkr_preflight_failure_happens_before_graph_construction(monkeypatch, tmp_path):
    monkeypatch.setattr(
        main,
        "load_portfolio_snapshot",
        lambda *args: (_ for _ in ()).throw(IBKRPortfolioError("TWS unavailable")),
    )
    constructed = []
    monkeypatch.setattr(main, "TradingAgentsGraph", lambda *args, **kwargs: constructed.append(1))

    with pytest.raises(IBKRPortfolioError, match="TWS unavailable"):
        main._prepare_ibkr_context(True, "127.0.0.1", 7496, 71, tmp_path)

    assert constructed == []


def test_batch_shares_snapshot_and_writes_portfolio_review(monkeypatch, tmp_path):
    snapshot = _snapshot()
    seen_contexts = []
    reviewed = {}

    class FakeGraph:
        def __init__(self, *args, **kwargs):
            self.deep_thinking_llm = object()

        def propagate(self, ticker, analysis_date, asset_type="stock", portfolio_context=None):
            seen_contexts.append(portfolio_context)
            return {"final_trade_decision": f"Decision {ticker}"}, f"Signal-{ticker}"

        def save_reports(self, final_state, ticker, save_path=None):
            path = Path(save_path) / "complete_report.md"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(final_state["final_trade_decision"], encoding="utf-8")
            return path

    monkeypatch.setattr(main, "TradingAgentsGraph", FakeGraph)
    monkeypatch.setattr(
        main,
        "build_portfolio_review",
        lambda snap, rows, decisions, llm: reviewed.update(
            snapshot=snap, rows=rows, decisions=decisions, llm=llm
        )
        or "REVIEW",
    )
    monkeypatch.setattr(
        main,
        "write_portfolio_review",
        lambda review, batch_dir: (
            Path(batch_dir) / "portfolio_review.md",
            Path(batch_dir) / "portfolio_actions.csv",
        ),
    )

    rows = main._run_batch_analysis(
        tickers=["OUST", "HIMS"],
        analysis_date="2026-07-09",
        analyst_keys=["market", "news"],
        config={"results_dir": str(tmp_path)},
        batch_dir=tmp_path / "batch",
        portfolio_context=snapshot,
    )

    assert [row["status"] for row in rows] == ["success", "success"]
    assert seen_contexts == [snapshot, snapshot]
    assert seen_contexts[0] is seen_contexts[1]
    assert reviewed["decisions"] == {"OUST": "Decision OUST", "HIMS": "Decision HIMS"}


def test_technical_only_rejects_portfolio_context(tmp_path):
    with pytest.raises(ValueError, match="full decision-stage analysis"):
        main._run_batch_analysis(
            tickers=["OUST"],
            analysis_date="2026-07-09",
            analyst_keys=["market"],
            config={"results_dir": str(tmp_path)},
            batch_dir=tmp_path / "batch",
            technical_only=True,
            portfolio_context=_snapshot(),
        )
