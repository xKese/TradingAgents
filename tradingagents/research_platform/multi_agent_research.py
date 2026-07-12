"""Provider-neutral, evidence-bounded LLM research orchestration.

This is an additive adapter over normalized research records.  It never emits
trade signals and therefore cannot bypass the deterministic risk layer.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from time import perf_counter
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from tradingagents.llm_clients.api_key_env import get_api_key_env
from tradingagents.llm_clients.factory import create_llm_client

from .agent_contracts import (
    AgentOutputEnvelope,
    AgentOutputSection,
    AgentOutputType,
    AnalystNote,
    ConfidenceLevel,
    EvidenceRef,
    InvestmentThesis,
)
from .narrative_provider import (
    NarrativeProviderError,
    NarrativeProviderUnavailableError,
    ResearchNarrativeContext,
)

PROMPT_VERSION = "multi-agent-research-v2"
MAX_DETERMINISTIC_REPORT_CHARS = 24_000
ANALYST_ROLES = (
    ("fundamentals", "基本面分析师"),
    ("market", "技术与市场分析师"),
    ("game_news", "新闻、游戏产品与版号分析师"),
    ("valuation", "估值分析师"),
)


class StructuredResearchNote(BaseModel):
    model_config = ConfigDict(frozen=True)
    headline: str = Field(min_length=1, max_length=180)
    summary: str = Field(min_length=1, max_length=3000)
    supporting_points: list[str] = Field(default_factory=list, max_length=8)
    risks: list[str] = Field(default_factory=list, max_length=8)
    evidence_source_ids: list[str] = Field(default_factory=list)
    confidence: ConfidenceLevel = ConfidenceLevel.MEDIUM


class StructuredThesis(BaseModel):
    model_config = ConfigDict(frozen=True)
    headline: str = Field(min_length=1, max_length=180)
    base_case: str = Field(min_length=1, max_length=4000)
    bull_case: str = Field(min_length=1, max_length=3000)
    bear_case: str = Field(min_length=1, max_length=3000)
    catalysts: list[str] = Field(default_factory=list, max_length=8)
    disconfirming_evidence: list[str] = Field(default_factory=list, max_length=8)
    evidence_source_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)


class LLMResearchConfig(BaseModel):
    model_config = ConfigDict(frozen=True)
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    base_url: str | None = None

    @classmethod
    def from_environment(cls) -> LLMResearchConfig:
        legacy_deepseek_key = os.getenv("Deepseek Token-TA", "").strip()  # noqa: SIM112
        standard_deepseek_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
        if legacy_deepseek_key and not standard_deepseek_key:
            os.environ["DEEPSEEK_API_KEY"] = legacy_deepseek_key
            standard_deepseek_key = legacy_deepseek_key
        provider = os.getenv("TRADINGAGENTS_RESEARCH_LLM_PROVIDER", "").strip().lower()
        if not provider and standard_deepseek_key:
            provider = "deepseek"
        model = os.getenv("TRADINGAGENTS_RESEARCH_LLM_MODEL", "").strip()
        if not model and provider == "deepseek":
            model = "deepseek-v4-pro"
        base_url = os.getenv("TRADINGAGENTS_RESEARCH_LLM_BASE_URL", "").strip() or None
        if not provider or not model:
            raise NarrativeProviderUnavailableError(
                "Multi-agent research requires TRADINGAGENTS_RESEARCH_LLM_PROVIDER and "
                "TRADINGAGENTS_RESEARCH_LLM_MODEL. For DeepSeek set DEEPSEEK_API_KEY (legacy alias: Deepseek Token-TA)."
            )
        key_env = get_api_key_env(provider)
        if key_env and not os.getenv(key_env, "").strip() and provider not in {"ollama", "openai_compatible"}:
            raise NarrativeProviderUnavailableError(
                f"Multi-agent research provider '{provider}' requires server-side {key_env}."
            )
        return cls(provider=provider, model=model, base_url=base_url)


def multi_agent_configuration_status() -> dict[str, Any]:
    """Return safe readiness metadata; never return credential values."""
    try:
        config = LLMResearchConfig.from_environment()
    except NarrativeProviderUnavailableError as error:
        return {"ready": False, "message": str(error), "provider": None, "model": None}
    return {"ready": True, "message": "服务端多智能体模型配置已就绪", "provider": config.provider, "model": config.model}


class MultiAgentResearchProvider:
    """Run four specialists, a bounded bull/bear debate, and a manager synthesis."""

    def __init__(self, *, config: LLMResearchConfig, llm: Any, progress_callback: Callable[[str], None] | None = None):
        self.config = config
        self.llm = llm
        self.progress_callback = progress_callback
        self._context_audit: dict[str, str | int | float | bool | None] = {}

    def set_progress_callback(self, callback: Callable[[str], None]) -> None:
        self.progress_callback = callback

    @classmethod
    def from_environment(cls) -> MultiAgentResearchProvider:
        config = LLMResearchConfig.from_environment()
        try:
            llm = create_llm_client(
                config.provider, config.model, base_url=config.base_url
            ).get_llm()
        except Exception as error:
            raise NarrativeProviderUnavailableError(f"Unable to initialize multi-agent provider: {error}") from error
        return cls(config=config, llm=llm)

    def generate(self, context: ResearchNarrativeContext) -> list[AgentOutputEnvelope]:
        report_chars = len(context.deterministic_report_markdown or "")
        self._context_audit = {
            "deterministic_output_count": len(context.deterministic_outputs),
            "deterministic_report_chars": min(report_chars, MAX_DETERMINISTIC_REPORT_CHARS),
            "deterministic_report_truncated": report_chars > MAX_DETERMINISTIC_REPORT_CHARS,
        }
        outputs: list[AgentOutputEnvelope] = []
        notes: list[AgentOutputEnvelope] = []
        for role_id, role_name in ANALYST_ROLES:
            self._progress(f"multi_agent:{role_id}")
            result = self._note_call(context, role_id, role_name, _context_prompt(context))
            outputs.append(result)
            if result.payload is not None:
                notes.append(result)

        debate_context = _outputs_prompt(notes)
        self._progress("multi_agent:bull")
        bull = self._note_call(context, "bull", "Bull 研究员", debate_context)
        outputs.append(bull)
        self._progress("multi_agent:bear")
        bear = self._note_call(context, "bear", "Bear 研究员", debate_context + "\nBull观点:\n" + bull.summary)
        outputs.append(bear)

        self._progress("multi_agent:manager")
        manager_prompt = debate_context + "\nBull观点:\n" + bull.summary + "\nBear观点:\n" + bear.summary
        try:
            thesis, audit = self._invoke(StructuredThesis, _system("Research Manager", context) + manager_prompt)
            evidence = _select_evidence(context.evidence, thesis.evidence_source_ids)
            payload = InvestmentThesis(
                symbol=context.symbol, as_of_date=context.as_of_date,
                base_case=thesis.base_case, bull_case=thesis.bull_case, bear_case=thesis.bear_case,
                catalysts=thesis.catalysts, disconfirming_evidence=thesis.disconfirming_evidence,
                evidence=evidence, confidence=thesis.confidence,
            )
            outputs.append(AgentOutputEnvelope(
                symbol=context.symbol, as_of_date=context.as_of_date,
                agent_id="llm-research-manager", agent_role="Research Manager",
                output_type=AgentOutputType.INVESTMENT_THESIS, headline=thesis.headline,
                summary=thesis.base_case, evidence=evidence,
                sections=[
                    AgentOutputSection(title="基础情景", summary=thesis.base_case, evidence=evidence),
                    AgentOutputSection(title="乐观情景", summary=thesis.bull_case),
                    AgentOutputSection(title="悲观情景", summary=thesis.bear_case),
                    AgentOutputSection(title="潜在催化剂", bullets=thesis.catalysts),
                    AgentOutputSection(title="反证与失效条件", bullets=thesis.disconfirming_evidence),
                ],
                risks=thesis.disconfirming_evidence, payload=payload,
                confidence=_confidence(thesis.confidence), metadata=self._metadata("manager", audit),
            ))
        except Exception as error:
            outputs.append(self._failure(context, "manager", "Research Manager", error))
        self._progress("multi_agent:complete")
        return outputs

    def _progress(self, phase: str) -> None:
        if self.progress_callback is not None:
            self.progress_callback(phase)

    def _note_call(self, context: ResearchNarrativeContext, role_id: str, role_name: str, material: str) -> AgentOutputEnvelope:
        try:
            note, audit = self._invoke(StructuredResearchNote, _system(role_name, context) + material)
            evidence = _select_evidence(context.evidence, note.evidence_source_ids)
            payload = AnalystNote(symbol=context.symbol, analyst_role=role_name,
                as_of_date=context.as_of_date, summary=note.summary, evidence=evidence,
                risks=note.risks, confidence=note.confidence)
            return AgentOutputEnvelope(symbol=context.symbol, as_of_date=context.as_of_date,
                agent_id=f"llm-{role_id}", agent_role=role_name,
                output_type=AgentOutputType.ANALYST_NOTE, headline=note.headline,
                summary=note.summary, sections=[AgentOutputSection(title="研究要点", bullets=note.supporting_points, evidence=evidence)],
                evidence=evidence, risks=note.risks, confidence=note.confidence, payload=payload,
                metadata=self._metadata(role_id, audit))
        except Exception as error:
            return self._failure(context, role_id, role_name, error)

    def _invoke(self, schema: type[BaseModel], prompt: str) -> tuple[Any, dict[str, Any]]:
        started = perf_counter()
        response = self.llm.with_structured_output(schema).invoke(prompt)
        elapsed = round((perf_counter() - started) * 1000)
        value = response if isinstance(response, schema) else schema.model_validate(response)
        usage = getattr(response, "usage_metadata", None) or {}
        return value, {"latency_ms": elapsed, **{f"usage_{k}": v for k, v in usage.items() if isinstance(v, (str, int, float, bool))}}

    def _metadata(self, stage: str, audit: dict[str, Any]) -> dict[str, Any]:
        return {"provider": self.config.provider, "model": self.config.model,
            "prompt_version": PROMPT_VERSION, "stage": stage, "research_only": True, **self._context_audit, **audit}

    def _failure(self, context: ResearchNarrativeContext, role_id: str, role_name: str, error: Exception) -> AgentOutputEnvelope:
        return AgentOutputEnvelope(symbol=context.symbol, as_of_date=context.as_of_date,
            agent_id=f"llm-{role_id}", agent_role=role_name,
            output_type=AgentOutputType.COCKPIT_PANEL, headline=f"{role_name}已降级",
            summary=f"该阶段未生成模型结论；其余研究阶段继续。原因：{error.__class__.__name__}",
            risks=["模型阶段失败，不能将此输出视为完整研究结论。"], confidence=ConfidenceLevel.LOW,
            metadata=self._metadata(role_id, {"failed": True, "error_type": error.__class__.__name__}))


def _system(role: str, context: ResearchNarrativeContext) -> str:
    ids = ", ".join(item.source_id for item in context.evidence) or "none"
    return (f"你是{role}。只可使用所给 normalized records 和由它们生成的确定性研究底稿，禁止引入外部事实或虚构引用。"
            f"只能引用这些 evidence_source_ids: {ids}。不得给出交易方向、仓位或绕过风控。\n")


def _context_prompt(context: ResearchNarrativeContext) -> str:
    prices = [f"{x.date}:{x.close}/{x.volume}" for x in sorted(context.price_bars, key=lambda x: x.date)[-20:]]
    fundamentals = [f"{k}={v}" for row in context.fundamentals[-2:] for k, v in sorted(row.metrics.items())]
    news = [f"{x.published_at.date()}|{x.provider}|{x.title}" for x in context.news[:12]]
    refs = [f"{x.source_id}|{x.description}" for x in context.evidence]
    deterministic_outputs = [
        f"{x.agent_role}|{x.output_type.value}|{x.headline}|{x.summary}"
        for x in context.deterministic_outputs
    ]
    report = context.deterministic_report_markdown or "None available"
    report_truncated = len(report) > MAX_DETERMINISTIC_REPORT_CHARS
    report = report[:MAX_DETERMINISTIC_REPORT_CHARS]
    return (
        f"Symbol={context.symbol}; as_of={context.as_of_date}\n"
        f"Prices={prices}\nFundamentals={fundamentals}\nNews={news}\nEvidence={refs}\n"
        f"Deterministic outputs={deterministic_outputs}\n"
        f"Deterministic report (truncated={report_truncated}):\n{report}\n"
    )


def _outputs_prompt(outputs: list[AgentOutputEnvelope]) -> str:
    return "\n".join(f"{x.agent_role}: {x.summary}; refs={[e.source_id for e in x.evidence]}" for x in outputs)


def _select_evidence(allowed: list[EvidenceRef], requested: list[str]) -> list[EvidenceRef]:
    by_id = {item.source_id: item for item in allowed}
    unknown = sorted(set(requested) - set(by_id))
    if unknown:
        raise NarrativeProviderError(f"Model cited evidence outside the normalized context: {unknown}")
    return [by_id[item] for item in dict.fromkeys(requested)]


def _confidence(value: float) -> ConfidenceLevel:
    return ConfidenceLevel.HIGH if value >= .75 else ConfidenceLevel.LOW if value < .4 else ConfidenceLevel.MEDIUM
