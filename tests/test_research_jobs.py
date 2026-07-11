from datetime import date, datetime, timezone

from tradingagents.research_platform.data_contracts import (
    DataProvenance,
    FundamentalSnapshot,
    NewsItem,
    PriceBar,
)
from tradingagents.research_platform.research_jobs import (
    LocalResearchJobRunner,
    ResearchJobRequest,
    ResearchJobStatus,
)


class FixtureProvider:
    name = "fixture"

    def get_price_bars(self, identity, start, end, *, as_of_date=None):
        return [
            PriceBar(
                symbol=identity.symbol,
                date=date(2026, 1, 2),
                open=100,
                high=104,
                low=99,
                close=103,
                volume=100,
                currency="USD",
                provenance=_provenance(),
            )
        ]

    def get_fundamentals(self, identity, *, as_of_date=None):
        return [
            FundamentalSnapshot(
                symbol=identity.symbol,
                period_end=date(2026, 1, 2),
                metrics={"market_cap": 1_000_000},
                provenance=_provenance(),
            )
        ]

    def get_news(self, identity, start, end, *, as_of_date=None):
        return [
            NewsItem(
                symbol=identity.symbol,
                title="Fixture headline",
                published_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
                as_of_date=date(2026, 1, 2),
                provider="fixture-news",
                source_id="fixture-news-1",
            )
        ]


def _provenance() -> DataProvenance:
    return DataProvenance(
        provider="fixture",
        as_of_date=date(2026, 1, 2),
        retrieved_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
    )


def test_local_job_runner_executes_workflow_archives_and_reports(tmp_path):
    runner = LocalResearchJobRunner(tmp_path, provider_factory=lambda request: FixtureProvider())

    queued = runner.submit(ResearchJobRequest(symbol=" nvda ", as_of_date=date(2026, 1, 2)))
    completed = runner.wait(queued.job_id, timeout=2)

    assert completed.status == ResearchJobStatus.SUCCEEDED
    assert completed.report_path is not None
    assert completed.report_path.exists()
    assert completed.run_id is not None
    assert runner.list_jobs() == [completed]
    runner.shutdown()


def test_local_job_runner_exposes_provider_failures_as_job_state(tmp_path):
    def failing_provider(request):
        raise RuntimeError("fixture provider unavailable")

    runner = LocalResearchJobRunner(tmp_path, provider_factory=failing_provider)

    queued = runner.submit(ResearchJobRequest(symbol="NVDA"))
    completed = runner.wait(queued.job_id, timeout=2)

    assert completed.status == ResearchJobStatus.FAILED
    assert completed.error == "fixture provider unavailable"
    runner.shutdown()
