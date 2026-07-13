"""Sleeves: journal-replay positions, snapshot-based P&L, per-sleeve isolation."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from ops import events
from ops.config import OpsConfig
from ops.dashboard.snapshot import build_snapshot
from ops.journal import Journal


def _config(tmp_path) -> OpsConfig:
    return OpsConfig(
        journal_path=str(tmp_path / "ops.sqlite"),
        baseline_journal_path=str(tmp_path / "baseline.sqlite"),
        research_journal_path=str(tmp_path / "research.sqlite"),
        screen_store_path=str(tmp_path / "screen.sqlite"),
        memo_store_path=str(tmp_path / "memos.sqlite"),
        guardian_liveness_path=str(tmp_path / "guardian.alive"),
        research_pause_flag_path=str(tmp_path / "research.paused"),
    )


def _seed_momentum(cfg: OpsConfig, now: datetime) -> None:
    # Seed side="BUY" (matches ops.broker.types.Side.BUY.value, which the
    # replay and last_buy_fill_for both key on) and a matching order row so
    # PaperBroker.from_journal replays without hitting its readonly-crashing
    # journal_replay_fallback write path. See task-4 report for the disclosed
    # deviation from the brief's seed (which used "buy" and no order row).
    with Journal(cfg.journal_path) as j:
        j.record_cash_adjustment(kind="seed", amount=Decimal("250"), note="test")
        j.record_equity_snapshot(
            equity=Decimal("250"), cash=Decimal("250"), kind="open_day",
            at=now - timedelta(days=1))
        j.record_equity_snapshot(
            equity=Decimal("260"), cash=Decimal("160"), kind="open_day", at=now)
        j.record_order(
            client_order_id="c1", symbol="XYZ", side="BUY",
            notional_dollars=Decimal("100"), stop_loss_price=Decimal("9.20"))
        j.record_fill(
            order_id="o1", client_order_id="c1", symbol="XYZ", side="BUY",
            quantity=Decimal("10"), price=Decimal("10"), filled_at=now,
            stop_loss_price=Decimal("9.20"))


def test_sleeve_positions_and_fills_from_replay(tmp_path):
    cfg = _config(tmp_path)
    now = datetime.now(timezone.utc)
    _seed_momentum(cfg, now)
    mom = build_snapshot(cfg, now=now)["sleeves"]["momentum"]
    assert mom["positions"] == [
        {"symbol": "XYZ", "quantity": "10", "entry": "10", "stop": "9.20"}]
    assert len(mom["fills_today"]) == 1
    assert mom["equity"] == "260"


def test_day_pnl_pct_from_consecutive_snapshots(tmp_path):
    cfg = _config(tmp_path)
    now = datetime.now(timezone.utc)
    _seed_momentum(cfg, now)
    mom = build_snapshot(cfg, now=now)["sleeves"]["momentum"]
    # (260-250)/250 = 0.04
    assert Decimal(mom["day_pnl_pct"]) == Decimal("0.04")


def test_missing_sleeve_journal_isolated(tmp_path):
    cfg = _config(tmp_path)
    now = datetime.now(timezone.utc)
    _seed_momentum(cfg, now)  # research + baseline journals never created
    sleeves = build_snapshot(cfg, now=now)["sleeves"]
    assert "error" in sleeves["research"]
    assert "error" in sleeves["baseline"]
    assert "error" not in sleeves["momentum"]


def test_anomalies_counts_and_last_at(tmp_path):
    cfg = _config(tmp_path)
    now = datetime.now(timezone.utc)
    _seed_momentum(cfg, now)
    with Journal(cfg.journal_path) as j:
        j.record_event(events.KIND_STOP_FAILED, {"symbol": "XYZ"})
        j.record_event(events.KIND_STOP_FAILED, {"symbol": "XYZ"})
    anom = build_snapshot(cfg, now=now)["anomalies_7d"]
    assert anom[events.KIND_STOP_FAILED]["count"] == 2
    assert anom[events.KIND_STOP_FAILED]["last_at"] is not None
    assert anom[events.KIND_GUARDIAN_BLIND]["count"] == 0
