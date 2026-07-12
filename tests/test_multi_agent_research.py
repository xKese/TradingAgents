from datetime import date

from tradingagents.research_platform.agent_contracts import AgentOutputType, EvidenceRef
from tradingagents.research_platform.multi_agent_research import (
    LLMResearchConfig,
    MultiAgentResearchProvider,
    StructuredResearchNote,
    StructuredThesis,
    multi_agent_configuration_status,
)
from tradingagents.research_platform.narrative_provider import ResearchNarrativeContext


class FakeStructured:
    def __init__(self, owner, schema):
        self.owner, self.schema = owner, schema

    def invoke(self, prompt):
        self.owner.prompts.append(prompt)
        call = len(self.owner.prompts)
        if self.owner.fail_call == call:
            raise RuntimeError("fixture failure")
        if self.schema is StructuredThesis:
            return StructuredThesis(
                headline="综合研究命题", base_case="基本情景", bull_case="乐观情景",
                bear_case="悲观情景", catalysts=["催化剂"],
                disconfirming_evidence=["反证"], evidence_source_ids=["price:fixture"],
                confidence=.65,
            )
        return StructuredResearchNote(
            headline="角色结论", summary="仅依据归一化证据的结论。",
            supporting_points=["研究要点"], risks=["不确定性"],
            evidence_source_ids=["price:fixture"], confidence="medium",
        )


class FakeLLM:
    def __init__(self, fail_call=None):
        self.fail_call = fail_call
        self.prompts = []

    def with_structured_output(self, schema):
        return FakeStructured(self, schema)


def context():
    return ResearchNarrativeContext(
        symbol="002624", as_of_date=date(2026, 7, 12),
        evidence=[EvidenceRef(source_id="price:fixture", description="normalized bars",
                              as_of_date=date(2026, 7, 12), confidence=.9)],
    )


def test_orchestration_runs_specialists_debate_and_manager():
    llm = FakeLLM()
    outputs = MultiAgentResearchProvider(
        config=LLMResearchConfig(provider="deepseek", model="configured-model"), llm=llm
    ).generate(context())

    assert len(outputs) == 7
    assert len(llm.prompts) == 7
    assert outputs[-1].output_type == AgentOutputType.INVESTMENT_THESIS
    assert outputs[-1].payload.bull_case == "乐观情景"
    assert outputs[-1].metadata["provider"] == "deepseek"
    assert outputs[-1].metadata["research_only"] is True
    assert all(output.output_type != AgentOutputType.TRADE_SIGNAL for output in outputs)


def test_one_agent_failure_degrades_and_later_stages_continue():
    outputs = MultiAgentResearchProvider(
        config=LLMResearchConfig(provider="deepseek", model="configured-model"),
        llm=FakeLLM(fail_call=2),
    ).generate(context())
    assert outputs[1].output_type == AgentOutputType.COCKPIT_PANEL
    assert outputs[1].metadata["failed"] is True
    assert outputs[-1].output_type == AgentOutputType.INVESTMENT_THESIS


def test_unknown_evidence_is_rejected_and_degraded():
    class EscapingLLM(FakeLLM):
        def with_structured_output(self, schema):
            structured = super().with_structured_output(schema)
            original = structured.invoke
            def invoke(prompt):
                value = original(prompt)
                return value.model_copy(update={"evidence_source_ids": ["outside"]})
            structured.invoke = invoke
            return structured

    outputs = MultiAgentResearchProvider(
        config=LLMResearchConfig(provider="deepseek", model="configured-model"), llm=EscapingLLM()
    ).generate(context())
    assert all(output.output_type == AgentOutputType.COCKPIT_PANEL for output in outputs)


def test_configuration_status_never_exposes_key(monkeypatch):
    monkeypatch.setenv("TRADINGAGENTS_RESEARCH_LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("TRADINGAGENTS_RESEARCH_LLM_MODEL", "user-supplied-v4-pro-id")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "super-secret")
    status = multi_agent_configuration_status()
    assert status == {"ready": True, "message": "服务端多智能体模型配置已就绪",
                      "provider": "deepseek", "model": "user-supplied-v4-pro-id"}
    assert "secret" not in repr(status)


def test_configuration_requires_explicit_model(monkeypatch):
    monkeypatch.delenv("TRADINGAGENTS_RESEARCH_LLM_PROVIDER", raising=False)
    monkeypatch.delenv("TRADINGAGENTS_RESEARCH_LLM_MODEL", raising=False)
    assert multi_agent_configuration_status()["ready"] is False


def test_progress_callback_reports_each_bounded_stage():
    phases = []
    provider = MultiAgentResearchProvider(
        config=LLMResearchConfig(provider="deepseek", model="configured-model"),
        llm=FakeLLM(),
    )
    provider.set_progress_callback(phases.append)
    provider.generate(context())
    assert phases == [
        "multi_agent:fundamentals", "multi_agent:market", "multi_agent:game_news",
        "multi_agent:valuation", "multi_agent:bull", "multi_agent:bear",
        "multi_agent:manager", "multi_agent:complete",
    ]
