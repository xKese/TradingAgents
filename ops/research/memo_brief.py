"""Deterministic memo -> research-brief distillation for graph injection.

The graph vets a brain memo by receiving a compact, labelled summary of it
as ``research_memo_context``. This is a pure function — no LLM — so the
brief is reproducible and its length is bounded (ds4's context is finite;
the brief must never crowd out the analysts' own tool output).
"""

from __future__ import annotations

from tradingagents.memos.schema import Memo

MAX_EVIDENCE_ITEMS = 8
MAX_QUOTE_CHARS = 240
MAX_BRIEF_CHARS = 6000


def build_research_brief(memo: Memo) -> str:
    """Distill a memo into a labelled plain-text brief, bounded in length."""
    lines: list[str] = [
        f"RESEARCH MEMO BRIEF — {memo.ticker} "
        f"(as of {memo.as_of_date.isoformat()}, {memo.thesis_type} thesis, "
        f"researcher conviction: {memo.conviction_tier})",
        f"THESIS: {memo.thesis}",
    ]
    if memo.value_block is not None:
        vb = memo.value_block
        lines += [
            f"WHY CHEAP (the bear's answer): {vb.why_cheap}",
            f"CHANGE TRIGGER: {vb.change_trigger}",
            f"NORMALIZED EARNINGS VIEW: {vb.normalized_earnings_view}",
            f"QUALITY: {vb.quality_assessment}",
        ]
    if memo.event_block is not None:
        eb = memo.event_block
        lines += [
            f"EVENT: {eb.event_type} — seller: {eb.seller_identity}",
            f"WHY NON-ECONOMIC: {eb.why_non_economic}",
        ]
        if eb.pressure_end_estimate is not None:
            lines.append(
                f"PRESSURE END (est.): {eb.pressure_end_estimate.isoformat()}"
            )
        for kd in eb.key_dates:
            when = kd.expected_date.isoformat() if kd.expected_date else "date TBD"
            hard = " [hard date]" if kd.hard_date else ""
            lines.append(f"KEY DATE: {when}{hard} — {kd.description}")
    lines.append(
        f"PRICE: ref {memo.entry_price_ref}, "
        f"target {memo.price_target_low}-{memo.price_target_high}, "
        f"horizon {memo.expected_holding_months}mo"
    )
    lines.append("MUST BE TRUE:")
    lines += [f"- {m}" for m in memo.must_be_true]
    shown = min(len(memo.evidence), MAX_EVIDENCE_ITEMS)
    lines.append(f"EVIDENCE (cited; top {shown} of {len(memo.evidence)}):")
    for item in memo.evidence[:MAX_EVIDENCE_ITEMS]:
        quote = f' "{item.quote[:MAX_QUOTE_CHARS]}"' if item.quote else ""
        lines.append(f"- {item.claim} [{item.source_ref}]{quote}")
    lines.append("FALSIFIERS (pre-committed exit conditions):")
    for f in memo.falsifiers:
        mech = (
            f" [{f.metric} {f.operator} {f.threshold}]"
            if f.metric and f.operator is not None and f.threshold is not None
            else ""
        )
        lines.append(f"- {f.description}{mech}")
    return "\n".join(lines)[:MAX_BRIEF_CHARS]
