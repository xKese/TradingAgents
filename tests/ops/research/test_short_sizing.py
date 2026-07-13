"""Short-sleeve sizing fences — pure, no I/O."""

from decimal import Decimal

import pytest

from ops.research.short_sizing import size_short_entry

pytestmark = pytest.mark.unit

D = Decimal
EQUITY = D("10000")


def _size(**overrides):
    kwargs = dict(
        tier="starter", equity=EQUITY, exposure_by_symbol={}, symbol="GHST",
        sector="Industrials", exposure_by_sector={},
        gross_short_exposure=D("0"), adv_20d=D("1000000"),
    )
    kwargs.update(overrides)
    return size_short_entry(**kwargs)


def test_tiers_are_half_the_long_sleeve():
    assert _size(tier="starter").notional == D("100")
    assert _size(tier="medium").notional == D("200")
    assert _size(tier="high").notional == D("300")


def test_unknown_tier_rejects():
    assert "unknown tier" in _size(tier="jumbo").rejected


def test_name_cap_rejects_when_room_below_min_order():
    d = _size(exposure_by_symbol={"GHST": D("450")})   # 5% of 10k = 500 cap
    assert d.rejected is not None and "name cap" in d.rejected


def test_sector_cap_rejects():
    d = _size(exposure_by_sector={"Industrials": D("1450")})  # 15% = 1500 cap
    assert d.rejected is not None and "sector cap" in d.rejected


def test_gross_exposure_cap_rejects():
    d = _size(gross_short_exposure=D("4950"))          # 50% = 5000 cap
    assert d.rejected is not None and "gross exposure cap" in d.rejected


def test_gross_exposure_room_clamps_notional():
    d = _size(tier="high", gross_short_exposure=D("4850"))  # 150 room < 300 tier
    assert d.rejected is None and d.notional == D("150")


def test_adv_cap_clamps_and_rejects():
    assert _size(adv_20d=None).rejected == "adv unavailable for GHST"
    d = _size(tier="high", adv_20d=D("10000"))          # 2% ADV = 200 < 300
    assert d.notional == D("200")
    tiny = _size(adv_20d=D("4000"))                     # 2% = 80 < 100 min
    assert tiny.rejected is not None and "below min order" in tiny.rejected
