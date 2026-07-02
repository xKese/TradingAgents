from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from ops.main import _build_broker, _wire, _emit_halt_events
from ops.reconcile import ReconcileResult, PositionDiff
from ops.config import OpsConfig
from ops.journal import Journal


def test_build_broker_paper(tmp_path):
    cfg = OpsConfig()  # broker_mode default "paper"
    j = Journal(str(tmp_path / "j.sqlite"))
    broker = _build_broker(cfg, j)
    # We don't assert internal type — just that place_order is callable
    assert callable(broker.place_order)


def test_build_broker_robinhood(monkeypatch, tmp_path):
    cfg = OpsConfig(broker_mode="robinhood")
    j = Journal(str(tmp_path / "j.sqlite"))
    # Stub RealRobinhoodMCPClient so no OAuth flow triggers.
    from tests.ops.broker.fakes import FakeMCPClient
    monkeypatch.setattr(
        "ops.broker.mcp_client.RealRobinhoodMCPClient",
        lambda: FakeMCPClient(),
    )
    broker = _build_broker(cfg, j)
    assert callable(broker.place_order)


def test_wire_returns_orchestrator_guardian_calendar(tmp_path):
    cfg = OpsConfig()
    j = Journal(str(tmp_path / "j.sqlite"))
    broker = _build_broker(cfg, j)
    orch, guardian, cal = _wire(broker, j, cfg)
    assert callable(orch.tick)
    assert callable(guardian.check_stops_once)
    assert callable(cal.is_open_now)


def test_emit_halt_events_writes_inconsistency_and_startup_halted(tmp_path):
    j = Journal(str(tmp_path / "j.sqlite"))
    result = ReconcileResult(
        diffs=[PositionDiff(symbol="AAPL", journal_qty=Decimal("5"),
                            broker_qty=Decimal("3"), kind="qty_mismatch")],
        cash_journal=Decimal("100"), cash_broker=Decimal("100"),
        cash_diff=Decimal("0"),
    )
    _emit_halt_events(j, result)
    kinds = [e["kind"] for e in j.read_events()]
    assert "inconsistency" in kinds
    assert "startup_halted" in kinds


def test_paper_mode_restart_preserves_positions(tmp_path):
    """Two ops-run sessions against the same journal must see the same
    positions and stops. Session B rehydrates from the journal that
    session A wrote, so a BUY placed in A is visible (with its stop)
    in B without any reconciler diffs."""
    from ops import build_guarded_paper_broker_from_journal
    from ops.broker.types import Order, OrderType, Side
    from ops.reconcile import reconcile as _reconcile

    class _Q:
        def __init__(self): self._m = {}
        def set(self, s, p): self._m[s] = p
        def get(self, s): return self._m[s]

    journal_path = str(tmp_path / "j.sqlite")
    quotes = _Q()
    quotes.set("AAPL", Decimal("10"))

    # Session A — place a BUY with a stop.
    j_a = Journal(journal_path)
    broker_a = build_guarded_paper_broker_from_journal(
        config=OpsConfig(), journal=j_a, quote_source=quotes.get,
        starting_cash=Decimal("250"),
        start_of_day_equity=lambda: Decimal("250"),
        start_of_week_equity=lambda: Decimal("250"),
    )
    broker_a.place_order(Order(
        client_order_id="b-1", symbol="AAPL", side=Side.BUY,
        notional_dollars=Decimal("20"), order_type=OrderType.MARKET,
        stop_loss_price=Decimal("9.5"),
    ))
    positions_a = broker_a.get_positions()
    assert len(positions_a) == 1
    assert positions_a[0].stop_loss_price == Decimal("9.5")
    j_a.close()

    # Session B — restart on the same journal.
    j_b = Journal(journal_path)
    broker_b = build_guarded_paper_broker_from_journal(
        config=OpsConfig(), journal=j_b, quote_source=quotes.get,
        starting_cash=Decimal("250"),
        start_of_day_equity=lambda: Decimal("250"),
        start_of_week_equity=lambda: Decimal("250"),
    )
    positions_b = broker_b.get_positions()
    assert len(positions_b) == 1
    assert positions_b[0].symbol == "AAPL"
    assert positions_b[0].quantity == positions_a[0].quantity
    assert positions_b[0].stop_loss_price == Decimal("9.5")

    # Reconcile in paper mode must produce zero diffs on the restarted broker.
    result = _reconcile(journal=j_b, broker=broker_b, broker_mode="paper")
    assert result.diffs == []
    j_b.close()
