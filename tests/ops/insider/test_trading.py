"""Insider trade step: strength-sized entries, fixed exits, fences, events."""

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from ops import events
from ops.broker.paper import PaperBroker
from ops.broker.types import Order, OrderType, Side
from ops.insider.store import SignalStore
from ops.insider.trading import trade_insider_sleeve
from ops.journal import Journal
from tradingagents.dataflows.form4 import InsiderTransaction

pytestmark = pytest.mark.unit

D = Decimal
ASOF = date(2026, 7, 13)
NOW = datetime(2026, 7, 13, 20, 30, tzinfo=timezone.utc)
_acc = iter(range(1000))


def _buy(name, *, dollars="20000", title="Director", when=date(2026, 7, 1)):
    shares = D("1000")
    return InsiderTransaction(
        insider_name=name, insider_title=title, is_director=True,
        is_officer=False, is_ten_pct_owner=False, transaction_date=when,
        code="P", shares=shares, price=D(dollars) / shares, acquired=True,
        ten_b5_1=False, accession=f"0001-26-{next(_acc):06d}", filed_date=when,
    )


@pytest.fixture
def stores(tmp_path):
    signal_store = SignalStore(tmp_path / "signals.sqlite")
    with Journal(str(tmp_path / "insider.sqlite")) as insider_journal, \
            Journal(str(tmp_path / "main.sqlite")) as main_journal:
        yield signal_store, insider_journal, main_journal


def _trade(stores, prices, *, deny=frozenset(), resolver=None,
           adv=lambda t: D("1000000")):
    signal_store, insider_journal, main_journal = stores
    return trade_insider_sleeve(
        signal_store=signal_store, insider_journal=insider_journal,
        main_journal=main_journal, quote_source=lambda s: prices[s],
        starting_cash=D("10000"), deny_list=deny, asof=ASOF, now=NOW,
        adv_fetcher=adv, resolver=resolver,
    )


def _seed_position(stores, prices, *, symbol="AAA", entry_date="2026-07-01",
                   memo_id=""):
    signal_store, insider_journal, _ = stores
    broker = PaperBroker(
        journal=insider_journal, quote_source=lambda s: prices[s],
        starting_cash=D("10000"),
    )
    broker.place_order(Order(
        client_order_id=f"seed-{symbol}", symbol=symbol, side=Side.BUY,
        notional_dollars=D("300"), order_type=OrderType.MARKET,
    ))
    insider_journal.record_event(
        events.KIND_INSIDER_POSITION_OPENED,
        events.insider_position_opened_payload(
            symbol=symbol, strength="BASIC", entry_date=entry_date,
            client_order_id=f"seed-{symbol}", notional="300",
            buyers=["A", "B"], accessions=["0001-26-000001"], memo_id=memo_id,
        ),
    )
    signal_store.record_entry(symbol, asof=date.fromisoformat(entry_date))


# --- entries -----------------------------------------------------------------

def test_basic_cluster_enters_at_3_percent(stores):
    signal_store, insider_journal, main_journal = stores
    signal_store.record_transactions("AAA", [_buy("A"), _buy("B")])
    outcome = _trade(stores, {"AAA": D("10")})
    assert outcome.entered == ["AAA"]
    assert outcome.cash == D("10000") - D("300")   # 3% of 10k
    assert main_journal.count_events(events.KIND_INSIDER_TRADE_RUN) == 1
    (prov,) = [e for e in insider_journal.read_events()
               if e["kind"] == events.KIND_INSIDER_POSITION_OPENED]
    assert prov["payload"]["strength"] == "BASIC"
    assert prov["payload"]["buyers"] == ["A", "B"]
    # entry recorded for cooldown + memo queue
    assert signal_store.last_entry_date("AAA") == ASOF
    assert signal_store.entries_without_memo() == [{"symbol": "AAA", "asof": ASOF}]


def test_strong_cluster_enters_at_5_percent(stores):
    signal_store, _, _ = stores
    signal_store.record_transactions("AAA", [_buy("A"), _buy("B"), _buy("C")])
    outcome = _trade(stores, {"AAA": D("10")})
    assert outcome.cash == D("10000") - D("500")


def test_deny_listed_cluster_is_skipped(stores):
    signal_store, _, _ = stores
    signal_store.record_transactions("SPOT", [_buy("A"), _buy("B")])
    outcome = _trade(stores, {"SPOT": D("10")}, deny=frozenset({"SPOT"}))
    assert outcome.entered == []
    assert any("deny-listed" in s for s in outcome.skipped)


def test_adv_cap_clamps_and_min_order_rejects(stores):
    signal_store, _, _ = stores
    signal_store.record_transactions("AAA", [_buy("A"), _buy("B")])
    # 5% of $1500 ADV = $75 < $100 min order -> reject.
    outcome = _trade(stores, {"AAA": D("10")}, adv=lambda t: D("1500"))
    assert outcome.entered == []
    assert any("below min order" in s for s in outcome.skipped)
    # 5% of $4000 ADV = $200: clamps the 3% slice ($300) down to $200.
    outcome = _trade(stores, {"AAA": D("10")}, adv=lambda t: D("4000"))
    assert outcome.entered == ["AAA"]
    assert outcome.cash == D("10000") - D("200")


# --- exits (first-match-wins) --------------------------------------------------

def test_stop_exit(stores):
    prices = {"AAA": D("10")}
    _seed_position(stores, prices)
    prices["AAA"] = D("7.9")   # -21%
    outcome = _trade(stores, prices)
    assert outcome.exited == ["AAA"]
    (closed,) = [e for e in stores[1].read_events()
                 if e["kind"] == events.KIND_INSIDER_POSITION_CLOSED]
    assert closed["payload"]["reason"] == "stop"


def test_target_exit(stores):
    prices = {"AAA": D("10")}
    _seed_position(stores, prices)
    prices["AAA"] = D("14.1")   # +41%
    outcome = _trade(stores, prices)
    assert outcome.exited == ["AAA"]


def test_time_exit_after_126_days(stores):
    prices = {"AAA": D("10")}
    _seed_position(stores, prices, entry_date="2026-03-01")
    outcome = _trade(stores, prices)
    assert outcome.exited == ["AAA"]


def test_within_bounds_holds(stores):
    prices = {"AAA": D("10")}
    _seed_position(stores, prices)
    outcome = _trade(stores, prices)
    assert outcome.exited == [] and outcome.entered == []


def test_resolver_called_on_exit_and_failure_is_isolated(stores):
    prices = {"AAA": D("10")}
    _seed_position(stores, prices, memo_id="memo-7")
    prices["AAA"] = D("7")
    calls = []

    def resolver(**kw):
        calls.append(kw)
        raise RuntimeError("store locked")

    outcome = _trade(stores, prices, resolver=resolver)
    assert outcome.exited == ["AAA"]           # exit stands despite resolver boom
    assert calls[0]["memo_id"] == "memo-7"
    assert calls[0]["reason"] == "stop"
    assert any("memo resolution failed" in e for e in outcome.errors)


def test_cooldown_prevents_reentry_after_exit(stores):
    signal_store, _, _ = stores
    prices = {"AAA": D("10")}
    _seed_position(stores, prices)                  # entry recorded 07-01
    signal_store.record_transactions("AAA", [_buy("A"), _buy("B")])
    prices["AAA"] = D("7")                          # stop fires
    outcome = _trade(stores, prices)
    assert outcome.exited == ["AAA"]
    assert outcome.entered == []                    # cooldown blocks the cluster
