"""End-to-end insider lifecycle in PRODUCTION shape: cluster -> entry ->
overnight memo authoring -> stop exit -> memo RESOLVED.

Regression test for review finding P1: the opened-event payload carries
memo_id="" (the memo is authored the night after entry, and journal events
are immutable), so the exit pass must read the id from the signal store —
a fabricated-provenance test would miss that, as the original one did.
"""

from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal

import pytest

from ops import events
from ops.insider.memo_lite import (
    MemoLiteDraft, author_pending_memos, resolve_on_exit,
)
from ops.insider.store import SignalStore
from ops.insider.trading import trade_insider_sleeve
from ops.journal import Journal
from ops.research.prices import PriceContext
from tradingagents.dataflows.form4 import InsiderTransaction
from tradingagents.memos.store import MemoStore

pytestmark = pytest.mark.unit

D = Decimal
ENTRY_DAY = date(2026, 7, 13)
EXIT_DAY = date(2026, 7, 20)


def _post_close(day: date) -> datetime:
    return datetime.combine(day, time(20, 30), tzinfo=timezone.utc)


def _buy(name, accession):
    when = ENTRY_DAY - timedelta(days=5)
    return InsiderTransaction(
        insider_name=name, insider_title="Director", is_director=True,
        is_officer=False, is_ten_pct_owner=False, transaction_date=when,
        code="P", shares=D("2000"), price=D("10"), acquired=True,
        ten_b5_1=False, accession=accession, filed_date=when,
    )


class FakeLLM:
    def with_structured_output(self, schema):
        return self

    def invoke(self, prompt):
        from tradingagents.memos.schema import ReturnScenario

        return MemoLiteDraft(
            thesis="Directors bought size into weakness.",
            must_be_true=["buys are discretionary conviction"],
            scenarios=[ReturnScenario(probability=0.5, return_pct=0.3,
                                      description="rerate")],
        )


def test_full_lifecycle_resolves_the_memo(tmp_path):
    signal_store = SignalStore(tmp_path / "signals.sqlite")
    memo_store = MemoStore(tmp_path / "insider_memos.sqlite")
    prices = {"AAA": D("10")}

    def resolver(**kwargs):
        resolve_on_exit(
            memo_store=memo_store,
            benchmark_fetcher=lambda s: PriceContext(closes={
                ENTRY_DAY: D("100"), EXIT_DAY: D("101"),
            }),
            **kwargs,
        )

    signal_store.record_transactions(
        "AAA", [_buy("DOE JANE", "0001-26-000001"), _buy("ROE RICH", "0001-26-000002")],
    )

    with Journal(str(tmp_path / "insider.sqlite")) as insider_journal, \
            Journal(str(tmp_path / "main.sqlite")) as main_journal:
        # Day 1, 16:29: the cluster enters. No memo exists yet.
        outcome = trade_insider_sleeve(
            signal_store=signal_store, insider_journal=insider_journal,
            main_journal=main_journal, quote_source=lambda s: prices[s],
            starting_cash=D("10000"), deny_list=frozenset(), asof=ENTRY_DAY,
            now=_post_close(ENTRY_DAY), adv_fetcher=lambda t: D("1000000"),
            resolver=resolver,
        )
        assert outcome.entered == ["AAA"]

        # That night: the overnight pass authors the memo (one cheap call).
        written = author_pending_memos(
            signal_store=signal_store, memo_store=memo_store,
            thesis_llm=FakeLLM(),
            price_fetcher=lambda s: PriceContext(closes={ENTRY_DAY: D("10")}),
        )
        assert written == 1
        (memo,) = memo_store.open_memos()

        # A week later: the stop fires. The exit must find the memo via the
        # signal store (the journal payload still says memo_id="").
        prices["AAA"] = D("7.5")
        outcome = trade_insider_sleeve(
            signal_store=signal_store, insider_journal=insider_journal,
            main_journal=main_journal, quote_source=lambda s: prices[s],
            starting_cash=D("10000"), deny_list=frozenset(), asof=EXIT_DAY,
            now=_post_close(EXIT_DAY), adv_fetcher=lambda t: D("1000000"),
            resolver=resolver,
        )
        assert outcome.exited == ["AAA"]

        (closed,) = [e for e in insider_journal.read_events()
                     if e["kind"] == events.KIND_INSIDER_POSITION_CLOSED]
        assert closed["payload"]["memo_id"] == memo.memo_id

    resolved = memo_store.get(memo.memo_id)
    assert resolved.status == "resolved"
    assert resolved.resolution.outcome_label == "thesis_wrong_lost_money"
    assert resolved.resolution.falsifiers_tripped == [0]
    assert resolved.resolution.benchmark_return_pct == pytest.approx(0.01)
