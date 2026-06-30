from decimal import Decimal

from ops import build_guarded_paper_broker
from ops.broker.types import Order, OrderType, Side
from ops.config import OpsConfig
from ops.journal import Journal
from ops.position_guardian import PositionGuardian


def _stack(tmp_path, *, starting_cash="250", quotes=None):
    quotes = quotes or {"AAPL": Decimal("200")}
    j = Journal(str(tmp_path / "j.sqlite"))
    cfg = OpsConfig()
    guarded = build_guarded_paper_broker(
        config=cfg, journal=j,
        quote_source=lambda s: quotes[s],
        starting_cash=Decimal(starting_cash),
        start_of_day_equity=lambda: Decimal(starting_cash),
        start_of_week_equity=lambda: Decimal(starting_cash),
    )
    return j, guarded, cfg, quotes


def _open_position(guarded):
    guarded.place_order(Order(
        client_order_id="open", symbol="AAPL", side=Side.BUY,
        notional_dollars=Decimal("25"), order_type=OrderType.MARKET,
        stop_loss_price=Decimal("184"),
    ))


def test_guardian_does_nothing_when_above_stop(tmp_path):
    j, guarded, cfg, quotes = _stack(tmp_path)
    _open_position(guarded)
    quotes["AAPL"] = Decimal("190")   # -5%, above -8% threshold
    g = PositionGuardian(broker=guarded, quote_source=guarded.get_quote, config=cfg)
    actions = g.check_stops_once()
    assert len(actions) == 1
    assert actions[0].sold is False
    assert len(guarded.get_positions()) == 1


def test_guardian_closes_position_at_stop(tmp_path):
    j, guarded, cfg, quotes = _stack(tmp_path)
    _open_position(guarded)
    quotes["AAPL"] = Decimal("184")   # -8% exactly
    g = PositionGuardian(broker=guarded, quote_source=guarded.get_quote, config=cfg)
    actions = g.check_stops_once()
    assert actions[0].sold is True
    assert actions[0].symbol == "AAPL"
    assert guarded.get_positions() == []
    # Stop event journaled
    events = j.read_events()
    stops = [e for e in events if e["kind"] == "stop_hit"]
    assert len(stops) == 1
    assert stops[0]["payload"]["symbol"] == "AAPL"


def test_guardian_handles_multiple_positions(tmp_path):
    quotes = {"AAPL": Decimal("200"), "MSFT": Decimal("200")}
    j, guarded, cfg, _ = _stack(tmp_path, starting_cash="10000", quotes=quotes)
    guarded.place_order(Order(
        client_order_id="a", symbol="AAPL", side=Side.BUY,
        notional_dollars=Decimal("100"), order_type=OrderType.MARKET,
        stop_loss_price=Decimal("184"),
    ))
    guarded.place_order(Order(
        client_order_id="m", symbol="MSFT", side=Side.BUY,
        notional_dollars=Decimal("100"), order_type=OrderType.MARKET,
        stop_loss_price=Decimal("184"),
    ))
    quotes["AAPL"] = Decimal("220")    # +10%, hold
    quotes["MSFT"] = Decimal("180")    # -10%, stop
    g = PositionGuardian(broker=guarded, quote_source=guarded.get_quote, config=cfg)
    actions = g.check_stops_once()
    assert {a.symbol for a in actions} == {"AAPL", "MSFT"}
    sold = {a.symbol for a in actions if a.sold}
    assert sold == {"MSFT"}
    remaining = {p.symbol for p in guarded.get_positions()}
    assert remaining == {"AAPL"}
