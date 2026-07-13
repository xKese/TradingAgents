"""Operational Signals Analyst with point-in-time, claim-level evidence."""

from __future__ import annotations

import json
import logging
from datetime import date
from typing import Any

from langchain_core.messages import AIMessage, ToolMessage

from tradingagents.agents.schemas import (
    OperationalAssessment,
    OperationalSignalsOutput,
    PortfolioRating,
)
from tradingagents.agents.utils.agent_utils import (
    get_instrument_context_from_state,
    get_language_instruction,
)
from tradingagents.agents.utils.structured import bind_structured
from tradingagents.dataflows.config import get_config
from tradingagents.evidence import (
    CitationValidationResult,
    ClaimRecord,
    EvidenceRecord,
    append_sources,
    company_name_matches,
    consolidate_evidence,
    finding_to_claim,
    is_valid_source_url,
    prepare_evidence,
    render_claim_text,
    validate_citations,
)

logger = logging.getLogger(__name__)


def _tool_payload(messages: list[Any]) -> dict[str, Any] | None:
    for message in reversed(messages):
        if not isinstance(message, ToolMessage):
            continue
        if getattr(message, "name", None) not in {None, "get_operational_evidence"}:
            continue
        content = message.content
        if not isinstance(content, str):
            continue
        try:
            payload = json.loads(content)
        except json.JSONDecodeError:
            return {
                "status": "unavailable",
                "evidence_records": [],
                "retrieval_failures": [content[:240]],
            }
        return payload if isinstance(payload, dict) else None
    return None


def _no_data_assessment(reason: str) -> OperationalAssessment:
    return OperationalAssessment(
        overall_operational_signal="Unavailable — no usable point-in-time evidence was retrieved.",
        signal_rating=PortfolioRating.HOLD,
        confidence_level="low",
        missing_or_unavailable_information=[
            "Backlog, demand, capacity, concentration, and supply-chain evidence unavailable."
        ],
        analyst_conclusion=(
            "No operational directional conclusion is supportable from the available evidence."
        ),
        limitations=[reason, "Missing data was not estimated or inferred."],
    )


def _assessment_prompt(
    *,
    ticker: str,
    company_name: str,
    analysis_date: str,
    instrument_context: str,
    evidence: list[EvidenceRecord],
) -> str:
    evidence_json = json.dumps(
        [record.model_dump(mode="json") for record in evidence],
        indent=2,
        sort_keys=True,
    )
    return f"""You are the Operational Signals Analyst for {company_name} ({ticker}).

{instrument_context}
The analysis date is {analysis_date}. Every usable source below was deterministically
checked as public on or before that date. Use only these evidence records. Never invent
a URL, document, number, reporting period, customer, supplier, facility, or citation ID.

Evaluate only concepts that apply to this company: backlog, bookings/book-to-bill,
remaining performance obligations, demand visibility, capacity and capex, facility
expansion, customer/supplier/segment/geographic concentration, production or supply
constraints, lead times, inventory commentary, cancellations/delays, contract duration,
major customer wins/losses, and management demand/capacity guidance.

For each finding:
- choose Reported, Derivable from reported figures, Qualitative commentary, Not
  disclosed, or Not applicable;
- cite one or more exact evidence_id values for factual Reported/Derivable/Qualitative
  findings;
- include reporting_period and unit for numerical claims;
- do not treat silence as proof that a risk does not exist;
- surface conflicting sources instead of reconciling them silently.

Evidence records:
<evidence_json>
{evidence_json}
</evidence_json>

Produce the OperationalAssessment schema. Keep excerpts out of the prose; citations are
the traceability mechanism.{get_language_instruction()}"""


def _invoke_assessment(
    structured_llm: Any | None,
    plain_llm: Any,
    prompt: str,
) -> OperationalAssessment:
    if structured_llm is not None:
        try:
            result = structured_llm.invoke(prompt)
            if result is not None:
                return OperationalAssessment.model_validate(result)
        except Exception as exc:  # noqa: BLE001 - explicit safe fallback is intentional
            logger.warning(
                "Operational Signals Analyst structured output failed (%s); "
                "falling back to a conservative prose-only conclusion",
                exc,
            )

    try:
        response = plain_llm.invoke(prompt)
        conclusion = str(getattr(response, "content", "")).strip()
    except Exception as exc:  # noqa: BLE001 - provider failure becomes explicit missing data
        logger.warning("Operational Signals Analyst fallback invocation failed: %s", exc)
        conclusion = ""
    return OperationalAssessment(
        overall_operational_signal="Indeterminate — structured assessment unavailable.",
        signal_rating=PortfolioRating.HOLD,
        confidence_level="low",
        analyst_conclusion=conclusion or "No supportable operational conclusion was produced.",
        limitations=[
            "The configured model did not return the structured operational schema.",
            "No individual factual findings were accepted from the free-text fallback.",
        ],
    )


def _deduplicate_claims(assessment: OperationalAssessment) -> list[ClaimRecord]:
    claims: dict[str, ClaimRecord] = {}
    for finding in assessment.all_findings():
        claim = finding_to_claim(finding)
        claims.setdefault(claim.claim_id, claim)
    return list(claims.values())


def _render_findings(
    title: str,
    findings: list,
    claims_by_id: dict[str, ClaimRecord],
) -> list[str]:
    lines = [f"## {title}", ""]
    if not findings:
        return [*lines, "- No supportable findings."]
    for finding in findings:
        claim = finding_to_claim(finding)
        validated = claims_by_id.get(claim.claim_id, claim)
        detail = f"{finding.disclosure_status.value}; {finding.signal}"
        if finding.reporting_period:
            detail += f"; period: {finding.reporting_period}"
        if finding.structured_value is not None:
            detail += f"; value: {finding.structured_value}"
            if finding.unit:
                detail += f" {finding.unit}"
        lines.append(f"- {render_claim_text(finding.finding, validated)} ({detail})")
    return lines


def render_operational_report(output: OperationalSignalsOutput) -> str:
    """Render the structured operational output with inline IDs and Sources."""
    assessment = output.assessment
    claims_by_id = {claim.claim_id: claim for claim in output.claims}
    lines = [
        "# Operational Signals Analyst",
        "",
        f"- **Company:** {output.company_name}",
        f"- **Ticker:** {output.ticker}",
        f"- **Analysis date:** {output.analysis_date}",
        f"- **Overall operational signal:** {assessment.overall_operational_signal}",
        f"- **Signal rating:** {assessment.signal_rating.value}",
        f"- **Confidence:** {assessment.confidence_level}",
        "",
    ]
    for title, findings in (
        ("Positive operating indicators", assessment.positive_operating_indicators),
        ("Negative operating indicators", assessment.negative_operating_indicators),
        ("Backlog and demand", assessment.backlog_and_demand_findings),
        (
            "Capacity and capital spending",
            assessment.capacity_and_capital_spending_findings,
        ),
        ("Concentration risk", assessment.concentration_risk_findings),
        ("Supply chain", assessment.supply_chain_findings),
    ):
        lines.extend(_render_findings(title, findings, claims_by_id))
        lines.append("")

    lines.extend(["## Missing or unavailable information", ""])
    lines.extend(
        f"- {item}" for item in assessment.missing_or_unavailable_information
    )
    if not assessment.missing_or_unavailable_information:
        lines.append("- None explicitly identified.")
    lines.extend(["", "## Analyst conclusion", "", assessment.analyst_conclusion])
    lines.extend(["", "## Limitations", ""])
    lines.extend(f"- {item}" for item in assessment.limitations)
    if not assessment.limitations:
        lines.append("- Operational disclosures may be incomplete or company-specific.")
    result = output.citation_validation
    lines.extend(
        [
            "",
            "## Citation validation",
            "",
            f"- **Valid:** {result.valid}",
            f"- **Claims checked:** {result.checked_claims}",
            f"- **Evidence records checked:** {result.checked_evidence}",
            f"- **Unsupported high-materiality claims:** "
            f"{result.unsupported_high_materiality_claims}",
        ]
    )
    if result.issues:
        lines.append("- **Issues:**")
        lines.extend(f"  - `{issue.code}`: {issue.message}" for issue in result.issues)
    return append_sources("\n".join(lines), output.evidence_records)


def create_operational_signals_analyst(llm):
    """Create the optional evidence-grounded operational analyst node."""
    structured_llm = bind_structured(
        llm,
        OperationalAssessment,
        "Operational Signals Analyst",
    )

    def operational_signals_node(state):
        ticker = state["company_of_interest"]
        analysis_date = str(state["trade_date"])
        messages = state["messages"]
        payload = _tool_payload(messages)

        if payload is None:
            tool_call_id = f"operational-{ticker}-{analysis_date}".replace(" ", "-")
            return {
                "messages": [
                    AIMessage(
                        content="",
                        tool_calls=[
                            {
                                "name": "get_operational_evidence",
                                "args": {"ticker": ticker, "curr_date": analysis_date},
                                "id": tool_call_id,
                                "type": "tool_call",
                            }
                        ],
                    )
                ]
            }

        config = get_config()
        strict_temporal = bool(config.get("strict_temporal_grounding", True))
        raw_records = payload.get("evidence_records") or []
        try:
            records = prepare_evidence(
                raw_records,
                date.fromisoformat(analysis_date),
                strict_temporal=strict_temporal,
            )
            records = consolidate_evidence(records)
        except (TypeError, ValueError) as exc:
            logger.warning("Operational evidence payload was invalid: %s", exc)
            records = []

        company_name = str(payload.get("company_name") or ticker)
        usable = [
            record
            for record in records
            if record.analysis_date_valid
            and record.ticker == ticker.upper()
            and is_valid_source_url(record.source_url)
            and company_name_matches(record, company_name)
        ]
        retrieval_failures = payload.get("retrieval_failures") or []
        if usable:
            assessment = _invoke_assessment(
                structured_llm,
                llm,
                _assessment_prompt(
                    ticker=ticker,
                    company_name=company_name,
                    analysis_date=analysis_date,
                    instrument_context=get_instrument_context_from_state(state),
                    evidence=usable,
                ),
            )
        else:
            reason = "; ".join(str(item) for item in retrieval_failures) or (
                "The provider returned no date-valid records with valid source URLs."
            )
            assessment = _no_data_assessment(reason)

        claims = _deduplicate_claims(assessment)
        if config.get("citation_validation_enabled", True):
            validation = validate_citations(
                records,
                claims,
                ticker=ticker,
                analysis_date=date.fromisoformat(analysis_date),
                strict_temporal=strict_temporal,
                expected_company_name=company_name,
            )
        else:
            for claim in claims:
                claim.support_status = "supported" if claim.citation_ids else "unsupported"
            validation = CitationValidationResult(
                valid=True,
                checked_claims=0,
                checked_evidence=0,
            )

        output = OperationalSignalsOutput(
            company_name=company_name,
            ticker=ticker.upper(),
            analysis_date=analysis_date,
            assessment=assessment,
            evidence_records=records,
            claims=claims,
            citation_validation=validation,
        )
        report = render_operational_report(output)
        return {
            "messages": [AIMessage(content=report)],
            "operational_report": report,
            "operational_analysis": output.model_dump(mode="json"),
            "operational_evidence": [record.model_dump(mode="json") for record in records],
            "operational_claims": [claim.model_dump(mode="json") for claim in claims],
            "citation_validation": validation.model_dump(mode="json"),
        }

    return operational_signals_node
