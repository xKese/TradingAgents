"""Public evidence and citation API."""

from .models import (
    CitationValidationResult,
    ClaimRecord,
    DisclosureStatus,
    EvidenceRecord,
    OperationalFinding,
    ValidationIssue,
    finding_to_claim,
    stable_claim_id,
    stable_evidence_id,
)
from .rendering import append_sources, render_claim_text, render_sources
from .validation import (
    company_name_matches,
    consolidate_evidence,
    is_valid_source_url,
    prepare_evidence,
    temporal_validity,
    validate_citations,
)

__all__ = [
    "CitationValidationResult",
    "ClaimRecord",
    "DisclosureStatus",
    "EvidenceRecord",
    "OperationalFinding",
    "ValidationIssue",
    "append_sources",
    "company_name_matches",
    "consolidate_evidence",
    "finding_to_claim",
    "is_valid_source_url",
    "prepare_evidence",
    "render_claim_text",
    "render_sources",
    "stable_claim_id",
    "stable_evidence_id",
    "temporal_validity",
    "validate_citations",
]
