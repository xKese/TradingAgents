"""Coordinated advisory review for an IBKR-aware batch analysis."""

from __future__ import annotations

import csv
import json
import logging
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from tradingagents.agents.utils.structured import bind_structured

logger = logging.getLogger(__name__)


class PortfolioAction(BaseModel):
    ticker: str
    action: Literal["Add", "Hold existing", "Trim", "Exit", "Avoid", "Review"]
    priority: Literal["High", "Medium", "Low"]
    current_shares: float | None = None
    proposed_shares: float | None = None
    share_change: float | None = None
    current_weight_pct: float | None = None
    proposed_weight_pct: float | None = None
    rationale: str


class PortfolioReview(BaseModel):
    executive_assessment: str = Field(
        description="Concise assessment of portfolio exposure and the highest-priority actions."
    )
    conflicts_and_overrides: list[str]
    risk_triggers: list[str]
    data_quality_warnings: list[str]
    actions: list[PortfolioAction]


def _position_lines(snapshot: dict) -> list[str]:
    positions = list(snapshot.get("positions") or [])
    largest = positions[0].get("symbol") if positions else None
    lines = []
    for position in positions:
        label = position.get("symbol", "UNKNOWN")
        if label == largest:
            label += " (largest holding)"
        lines.append(f"- {label}: {json.dumps(position, sort_keys=True)}")
    return lines


def _review_prompt(snapshot: dict, rows: list[dict], decisions: dict[str, str]) -> str:
    failures = [
        f"- {row.get('ticker')}: {row.get('error') or 'analysis failed'}"
        for row in rows
        if row.get("status") != "success"
    ]
    decision_lines = [
        f"### {ticker}\n{decision}" for ticker, decision in decisions.items()
    ]
    return "\n".join(
        [
            "You are the final portfolio reviewer. Produce an advisory, read-only rebalance review.",
            "Never place or imply automatic execution of an order.",
            "Use whole shares where practical and explain any action that cannot be quantified.",
            "Soft concentration warning: 10% of NAV.",
            "Never sum values across currencies or invent a currency conversion.",
            "Reconcile standalone ratings with current ownership, cash, position weights, and concentration.",
            "Flag a Buy or Overweight conflict when the ticker is already a large holding.",
            "\nAccount summary:",
            json.dumps(
                {
                    "base_currency": snapshot.get("base_currency"),
                    "net_liquidation": snapshot.get("net_liquidation"),
                    "cash": snapshot.get("cash"),
                    "available_funds": snapshot.get("available_funds"),
                },
                sort_keys=True,
            ),
            "\nPositions:",
            *(_position_lines(snapshot) or ["- none"]),
            "\nSuccessful ticker decisions:",
            *(decision_lines or ["- none"]),
            "\nCoverage warnings from failed analyses:",
            *(failures or ["- none"]),
        ]
    )


def build_portfolio_review(
    snapshot: dict,
    rows: list[dict],
    decisions: dict[str, str],
    llm,
) -> PortfolioReview:
    """Ask one LLM to reconcile per-ticker decisions with the frozen account."""
    prompt = _review_prompt(snapshot, rows, decisions)
    structured_llm = bind_structured(llm, PortfolioReview, "Portfolio Reviewer")
    if structured_llm is not None:
        try:
            result = structured_llm.invoke(prompt)
            if isinstance(result, PortfolioReview):
                return result
            return PortfolioReview.model_validate(result)
        except Exception as exc:
            logger.warning("Portfolio Reviewer structured output failed: %s", exc)

    response = llm.invoke(prompt)
    failed = [row.get("ticker", "UNKNOWN") for row in rows if row.get("status") != "success"]
    return PortfolioReview(
        executive_assessment=response.content,
        conflicts_and_overrides=[],
        risk_triggers=[],
        data_quality_warnings=(
            ["Unstructured reviewer fallback used."]
            + ([f"Missing analysis coverage: {', '.join(failed)}"] if failed else [])
        ),
        actions=[],
    )


def _fmt(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def write_portfolio_review(
    review: PortfolioReview, batch_dir: Path
) -> tuple[Path, Path]:
    """Write deterministic Markdown and CSV advisory artifacts."""
    batch_dir = Path(batch_dir)
    batch_dir.mkdir(parents=True, exist_ok=True)
    markdown_path = batch_dir / "portfolio_review.md"
    csv_path = batch_dir / "portfolio_actions.csv"

    lines = [
        "# Portfolio Review",
        "",
        review.executive_assessment,
        "",
        "## Prioritized Actions",
        "",
        "| Priority | Ticker | Action | Current Shares | Proposed Shares | Share Change | Current Weight | Proposed Weight | Rationale |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for action in review.actions:
        lines.append(
            "| "
            + " | ".join(
                [
                    action.priority,
                    action.ticker,
                    action.action,
                    _fmt(action.current_shares),
                    _fmt(action.proposed_shares),
                    _fmt(action.share_change),
                    _fmt(action.current_weight_pct),
                    _fmt(action.proposed_weight_pct),
                    action.rationale.replace("|", "\\|"),
                ]
            )
            + " |"
        )
    for heading, values in (
        ("Conflicts and Overrides", review.conflicts_and_overrides),
        ("Risk Triggers", review.risk_triggers),
        ("Data Quality Warnings", review.data_quality_warnings),
    ):
        lines.extend(["", f"## {heading}", ""])
        lines.extend([f"- {value}" for value in values] or ["- None"])
    markdown_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    fields = list(PortfolioAction.model_fields)
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for action in review.actions:
            writer.writerow(action.model_dump())
    return markdown_path, csv_path
