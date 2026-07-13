import json
from datetime import date
from unittest.mock import MagicMock

from langchain_core.messages import HumanMessage, ToolMessage

from cli.models import AnalystType, AssetType
from cli.utils import ANALYST_ORDER as CLI_ANALYST_ORDER, filter_analysts_for_asset_type
from tradingagents.agents.analysts.operational_signals_analyst import (
    create_operational_signals_analyst,
)
from tradingagents.agents.researchers.bull_researcher import create_bull_researcher
from tradingagents.agents.schemas import OperationalAssessment, PortfolioRating
from tradingagents.graph.analyst_execution import build_analyst_execution_plan
from tradingagents.graph.propagation import Propagator
from tradingagents.graph.trading_graph import TradingAgentsGraph


def _state(messages):
    return {
        "messages": messages,
        "company_of_interest": "TEST",
        "trade_date": "2024-03-31",
        "asset_type": "stock",
        "instrument_context": "Company: Test Company; ticker TEST.",
    }


def _valid_payload():
    return {
        "status": "ok",
        "ticker": "TEST",
        "company_name": "Test Company",
        "analysis_date": "2024-03-31",
        "evidence_records": [
            {
                "claim_category": "backlog_and_demand",
                "source_type": "sec_filing",
                "source_title": "Test 10-K",
                "source_url": "https://www.sec.gov/test-10k",
                "publisher": "SEC",
                "publication_date": "2024-02-01",
                "filing_date": "2024-02-01",
                "reporting_period": "FY2023",
                "ticker": "TEST",
                "short_excerpt": "The test company reported backlog.",
                "confidence": "high",
                "is_primary_source": True,
                "metadata": {"company_name": "Test Company"},
            }
        ],
        "retrieval_failures": [],
    }


def test_factory_first_turn_requests_operational_tool():
    llm = MagicMock()
    llm.with_structured_output.return_value = MagicMock()
    node = create_operational_signals_analyst(llm)
    result = node(_state([HumanMessage(content="TEST")]))
    tool_call = result["messages"][0].tool_calls[0]
    assert tool_call["name"] == "get_operational_evidence"
    assert tool_call["args"] == {"ticker": "TEST", "curr_date": "2024-03-31"}


def test_structured_output_and_evidence_propagate_to_state():
    payload = _valid_payload()
    from tradingagents.evidence import prepare_evidence

    evidence = prepare_evidence(payload["evidence_records"], date(2024, 3, 31))[0]
    structured = MagicMock()
    structured.invoke.return_value = OperationalAssessment(
        overall_operational_signal="Backlog is a positive demand indicator.",
        signal_rating=PortfolioRating.OVERWEIGHT,
        confidence_level="high",
        backlog_and_demand_findings=[
            {
                "finding": "The company reported backlog.",
                "claim_category": "backlog_and_demand",
                "disclosure_status": "Reported",
                "signal": "Positive",
                "materiality": "high",
                "citation_ids": [evidence.evidence_id],
                "reporting_period": "FY2023",
            }
        ],
        analyst_conclusion="Backlog supports demand visibility.",
        limitations=["Only one filing was available."],
    )
    llm = MagicMock()
    llm.with_structured_output.return_value = structured
    node = create_operational_signals_analyst(llm)
    messages = [
        HumanMessage(content="TEST"),
        ToolMessage(
            content=json.dumps(payload),
            tool_call_id="operational-test",
            name="get_operational_evidence",
        ),
    ]
    result = node(_state(messages))
    assert result["operational_analysis"]["ticker"] == "TEST"
    assert result["citation_validation"]["valid"] is True
    assert result["operational_evidence"][0]["evidence_id"] == evidence.evidence_id
    assert evidence.evidence_id in result["operational_report"]
    assert "## Sources" in result["operational_report"]


def test_missing_data_is_explicit_and_does_not_invoke_model():
    llm = MagicMock()
    structured = MagicMock()
    llm.with_structured_output.return_value = structured
    node = create_operational_signals_analyst(llm)
    payload = {
        "status": "unavailable",
        "company_name": "Test Company",
        "evidence_records": [],
        "retrieval_failures": ["SyntheticProviderError"],
    }
    result = node(
        _state(
            [
                HumanMessage(content="TEST"),
                ToolMessage(
                    content=json.dumps(payload),
                    tool_call_id="operational-test",
                    name="get_operational_evidence",
                ),
            ]
        )
    )
    structured.invoke.assert_not_called()
    assert "Unavailable" in result["operational_report"]
    assert "Missing data was not estimated" in result["operational_report"]


def test_plan_state_toolnode_and_cli_registration():
    plan = build_analyst_execution_plan(["market", "operational"])
    assert plan.specs[-1].agent_node == "Operational Signals Analyst"
    initial = Propagator().create_initial_state("TEST", "2024-03-31")
    assert initial["operational_report"] == ""
    graph = TradingAgentsGraph.__new__(TradingAgentsGraph)
    tool_nodes = TradingAgentsGraph._create_tool_nodes(graph)
    assert "get_operational_evidence" in tool_nodes["operational"].tools_by_name
    assert ("Operational Signals Analyst", AnalystType.OPERATIONAL) in CLI_ANALYST_ORDER
    assert AnalystType.OPERATIONAL not in filter_analysts_for_asset_type(
        list(AnalystType),
        AssetType.CRYPTO,
    )


def test_operational_report_is_in_downstream_researcher_context():
    llm = MagicMock()
    llm.invoke.return_value.content = "Bull response"
    node = create_bull_researcher(llm)
    state = Propagator().create_initial_state("TEST", "2024-03-31")
    state["operational_report"] = "CITED OPERATIONAL REPORT [EVID-123]"
    node(state)
    prompt = llm.invoke.call_args.args[0]
    assert "CITED OPERATIONAL REPORT [EVID-123]" in prompt
