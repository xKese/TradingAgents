"""Daemon wiring for the insider sleeve: scan/trade tick gates + error
discipline, and the overnight memo pass never spinning ds4 on an empty queue."""

from datetime import date, datetime, timedelta, timezone

import pytest

from ops import events
from ops.config import OpsConfig
from ops.insider.store import SignalStore
from ops.journal import Journal
from ops.main import (
    _insider_memo_pass,
    _insider_memo_work_pending,
    _insider_scan_tick,
    _insider_trade_tick,
)

pytestmark = pytest.mark.unit


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    return OpsConfig()


@pytest.fixture
def journal(tmp_path):
    with Journal(str(tmp_path / "main.sqlite")) as j:
        yield j


def test_scan_tick_gates_and_journals_summary(journal, cfg, monkeypatch):
    from ops.insider.scan import ScanSummary

    monkeypatch.setattr(
        "ops.insider.scan.run_insider_scan",
        lambda **kw: [ScanSummary(day=date(2026, 7, 10), form4_seen=5,
                                  universe_matches=2, transactions_recorded=3)],
    )
    _insider_scan_tick(journal, cfg)
    assert journal.count_events(events.KIND_INSIDER_SCAN_RUN) == 1

    def boom(**kw):
        raise AssertionError("must gate on today's run event")

    monkeypatch.setattr("ops.insider.scan.run_insider_scan", boom)
    _insider_scan_tick(journal, cfg)  # gated: no second run, no error
    assert journal.count_events(events.KIND_INSIDER_SCAN_ERROR) == 0


def test_scan_tick_journals_errors_instead_of_raising(journal, cfg, monkeypatch):
    def boom(**kw):
        raise RuntimeError("sec.gov unreachable")

    monkeypatch.setattr("ops.insider.scan.run_insider_scan", boom)
    _insider_scan_tick(journal, cfg)
    assert journal.count_events(events.KIND_INSIDER_SCAN_ERROR) == 1


def test_trade_tick_gates_and_journals_errors(journal, cfg, monkeypatch):
    def boom(**kw):
        raise RuntimeError("quote feed down")

    monkeypatch.setattr("ops.insider.trading.trade_insider_sleeve", boom)
    _insider_trade_tick(journal, cfg)
    assert journal.count_events(events.KIND_INSIDER_TRADE_ERROR) == 1

    journal.record_event(
        events.KIND_INSIDER_TRADE_RUN,
        events.insider_trade_run_payload(
            asof="2026-07-13", entered=[], exited=[], skipped=[],
            equity="10000", cash="10000",
        ),
    )
    monkeypatch.setattr(
        "ops.insider.trading.trade_insider_sleeve",
        lambda **kw: (_ for _ in ()).throw(AssertionError("gated")),
    )
    _insider_trade_tick(journal, cfg)
    assert journal.count_events(events.KIND_INSIDER_TRADE_ERROR) == 1  # unchanged


def _window():
    now = datetime.now(timezone.utc)
    return {"deadline": now + timedelta(hours=2),
            "should_stop": lambda: False,
            "tick_now": lambda: datetime.now(timezone.utc)}


class _Backend:
    def __init__(self):
        self.ensured = 0

    def ensure_up(self):
        self.ensured += 1


def test_memo_pass_empty_queue_never_touches_ds4(journal, cfg, monkeypatch):
    monkeypatch.setattr(
        "ops.research.models.build_stage_llm",
        lambda spec: (_ for _ in ()).throw(AssertionError("LLM must not be built")),
    )
    backend = _Backend()
    assert not _insider_memo_work_pending(cfg)
    _insider_memo_pass(journal, cfg, backend=backend, **_window())
    assert backend.ensured == 0
    assert journal.read_events() == []


def test_memo_pass_failure_is_journaled(journal, cfg, monkeypatch):
    SignalStore(cfg.insider_signal_store_path).record_entry(
        "AAA", asof=date(2026, 7, 13))
    assert _insider_memo_work_pending(cfg)
    monkeypatch.setattr(
        "ops.research.models.build_stage_llm",
        lambda spec: (_ for _ in ()).throw(RuntimeError("model config broken")),
    )
    _insider_memo_pass(journal, cfg, backend=_Backend(), **_window())
    assert journal.count_events(events.KIND_INSIDER_MEMO_ERROR) == 1
