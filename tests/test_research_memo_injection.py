"""Backward-compat pin for the research_memo_context injection channel.

The graph is shared with the live momentum sleeve. With an empty context,
initial state (existing keys) and the rendered fundamentals/bull/bear
prompts must be BYTE-IDENTICAL to the pre-injection code. The golden
templates below are copied verbatim from the pre-change source; if these
tests fail, the momentum path changed — that is a bug in the change, not
in the tests.
"""
from types import SimpleNamespace

from langchain_core.messages import AIMessage, HumanMessage

from tradingagents.agents.analysts.fundamentals_analyst import create_fundamentals_analyst
from tradingagents.agents.researchers.bear_researcher import create_bear_researcher
from tradingagents.agents.researchers.bull_researcher import create_bull_researcher
from tradingagents.agents.utils.agent_utils import (
    get_instrument_context_from_state,
    get_language_instruction,
)
from tradingagents.graph.propagation import Propagator


class RecorderLLM:
    """Captures the exact prompt string a debater node sends."""

    def __init__(self):
        self.prompt = None

    def invoke(self, prompt):
        self.prompt = prompt
        return SimpleNamespace(content="ok")


class RecorderChatModel:
    """Captures the rendered ChatPromptValue an analyst chain produces."""

    def __init__(self):
        self.prompt_value = None

    def bind_tools(self, tools):
        def _capture(prompt_value):
            self.prompt_value = prompt_value
            return AIMessage(content="report")

        return _capture


def _state(research_memo_context=""):
    state = {
        "messages": [HumanMessage(content="ACME")],
        "company_of_interest": "ACME",
        "asset_type": "stock",
        "instrument_context": "IC",
        "trade_date": "2026-07-09",
        "market_report": "MR",
        "sentiment_report": "SR",
        "news_report": "NR",
        "fundamentals_report": "FR",
        "investment_debate_state": {
            "history": "H", "bull_history": "BH", "bear_history": "RH",
            "current_response": "CR", "judge_decision": "", "count": 1,
        },
    }
    if research_memo_context:
        state["research_memo_context"] = research_memo_context
    return state


# --- initial state ------------------------------------------------------

def test_initial_state_existing_keys_unchanged_and_context_defaults_empty():
    state = Propagator().create_initial_state("ACME", "2026-07-09")
    assert state["research_memo_context"] == ""
    expected_existing = {
        "messages": [("human", "ACME")],
        "company_of_interest": "ACME",
        "asset_type": "stock",
        "instrument_context": "",
        "trade_date": "2026-07-09",
        "past_context": "",
        "investment_debate_state": {
            "bull_history": "", "bear_history": "", "history": "",
            "current_response": "", "judge_decision": "", "count": 0,
        },
        "risk_debate_state": {
            "aggressive_history": "", "conservative_history": "",
            "neutral_history": "", "history": "", "latest_speaker": "",
            "current_aggressive_response": "", "current_conservative_response": "",
            "current_neutral_response": "", "judge_decision": "", "count": 0,
        },
        "market_report": "", "fundamentals_report": "",
        "sentiment_report": "", "news_report": "",
    }
    for key, value in expected_existing.items():
        assert state[key] == value, key
    assert set(state) == set(expected_existing) | {"research_memo_context"}


def test_initial_state_threads_context_through():
    state = Propagator().create_initial_state(
        "ACME", "2026-07-09", research_memo_context="THE BRIEF"
    )
    assert state["research_memo_context"] == "THE BRIEF"
    assert state["past_context"] == ""   # separate channels, never conflated


# --- bull/bear golden prompts -------------------------------------------

BULL_TEMPLATE = """You are a Bull Analyst advocating for investing in the {target_label}. Your task is to build a strong, evidence-based case emphasizing growth potential, competitive advantages, and positive market indicators. Leverage the provided research and data to address concerns and counter bearish arguments effectively.

Key points to focus on:
- Growth Potential: Highlight the company's market opportunities, revenue projections, and scalability.
- Competitive Advantages: Emphasize factors like unique products, strong branding, or dominant market positioning.
- Positive Indicators: Use financial health, industry trends, and recent positive news as evidence.
- Bear Counterpoints: Critically analyze the bear argument with specific data and sound reasoning, addressing concerns thoroughly and showing why the bull perspective holds stronger merit.
- Engagement: Present your argument in a conversational style, engaging directly with the bear analyst's points and debating effectively rather than just listing data.

Resources available:
{instrument_context}
Market research report: {market_research_report}
Social media sentiment report: {sentiment_report}
Latest world affairs news: {news_report}
{fundamentals_label}: {fundamentals_report}
Conversation history of the debate: {history}
Last bear argument: {current_response}
Use this information to deliver a compelling bull argument, refute the bear's concerns, and engage in a dynamic debate that demonstrates the strengths of the bull position.
"""

BEAR_TEMPLATE = """You are a Bear Analyst making the case against investing in the {target_label}. Your goal is to present a well-reasoned argument emphasizing risks, challenges, and negative indicators. Leverage the provided research and data to highlight potential downsides and counter bullish arguments effectively.

Key points to focus on:

- Risks and Challenges: Highlight factors like market saturation, financial instability, or macroeconomic threats that could hinder the stock's performance.
- Competitive Weaknesses: Emphasize vulnerabilities such as weaker market positioning, declining innovation, or threats from competitors.
- Negative Indicators: Use evidence from financial data, market trends, or recent adverse news to support your position.
- Bull Counterpoints: Critically analyze the bull argument with specific data and sound reasoning, exposing weaknesses or over-optimistic assumptions.
- Engagement: Present your argument in a conversational style, directly engaging with the bull analyst's points and debating effectively rather than simply listing facts.

Resources available:

{instrument_context}
Market research report: {market_research_report}
Social media sentiment report: {sentiment_report}
Latest world affairs news: {news_report}
{fundamentals_label}: {fundamentals_report}
Conversation history of the debate: {history}
Last bull argument: {current_response}
Use this information to deliver a compelling bear argument, refute the bull's claims, and engage in a dynamic debate that demonstrates the risks and weaknesses of investing in the {target_label}.
"""


def _expected_debater_prompt(template):
    state = _state()
    return template.format(
        target_label="stock",
        instrument_context=get_instrument_context_from_state(state),
        market_research_report="MR", sentiment_report="SR", news_report="NR",
        fundamentals_label="Company fundamentals report",
        fundamentals_report="FR", history="H", current_response="CR",
    ) + get_language_instruction()


def test_bull_prompt_byte_identical_with_empty_context():
    llm = RecorderLLM()
    create_bull_researcher(llm)(_state())
    assert llm.prompt == _expected_debater_prompt(BULL_TEMPLATE)


def test_bear_prompt_byte_identical_with_empty_context():
    llm = RecorderLLM()
    create_bear_researcher(llm)(_state())
    assert llm.prompt == _expected_debater_prompt(BEAR_TEMPLATE)


def test_bull_prompt_includes_context_when_set():
    llm = RecorderLLM()
    create_bull_researcher(llm)(_state(research_memo_context="THE BRIEF"))
    assert "THE BRIEF" in llm.prompt
    assert llm.prompt != _expected_debater_prompt(BULL_TEMPLATE)


def test_bear_prompt_includes_context_when_set():
    llm = RecorderLLM()
    create_bear_researcher(llm)(_state(research_memo_context="THE BRIEF"))
    assert "THE BRIEF" in llm.prompt


# --- fundamentals analyst golden prompt ---------------------------------

FUNDAMENTALS_SYSTEM_TEMPLATE = (
    "You are a helpful AI assistant, collaborating with other assistants."
    " Use the provided tools to progress towards answering the question."
    " If you are unable to fully answer, that's OK; another assistant with different tools"
    " will help where you left off. Execute what you can to make progress."
    " If you or any other assistant has the FINAL TRANSACTION PROPOSAL: **BUY/HOLD/SELL** or deliverable,"
    " prefix your response with FINAL TRANSACTION PROPOSAL: **BUY/HOLD/SELL** so the team knows to stop."
    " You have access to the following tools: {tool_names}."
    " Today's date is {current_date}; treat it as 'now' for all analysis and tool-call date ranges. {instrument_context}\n"
    "{system_message}"
)


def _expected_fundamentals_system(state):
    # NOTE: system_message is a TUPLE in the production code (trailing
    # comma) and renders via str(); reproduce exactly — do not "fix" it.
    system_message = (
        "You are a researcher tasked with analyzing fundamental information over the past week about a company. Please write a comprehensive report of the company's fundamental information such as financial documents, company profile, basic company financials, and company financial history to gain a full view of the company's fundamental information to inform traders. Make sure to include as much detail as possible. Provide specific, actionable insights with supporting evidence to help traders make informed decisions."
        + " Make sure to append a Markdown table at the end of the report to organize key points in the report, organized and easy to read."
        + " Use the available tools: `get_fundamentals` for comprehensive company analysis, `get_balance_sheet`, `get_cashflow`, and `get_income_statement` for specific financial statements."
        + get_language_instruction(),
    )
    return FUNDAMENTALS_SYSTEM_TEMPLATE.format(
        tool_names="get_fundamentals, get_balance_sheet, get_cashflow, get_income_statement",
        current_date=state["trade_date"],
        instrument_context=get_instrument_context_from_state(state),
        system_message=system_message,
    )


def test_fundamentals_system_prompt_byte_identical_with_empty_context():
    rec = RecorderChatModel()
    state = _state()
    create_fundamentals_analyst(rec)(state)
    assert rec.prompt_value.messages[0].content == _expected_fundamentals_system(state)


def test_fundamentals_system_prompt_includes_context_when_set():
    rec = RecorderChatModel()
    state = _state(research_memo_context="THE BRIEF")
    create_fundamentals_analyst(rec)(state)
    system = rec.prompt_value.messages[0].content
    assert "THE BRIEF" in system
