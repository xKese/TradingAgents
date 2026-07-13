"""Mechanical accept/reject gate for locally-generated memos.

Local models produce plausible-looking garbage; the defense is structural,
not prompt hope (spec decision 3). Every check here is machine-decidable:
a memo that fails is rejected (the hit is marked failed after one retry),
never stored. The monitoring loop (Phase C) depends on check 2: without a
machine-checkable falsifier a memo can never be mechanically monitored.
"""

from __future__ import annotations

from tradingagents.memos.schema import EvidenceItem, Memo


def is_machine_checkable(falsifier) -> bool:
    """A falsifier is mechanically monitorable iff it has metric+operator+threshold.

    Shared with the graph-vetting stage's risk-debate extraction gate
    (ops/research/vetting.py), so the debate can only ADD falsifiers that
    the monitoring loop can actually evaluate.
    """
    return (
        bool(falsifier.metric)
        and falsifier.operator is not None
        and falsifier.threshold is not None
    )


_DRAWDOWN_METRIC = "drawdown_from_cost_pct"


def drawdown_convention_problem(falsifier) -> str | None:
    """Canonical drawdown falsifier form: positive percent below cost, with
    > or >=. The pre-check corpus mixed ratios (> 0.25), signed returns
    (< -25), and percents (> 25); the evaluator speaks only
    positive-percent-down, so any other form silently never trips — or
    trips on gains (the 2026-07-13 CRC false escalation). Shared by
    validate_memo (brain path) and the vetting keep-filter."""
    if falsifier.metric != _DRAWDOWN_METRIC or not is_machine_checkable(falsifier):
        return None
    if falsifier.operator not in (">", ">="):
        return (
            "drawdown_from_cost_pct falsifier must use > or >= with a "
            "positive percent below cost (e.g. '> 25' = down 25%), got "
            f"operator {falsifier.operator!r}"
        )
    if not 1 <= falsifier.threshold <= 100:
        return (
            "drawdown_from_cost_pct threshold must be a percent in [1, 100] "
            f"(25 means down 25% from cost), got {falsifier.threshold} — "
            "ratio-form thresholds like 0.25 are indistinguishable from "
            "sub-1% noise and are rejected outright"
        )
    return None


def resolve_evidence(
    items: list[EvidenceItem], allowed_refs: set[str],
) -> tuple[list[EvidenceItem], list[str]]:
    """Keep items citing a section we actually read; explain each drop.

    v1 evidence only ever comes from filings the pipeline fetched, so
    non-filing source types are confabulation by construction.
    """
    kept: list[EvidenceItem] = []
    dropped: list[str] = []
    for item in items:
        if item.source_type != "filing":
            dropped.append(
                f"non-filing evidence ({item.source_type}): {item.claim[:80]!r}"
            )
        elif item.source_ref not in allowed_refs:
            dropped.append(
                f"unresolvable citation {item.source_ref!r}: {item.claim[:80]!r}"
            )
        else:
            kept.append(item)
    return kept, dropped


def validate_memo(
    memo: Memo, *, allowed_refs: set[str], known_precedents: set[str],
) -> list[str]:
    """All reasons this memo must be rejected; empty means store it."""
    errors: list[str] = []
    if not memo.block_matches_type():
        errors.append(
            "thesis block does not match thesis_type (fill exactly the "
            f"{memo.thesis_type}_block)"
        )
    if not any(is_machine_checkable(f) for f in memo.falsifiers):
        errors.append(
            "no machine-checkable falsifier (need metric+operator+threshold "
            "on at least one)"
        )
    for f in memo.falsifiers:
        problem = drawdown_convention_problem(f)
        if problem:
            errors.append(problem)
    for item in memo.evidence:
        if item.source_type == "filing" and item.source_ref not in allowed_refs:
            errors.append(f"evidence cites unread section {item.source_ref!r}")
    for pid in memo.precedent_memo_ids:
        if pid not in known_precedents:
            errors.append(f"precedent memo id {pid!r} does not exist")
    if memo.entry_price_ref <= 0:
        errors.append(f"entry_price_ref must be positive, got {memo.entry_price_ref}")
    if memo.price_target_low > memo.price_target_high:
        errors.append(
            f"price_target_low {memo.price_target_low} exceeds "
            f"price_target_high {memo.price_target_high}"
        )
    return errors
