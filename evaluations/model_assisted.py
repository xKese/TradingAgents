"""Replaceable, optional model-assisted evaluation contracts."""

from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel, Field

DEFAULT_RUBRIC = {
    "claim_evidence_entailment": "Does cited evidence support the claim without adding facts?",
    "retrieval_relevance": "Is the evidence relevant to the operational question?",
    "operational_completeness": "Are applicable operational dimensions addressed?",
    "internal_contradiction": "Does the report contradict itself or its evidence?",
    "downstream_evidence_use": "Do downstream roles use cited analyst evidence?",
}


class ModelAssistedScore(BaseModel):
    """Subjective evaluator output; never treated as deterministic ground truth."""

    case_id: str
    evaluator_provider: str
    evaluator_model: str
    evaluator_version: str
    rubric: dict[str, str] = Field(default_factory=lambda: dict(DEFAULT_RUBRIC))
    raw_scores: dict[str, float]
    rationale: dict[str, str] = Field(default_factory=dict)


class EvaluatorProvider(Protocol):
    """Provider-neutral interface for optional model-assisted scoring."""

    provider_name: str
    model_name: str
    model_version: str

    def evaluate(
        self,
        *,
        case_id: str,
        artifact: dict[str, Any],
        rubric: dict[str, str],
    ) -> ModelAssistedScore:
        """Return raw subjective scores and rationale."""


def run_model_assisted(
    provider: EvaluatorProvider,
    artifacts: list[dict[str, Any]],
    *,
    rubric: dict[str, str] | None = None,
) -> list[ModelAssistedScore]:
    """Run an explicitly supplied evaluator provider; no default model is invoked."""
    selected_rubric = dict(rubric or DEFAULT_RUBRIC)
    return [
        provider.evaluate(
            case_id=artifact["case_id"],
            artifact=artifact,
            rubric=selected_rubric,
        )
        for artifact in artifacts
    ]
