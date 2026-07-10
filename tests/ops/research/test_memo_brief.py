"""build_research_brief: deterministic memo distillation for graph injection."""
from datetime import date

from ops.research.memo_brief import (
    MAX_BRIEF_CHARS, MAX_EVIDENCE_ITEMS, build_research_brief,
)
from tradingagents.memos.schema import (
    Catalyst, EventThesis, EvidenceItem, Falsifier, Memo, ValueThesis,
)


def _value_memo(**overrides):
    base = dict(
        ticker="ACME", as_of_date=date(2026, 7, 1), thesis_type="value",
        thesis="Mispriced earning power after a guidance-cut selloff.",
        evidence=[
            EvidenceItem(claim=f"claim {i}", source_type="filing",
                         source_ref=f"0001:mdna:{i}", quote=f"quote {i}")
            for i in range(12)
        ],
        value_block=ValueThesis(
            why_cheap="Segment X is in decline and the market extrapolates it.",
            change_trigger="New CEO with cost-cut mandate.",
            normalized_earnings_view="Through-cycle EPS ~2x screen optics.",
            quality_assessment="Net cash, 20% ROIC ex the declining segment.",
        ),
        conviction_tier="medium", entry_price_ref=10.0,
        price_target_low=15.0, price_target_high=20.0,
        expected_holding_months=12,
        must_be_true=["Segment Y keeps growing", "No covenant breach"],
        falsifiers=[
            Falsifier(description="Gross margin collapses",
                      check_type="fundamental", metric="gross_margin_pct",
                      operator="<", threshold=30.0),
            Falsifier(description="Story breaks down", check_type="event"),
        ],
    )
    base.update(overrides)
    return Memo(**base)


def test_brief_contains_the_load_bearing_fields():
    brief = build_research_brief(_value_memo())
    assert "ACME" in brief
    assert "Mispriced earning power" in brief
    assert "Segment X is in decline" in brief          # why_cheap
    assert "Segment Y keeps growing" in brief          # must_be_true
    assert "claim 0" in brief and "[0001:mdna:0]" in brief  # cited evidence
    assert "gross_margin_pct < 30.0" in brief          # machine falsifier
    assert "Story breaks down" in brief                # prose falsifier
    assert "15.0" in brief and "20.0" in brief         # targets


def test_brief_caps_evidence_at_top_n():
    brief = build_research_brief(_value_memo())
    assert f"claim {MAX_EVIDENCE_ITEMS - 1}" in brief
    assert f"claim {MAX_EVIDENCE_ITEMS}" not in brief


def test_brief_is_deterministic():
    memo = _value_memo()
    assert build_research_brief(memo) == build_research_brief(memo)


def test_brief_is_bounded_on_a_monster_memo():
    memo = _value_memo(
        thesis="T" * 20000,
        must_be_true=["M" * 2000] * 10,
    )
    assert len(build_research_brief(memo)) <= MAX_BRIEF_CHARS


def test_brief_truncates_long_quotes():
    memo = _value_memo(evidence=[
        EvidenceItem(claim="c", source_type="filing", source_ref="0001:mdna",
                     quote="q" * 5000),
    ])
    brief = build_research_brief(memo)
    assert "q" * 241 not in brief


def test_event_memo_renders_event_block():
    memo = _value_memo(
        thesis_type="event", value_block=None,
        event_block=EventThesis(
            event_type="spinoff", seller_identity="index funds",
            why_non_economic="Forced deletion selling at any price.",
            pressure_end_estimate=date(2026, 9, 30),
            key_dates=[Catalyst(description="distribution",
                                expected_date=date(2026, 8, 1), hard_date=True)],
        ),
    )
    brief = build_research_brief(memo)
    assert "spinoff" in brief
    assert "index funds" in brief
    assert "Forced deletion selling" in brief


def test_evidence_without_quote_is_fine():
    memo = _value_memo(evidence=[
        EvidenceItem(claim="bare claim", source_type="filing", source_ref="0001:rf"),
    ])
    assert "bare claim" in build_research_brief(memo)
