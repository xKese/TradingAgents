"""End-to-end personal ticker research workflow."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from .agent_artifacts import (
    agent_output_from_analyst_note,
    agent_output_from_investment_thesis,
    agent_output_from_trade_signal,
)
from .agent_contracts import (
    AgentOutputEnvelope,
    AnalystNote,
    ConfidenceLevel,
    EvidenceRef,
    InvestmentThesis,
    TradeSignal,
)
from .artifact_store import ArtifactStore
from .backtest_contracts import BacktestConfig, BacktestResult, ExecutionConfig
from .backtest_engine import run_daily_signal_backtest
from .data_contracts import (
    DataProvider,
    FundamentalSnapshot,
    InstrumentIdentity,
    NewsItem,
    PriceBar,
)
from .narrative_provider import (
    ResearchNarrativeContext,
    ResearchNarrativeProvider,
    build_narrative_evidence,
    validate_narrative_outputs,
)
from .research_report import (
    ResearchReportBundle,
    render_research_report,
    write_research_report,
)
from .risk_contracts import RiskPolicy, RiskReview, evaluate_basic_risk
from .run_archive import ResearchRunArchive, ResearchRunSummary


class ResearchWorkflowConfig(BaseModel):
    """Configuration for one local ticker research run."""

    model_config = ConfigDict(frozen=True)

    symbol: str = Field(min_length=1)
    as_of_date: date
    lookback_days: int = Field(default=90, ge=1)
    currency: str | None = None
    initial_cash: float = Field(default=100_000.0, gt=0)
    execution: ExecutionConfig = Field(default_factory=ExecutionConfig)
    current_position_pct: float = Field(default=0.0, ge=0.0, le=1.0)
    portfolio_drawdown_pct: float = Field(default=0.0, ge=0.0, le=1.0)
    realized_volatility_pct: float | None = Field(default=None, gt=0.0)


class ResearchWorkflowResult(BaseModel):
    """Outputs from one workflow run."""

    model_config = ConfigDict(frozen=True)

    bundle: ResearchReportBundle
    markdown: str
    report_path: Path | None = None
    archived_run: ResearchRunSummary | None = None


def run_ticker_research(
    *,
    config: ResearchWorkflowConfig,
    provider: DataProvider,
    store: ArtifactStore | None = None,
    signal: TradeSignal | None = None,
    risk_policy: RiskPolicy | None = None,
    output_dir: str | Path | None = None,
    archive: ResearchRunArchive | None = None,
    narrative_provider: ResearchNarrativeProvider | None = None,
) -> ResearchWorkflowResult:
    """Fetch data, build artifacts, optionally review/backtest, and render a report."""

    identity = InstrumentIdentity(symbol=config.symbol, currency=config.currency)
    start_date = config.as_of_date - timedelta(days=config.lookback_days)

    price_bars = list(
        provider.get_price_bars(
            identity,
            start_date,
            config.as_of_date,
            as_of_date=config.as_of_date,
        )
    )
    fundamentals = list(provider.get_fundamentals(identity, as_of_date=config.as_of_date))
    news = list(
        provider.get_news(
            identity,
            start_date,
            config.as_of_date,
            as_of_date=config.as_of_date,
        )
    )

    if store is not None:
        store.save_price_bars(price_bars)
        store.save_fundamentals(fundamentals)
        store.save_news(news)

    analyst_notes = build_deterministic_notes(
        symbol=config.symbol,
        as_of_date=config.as_of_date,
        price_bars=price_bars,
        fundamentals=fundamentals,
        news=news,
    )
    thesis = build_deterministic_thesis(
        symbol=config.symbol,
        as_of_date=config.as_of_date,
        notes=analyst_notes,
    )
    agent_outputs = build_agent_outputs(
        analyst_notes=analyst_notes,
        thesis=thesis,
        signal=signal,
    )
    if narrative_provider is not None:
        context = ResearchNarrativeContext(
            symbol=config.symbol,
            as_of_date=config.as_of_date,
            price_bars=price_bars,
            fundamentals=fundamentals,
            news=news,
            evidence=build_narrative_evidence(
                symbol=config.symbol,
                as_of_date=config.as_of_date,
                price_bars=price_bars,
                fundamentals=fundamentals,
                news=news,
            ),
        )
        agent_outputs.extend(
            validate_narrative_outputs(context, narrative_provider.generate(context))
        )
    if store is not None:
        store.save_agent_outputs(agent_outputs)

    risk_review: RiskReview | None = None
    backtest_result: BacktestResult | None = None
    if signal is not None:
        policy = risk_policy or RiskPolicy()
        risk_review = evaluate_basic_risk(
            signal,
            policy,
            current_position_pct=config.current_position_pct,
            portfolio_drawdown_pct=config.portfolio_drawdown_pct,
            realized_volatility_pct=config.realized_volatility_pct,
        )
        backtest_signal = signal.model_copy(
            update={"proposed_position_pct": risk_review.approved_position_pct}
        )
        backtest_result = run_daily_signal_backtest(
            config=BacktestConfig(
                start_date=start_date,
                end_date=config.as_of_date,
                initial_cash=config.initial_cash,
                symbols=[config.symbol],
                execution=config.execution,
            ),
            price_bars=price_bars,
            signals=[backtest_signal],
        )

    bundle = ResearchReportBundle(
        symbol=config.symbol,
        as_of_date=datetime.combine(config.as_of_date, time.min, tzinfo=timezone.utc),
        price_bars=price_bars,
        fundamentals=fundamentals,
        news=news,
        agent_outputs=agent_outputs,
        analyst_notes=analyst_notes,
        thesis=thesis,
        signal=signal,
        risk_review=risk_review,
        backtest_result=backtest_result,
    )
    markdown = render_research_report(bundle)
    report_path = write_research_report(bundle, output_dir) if output_dir is not None else None
    archived_run = archive.save_bundle(bundle) if archive is not None else None
    return ResearchWorkflowResult(
        bundle=bundle,
        markdown=markdown,
        report_path=report_path,
        archived_run=archived_run,
    )


def build_agent_outputs(
    *,
    analyst_notes: Sequence[AnalystNote],
    thesis: InvestmentThesis | None,
    signal: TradeSignal | None = None,
) -> list[AgentOutputEnvelope]:
    """Create standard envelopes for all agent-like workflow artifacts."""

    outputs = [agent_output_from_analyst_note(note) for note in analyst_notes]
    if thesis is not None:
        outputs.append(agent_output_from_investment_thesis(thesis))
    if signal is not None:
        outputs.append(agent_output_from_trade_signal(signal))
    return outputs

def build_deterministic_notes(
    *,
    symbol: str,
    as_of_date: date,
    price_bars: Sequence[PriceBar],
    fundamentals: Sequence[FundamentalSnapshot],
    news: Sequence[NewsItem],
) -> list[AnalystNote]:
    """Create lightweight deterministic notes from normalized records."""

    notes: list[AnalystNote] = []
    if price_bars:
        notes.append(_market_note(symbol, as_of_date, list(price_bars)))
    if fundamentals:
        notes.append(_fundamentals_note(symbol, as_of_date, list(fundamentals)))
    if news:
        notes.append(_news_note(symbol, as_of_date, list(news)))
    return notes


def build_deterministic_thesis(
    *,
    symbol: str,
    as_of_date: date,
    notes: Sequence[AnalystNote],
) -> InvestmentThesis | None:
    """Create a neutral thesis package from available deterministic notes."""

    if not notes:
        return None

    summaries = "\n\n".join(f"{note.analyst_role}: {note.summary}" for note in notes)
    evidence = [ref for note in notes for ref in note.evidence]
    return InvestmentThesis(
        symbol=symbol,
        as_of_date=as_of_date,
        base_case=summaries,
        bull_case="Bull case requires agent or analyst interpretation beyond deterministic data collection.",
        bear_case="Bear case requires agent or analyst interpretation beyond deterministic data collection.",
        catalysts=[],
        disconfirming_evidence=[],
        evidence=evidence,
        confidence=0.5,
    )


def _market_note(symbol: str, as_of_date: date, price_bars: list[PriceBar]) -> AnalystNote:
    ordered = sorted(price_bars, key=lambda bar: bar.date)
    first = ordered[0]
    last = ordered[-1]
    change = last.close / first.close - 1.0 if first.close else 0.0
    evidence = [
        EvidenceRef(
            source_id=f"price:{symbol}:{first.date.isoformat()}:{last.date.isoformat()}",
            description=f"{len(ordered)} daily bars from {first.date} to {last.date}",
            as_of_date=last.provenance.as_of_date,
            confidence=0.9,
        )
    ]
    return AnalystNote(
        symbol=symbol,
        analyst_role="Market Snapshot",
        as_of_date=as_of_date,
        summary=(
            f"Price moved {_pct(change)} from {first.close:.2f} to {last.close:.2f} "
            f"across {len(ordered)} normalized daily bars."
        ),
        evidence=evidence,
        confidence=ConfidenceLevel.MEDIUM,
    )


def _fundamentals_note(
    symbol: str,
    as_of_date: date,
    fundamentals: list[FundamentalSnapshot],
) -> AnalystNote:
    latest = sorted(
        fundamentals,
        key=lambda item: (item.provenance.as_of_date, item.period_end),
    )[-1]
    metric_names = sorted(latest.metrics)[:6]
    evidence = [
        EvidenceRef(
            source_id=f"fundamentals:{symbol}:{latest.provenance.as_of_date.isoformat()}",
            description=f"{len(latest.metrics)} normalized fundamental metrics",
            as_of_date=latest.provenance.as_of_date,
            confidence=0.85,
        )
    ]
    return AnalystNote(
        symbol=symbol,
        analyst_role="Fundamentals Snapshot",
        as_of_date=as_of_date,
        summary="Latest snapshot includes: " + ", ".join(metric_names) + ".",
        evidence=evidence,
        confidence=ConfidenceLevel.MEDIUM,
    )


def _news_note(symbol: str, as_of_date: date, news: list[NewsItem]) -> AnalystNote:
    ordered = sorted(news, key=lambda item: item.published_at, reverse=True)
    latest_titles = [item.title for item in ordered[:3]]
    evidence = [
        EvidenceRef(
            source_id=f"news:{symbol}:{as_of_date.isoformat()}",
            description=f"{len(news)} normalized news items",
            as_of_date=as_of_date,
            confidence=0.75,
        )
    ]
    return AnalystNote(
        symbol=symbol,
        analyst_role="News Snapshot",
        as_of_date=as_of_date,
        summary="Recent headlines: " + "; ".join(latest_titles) + ".",
        evidence=evidence,
        confidence=ConfidenceLevel.MEDIUM,
    )


def _pct(value: float) -> str:
    return f"{value * 100:.2f}%"
