"""Validated artifacts produced by research agents."""

from __future__ import annotations

from datetime import date
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class ConfidenceLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class TradeDirection(str, Enum):
    BUY = "buy"
    HOLD = "hold"
    SELL = "sell"


class TradeHorizon(str, Enum):
    SHORT = "short"
    MEDIUM = "medium"
    LONG = "long"


class ThesisScenario(str, Enum):
    BULL = "bull"
    BASE = "base"
    BEAR = "bear"


class EvidenceRef(BaseModel):
    """Reference to a deterministic data artifact or cited source."""

    model_config = ConfigDict(frozen=True)

    source_id: str = Field(min_length=1)
    description: str = Field(min_length=1)
    as_of_date: date
    confidence: float = Field(ge=0.0, le=1.0)


class AnalystNote(BaseModel):
    """Structured analyst observation used by downstream research flows."""

    model_config = ConfigDict(frozen=True)

    symbol: str = Field(min_length=1)
    analyst_role: str = Field(min_length=1)
    as_of_date: date
    summary: str = Field(min_length=1)
    evidence: list[EvidenceRef] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    confidence: ConfidenceLevel = ConfidenceLevel.MEDIUM


class InvestmentThesis(BaseModel):
    """Bull/base/bear thesis package for a ticker."""

    model_config = ConfigDict(frozen=True)

    symbol: str = Field(min_length=1)
    as_of_date: date
    base_case: str = Field(min_length=1)
    bull_case: str = Field(min_length=1)
    bear_case: str = Field(min_length=1)
    catalysts: list[str] = Field(default_factory=list)
    disconfirming_evidence: list[str] = Field(default_factory=list)
    evidence: list[EvidenceRef] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)


class TradeSignal(BaseModel):
    """Machine-consumable signal produced from a validated thesis."""

    model_config = ConfigDict(frozen=True)

    symbol: str = Field(min_length=1)
    as_of_date: date
    direction: TradeDirection
    horizon: TradeHorizon
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str = Field(min_length=1)
    proposed_position_pct: float | None = Field(default=None, ge=0.0, le=1.0)
    expected_return_pct: float | None = None
    stop_loss_pct: float | None = Field(default=None, ge=0.0, le=1.0)
    evidence: list[EvidenceRef] = Field(default_factory=list)
    invalidation_triggers: list[str] = Field(default_factory=list)
