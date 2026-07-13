from tradingagents.evidence import EvidenceRecord
from tradingagents.reporting import write_report_tree


def test_operational_report_and_sources_are_written(tmp_path):
    evidence = EvidenceRecord(
        evidence_id="EVID-TEST",
        claim_category="backlog_and_demand",
        source_type="fixture",
        source_title="Synthetic source",
        source_url="https://fixtures.example.test/source",
        publisher="Synthetic",
        publication_date="2024-01-01",
        ticker="TEST",
        short_excerpt="Synthetic excerpt.",
        analysis_date_valid=True,
        metadata={"synthetic": True},
    )
    final_state = {
        "operational_report": (
            "# Operational Signals Analyst\n\nFixture report.\n\n"
            "## Sources\n\n- **EVID-TEST** — embedded source."
        ),
        "operational_evidence": [evidence.model_dump(mode="json")],
    }
    complete = write_report_tree(final_state, "TEST", tmp_path)
    assert (tmp_path / "1_analysts" / "operational.md").exists()
    text = complete.read_text()
    assert "Operational Signals Analyst" in text
    assert text.count("## Sources") == 1
    assert text.rstrip().endswith(
        "secondary/structured provider; date-valid; published 2024-01-01."
    )
