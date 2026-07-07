"""Per-stage LLM construction for the research brain.

Each pipeline stage (evidence, thesis) gets its own model spec so any single
stage can be pointed at a bigger local model — or an API model — by config
change alone (spec decision 3's escape hatch). Spec format:

    provider:model              e.g.  anthropic:claude-sonnet-5
    provider:model@base_url     e.g.  openai_compatible:deepseek-v4-flash@http://127.0.0.1:8000/v1
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelSpec:
    provider: str
    model: str
    base_url: str | None = None


def parse_model_spec(spec: str) -> ModelSpec:
    head, _, url = spec.partition("@")
    provider, _, model = head.partition(":")
    provider, model, url = provider.strip(), model.strip(), url.strip()
    if not provider or not model or ("@" in spec and not url):
        raise ValueError(
            f"invalid model spec {spec!r}: expected 'provider:model' or "
            "'provider:model@base_url'"
        )
    return ModelSpec(provider=provider, model=model, base_url=url or None)


def build_stage_llm(spec: str):
    """Build a LangChain chat model for one pipeline stage."""
    parsed = parse_model_spec(spec)
    from tradingagents.llm_clients import create_llm_client

    return create_llm_client(
        provider=parsed.provider, model=parsed.model, base_url=parsed.base_url,
    ).get_llm()
