from datetime import date, datetime, timezone

from tradingagents.research_platform.agent_contracts import (
    TradeDirection,
    TradeHorizon,
    TradeSignal,
)
from tradingagents.research_platform.artifact_store import JsonArtifactStore
from tradingagents.research_platform.data_contracts import (
    DataProvenance,
    FundamentalSnapshot,
    InstrumentIdentity,
    NewsItem,
    PriceBar,
)
from tradingagents.research_platform.research_workflow import (
    ResearchWorkflowConfig,
    run_ticker_research,
)
from tradingagents.research_platform.risk_contracts import RiskDecision, RiskPolicy


class FakeProvider:
    name = "fixture"

    def __init__(self):
        self.price_request = None
        self.fundamentals_request = None
        self.news_request = None

    def get_price_bars(self, identity, start, end, *, as_of_date=None):
        self.price_request = (identity, start, end, as_of_date)
        return [
            _bar(identity.symbol, date(2026, 1, 1), 100),
            _bar(identity.symbol, date(2026, 1, 2), 105),
            _bar(identity.symbol, date(2026, 1, 3), 110),
            _bar(identity.symbol, date(2026, 1, 5), 120),
        ]

    def get_fundamentals(self, identity, *, as_of_date=None):
        self.fundamentals_request = (identity, as_of_date)
        return [
            FundamentalSnapshot(
                symbol=identity.symbol,
                period_end=date(2026, 1, 5),
                fiscal_period="snapshot",
                currency="USD",
                metrics={"market_cap": 3000000000000, "pe_ratio_ttm": 42.5},
                provenance=_provenance(identity.symbol, date(2026, 1, 5)),
            )
        ]

    def get_news(self, identity, start, end, *, as_of_date=None):
        self.news_request = (identity, start, end, as_of_date)
        return [
            NewsItem(
                symbol=identity.symbol,
                title="Nvidia launches platform",
                published_at=datetime(2026, 1, 4, 15, 30, tzinfo=timezone.utc),
                as_of_date=date(2026, 1, 5),
                provider="Example News",
                url="https://example.com/nvda",
                summary="Summary.",
                source_id="news-1",
            )
        ]


class EmptyProvider:
    name = "empty"

    def get_price_bars(self, identity, start, end, *, as_of_date=None):
        return []

    def get_fundamentals(self, identity, *, as_of_date=None):
        return []

    def get_news(self, identity, start, end, *, as_of_date=None):
        return []


def _provenance(symbol: str, day: date) -> DataProvenance:
    return DataProvenance(
        provider="fixture",
        as_of_date=day,
        retrieved_at=datetime(2026, 1, 5, tzinfo=timezone.utc),
        source="fixture",
        vendor_symbol=symbol,
    )


def _bar(symbol: str, day: date, close: float) -> PriceBar:
    return PriceBar(
        symbol=symbol,
        date=day,
        open=close,
        high=close,
        low=close,
        close=close,
        adjusted_close=close,
        volume=1000,
        currency="USD",
        provenance=_provenance(symbol, day),
    )


def _signal() -> TradeSignal:
    return TradeSignal(
        symbol="NVDA",
        as_of_date=date(2026, 1, 2),
        direction=TradeDirection.BUY,
        horizon=TradeHorizon.MEDIUM,
        confidence=0.8,
        rationale="Fixture signal.",
        proposed_position_pct=0.20,
    )


def test_run_ticker_research_fetches_stores_reviews_backtests_and_writes_report(tmp_path):
    provider = FakeProvider()
    store = JsonArtifactStore(tmp_path / "cache")

    result = run_ticker_research(
        config=ResearchWorkflowConfig(
            symbol="NVDA",
            as_of_date=date(2026, 1, 5),
            lookback_days=4,
            initial_cash=1000,
        ),
        provider=provider,
        store=store,
        signal=_signal(),
        risk_policy=RiskPolicy(max_single_position_pct=0.10),
        output_dir=tmp_path / "reports",
    )

    identity, start, end, as_of = provider.price_request
    assert isinstance(identity, InstrumentIdentity)
    assert start == date(2026, 1, 1)
    assert end == date(2026, 1, 5)
    assert as_of == date(2026, 1, 5)
    assert result.report_path is not None
    assert result.report_path.exists()
    assert "## Market Snapshot" in result.markdown
    assert "## Risk Review" in result.markdown
    assert result.bundle.risk_review.decision == RiskDecision.REDUCE
    assert result.bundle.backtest_result.trades
    assert len(result.bundle.agent_outputs) == 5
    assert store.load_agent_outputs("NVDA", as_of_date=date(2026, 1, 5))
    assert store.load_price_bars("NVDA", date(2026, 1, 1), date(2026, 1, 5))


def test_run_ticker_research_without_signal_still_returns_data_report():
    result = run_ticker_research(
        config=ResearchWorkflowConfig(
            symbol="NVDA",
            as_of_date=date(2026, 1, 5),
            lookback_days=4,
        ),
        provider=FakeProvider(),
    )

    assert result.report_path is None
    assert result.bundle.signal is None
    assert result.bundle.risk_review is None
    assert result.bundle.backtest_result is None
    assert len(result.bundle.analyst_notes) == 3
    assert len(result.bundle.agent_outputs) == 4
    assert result.bundle.thesis is not None


def test_run_ticker_research_handles_empty_provider():
    result = run_ticker_research(
        config=ResearchWorkflowConfig(
            symbol="NVDA",
            as_of_date=date(2026, 1, 5),
            lookback_days=4,
        ),
        provider=EmptyProvider(),
    )

    assert result.bundle.price_bars == []
    assert result.bundle.analyst_notes == []
    assert result.bundle.agent_outputs == []
    assert result.bundle.thesis is None
    assert "No normalized price bars available." in result.markdown
