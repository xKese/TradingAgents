"""Coordinated advisory review for an IBKR-aware batch analysis."""

from __future__ import annotations

import csv
import json
import logging
import re
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
            "Return exactly one action for every successful ticker decision.",
            "When weights_reconciled_to_base_nav is true, portfolio weights are authoritative base-NAV percentages; do not claim they lack FX conversion or reconciliation.",
            "Reconcile standalone ratings with current ownership, cash, position weights, and concentration.",
            "Flag a Buy or Overweight conflict when the ticker is already a large holding.",
            "\nAccount summary:",
            json.dumps(
                {
                    "base_currency": snapshot.get("base_currency"),
                    "net_liquidation": snapshot.get("net_liquidation"),
                    "cash": snapshot.get("cash"),
                    "available_funds": snapshot.get("available_funds"),
                    "weights_reconciled_to_base_nav": snapshot.get(
                        "weights_reconciled_to_base_nav", False
                    ),
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


def _decision_field(decision: str, field: str) -> str:
    match = re.search(
        rf"\*\*{re.escape(field)}\*\*:\s*(.*?)(?=\n\s*\n|\Z)",
        decision,
        flags=re.IGNORECASE | re.DOTALL,
    )
    return match.group(1).strip() if match else ""


def _derive_action(
    ticker: str, decision: str, position: dict | None
) -> PortfolioAction:
    rating = _decision_field(decision, "Rating").splitlines()[0].strip().lower()
    summary = _decision_field(decision, "Executive Summary")
    current_shares = position.get("quantity") if position else None
    current_weight = position.get("portfolio_weight_pct") if position else None
    owned = bool(position and current_shares)
    action_map = {
        "buy": "Add",
        "overweight": "Add",
        "hold": "Hold existing" if owned else "Avoid",
        "underweight": "Trim" if owned else "Avoid",
        "sell": "Exit" if owned else "Avoid",
    }
    action = action_map.get(rating, "Review")
    proposed_shares = None

    reduction = re.search(
        r"\b(?:sell|trim|reduce(?:\s+by)?)\s+(\d+(?:\.\d+)?)"
        r"(?:\s+(?:share|shares))?(?:\s+of\s+(\d+(?:\.\d+)?))?",
        summary,
        flags=re.IGNORECASE,
    )
    maintained = re.search(
        r"\b(?:maintain|hold|retain)\s+(?:the\s+)?(?:current\s+)?"
        r"(\d+(?:\.\d+)?)[- ]share",
        summary,
        flags=re.IGNORECASE,
    )
    if reduction and current_shares is not None:
        proposed_shares = max(0.0, float(current_shares) - float(reduction.group(1)))
    elif maintained:
        proposed_shares = float(maintained.group(1))
    elif action == "Hold existing" and current_shares is not None:
        proposed_shares = float(current_shares)
    elif action == "Exit" and current_shares is not None:
        proposed_shares = 0.0

    share_change = None
    proposed_weight = None
    if current_shares is not None and proposed_shares is not None:
        share_change = proposed_shares - float(current_shares)
        if current_weight is not None and float(current_shares) != 0:
            proposed_weight = (
                float(current_weight) * proposed_shares / float(current_shares)
            )

    return PortfolioAction(
        ticker=ticker,
        action=action,
        priority="High" if rating in {"sell", "underweight"} else "Medium",
        current_shares=current_shares,
        proposed_shares=proposed_shares,
        share_change=share_change,
        current_weight_pct=current_weight,
        proposed_weight_pct=proposed_weight,
        rationale=summary or decision.strip()[:500],
    )


def _false_threshold_claim(text: str, positions: dict[str, dict]) -> bool:
    lowered = text.lower()
    if "exceed" not in lowered or "10%" not in lowered:
        return False
    for ticker, position in positions.items():
        weight = position.get("portfolio_weight_pct")
        if ticker.lower() in lowered and weight is not None and float(weight) < 10:
            return True
    return False


def _false_reconciliation_warning(text: str) -> bool:
    lowered = text.lower()
    phrases = (
        "cannot be reconciled",
        "cannot reconcile",
        "without an fx conversion",
        "without conversion",
        "no conversion rate",
        "no conversion is available",
        "without an explicit exchange rate",
        "usd values only",
        "usd-terms positions",
        "usd values divided directly",
    )
    return any(phrase in lowered for phrase in phrases)


def normalize_portfolio_review(
    review: PortfolioReview,
    snapshot: dict,
    decisions: dict[str, str],
) -> PortfolioReview:
    """Enforce action coverage and remove claims contradicted by account facts."""
    normalized = review.model_copy(deep=True)
    positions = {
        str(position.get("symbol", "")).upper(): position
        for position in snapshot.get("positions") or []
    }
    existing = {action.ticker.upper() for action in normalized.actions}
    for ticker, decision in decisions.items():
        if ticker.upper() not in existing:
            normalized.actions.append(
                _derive_action(ticker, decision, positions.get(ticker.upper()))
            )
            existing.add(ticker.upper())

    normalized.conflicts_and_overrides = [
        text
        for text in normalized.conflicts_and_overrides
        if not _false_threshold_claim(text, positions)
    ]
    if snapshot.get("weights_reconciled_to_base_nav"):
        normalized.data_quality_warnings = [
            text
            for text in normalized.data_quality_warnings
            if not _false_reconciliation_warning(text)
        ]
    return normalized


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
                review = result
            else:
                review = PortfolioReview.model_validate(result)
            return normalize_portfolio_review(review, snapshot, decisions)
        except Exception as exc:
            logger.warning("Portfolio Reviewer structured output failed: %s", exc)

    response = llm.invoke(prompt)
    failed = [row.get("ticker", "UNKNOWN") for row in rows if row.get("status") != "success"]
    review = PortfolioReview(
        executive_assessment=response.content,
        conflicts_and_overrides=[],
        risk_triggers=[],
        data_quality_warnings=(
            ["Unstructured reviewer fallback used."]
            + ([f"Missing analysis coverage: {', '.join(failed)}"] if failed else [])
        ),
        actions=[],
    )
    return normalize_portfolio_review(review, snapshot, decisions)


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
