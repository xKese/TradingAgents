from datetime import date, datetime, timedelta, timezone
from json import loads
from threading import Thread
from urllib.request import urlopen

from tradingagents.research_platform.agent_artifacts import agent_output_from_analyst_note
from tradingagents.research_platform.agent_contracts import (
    AnalystNote,
    ConfidenceLevel,
    TradeDirection,
    TradeHorizon,
    TradeSignal,
)
from tradingagents.research_platform.artifact_store import JsonArtifactStore
from tradingagents.research_platform.backtest_contracts import (
    BacktestConfig,
    BacktestMetrics,
    BacktestResult,
)
from tradingagents.research_platform.cockpit import (
    build_cockpit_snapshot,
    create_cockpit_server,
    discover_cached_symbols,
)
from tradingagents.research_platform.data_contracts import (
    DataProvenance,
    FundamentalSnapshot,
    NewsItem,
    PriceBar,
)
from tradingagents.research_platform.research_report import ResearchReportBundle
from tradingagents.research_platform.risk_contracts import RiskDecision, RiskReview
from tradingagents.research_platform.run_archive import JsonResearchRunArchive
from tradingagents.research_platform.watchlist import JsonWatchlistStore


def _provenance() -> DataProvenance:
    return DataProvenance(
        provider="fixture",
        as_of_date=date(2026, 1, 5),
        retrieved_at=datetime(2026, 1, 5, tzinfo=timezone.utc),
    )


def test_cockpit_discovers_symbols_and_builds_local_snapshot(tmp_path):
    store = JsonArtifactStore(tmp_path)
    store.save_price_bars(
        [
            PriceBar(
                symbol="NVDA",
                date=date(2026, 1, 2),
                open=100,
                high=102,
                low=99,
                close=101,
                volume=100,
                currency="USD",
                provenance=_provenance(),
            ),
            PriceBar(
                symbol="NVDA",
                date=date(2026, 1, 5),
                open=101,
                high=106,
                low=100,
                close=105,
                volume=200,
                currency="USD",
                provenance=_provenance(),
            ),
        ]
    )
    store.save_fundamentals(
        [
            FundamentalSnapshot(
                symbol="NVDA",
                period_end=date(2026, 1, 5),
                currency="USD",
                metrics={"market_cap": 3_000_000, "pe_ratio_ttm": 42.5},
                provenance=_provenance(),
            )
        ]
    )
    store.save_news(
        [
            NewsItem(
                symbol="NVDA",
                title="New platform announced",
                published_at=datetime(2026, 1, 5, 14, tzinfo=timezone.utc),
                as_of_date=date(2026, 1, 5),
                provider="fixture-news",
                source_id="news-1",
            )
        ]
    )
    store.save_agent_outputs(
        [
            agent_output_from_analyst_note(
                AnalystNote(
                    symbol="NVDA",
                    analyst_role="Market Analyst",
                    as_of_date=date(2026, 1, 5),
                    summary="Trend is constructive.",
                    confidence=ConfidenceLevel.HIGH,
                )
            )
        ]
    )

    snapshot = build_cockpit_snapshot(store, "nvda")

    assert discover_cached_symbols(store) == ["NVDA"]
    assert snapshot["symbol"] == "NVDA"
    assert snapshot["has_data"] is True
    assert snapshot["market"]["last_close"] == 105
    assert snapshot["market"]["period_return_pct"] == 105 / 101 - 1
    assert snapshot["fundamentals"]["metrics"]["pe_ratio_ttm"] == 42.5
    assert snapshot["news"][0]["title"] == "New platform announced"
    assert snapshot["agent_outputs"][0]["agent_role"] == "Market Analyst"


def test_cockpit_snapshot_has_clear_empty_state(tmp_path):
    snapshot = build_cockpit_snapshot(JsonArtifactStore(tmp_path), "MSFT")

    assert snapshot["has_data"] is False
    assert snapshot["market"] is None
    assert snapshot["fundamentals"] is None
    assert snapshot["artifact_counts"] == {
        "price_bars": 0,
        "fundamental_snapshots": 0,
        "news_items": 0,
        "agent_outputs": 0,
    }


def test_cockpit_includes_latest_archived_decision_and_backtest(tmp_path):
    archive = JsonResearchRunArchive(tmp_path)
    archive.save_bundle(
        ResearchReportBundle(
            symbol="NVDA",
            as_of_date=datetime(2026, 1, 5, tzinfo=timezone.utc),
            generated_at=datetime(2026, 1, 5, 12, tzinfo=timezone.utc),
            signal=TradeSignal(
                symbol="NVDA",
                as_of_date=date(2026, 1, 5),
                direction=TradeDirection.BUY,
                horizon=TradeHorizon.MEDIUM,
                confidence=0.8,
                rationale="Fixture signal.",
                proposed_position_pct=0.05,
            ),
            risk_review=RiskReview(
                symbol="NVDA",
                as_of_date=date(2026, 1, 5),
                decision=RiskDecision.APPROVE,
                approved_position_pct=0.05,
            ),
            backtest_result=BacktestResult(
                config=BacktestConfig(
                    start_date=date(2026, 1, 1),
                    end_date=date(2026, 1, 5),
                    initial_cash=1000,
                    symbols=["NVDA"],
                ),
                metrics=BacktestMetrics(total_return_pct=0.08, max_drawdown_pct=0.03),
            ),
        )
    )

    snapshot = build_cockpit_snapshot(JsonArtifactStore(tmp_path), "NVDA")

    assert discover_cached_symbols(JsonArtifactStore(tmp_path)) == ["NVDA"]
    assert snapshot["has_data"] is True
    assert snapshot["latest_run"]["signal"]["direction"] == "buy"
    assert snapshot["latest_run"]["risk_review"]["decision"] == "approve"
    assert snapshot["latest_run"]["backtest"]["metrics"]["total_return_pct"] == 0.08


def test_cockpit_combines_watchlist_symbols_and_selects_archived_run(tmp_path):
    store = JsonArtifactStore(tmp_path)
    watchlist = JsonWatchlistStore(tmp_path)
    watchlist.add("MSFT")
    archive = JsonResearchRunArchive(tmp_path)
    older = archive.save_bundle(
        ResearchReportBundle(
            symbol="NVDA",
            as_of_date=datetime(2026, 1, 4, tzinfo=timezone.utc),
            generated_at=datetime(2026, 1, 4, 12, tzinfo=timezone.utc),
        )
    )
    archive.save_bundle(
        ResearchReportBundle(
            symbol="NVDA",
            as_of_date=datetime(2026, 1, 5, tzinfo=timezone.utc),
            generated_at=datetime(2026, 1, 5, 12, tzinfo=timezone.utc),
        )
    )

    snapshot = build_cockpit_snapshot(store, "NVDA", run_id=older.run_id)

    assert discover_cached_symbols(store, watchlist) == ["MSFT", "NVDA"]
    assert snapshot["latest_run"]["run_id"] == older.run_id
    assert snapshot["latest_run"]["as_of_date"] == "2026-01-04T00:00:00+00:00"
    assert len(snapshot["runs"]) == 2


def test_cockpit_posts_selected_narrative_mode():
    from tradingagents.research_platform.cockpit import _APP_HTML

    assert 'id="valuationContext"' in _APP_HTML
    assert 'id="financialQuality"' in _APP_HTML
    assert 'id="financialTrend"' in _APP_HTML
    assert 'id="dataProvider"' in _APP_HTML
    assert 'value="tushare"' in _APP_HTML
    assert "data_provider: $('dataProvider').value" in _APP_HTML
    assert 'id="narrativeMode"' in _APP_HTML
    assert 'value="openai_narrative"' in _APP_HTML
    assert "narrative_mode: $('narrativeMode').value" in _APP_HTML


def test_cockpit_serves_and_exports_archived_markdown_report(tmp_path):
    archive = JsonResearchRunArchive(tmp_path)
    summary = archive.save_bundle(
        ResearchReportBundle(
            symbol="NVDA",
            as_of_date=datetime(2026, 1, 5, tzinfo=timezone.utc),
            generated_at=datetime(2026, 1, 5, 12, tzinfo=timezone.utc),
        )
    )
    JsonWatchlistStore(tmp_path).add("NVDA")
    server = create_cockpit_server(tmp_path, port=0)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    url = f"http://{host}:{port}/api/reports/NVDA/{summary.run_id}.md"

    try:
        with urlopen(url, timeout=2) as response:
            report = response.read().decode("utf-8")
            assert response.headers["Content-Type"].startswith("text/markdown")
        with urlopen(url + "?download=1", timeout=2) as response:
            assert response.headers["Content-Disposition"].startswith("attachment;")
        with urlopen(f"http://{host}:{port}/api/watchlist-board", timeout=2) as response:
            board = loads(response.read().decode("utf-8"))
            assert board["total"] == 1
            assert board["items"][0]["symbol"] == "NVDA"
            assert board["researched"] == 1
    finally:
        server.shutdown()
        server.server_close()
        server.RequestHandlerClass.jobs.shutdown()

    assert "# Personal Research Report: NVDA" in report


def test_cockpit_exposes_latest_financial_quality_snapshot(tmp_path):
    store = JsonArtifactStore(tmp_path)
    store.save_fundamentals(
        [
            FundamentalSnapshot(
                symbol="600519",
                period_end=date(2025, 12, 31),
                fiscal_period="financial_report_2025-12-31",
                currency="CNY",
                metrics={"return_on_equity_pct": 15.0},
                provenance=_provenance(),
            )
        ]
    )

    snapshot = build_cockpit_snapshot(store, "600519")

    assert snapshot["financial_quality"]["period_end"] == "2025-12-31"
    assert snapshot["financial_quality"]["metrics"]["return_on_equity_pct"] == 15.0
    assert snapshot["financial_health"]["status"] == "watch"
    assert snapshot["financial_health"]["score"] == 1
    assert len(snapshot["financial_quality_history"]) == 1


def test_cockpit_exposes_historical_valuation_context(tmp_path):
    store = JsonArtifactStore(tmp_path)
    store.save_fundamentals(
        [
            FundamentalSnapshot(
                symbol="600519",
                period_end=date(2026, 1, 1) + timedelta(days=index),
                fiscal_period="daily_snapshot",
                metrics={
                    "pe_ratio_ttm": float(index + 10),
                    "price_to_book": 3.0,
                    "price_to_sales_ttm": 2.0,
                    "dividend_yield_pct": 2.0,
                },
                provenance=_provenance(),
            )
            for index in range(20)
        ]
    )

    snapshot = build_cockpit_snapshot(store, "600519")
    pe = snapshot["valuation_context"]["metrics"][0]

    assert snapshot["valuation_context"]["available"] is True
    assert snapshot["valuation_context"]["daily_snapshot_count"] == 20
    assert pe["latest"] == 29.0
    assert pe["percentile"] == 100.0
