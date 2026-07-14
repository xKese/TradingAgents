"""Inverted screen for the short sleeve: expensive/deteriorating AND red flag.

The mirror of ops/research/screener.py's "cheap AND change trigger": a name
is a short candidate only when >= MIN_BARS of the expensive/deteriorating
bars fire AND at least one red-flag trigger (SHORT_TRIGGER_KINDS, produced
by ops/research/short_triggers.py) is present. Thresholds are tunable
constants, same convention as the long screen. Pure module: no I/O, every
input arrives via NameInputs.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from ops.research.screener import Bar, NameInputs, _ev_ebit
from ops.research.triggers import Trigger

EV_EBIT_EXPENSIVE = Decimal("20")
NEGATIVE_EBIT_MIN_CAP = Decimal("500000000")
NET_DEBT_EBITDA_HIGH = Decimal("4")
GROSS_MARGIN_DECLINE_PP = Decimal("0.03")
MIN_BARS = 2

SHORT_TRIGGER_KINDS = frozenset({"red_flag_8k", "insider_sell_cluster", "going_concern"})


@dataclass(frozen=True)
class ShortScreenResult:
    symbol: str
    asof: date
    passed: bool
    bars: tuple[Bar, ...]
    red_flags: tuple[Trigger, ...]
    market_cap: Decimal
    ev_ebit: Decimal | None


def _expensive_bar(inputs: NameInputs) -> Bar:
    name = "ev_ebit_expensive"
    f = inputs.fundamentals
    if f.ebit is not None and f.ebit <= 0:
        # No earnings at all: "expensive" iff a real market cap rides on them.
        passed = inputs.market_cap > NEGATIVE_EBIT_MIN_CAP
        return Bar(name, passed,
                   f"EBIT <= 0 with market cap {inputs.market_cap}")
    ev = _ev_ebit(inputs)
    if ev is None:
        return Bar(name, False, "missing: EV/EBIT not computable")
    return Bar(name, ev > EV_EBIT_EXPENSIVE,
               f"EV/EBIT {ev:.1f} vs {EV_EBIT_EXPENSIVE}")


def _net_debt_bar(inputs: NameInputs) -> Bar:
    name = "net_debt_ebitda_high"
    f = inputs.fundamentals
    if f.total_debt is None or f.ebitda is None or f.ebitda <= 0:
        return Bar(name, False, "missing: net debt/EBITDA not computable")
    cash = f.cash if f.cash is not None else Decimal("0")
    ratio = (f.total_debt - cash) / f.ebitda
    return Bar(name, ratio > NET_DEBT_EBITDA_HIGH,
               f"net debt/EBITDA {ratio:.2f} vs {NET_DEBT_EBITDA_HIGH}")


def _margin_decline_bar(inputs: NameInputs) -> Bar:
    name = "gross_margin_declining"
    hist = sorted(inputs.fundamentals.gross_margin_history,
                  key=lambda yv: yv.fiscal_year_end, reverse=True)
    if len(hist) < 2:
        return Bar(name, False, "missing: insufficient margin history")
    decline = hist[1].value - hist[0].value
    return Bar(
        name, decline >= GROSS_MARGIN_DECLINE_PP,
        f"gross margin {(-decline * 100):.1f}pp YoY vs -{GROSS_MARGIN_DECLINE_PP * 100}pp",
    )


def screen_short_universe(
    inputs: list[NameInputs], *, asof: date,
) -> list[ShortScreenResult]:
    out = []
    for name in inputs:
        bars = (_expensive_bar(name), _net_debt_bar(name), _margin_decline_bar(name))
        red_flags = tuple(t for t in name.triggers if t.kind in SHORT_TRIGGER_KINDS)
        fired = sum(1 for b in bars if b.passed)
        out.append(ShortScreenResult(
            symbol=name.symbol, asof=asof,
            passed=fired >= MIN_BARS and bool(red_flags),
            bars=bars, red_flags=red_flags,
            market_cap=name.market_cap, ev_ebit=_ev_ebit(name),
        ))
    return out
