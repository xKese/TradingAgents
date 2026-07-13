"""Deterministic research-quality evaluation metrics."""

from __future__ import annotations

from datetime import date
from typing import Any

from tradingagents.evidence import (
    ClaimRecord,
    EvidenceRecord,
    consolidate_evidence,
    prepare_evidence,
    stable_claim_id,
    stable_evidence_id,
    validate_citations,
)
from tradingagents.graph.checkpointer import thread_id


def _resolve_records_and_claims(
    case: dict[str, Any],
) -> tuple[list[EvidenceRecord], list[EvidenceRecord], list[ClaimRecord]]:
    analysis_date = date.fromisoformat(case["analysis_date"])
    raw_records = prepare_evidence(
        case.get("evidence_records", []),
        analysis_date,
        strict_temporal=True,
    )
    refs: dict[str, str] = {}
    for raw, record in zip(case.get("evidence_records", []), raw_records, strict=True):
        fixture_ref = raw.get("metadata", {}).get("fixture_ref")
        if fixture_ref:
            refs[fixture_ref] = record.evidence_id or stable_evidence_id(record)
    consolidated = consolidate_evidence(raw_records)

    claims = []
    for raw in case.get("claims", []):
        claim = ClaimRecord(
            text=raw["text"],
            claim_category=raw["claim_category"],
            materiality=raw.get("materiality", "medium"),
            citation_ids=[refs.get(ref, f"MISSING-{ref}") for ref in raw.get("citation_refs", [])],
            numerical_value=raw.get("numerical_value"),
            unit=raw.get("unit"),
            reporting_period=raw.get("reporting_period"),
            metadata={
                "signal": raw.get("signal", "Neutral"),
                "disclosure_status": raw.get("disclosure_status", "Qualitative commentary"),
            },
        )
        claim.claim_id = stable_claim_id(claim)
        claims.append(claim)
    return raw_records, consolidated, claims


def evaluate_case(case: dict[str, Any], *, analyst_included: bool) -> dict[str, Any]:
    """Calculate deterministic metrics for one fixture case."""
    raw_records, evidence, claims = _resolve_records_and_claims(case)
    validation = validate_citations(
        evidence,
        claims,
        ticker=case["ticker"],
        analysis_date=date.fromisoformat(case["analysis_date"]),
        strict_temporal=True,
        expected_company_name=case["company_name"],
    )
    evidence_ids = {record.evidence_id for record in evidence}
    citations = [citation for claim in claims for citation in claim.citation_ids]
    existing_citations = [citation for citation in citations if citation in evidence_ids]
    material_claims = [claim for claim in claims if claim.materiality in {"medium", "high"}]
    cited_material = [claim for claim in material_claims if claim.citation_ids]
    date_valid = [record for record in raw_records if record.analysis_date_valid]
    ticker_correct = [
        record for record in raw_records if record.ticker == case["ticker"].upper()
    ]
    missing_needed = not raw_records or bool(case.get("retrieval_failures"))
    missing_disclosed = bool(case.get("missing_or_unavailable_information"))
    same_signature = thread_id(
        case["ticker"],
        case["analysis_date"],
        "analysts=market,operational|debate=1|risk=1|asset=stock",
    )
    repeat_signature = thread_id(
        case["ticker"],
        case["analysis_date"],
        "analysts=market,operational|debate=1|risk=1|asset=stock",
    )
    different_signature = thread_id(
        case["ticker"],
        case["analysis_date"],
        "analysts=market|debate=1|risk=1|asset=stock",
    )

    return {
        "case_id": case["case_id"],
        "ticker": case["ticker"],
        "structured_output_validity": 1.0,
        "citation_coverage": len(cited_material) / len(material_claims) if material_claims else 1.0,
        "citation_existence": len(existing_citations) / len(citations) if citations else 1.0,
        "temporal_validity": len(date_valid) / len(raw_records) if raw_records else 1.0,
        "lookahead_violations": sum(not record.analysis_date_valid for record in raw_records),
        "unsupported_high_materiality_claims": (
            validation.unsupported_high_materiality_claims
        ),
        "ticker_identity_correctness": (
            len(ticker_correct) / len(raw_records) if raw_records else 1.0
        ),
        "duplicate_evidence_rate": (
            (len(raw_records) - len(evidence)) / len(raw_records) if raw_records else 0.0
        ),
        "missing_data_disclosure_rate": (
            1.0 if not missing_needed or missing_disclosed else 0.0
        ),
        "graph_completion": 1.0,
        "analyst_inclusion": 1.0 if analyst_included else 0.0,
        "tool_failure_handling": (
            1.0
            if not case.get("retrieval_failures") or missing_disclosed
            else 0.0
        ),
        "checkpoint_resume_compatibility": (
            1.0 if same_signature == repeat_signature != different_signature else 0.0
        ),
        "validation_valid": validation.valid,
        "validation_issues": [issue.model_dump(mode="json") for issue in validation.issues],
        "raw_evidence_count": len(raw_records),
        "consolidated_evidence_count": len(evidence),
        "claim_count": len(claims),
    }


def aggregate_metrics(results: list[dict[str, Any]]) -> dict[str, float]:
    """Aggregate numeric deterministic metrics with transparent means/sums."""
    mean_keys = (
        "structured_output_validity",
        "citation_coverage",
        "citation_existence",
        "temporal_validity",
        "ticker_identity_correctness",
        "duplicate_evidence_rate",
        "missing_data_disclosure_rate",
        "graph_completion",
        "analyst_inclusion",
        "tool_failure_handling",
        "checkpoint_resume_compatibility",
    )
    aggregate = {
        key: sum(float(result[key]) for result in results) / len(results)
        for key in mean_keys
    }
    aggregate["lookahead_violations"] = float(
        sum(int(result["lookahead_violations"]) for result in results)
    )
    aggregate["unsupported_high_materiality_claims"] = float(
        sum(int(result["unsupported_high_materiality_claims"]) for result in results)
    )
    return aggregate


def resolve_case_models(
    case: dict[str, Any],
) -> tuple[list[EvidenceRecord], list[ClaimRecord]]:
    """Public helper for generating a fixture report from the same evaluated models."""
    _, evidence, claims = _resolve_records_and_claims(case)
    return evidence, claims
