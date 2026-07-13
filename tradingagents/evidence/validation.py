"""Deterministic citation, identity, URL, and temporal validation."""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Any
from urllib.parse import urlparse

from .models import (
    CitationValidationResult,
    ClaimRecord,
    EvidenceRecord,
    ValidationIssue,
    stable_evidence_id,
)


def is_valid_source_url(url: str | None) -> bool:
    """Return whether ``url`` is an absolute HTTP(S) source URL."""
    if not url:
        return False
    parsed = urlparse(url)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def company_name_matches(
    evidence: EvidenceRecord,
    expected_company_name: str | None,
) -> bool:
    """Reject an explicit company mismatch while allowing absent provider metadata."""
    if not expected_company_name:
        return True
    record_company = str(evidence.metadata.get("company_name", "")).strip()
    if not record_company:
        return True
    return record_company.casefold() == expected_company_name.strip().casefold()


def temporal_validity(
    evidence: EvidenceRecord,
    analysis_date: date,
    *,
    strict: bool = True,
) -> tuple[bool, str]:
    """Check that a source was public on or before ``analysis_date``.

    Filing date and publication date are both treated as public-availability
    controls. Unknown dates are unusable in strict mode; this prevents a live
    page retrieved today from masquerading as historical evidence.
    """
    dated_fields = [
        ("publication_date", evidence.publication_date),
        ("filing_date", evidence.filing_date),
    ]
    known = [(name, value) for name, value in dated_fields if value is not None]
    for name, value in known:
        if value > analysis_date:
            return False, f"{name} {value.isoformat()} is after {analysis_date.isoformat()}"
    if not known and strict:
        return False, "publication and filing dates are both unknown"
    if not known:
        return True, "date unknown; accepted because strict temporal grounding is disabled"
    return True, "source was public on or before the analysis date"


def prepare_evidence(
    records: list[EvidenceRecord | dict[str, Any]],
    analysis_date: date,
    *,
    strict_temporal: bool = True,
) -> list[EvidenceRecord]:
    """Normalize IDs and recompute temporal validity for provider records."""
    prepared: list[EvidenceRecord] = []
    for raw in records:
        record = raw if isinstance(raw, EvidenceRecord) else EvidenceRecord.model_validate(raw)
        record.evidence_id = stable_evidence_id(record)
        valid, reason = temporal_validity(record, analysis_date, strict=strict_temporal)
        record.analysis_date_valid = valid
        record.metadata = {**record.metadata, "temporal_validity_reason": reason}
        prepared.append(record)
    return prepared


def consolidate_evidence(records: list[EvidenceRecord]) -> list[EvidenceRecord]:
    """Consolidate duplicate source records while preserving category metadata."""
    consolidated: dict[str, EvidenceRecord] = {}
    for record in records:
        evidence_id = record.evidence_id or stable_evidence_id(record)
        record.evidence_id = evidence_id
        current = consolidated.get(evidence_id)
        if current is None:
            consolidated[evidence_id] = record.model_copy(deep=True)
            continue

        categories = {
            current.claim_category,
            record.claim_category,
            *current.metadata.get("claim_categories", []),
            *record.metadata.get("claim_categories", []),
        }
        excerpts = [
            text
            for text in (current.short_excerpt, record.short_excerpt)
            if text
        ]
        current.short_excerpt = " / ".join(dict.fromkeys(excerpts))[:500]
        current.confidence = (
            "high"
            if "high" in {current.confidence, record.confidence}
            else current.confidence
        )
        current.is_primary_source = current.is_primary_source or record.is_primary_source
        current.analysis_date_valid = (
            current.analysis_date_valid and record.analysis_date_valid
        )
        current.metadata = {
            **current.metadata,
            **record.metadata,
            "claim_categories": sorted(categories),
        }
    return sorted(consolidated.values(), key=lambda item: item.evidence_id)


def _has_conflicting_values(records: list[EvidenceRecord]) -> bool:
    values = {
        str(record.structured_value)
        for record in records
        if record.structured_value is not None
    }
    return len(values) > 1


def validate_citations(
    evidence: list[EvidenceRecord],
    claims: list[ClaimRecord],
    *,
    ticker: str,
    analysis_date: date,
    strict_temporal: bool = True,
    expected_company_name: str | None = None,
) -> CitationValidationResult:
    """Validate claim support without using an LLM."""
    issues: list[ValidationIssue] = []
    by_id = {record.evidence_id: record for record in evidence}
    expected_ticker = ticker.strip().upper()

    for record in evidence:
        valid_date, reason = temporal_validity(
            record,
            analysis_date,
            strict=strict_temporal,
        )
        record.analysis_date_valid = valid_date
        record.metadata = {**record.metadata, "temporal_validity_reason": reason}
        if not valid_date:
            issues.append(
                ValidationIssue(
                    code="temporal_invalid",
                    severity="error",
                    message=reason,
                    evidence_id=record.evidence_id,
                )
            )
        if record.source_url is None:
            issues.append(
                ValidationIssue(
                    code="missing_source_url",
                    severity="error",
                    message="Evidence has no source URL.",
                    evidence_id=record.evidence_id,
                )
            )
        elif not is_valid_source_url(record.source_url):
            issues.append(
                ValidationIssue(
                    code="invalid_source_url",
                    severity="error",
                    message=f"Invalid source URL: {record.source_url!r}",
                    evidence_id=record.evidence_id,
                )
            )
        if record.ticker.strip().upper() != expected_ticker:
            issues.append(
                ValidationIssue(
                    code="wrong_ticker",
                    severity="error",
                    message=(
                        f"Evidence ticker {record.ticker!r} does not match "
                        f"analysis ticker {expected_ticker!r}."
                    ),
                    evidence_id=record.evidence_id,
                )
            )
        record_company = str(record.metadata.get("company_name", "")).strip()
        if not company_name_matches(record, expected_company_name):
            issues.append(
                ValidationIssue(
                    code="wrong_company",
                    severity="error",
                    message=(
                        f"Evidence company {record_company!r} does not match "
                        f"resolved company {expected_company_name!r}."
                    ),
                    evidence_id=record.evidence_id,
                )
            )
        if isinstance(record.structured_value, (int, float)) and not record.unit:
            issues.append(
                ValidationIssue(
                    code="missing_unit",
                    severity="error",
                    message="Numerical evidence requires a unit.",
                    evidence_id=record.evidence_id,
                )
            )

    supported_high = 0
    unsupported_high = 0
    for claim in claims:
        resolved = [by_id[citation_id] for citation_id in claim.citation_ids if citation_id in by_id]
        missing = [citation_id for citation_id in claim.citation_ids if citation_id not in by_id]
        for citation_id in missing:
            issues.append(
                ValidationIssue(
                    code="missing_citation",
                    severity="error",
                    message=f"Claim references unknown citation {citation_id}.",
                    claim_id=claim.claim_id,
                )
            )

        usable = [
            record
            for record in resolved
            if record.analysis_date_valid
            and record.ticker.strip().upper() == expected_ticker
            and is_valid_source_url(record.source_url)
            and company_name_matches(record, expected_company_name)
        ]
        if _has_conflicting_values(usable):
            claim.support_status = "conflicting"
            issues.append(
                ValidationIssue(
                    code="conflicting_evidence",
                    severity="warning",
                    message="Supporting sources contain conflicting structured values.",
                    claim_id=claim.claim_id,
                )
            )
        elif usable:
            claim.support_status = "supported"
        elif resolved:
            claim.support_status = "unusable"
        else:
            claim.support_status = "unsupported"

        if claim.numerical_value is not None and (not claim.unit or not claim.reporting_period):
            issues.append(
                ValidationIssue(
                    code="incomplete_numerical_claim",
                    severity="error",
                    message="Numerical claims require both unit and reporting period.",
                    claim_id=claim.claim_id,
                )
            )

        if claim.materiality == "high":
            if claim.support_status in {"supported", "conflicting"}:
                supported_high += 1
            else:
                unsupported_high += 1
                issues.append(
                    ValidationIssue(
                        code="unsupported_high_materiality_claim",
                        severity="error",
                        message="High-materiality claim has no usable supporting evidence.",
                        claim_id=claim.claim_id,
                    )
                )

    # Surface category/period/unit conflicts even if a claim cited only one side.
    groups: dict[tuple[str, str, str], list[EvidenceRecord]] = defaultdict(list)
    for record in evidence:
        key = (
            record.claim_category,
            record.reporting_period or "",
            record.unit or "",
        )
        groups[key].append(record)
    for records in groups.values():
        if len(records) > 1 and _has_conflicting_values(records):
            issues.append(
                ValidationIssue(
                    code="source_conflict",
                    severity="warning",
                    message="Sources disagree for the same category, period, and unit.",
                    evidence_id=records[0].evidence_id,
                )
            )

    return CitationValidationResult(
        valid=not any(issue.severity == "error" for issue in issues),
        issues=issues,
        checked_claims=len(claims),
        checked_evidence=len(evidence),
        supported_high_materiality_claims=supported_high,
        unsupported_high_materiality_claims=unsupported_high,
    )
