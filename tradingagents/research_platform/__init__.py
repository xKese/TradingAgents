"""Contract-first models for the personal research platform migration."""

from .agent_contracts import (
    AnalystNote,
    ConfidenceLevel,
    EvidenceRef,
    InvestmentThesis,
    ThesisScenario,
    TradeDirection,
    TradeHorizon,
    TradeSignal,
)
from .backtest_contracts import (
    BacktestConfig,
    BacktestMetrics,
    BacktestResult,
    BacktestTrade,
    EquityPoint,
    ExecutionConfig,
    validate_signal_timing,
)
from .data_contracts import (
    AssetClass,
    DataProvenance,
    DataProvider,
    FundamentalSnapshot,
    InstrumentIdentity,
    NewsItem,
    PriceBar,
)
from .risk_contracts import (
    RiskDecision,
    RiskLimitBreach,
    RiskPolicy,
    RiskReview,
    evaluate_basic_risk,
)

__all__ = [
    "AnalystNote",
    "AssetClass",
    "BacktestConfig",
    "BacktestMetrics",
    "BacktestResult",
    "BacktestTrade",
    "ConfidenceLevel",
    "DataProvider",
    "DataProvenance",
    "EquityPoint",
    "EvidenceRef",
    "ExecutionConfig",
    "FundamentalSnapshot",
    "InstrumentIdentity",
    "InvestmentThesis",
    "NewsItem",
    "PriceBar",
    "RiskDecision",
    "RiskLimitBreach",
    "RiskPolicy",
    "RiskReview",
    "ThesisScenario",
    "TradeDirection",
    "TradeHorizon",
    "TradeSignal",
    "evaluate_basic_risk",
    "validate_signal_timing",
]
