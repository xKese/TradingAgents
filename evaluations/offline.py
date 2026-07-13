"""Deterministic fixture-based Operational Signals Analyst output."""

from __future__ import annotations

from datetime import date
from typing import Any

from tradingagents.agents.analysts.operational_signals_analyst import (
    render_operational_report,
)
from tradingagents.agents.schemas import (
    OperationalAssessment,
    OperationalSignalsOutput,
    PortfolioRating,
)
from tradingagents.evidence import (
    CitationValidationResult,
    DisclosureStatus,
    OperationalFinding,
    validate_citations,
)

from .metrics import resolve_case_models


def build_fixture_output(
    case: dict[str, Any],
    *,
    citation_validation_enabled: bool,
) -> OperationalSignalsOutput:
    """Build a schema-valid analyst output without a model, API, or network."""
    evidence, claims = resolve_case_models(case)
    findings_by_category: dict[str, list[OperationalFinding]] = {
        "backlog_and_demand": [],
        "capacity_and_capex": [],
        "concentration_risk": [],
        "supply_chain": [],
    }
    raw_by_text = {raw["text"]: raw for raw in case.get("claims", [])}
    for claim in claims:
        raw = raw_by_text[claim.text]
        status = DisclosureStatus(raw.get("disclosure_status", "Qualitative commentary"))
        finding = OperationalFinding(
            finding=claim.text,
            claim_category=claim.claim_category,
            disclosure_status=status,
            signal=raw.get("signal", "Neutral"),
            materiality=claim.materiality,
            citation_ids=claim.citation_ids,
            structured_value=claim.numerical_value,
            unit=claim.unit,
            reporting_period=claim.reporting_period,
        )
        findings_by_category.setdefault(claim.claim_category, []).append(finding)

    assessment = OperationalAssessment(
        overall_operational_signal=(
            "Synthetic fixture contains supportable operational findings."
            if claims
            else "Unavailable — the synthetic fixture contains no factual findings."
        ),
        signal_rating=PortfolioRating.HOLD,
        confidence_level="medium" if evidence else "low",
        backlog_and_demand_findings=findings_by_category["backlog_and_demand"],
        capacity_and_capital_spending_findings=findings_by_category["capacity_and_capex"],
        concentration_risk_findings=findings_by_category["concentration_risk"],
        supply_chain_findings=findings_by_category["supply_chain"],
        missing_or_unavailable_information=case.get(
            "missing_or_unavailable_information", []
        ),
        analyst_conclusion=(
            "This output validates evidence plumbing only; it is not an investment conclusion."
        ),
        limitations=[
            "Synthetic fixture data; no real company or financial document is represented.",
            *[f"Retrieval failure: {item}" for item in case.get("retrieval_failures", [])],
        ],
    )
    if citation_validation_enabled:
        validation = validate_citations(
            evidence,
            claims,
            ticker=case["ticker"],
            analysis_date=date.fromisoformat(case["analysis_date"]),
            strict_temporal=True,
            expected_company_name=case["company_name"],
        )
    else:
        for claim in claims:
            claim.support_status = "supported" if claim.citation_ids else "unsupported"
        validation = CitationValidationResult(valid=True)

    return OperationalSignalsOutput(
        company_name=case["company_name"],
        ticker=case["ticker"],
        analysis_date=case["analysis_date"],
        assessment=assessment,
        evidence_records=evidence,
        claims=claims,
        citation_validation=validation,
    )


def render_fixture_output(
    case: dict[str, Any],
    *,
    citation_validation_enabled: bool,
) -> tuple[OperationalSignalsOutput, str]:
    output = build_fixture_output(
        case,
        citation_validation_enabled=citation_validation_enabled,
    )
    return output, render_operational_report(output)
