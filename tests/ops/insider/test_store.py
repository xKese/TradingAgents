"""Insider signal store: idempotent upsert, window queries, cooldowns,
memo queue, and the scan watermark."""

from datetime import date
from decimal import Decimal

import pytest

from ops.insider.store import SignalStore
from tradingagents.dataflows.form4 import InsiderTransaction

pytestmark = pytest.mark.unit


def _txn(name, *, code="P", ten_b5_1=False, when=date(2026, 7, 1),
         shares="1000", price="10", title="Director", accession="0009-26-000009"):
    return InsiderTransaction(
        insider_name=name, insider_title=title, is_director=True,
        is_officer=False, is_ten_pct_owner=False, transaction_date=when,
        code=code, shares=Decimal(shares), price=Decimal(price),
        acquired=code == "P", ten_b5_1=ten_b5_1, accession=accession,
        filed_date=when,
    )


@pytest.fixture
def store(tmp_path):
    return SignalStore(tmp_path / "signals.sqlite")


def test_record_transactions_is_idempotent(store):
    txns = [_txn("A"), _txn("B", accession="0009-26-000010")]
    assert store.record_transactions("ghst", txns) == 2
    assert store.record_transactions("ghst", txns) == 0


def test_buys_in_window_filters_code_plan_and_window(store):
    store.record_transactions("GHST", [
        _txn("A"),                                            # counts
        _txn("B", code="S", accession="a2"),                  # sale
        _txn("C", ten_b5_1=True, accession="a3"),             # plan buy
        _txn("D", when=date(2026, 5, 1), accession="a4"),     # out of window
    ])
    buys = store.buys_in_window("GHST", since=date(2026, 6, 15),
                                until=date(2026, 7, 13))
    assert [b["insider_name"] for b in buys] == ["A"]
    assert buys[0]["shares"] == Decimal("1000")
    assert buys[0]["price"] == Decimal("10")


def test_symbols_with_new_buys(store):
    store.record_transactions("AAA", [_txn("A", accession="x1")])
    store.record_transactions("BBB", [_txn("B", code="S", accession="x2")])
    assert store.symbols_with_new_buys(since=date(2026, 6, 1)) == ["AAA"]


def test_entry_cooldown_roundtrip(store):
    assert store.last_entry_date("GHST") is None
    store.record_entry("GHST", asof=date(2026, 7, 13))
    assert store.last_entry_date("GHST") == date(2026, 7, 13)


def test_memo_queue_transition(store):
    store.record_entry("GHST", asof=date(2026, 7, 13))
    assert store.entries_without_memo() == [
        {"symbol": "GHST", "asof": date(2026, 7, 13)}]
    store.set_entry_memo("GHST", date(2026, 7, 13), "memo-1")
    assert store.entries_without_memo() == []


def test_scan_watermark_roundtrip(store):
    assert store.scan_watermark() is None
    store.set_scan_watermark(date(2026, 7, 10))
    store.set_scan_watermark(date(2026, 7, 11))
    assert store.scan_watermark() == date(2026, 7, 11)
