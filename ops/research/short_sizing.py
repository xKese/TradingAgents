"""Conviction-tier sizing + hard fences for the short sleeve.

Mirror of ops/research/sizing.py with the fences tightened for unbounded
loss: half tiers, 5% name cap, 15% sector cap, a 50% gross-short-exposure
cap (the sleeve's margin discipline — there is no cash clamp because
shorting ADDS cash), and a 2% ADV cap (a short must be coverable in a
squeeze). Exposure inputs are LIVE market values (qty × current price),
not cost — shorts grow as they go wrong, so caps must read live exposure.

Every fence is mechanical; LLM-stated probabilities are NEVER inputs — the
only research signal used is the memo's conviction_tier.
"""
from __future__ import annotations

from decimal import Decimal

from ops.research.sizing import SizingDecision

SHORT_TIER_SIZING: dict[str, Decimal] = {
    "starter": Decimal("0.01"),
    "medium": Decimal("0.02"),
    "high": Decimal("0.03"),
}
NAME_CAP_PCT = Decimal("0.05")             # single name <= 5% of sleeve equity, live
SECTOR_CAP_PCT = Decimal("0.15")           # sector <= 15%, live
GROSS_EXPOSURE_CAP_PCT = Decimal("0.50")   # total short book <= 50% of equity
ADV_CAP_PCT = Decimal("0.02")              # position <= 2% of 20-day dollar ADV
MIN_ORDER_DOLLARS = Decimal("100")


def _quantize_money(d: Decimal) -> Decimal:
    return d.quantize(Decimal("0.01"))


def size_short_entry(
    *,
    tier: str,
    equity: Decimal,
    exposure_by_symbol: dict[str, Decimal],
    symbol: str,
    sector: str,
    exposure_by_sector: dict[str, Decimal],
    gross_short_exposure: Decimal,
    adv_20d: Decimal | None,
) -> SizingDecision:
    pct = SHORT_TIER_SIZING.get(tier)
    if pct is None:
        return SizingDecision(Decimal("0"), f"unknown tier {tier!r}")
    notional = _quantize_money(equity * pct)

    name_room = NAME_CAP_PCT * equity - exposure_by_symbol.get(symbol, Decimal("0"))
    if name_room < MIN_ORDER_DOLLARS:
        return SizingDecision(Decimal("0"), (
            f"name cap: {symbol} exposure {exposure_by_symbol.get(symbol, Decimal('0'))} "
            f"leaves {name_room:.2f} of {NAME_CAP_PCT * equity:.2f}"
        ))
    notional = min(notional, _quantize_money(name_room))

    sector_room = SECTOR_CAP_PCT * equity - exposure_by_sector.get(sector, Decimal("0"))
    if sector_room < MIN_ORDER_DOLLARS:
        return SizingDecision(Decimal("0"), (
            f"sector cap: {sector} exposure {exposure_by_sector.get(sector, Decimal('0'))} "
            f"leaves {sector_room:.2f} of {SECTOR_CAP_PCT * equity:.2f}"
        ))
    notional = min(notional, _quantize_money(sector_room))

    gross_room = GROSS_EXPOSURE_CAP_PCT * equity - gross_short_exposure
    if gross_room < MIN_ORDER_DOLLARS:
        return SizingDecision(Decimal("0"), (
            f"gross exposure cap: short book {gross_short_exposure:.2f} "
            f"leaves {gross_room:.2f} of {GROSS_EXPOSURE_CAP_PCT * equity:.2f}"
        ))
    notional = min(notional, _quantize_money(gross_room))

    if adv_20d is None:
        return SizingDecision(Decimal("0"), f"adv unavailable for {symbol}")
    adv_room = _quantize_money(ADV_CAP_PCT * adv_20d)
    notional = min(notional, adv_room)

    if notional < MIN_ORDER_DOLLARS:
        return SizingDecision(Decimal("0"), (
            f"below min order: {notional} < {MIN_ORDER_DOLLARS} "
            f"(adv room {adv_room})"
        ))
    return SizingDecision(notional)
