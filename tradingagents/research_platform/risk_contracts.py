"""Deterministic risk review contracts for validated trade signals."""

from __future__ import annotations

from datetime import date
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from .agent_contracts import TradeDirection, TradeSignal


class RiskDecision(str, Enum):
    APPROVE = "approve"
    REDUCE = "reduce"
    REJECT = "reject"
    WATCH = "watch"


class RiskPolicy(BaseModel):
    """Personal portfolio-level risk policy."""

    model_config = ConfigDict(frozen=True)

    max_single_position_pct: float = Field(default=0.10, gt=0.0, le=1.0)
    default_position_pct: float = Field(default=0.03, ge=0.0, le=1.0)
    min_signal_confidence: float = Field(default=0.55, ge=0.0, le=1.0)
    max_portfolio_drawdown_pct: float = Field(default=0.15, ge=0.0, le=1.0)
    max_realized_volatility_pct: float | None = Field(default=None, gt=0.0)


class RiskLimitBreach(BaseModel):
    """One breached risk rule."""

    model_config = ConfigDict(frozen=True)

    rule: str = Field(min_length=1)
    observed: float | str
    limit: float | str
    message: str = Field(min_length=1)


class RiskReview(BaseModel):
    """Final deterministic risk artifact for a signal."""

    model_config = ConfigDict(frozen=True)

    symbol: str = Field(min_length=1)
    as_of_date: date
    decision: RiskDecision
    approved_position_pct: float = Field(ge=0.0, le=1.0)
    breaches: list[RiskLimitBreach] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


def evaluate_basic_risk(
    signal: TradeSignal,
    policy: RiskPolicy,
    *,
    current_position_pct: float = 0.0,
    portfolio_drawdown_pct: float = 0.0,
    realized_volatility_pct: float | None = None,
) -> RiskReview:
    """Apply a small deterministic risk policy to a validated signal."""

    breaches: list[RiskLimitBreach] = []
    notes: list[str] = []

    proposed = signal.proposed_position_pct
    if proposed is None:
        proposed = 0.0 if signal.direction == TradeDirection.HOLD else policy.default_position_pct

    approved = min(proposed, policy.max_single_position_pct)
    decision = RiskDecision.APPROVE

    if signal.direction == TradeDirection.HOLD:
        return RiskReview(
            symbol=signal.symbol,
            as_of_date=signal.as_of_date,
            decision=RiskDecision.WATCH,
            approved_position_pct=current_position_pct,
            notes=["Hold signal does not add portfolio risk."],
        )

    if signal.confidence < policy.min_signal_confidence:
        breaches.append(
            RiskLimitBreach(
                rule="min_signal_confidence",
                observed=signal.confidence,
                limit=policy.min_signal_confidence,
                message="Signal confidence is below the risk policy floor.",
            )
        )
        decision = RiskDecision.REJECT
        approved = 0.0

    if portfolio_drawdown_pct > policy.max_portfolio_drawdown_pct:
        breaches.append(
            RiskLimitBreach(
                rule="max_portfolio_drawdown_pct",
                observed=portfolio_drawdown_pct,
                limit=policy.max_portfolio_drawdown_pct,
                message="Portfolio drawdown limit is breached.",
            )
        )
        decision = RiskDecision.REJECT
        approved = 0.0

    if proposed > policy.max_single_position_pct and decision != RiskDecision.REJECT:
        breaches.append(
            RiskLimitBreach(
                rule="max_single_position_pct",
                observed=proposed,
                limit=policy.max_single_position_pct,
                message="Proposed position exceeds single-name cap.",
            )
        )
        decision = RiskDecision.REDUCE
        notes.append("Position capped at policy maximum.")

    if (
        realized_volatility_pct is not None
        and policy.max_realized_volatility_pct is not None
        and realized_volatility_pct > policy.max_realized_volatility_pct
        and decision != RiskDecision.REJECT
    ):
        breaches.append(
            RiskLimitBreach(
                rule="max_realized_volatility_pct",
                observed=realized_volatility_pct,
                limit=policy.max_realized_volatility_pct,
                message="Realized volatility is above the policy limit.",
            )
        )
        decision = RiskDecision.REDUCE
        approved = min(approved, policy.default_position_pct)
        notes.append("Volatility breach reduces position to the default size.")

    return RiskReview(
        symbol=signal.symbol,
        as_of_date=signal.as_of_date,
        decision=decision,
        approved_position_pct=approved,
        breaches=breaches,
        notes=notes,
    )
