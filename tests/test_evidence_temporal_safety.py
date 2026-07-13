from datetime import date

from tradingagents.evidence import EvidenceRecord, prepare_evidence, temporal_validity


def _record(**overrides):
    data = {
        "claim_category": "capacity_and_capex",
        "source_type": "sec_filing",
        "source_title": "Filing",
        "source_url": "https://www.sec.gov/filing",
        "ticker": "TEST",
        "short_excerpt": "Capacity commentary.",
        "is_primary_source": True,
    }
    data.update(overrides)
    return EvidenceRecord.model_validate(data)


def test_rejects_source_published_after_analysis_date():
    record = _record(publication_date="2024-04-01")
    valid, _ = temporal_validity(record, date(2024, 3, 31))
    assert valid is False


def test_accepts_source_published_on_analysis_date():
    record = _record(publication_date="2024-03-31")
    valid, _ = temporal_validity(record, date(2024, 3, 31))
    assert valid is True


def test_unknown_publication_date_is_unusable_in_strict_mode():
    record = _record()
    strict, _ = temporal_validity(record, date(2024, 3, 31), strict=True)
    permissive, _ = temporal_validity(record, date(2024, 3, 31), strict=False)
    assert strict is False
    assert permissive is True


def test_future_filing_date_cannot_leak_even_with_older_publication_date():
    record = _record(publication_date="2024-03-01", filing_date="2024-04-01")
    prepared = prepare_evidence([record], date(2024, 3, 31))
    assert prepared[0].analysis_date_valid is False
    assert "filing_date" in prepared[0].metadata["temporal_validity_reason"]
