from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock

import pytest
from ops.scheduler.orchestrator import Orchestrator
from ops.broker.types import Order, OrderType, Side
from ops.broker.base import OrderRejected, BrokerError
from ops.strategy.base import StrategyOrder


def _fake_calendar(is_open: bool):
    cal = MagicMock()
    cal.is_open_now.return_value = is_open
    return cal


def _fake_pipeline():
    return MagicMock()


def _fake_strategy(propose_orders_return):
    strat = MagicMock()
    strat.propose_orders.return_value = propose_orders_return
    return strat


def _fake_universe(symbols):
    return MagicMock(return_value=[MagicMock(symbol=s) for s in symbols])


def _fake_broker(positions=None, equity=Decimal("1000"), cash=Decimal("500")):
    b = MagicMock()
    b.get_positions.return_value = positions or []
    b.get_equity.return_value = equity
    b.get_cash.return_value = cash
    return b


def _order(symbol):
    return Order(
        client_order_id=f"b-{symbol}", symbol=symbol, side=Side.BUY,
        notional_dollars=Decimal("50"), order_type=OrderType.MARKET,
        stop_pct=Decimal("-0.08"),
    )


def _strategy_order(symbol):
    return StrategyOrder(
        order=_order(symbol),
        reason="test",
        candidate=MagicMock(symbol=symbol),
        pipeline=MagicMock(),
    )


def test_tick_market_closed_noop(tmp_path):
    from ops.journal import Journal
    j = Journal(str(tmp_path / "j.sqlite"))
    broker = _fake_broker()
    orch = Orchestrator(
        broker=broker, universe_builder=_fake_universe([]),
        strategy=_fake_strategy([]), pipeline_adapter=_fake_pipeline(),
        calendar=_fake_calendar(is_open=False), journal=j,
        config=MagicMock(),
    )
    orch.tick()
    assert j.read_events() == []
    broker.get_equity.assert_not_called()


def test_tick_journals_orchestrator_tick_error_on_unexpected_exception(tmp_path):
    """Any unexpected exception from a collaborator is swallowed and journaled."""
    from ops.journal import Journal
    from ops.config import OpsConfig
    j = Journal(str(tmp_path / "j.sqlite"))
    broker = _fake_broker()
    universe = _fake_universe(["AAPL"])
    universe.side_effect = RuntimeError("boom")
    orch = Orchestrator(
        broker=broker, universe_builder=universe,
        strategy=_fake_strategy([_strategy_order("AAPL")]),
        pipeline_adapter=_fake_pipeline(),
        calendar=_fake_calendar(is_open=True), journal=j,
        config=OpsConfig(),
    )
    orch.tick()  # must NOT raise
    events = j.read_events()
    err_events = [e for e in events if e["kind"] == "orchestrator_tick_error"]
    assert len(err_events) == 1
    assert "boom" in err_events[0]["payload"]["error"]


def test_tick_places_buy_when_strategy_proposes_order(tmp_path):
    from ops.journal import Journal
    from ops.config import OpsConfig
    j = Journal(str(tmp_path / "j.sqlite"))
    broker = _fake_broker()
    orch = Orchestrator(
        broker=broker,
        universe_builder=_fake_universe(["AAPL"]),
        strategy=_fake_strategy([_strategy_order("AAPL")]),
        pipeline_adapter=_fake_pipeline(),
        calendar=_fake_calendar(is_open=True), journal=j,
        config=OpsConfig(),
    )
    orch.tick()
    broker.place_order.assert_called_once()
    placed = broker.place_order.call_args.args[0]
    assert placed.symbol == "AAPL"


def test_tick_skips_when_strategy_proposes_nothing(tmp_path):
    """Real Strategy filters non-BUY decisions internally via pipeline.propagate;
    the orchestrator just sees an empty proposal list."""
    from ops.journal import Journal
    from ops.config import OpsConfig
    j = Journal(str(tmp_path / "j.sqlite"))
    broker = _fake_broker()
    orch = Orchestrator(
        broker=broker,
        universe_builder=_fake_universe(["AAPL"]),
        strategy=_fake_strategy([]),
        pipeline_adapter=_fake_pipeline(),
        calendar=_fake_calendar(is_open=True), journal=j,
        config=OpsConfig(),
    )
    orch.tick()
    broker.place_order.assert_not_called()


def test_tick_continues_after_rule_reject(tmp_path):
    """OrderRejected on one candidate → next candidate still tried."""
    from ops.journal import Journal
    from ops.config import OpsConfig
    j = Journal(str(tmp_path / "j.sqlite"))
    broker = _fake_broker()
    broker.place_order.side_effect = [OrderRejected("Some", "reason"), MagicMock()]
    orch = Orchestrator(
        broker=broker,
        universe_builder=_fake_universe(["AAPL", "MSFT"]),
        strategy=_fake_strategy([_strategy_order("AAPL"), _strategy_order("MSFT")]),
        pipeline_adapter=_fake_pipeline(),
        calendar=_fake_calendar(is_open=True), journal=j,
        config=OpsConfig(),
    )
    orch.tick()
    assert broker.place_order.call_count == 2


def test_tick_breaks_on_broker_error(tmp_path):
    from ops.journal import Journal
    from ops.config import OpsConfig
    j = Journal(str(tmp_path / "j.sqlite"))
    broker = _fake_broker()
    broker.place_order.side_effect = BrokerError("mcp died")
    orch = Orchestrator(
        broker=broker,
        universe_builder=_fake_universe(["AAPL", "MSFT"]),
        strategy=_fake_strategy([_strategy_order("AAPL"), _strategy_order("MSFT")]),
        pipeline_adapter=_fake_pipeline(),
        calendar=_fake_calendar(is_open=True), journal=j,
        config=OpsConfig(),
    )
    orch.tick()
    assert broker.place_order.call_count == 1


def test_maybe_snapshot_equity_writes_open_day_once(tmp_path):
    from ops.journal import Journal
    from ops.config import OpsConfig
    j = Journal(str(tmp_path / "j.sqlite"))
    broker = _fake_broker(equity=Decimal("1000"), cash=Decimal("500"))
    orch = Orchestrator(
        broker=broker, universe_builder=_fake_universe([]),
        strategy=_fake_strategy([]), pipeline_adapter=_fake_pipeline(),
        calendar=_fake_calendar(is_open=True), journal=j,
        config=OpsConfig(),
    )
    orch.tick()
    orch.tick()
    open_day_snaps = [
        s for s in j._conn.execute(
            "SELECT kind FROM equity_snapshots"
        ).fetchall() if s[0] == "open_day"
    ]
    assert len(open_day_snaps) == 1


def test_tick_shortcircuits_on_daily_halt(tmp_path):
    from ops.journal import Journal
    from ops.config import OpsConfig
    j = Journal(str(tmp_path / "j.sqlite"))
    j.record_event("daily_halt", {"reason": "drawdown"})
    broker = _fake_broker()
    universe = _fake_universe(["AAPL"])
    orch = Orchestrator(
        broker=broker, universe_builder=universe,
        strategy=_fake_strategy([_strategy_order("AAPL")]),
        pipeline_adapter=_fake_pipeline(),
        calendar=_fake_calendar(is_open=True), journal=j,
        config=OpsConfig(),
    )
    orch.tick()
    universe.assert_not_called()
    broker.place_order.assert_not_called()


def test_tick_passes_held_and_free_slots_to_builder(tmp_path):
    """Orchestrator computes held symbols + remaining slots and hands them
    to the universe builder (belt-and-suspenders: fresh_candidates filter
    still applies afterward)."""
    from ops.journal import Journal
    from ops.config import OpsConfig
    j = Journal(str(tmp_path / "j.sqlite"))
    broker = _fake_broker(
        positions=[MagicMock(symbol="AAPL"), MagicMock(symbol="MSFT")]
    )
    universe = _fake_universe([])
    orch = Orchestrator(
        broker=broker, universe_builder=universe,
        strategy=_fake_strategy([]), pipeline_adapter=_fake_pipeline(),
        calendar=_fake_calendar(is_open=True), journal=j,
        config=OpsConfig(),
    )
    orch.tick()
    kwargs = universe.call_args.kwargs
    assert kwargs["held_symbols"] == frozenset({"AAPL", "MSFT"})
    assert kwargs["free_slots"] == OpsConfig().max_open_positions - 2


def test_tick_shortcircuits_on_weekly_kill_switch(tmp_path):
    from ops.journal import Journal
    from ops.config import OpsConfig
    j = Journal(str(tmp_path / "j.sqlite"))
    j.record_event("kill_switch", {"reason": "weekly"})
    broker = _fake_broker()
    universe = _fake_universe(["AAPL"])
    orch = Orchestrator(
        broker=broker, universe_builder=universe,
        strategy=_fake_strategy([_strategy_order("AAPL")]),
        pipeline_adapter=_fake_pipeline(),
        calendar=_fake_calendar(is_open=True), journal=j,
        config=OpsConfig(),
    )
    orch.tick()
    universe.assert_not_called()
    broker.place_order.assert_not_called()


def _momentum_candidate(symbol, rank):
    from ops.universe import Candidate, CandidateSource
    from ops.universe.momentum import MomentumHit

    hit = MomentumHit(
        symbol=symbol,
        asof_date=date(2026, 7, 5),
        trailing_return_6m=Decimal("0.30"),
        close=Decimal("120"),
        sma_200=Decimal("100"),
        avg_dollar_volume_20d=Decimal("1000000000"),
        rank=rank,
    )
    return Candidate(
        symbol=symbol,
        source=CandidateSource.MOMENTUM,
        last_price=Decimal("120"),
        avg_dollar_volume_20d=Decimal("1000000000"),
        momentum=hit,
    )


def _strategy_order_for_candidate(candidate):
    return StrategyOrder(
        order=_order(candidate.symbol), reason="test",
        candidate=candidate, pipeline=MagicMock(),
    )


def test_tick_journals_position_opened_on_successful_buy(tmp_path):
    from ops.journal import Journal
    from ops.config import OpsConfig
    j = Journal(str(tmp_path / "j.sqlite"))
    broker = _fake_broker()
    candidate = _momentum_candidate("NVDA", rank=3)
    orch = Orchestrator(
        broker=broker,
        universe_builder=_fake_universe(["NVDA"]),
        strategy=_fake_strategy([_strategy_order_for_candidate(candidate)]),
        pipeline_adapter=_fake_pipeline(),
        calendar=_fake_calendar(is_open=True), journal=j,
        config=OpsConfig(),
    )
    orch.tick()
    evts = [e for e in j.read_events() if e["kind"] == "position_opened"]
    assert len(evts) == 1
    p = evts[0]["payload"]
    assert p["symbol"] == "NVDA"
    assert p["source"] == "MOMENTUM"
    assert p["entry_rank"] == 3
    assert p["entry_date"] == datetime.now(timezone.utc).date().isoformat()


def test_tick_rejected_order_does_not_journal_position_opened(tmp_path):
    from ops.journal import Journal
    from ops.config import OpsConfig
    j = Journal(str(tmp_path / "j.sqlite"))
    broker = _fake_broker()
    broker.place_order.side_effect = OrderRejected("Some", "reason")
    candidate = _momentum_candidate("NVDA", rank=3)
    orch = Orchestrator(
        broker=broker,
        universe_builder=_fake_universe(["NVDA"]),
        strategy=_fake_strategy([_strategy_order_for_candidate(candidate)]),
        pipeline_adapter=_fake_pipeline(),
        calendar=_fake_calendar(is_open=True), journal=j,
        config=OpsConfig(),
    )
    orch.tick()
    assert all(e["kind"] != "position_opened" for e in j.read_events())
