from datetime import date

from tradingagents.evidence import (
    ClaimRecord,
    EvidenceRecord,
    consolidate_evidence,
    prepare_evidence,
    stable_evidence_id,
    validate_citations,
)


def _evidence(**overrides):
    data = {
        "claim_category": "backlog_and_demand",
        "source_type": "sec_filing",
        "source_title": "Example 10-K",
        "source_url": "https://www.sec.gov/example",
        "publisher": "SEC",
        "publication_date": "2024-02-01",
        "filing_date": "2024-02-01",
        "reporting_period": "FY2023",
        "ticker": "TEST",
        "short_excerpt": "Backlog was reported for the synthetic test company.",
        "confidence": "high",
        "is_primary_source": True,
        "metadata": {"company_name": "Test Company"},
    }
    data.update(overrides)
    return EvidenceRecord.model_validate(data)


def _claim(citation_ids, **overrides):
    data = {
        "claim_id": "CLM-TEST",
        "text": "The company reported backlog.",
        "claim_category": "backlog_and_demand",
        "materiality": "high",
        "citation_ids": citation_ids,
    }
    data.update(overrides)
    return ClaimRecord.model_validate(data)


def test_stable_evidence_ids_ignore_retrieval_time():
    first = _evidence(retrieved_at="2024-03-01T00:00:00Z")
    second = _evidence(retrieved_at="2025-03-01T00:00:00Z")
    assert stable_evidence_id(first) == stable_evidence_id(second)


def test_duplicate_sources_are_consolidated():
    records = prepare_evidence(
        [
            _evidence(short_excerpt="Backlog increased."),
            _evidence(
                claim_category="supply_chain",
                short_excerpt="Lead times increased.",
            ),
        ],
        date(2024, 3, 31),
    )
    consolidated = consolidate_evidence(records)
    assert len(consolidated) == 1
    assert "Backlog increased" in consolidated[0].short_excerpt
    assert "Lead times increased" in consolidated[0].short_excerpt
    assert consolidated[0].metadata["claim_categories"] == [
        "backlog_and_demand",
        "supply_chain",
    ]


def test_supported_claim_resolves_existing_primary_source():
    evidence = prepare_evidence([_evidence()], date(2024, 3, 31))
    claim = _claim([evidence[0].evidence_id])
    result = validate_citations(
        evidence,
        [claim],
        ticker="TEST",
        analysis_date=date(2024, 3, 31),
        expected_company_name="Test Company",
    )
    assert result.valid
    assert claim.support_status == "supported"
    assert evidence[0].is_primary_source is True


def test_missing_citation_and_unsupported_high_claim_are_reported():
    claim = _claim(["EVID-MISSING"])
    result = validate_citations(
        [],
        [claim],
        ticker="TEST",
        analysis_date=date(2024, 3, 31),
    )
    codes = {issue.code for issue in result.issues}
    assert {"missing_citation", "unsupported_high_materiality_claim"} <= codes
    assert result.unsupported_high_materiality_claims == 1


def test_wrong_ticker_invalid_url_and_missing_unit_are_reported():
    evidence = prepare_evidence(
        [
            _evidence(
                ticker="WRONG",
                source_url="not-a-url",
                structured_value=42,
                unit=None,
            )
        ],
        date(2024, 3, 31),
    )
    result = validate_citations(
        evidence,
        [],
        ticker="TEST",
        analysis_date=date(2024, 3, 31),
    )
    codes = {issue.code for issue in result.issues}
    assert {"wrong_ticker", "invalid_source_url", "missing_unit"} <= codes


def test_wrong_company_evidence_is_reported_and_not_usable_support():
    evidence = prepare_evidence(
        [_evidence(metadata={"company_name": "Different Company"})],
        date(2024, 3, 31),
    )
    claim = _claim([evidence[0].evidence_id])
    result = validate_citations(
        evidence,
        [claim],
        ticker="TEST",
        analysis_date=date(2024, 3, 31),
        expected_company_name="Test Company",
    )
    assert "wrong_company" in {issue.code for issue in result.issues}
    assert claim.support_status == "unusable"


def test_numerical_claim_requires_unit_and_period():
    evidence = prepare_evidence([_evidence()], date(2024, 3, 31))
    claim = _claim([evidence[0].evidence_id], numerical_value=125)
    result = validate_citations(
        evidence,
        [claim],
        ticker="TEST",
        analysis_date=date(2024, 3, 31),
    )
    assert "incomplete_numerical_claim" in {issue.code for issue in result.issues}
