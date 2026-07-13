"""Typed evidence and claim models shared by analysts and evaluations."""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class DisclosureStatus(str, Enum):
    """How an operational finding relates to public disclosure."""

    REPORTED = "Reported"
    DERIVABLE = "Derivable from reported figures"
    QUALITATIVE = "Qualitative commentary"
    NOT_DISCLOSED = "Not disclosed"
    NOT_APPLICABLE = "Not applicable"


class EvidenceRecord(BaseModel):
    """A compact, source-linked unit of evidence.

    URL and temporal rules intentionally live in deterministic validators rather
    than Pydantic so invalid provider records can be represented, diagnosed, and
    excluded without crashing a run.
    """

    evidence_id: str = ""
    claim_category: str
    source_type: str
    source_title: str
    source_url: str | None = None
    publisher: str | None = None
    publication_date: date | None = None
    filing_date: date | None = None
    reporting_period: str | None = None
    retrieved_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    ticker: str
    short_excerpt: str = Field(default="", max_length=500)
    structured_value: Any | None = None
    unit: str | None = None
    confidence: Literal["low", "medium", "high"] = "medium"
    is_primary_source: bool = False
    analysis_date_valid: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("ticker")
    @classmethod
    def _canonical_ticker(cls, value: str) -> str:
        return value.strip().upper()

    @field_validator("short_excerpt")
    @classmethod
    def _compact_excerpt(cls, value: str) -> str:
        return " ".join(value.split())


class ClaimRecord(BaseModel):
    """A material factual claim and the citations asserted to support it."""

    claim_id: str = ""
    text: str
    claim_category: str
    materiality: Literal["low", "medium", "high"] = "medium"
    citation_ids: list[str] = Field(default_factory=list)
    numerical_value: float | None = None
    unit: str | None = None
    reporting_period: str | None = None
    support_status: Literal["supported", "unsupported", "conflicting", "unusable"] = (
        "unsupported"
    )
    metadata: dict[str, Any] = Field(default_factory=dict)


class ValidationIssue(BaseModel):
    """One deterministic evidence-validation finding."""

    code: str
    severity: Literal["error", "warning"]
    message: str
    evidence_id: str | None = None
    claim_id: str | None = None


class CitationValidationResult(BaseModel):
    """Machine-readable result of deterministic claim/evidence validation."""

    valid: bool
    issues: list[ValidationIssue] = Field(default_factory=list)
    checked_claims: int = 0
    checked_evidence: int = 0
    supported_high_materiality_claims: int = 0
    unsupported_high_materiality_claims: int = 0


class OperationalFinding(BaseModel):
    """One operational observation produced by the analyst."""

    finding: str
    claim_category: str
    disclosure_status: DisclosureStatus
    signal: Literal["Positive", "Negative", "Neutral"] = "Neutral"
    materiality: Literal["low", "medium", "high"] = "medium"
    citation_ids: list[str] = Field(default_factory=list)
    structured_value: float | str | None = None
    unit: str | None = None
    reporting_period: str | None = None


def stable_evidence_id(record: EvidenceRecord | dict[str, Any]) -> str:
    """Return a stable citation ID based on source identity, not retrieval time."""
    data = record.model_dump(mode="json") if isinstance(record, EvidenceRecord) else record
    identity = {
        "ticker": str(data.get("ticker", "")).upper(),
        "source_url": (data.get("source_url") or "").strip().lower(),
        "source_title": " ".join(str(data.get("source_title", "")).lower().split()),
        "publication_date": str(data.get("publication_date") or ""),
        "filing_date": str(data.get("filing_date") or ""),
        "reporting_period": str(data.get("reporting_period") or ""),
    }
    digest = hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:12]
    return f"EVID-{digest.upper()}"


def stable_claim_id(claim: ClaimRecord | dict[str, Any]) -> str:
    """Return a deterministic ID for a claim's normalized content."""
    data = claim.model_dump(mode="json") if isinstance(claim, ClaimRecord) else claim
    identity = {
        "text": " ".join(str(data.get("text", "")).lower().split()),
        "category": str(data.get("claim_category", "")).lower(),
        "period": str(data.get("reporting_period") or ""),
    }
    digest = hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:12]
    return f"CLM-{digest.upper()}"


def finding_to_claim(finding: OperationalFinding) -> ClaimRecord:
    """Convert an analyst finding to the shared claim representation."""
    numerical_value = (
        float(finding.structured_value)
        if isinstance(finding.structured_value, (int, float))
        else None
    )
    claim = ClaimRecord(
        text=finding.finding,
        claim_category=finding.claim_category,
        materiality=finding.materiality,
        citation_ids=finding.citation_ids,
        numerical_value=numerical_value,
        unit=finding.unit,
        reporting_period=finding.reporting_period,
    )
    claim.claim_id = stable_claim_id(claim)
    return claim
