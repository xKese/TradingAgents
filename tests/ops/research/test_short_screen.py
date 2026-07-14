"""Unit tests for the inverted short screen (no I/O)."""

from datetime import date
from decimal import Decimal

import pytest

from ops.research.screener import NameInputs
from ops.research.short_screen import screen_short_universe
from ops.research.triggers import Trigger
from tradingagents.dataflows.fundamentals import Fundamentals, YearValue

pytestmark = pytest.mark.unit

ASOF = date(2026, 7, 13)
D = Decimal

RED_FLAG = Trigger(kind="red_flag_8k", description="4.02 non-reliance",
                   date=date(2026, 7, 1), source="0001-26-000001")


def _yv(pairs):
    return tuple(YearValue(date(y, 12, 31), D(str(v))) for y, v in pairs)


def _fund(ticker, **overrides):
    defaults = {
        "ticker": ticker,
        "asof": ASOF,
        "ebit": D("10"),          # EV/EBIT = (1000 + 500 - 0) / 10 = 150: expensive
        "ebitda": D("100"),
        "total_debt": D("500"),   # net debt / EBITDA = 5 > 4: high
        "cash": D("0"),
        "fcf": D("5"),
        "eps_history": _yv([(2024, "1.0"), (2025, "0.5")]),
        "roic_history": _yv([(2024, "0.05"), (2025, "0.04")]),
        "gross_margin_history": _yv([(2024, "0.40"), (2025, "0.41")]),
    }
    defaults.update(overrides)
    return Fundamentals(**defaults)


def _inputs(symbol, *, triggers=(), fund=None, market_cap=D("1000")):
    return NameInputs(
        symbol=symbol, sector="Industrials", price=D("20"), market_cap=market_cap,
        fundamentals=fund or _fund(symbol), triggers=tuple(triggers),
        year_end_prices={date(y, 12, 31): D("40") for y in (2024, 2025)},
    )


def test_expensive_plus_red_flag_passes():
    (res,) = screen_short_universe([_inputs("BADCO", triggers=[RED_FLAG])], asof=ASOF)
    assert res.passed
    assert res.red_flags == (RED_FLAG,)
    assert sum(1 for b in res.bars if b.passed) >= 2


def test_no_red_flag_never_passes():
    (res,) = screen_short_universe([_inputs("BADCO")], asof=ASOF)
    assert not res.passed


def test_non_short_trigger_kind_is_not_a_red_flag():
    activist = Trigger(kind="activist_stake", description="13D", date=ASOF, source="a1")
    (res,) = screen_short_universe([_inputs("BADCO", triggers=[activist])], asof=ASOF)
    assert not res.passed and res.red_flags == ()


def test_one_bar_plus_red_flag_does_not_pass():
    # Cheap EV/EBIT and low debt: only the margin-decline bar can fire, and
    # margins here are stable — leave exactly one bar (net debt) on.
    fund = _fund("MEH", ebit=D("200"), total_debt=D("500"), ebitda=D("100"))
    (res,) = screen_short_universe([_inputs("MEH", triggers=[RED_FLAG], fund=fund)],
                                   asof=ASOF)
    assert sum(1 for b in res.bars if b.passed) == 1
    assert not res.passed


def test_negative_ebit_with_real_cap_counts_as_expensive():
    fund = _fund("BURN", ebit=D("-5"))
    (res,) = screen_short_universe(
        [_inputs("BURN", triggers=[RED_FLAG], fund=fund, market_cap=D("600000000"))],
        asof=ASOF,
    )
    expensive = next(b for b in res.bars if b.name == "ev_ebit_expensive")
    assert expensive.passed


def test_margin_decline_bar():
    fund = _fund("FADE", gross_margin_history=_yv([(2024, "0.45"), (2025, "0.41")]))
    (res,) = screen_short_universe([_inputs("FADE", fund=fund)], asof=ASOF)
    decline = next(b for b in res.bars if b.name == "gross_margin_declining")
    assert decline.passed
