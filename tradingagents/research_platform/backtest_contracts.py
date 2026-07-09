"""Backtest inputs and outputs for validated trade signals."""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .agent_contracts import TradeDirection, TradeSignal


class ExecutionConfig(BaseModel):
    """Execution assumptions for daily-bar simulation."""

    model_config = ConfigDict(frozen=True)

    commission_bps: float = Field(default=0.0, ge=0.0)
    slippage_bps: float = Field(default=0.0, ge=0.0)
    allow_short: bool = False
    rebalance_frequency: str = "daily"


class BacktestConfig(BaseModel):
    """Top-level simulation setup."""

    model_config = ConfigDict(frozen=True)

    start_date: date
    end_date: date
    initial_cash: float = Field(default=100_000.0, gt=0.0)
    symbols: list[str] = Field(min_length=1)
    benchmark_symbol: str | None = None
    execution: ExecutionConfig = Field(default_factory=ExecutionConfig)

    @model_validator(mode="after")
    def _dates_are_ordered(self):
        if self.end_date < self.start_date:
            raise ValueError("end_date must be on or after start_date")
        return self


class BacktestTrade(BaseModel):
    """Executed trade in the simulation ledger."""

    model_config = ConfigDict(frozen=True)

    symbol: str = Field(min_length=1)
    date: date
    direction: TradeDirection
    quantity: float
    price: float = Field(gt=0.0)
    notional: float
    commission: float = Field(default=0.0, ge=0.0)
    source_signal_date: date


class EquityPoint(BaseModel):
    """One point on the simulated equity curve."""

    model_config = ConfigDict(frozen=True)

    date: date
    equity: float = Field(ge=0.0)
    cash: float
    gross_exposure_pct: float = Field(ge=0.0)
    net_exposure_pct: float


class BacktestMetrics(BaseModel):
    """Standard metrics for comparing signal quality."""

    model_config = ConfigDict(frozen=True)

    total_return_pct: float
    cagr_pct: float | None = None
    annualized_volatility_pct: float | None = None
    sharpe: float | None = None
    sortino: float | None = None
    max_drawdown_pct: float
    win_rate_pct: float | None = None
    turnover_pct: float | None = None
    average_exposure_pct: float | None = None


class BacktestResult(BaseModel):
    """Complete deterministic simulation result."""

    model_config = ConfigDict(frozen=True)

    config: BacktestConfig
    metrics: BacktestMetrics
    trades: list[BacktestTrade] = Field(default_factory=list)
    equity_curve: list[EquityPoint] = Field(default_factory=list)
    assumptions: dict[str, str | float | int | bool | None] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


def validate_signal_timing(
    signal: TradeSignal,
    execution_date: date,
    *,
    allow_same_day: bool = False,
) -> None:
    """Reject signals that would require future information in a backtest."""

    if allow_same_day:
        invalid = signal.as_of_date > execution_date
    else:
        invalid = signal.as_of_date >= execution_date
    if invalid:
        raise ValueError(
            "signal.as_of_date must be before execution_date "
            "unless allow_same_day=True"
        )
