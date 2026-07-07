"""Unit tests for change-trigger detection (EDGAR mocked, no yfinance)."""

from datetime import date
from decimal import Decimal

import pytest

from ops.research.triggers import (
    find_edgar_triggers,
    find_insider_cluster_trigger,
    find_selloff_trigger,
    find_triggers,
)
from tradingagents.dataflows.edgar import Filing
from tradingagents.dataflows.form4 import InsiderTransaction

pytestmark = pytest.mark.unit

ASOF = date(2026, 7, 1)


def _filing(form, filed, items=(), accn="0001-26-000001"):
    return Filing(
        ticker="TEST", cik=1, accession_number=accn, form=form,
        filing_date=filed, report_date=None, primary_document="doc.htm",
        items=tuple(items),
    )


def test_edgar_triggers_classified_by_form():
    filings = [
        _filing("SC 13D", date(2026, 6, 20), accn="a1"),
        _filing("SC TO-I", date(2026, 6, 10), accn="a2"),
    ]
    triggers = find_edgar_triggers(
        "TEST", asof=ASOF, list_filings=lambda t, **kw: filings,
    )
    assert [(t.kind, t.source) for t in triggers] == [
        ("activist_stake", "a1"), ("tender_offer", "a2"),
    ]


def test_8k_only_triggers_on_notable_items():
    filings = [
        _filing("8-K", date(2026, 6, 20), items=("5.02", "9.01"), accn="a1"),
        _filing("8-K", date(2026, 6, 10), items=("7.01",), accn="a2"),
    ]
    triggers = find_edgar_triggers(
        "TEST", asof=ASOF, list_filings=lambda t, **kw: filings,
    )
    assert len(triggers) == 1
    assert triggers[0].kind == "material_event"
    assert "officer_departure_or_election" in triggers[0].description


def test_form4_is_excluded_and_lookback_forwarded():
    seen = {}

    def fake_list(ticker, *, forms=None, since=None, limit=100):
        seen["forms"] = forms
        seen["since"] = since
        return []

    find_edgar_triggers("TEST", asof=ASOF, list_filings=fake_list)
    assert "4" not in seen["forms"]           # deferred to build-order step 4
    assert "SC 13D" in seen["forms"]
    assert seen["since"] == date(2026, 4, 2)  # asof - 90 days


def test_filings_after_asof_are_ignored():
    filings = [_filing("SC 13D", date(2026, 7, 2))]
    assert find_edgar_triggers("TEST", asof=ASOF, list_filings=lambda t, **kw: filings) == []


def test_selloff_trigger_fires_at_25pct_drawdown():
    closes = [Decimal("100")] * 30 + [Decimal("74")]
    t = find_selloff_trigger("TEST", closes, asof=ASOF)
    assert t is not None
    assert t.kind == "selloff"
    assert t.source == "price"


def test_selloff_no_trigger_on_shallow_drawdown_or_short_history():
    assert find_selloff_trigger("TEST", [Decimal("100")] * 30 + [Decimal("80")], asof=ASOF) is None
    assert find_selloff_trigger("TEST", [Decimal("100"), Decimal("70")], asof=ASOF) is None


def _buy(name, day, *, ten_b5_1=False, code="P"):
    return InsiderTransaction(
        insider_name=name, insider_title="", is_director=True, is_officer=False,
        is_ten_pct_owner=False, transaction_date=day, code=code,
        shares=Decimal("1000"), price=Decimal("5"), acquired=(code == "P"),
        ten_b5_1=ten_b5_1, accession=f"acc-{name}-{day.isoformat()}",
        filed_date=day,
    )


def test_two_distinct_open_market_buyers_trigger():
    asof = date(2026, 7, 1)
    txns = [_buy("DOE JANE", date(2026, 6, 20)), _buy("ROE RICHARD", date(2026, 6, 25))]
    trig = find_insider_cluster_trigger(
        "WIDG", asof=asof, transactions_fetcher=lambda t, *, since, **kw: txns,
    )
    assert trig is not None
    assert trig.kind == "insider_cluster"
    assert trig.source == "acc-ROE RICHARD-2026-06-25"


def test_single_buyer_sales_grants_and_10b51_do_not_trigger():
    asof = date(2026, 7, 1)
    cases = [
        [_buy("DOE JANE", date(2026, 6, 20))],                                # one buyer
        [_buy("DOE JANE", date(2026, 6, 20)), _buy("DOE JANE", date(2026, 6, 25))],  # same buyer twice
        [_buy("A", date(2026, 6, 20), code="S"), _buy("B", date(2026, 6, 25), code="S")],
        [_buy("A", date(2026, 6, 20), code="A"), _buy("B", date(2026, 6, 25), code="A")],
        [_buy("A", date(2026, 6, 20), ten_b5_1=True), _buy("B", date(2026, 6, 25), ten_b5_1=True)],
        [_buy("A", date(2026, 2, 1)), _buy("B", date(2026, 2, 2))],           # outside lookback
    ]
    for txns in cases:
        trig = find_insider_cluster_trigger(
            "WIDG", asof=asof,
            transactions_fetcher=lambda t, *, since, txns=txns, **kw: txns,
        )
        assert trig is None, txns


def test_find_triggers_combines_edgar_and_cluster():
    asof = date(2026, 7, 1)
    txns = [_buy("DOE JANE", date(2026, 6, 20)), _buy("ROE RICHARD", date(2026, 6, 25))]
    out = find_triggers(
        "WIDG", asof=asof,
        list_filings=lambda ticker, **kw: [],
        transactions_fetcher=lambda t, *, since, **kw: txns,
    )
    assert [t.kind for t in out] == ["insider_cluster"]
