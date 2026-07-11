from datetime import date, datetime, timezone

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
from tradingagents.research_platform.cockpit import build_cockpit_snapshot, discover_cached_symbols
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

    assert 'id="narrativeMode"' in _APP_HTML
    assert 'value="openai_narrative"' in _APP_HTML
    assert 'narrative_mode: $(\'narrativeMode\').value' in _APP_HTML
