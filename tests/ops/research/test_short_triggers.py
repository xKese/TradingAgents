"""Unit tests for short red-flag triggers — canned filings/transactions, no network."""

from datetime import date
from decimal import Decimal

import pytest

from ops.research.short_triggers import find_short_triggers
from tradingagents.dataflows.edgar import Filing
from tradingagents.dataflows.form4 import InsiderTransaction

pytestmark = pytest.mark.unit

ASOF = date(2026, 7, 13)


def _filing(form="8-K", items=(), accession="0001-26-000001", filed=date(2026, 7, 1)):
    return Filing(
        ticker="BAD", cik=12345, accession_number=accession, form=form,
        filing_date=filed, report_date=None, primary_document="doc.htm",
        items=tuple(items),
    )


def _sale(name, *, ten_b5_1=False, when=date(2026, 6, 20), code="S"):
    return InsiderTransaction(
        insider_name=name, insider_title="Director", is_director=True,
        is_officer=False, is_ten_pct_owner=False, transaction_date=when,
        code=code, shares=Decimal("1000"), price=Decimal("10"), acquired=False,
        ten_b5_1=ten_b5_1, accession="0009-26-000009", filed_date=when,
    )


def _find(**kw):
    defaults = {
        "list_filings": lambda ticker, **_: [],
        "transactions_fetcher": lambda ticker, since: [],
        "full_text_search": lambda query, **_: [],
        "fetch_text": lambda filing: "",
        "cik_resolver": lambda ticker: 12345,
    }
    defaults.update(kw)
    return find_short_triggers("BAD", asof=ASOF, **defaults)


def test_402_8k_is_a_red_flag():
    out = _find(list_filings=lambda t, **_: [_filing(items=["4.02"])])
    assert [t.kind for t in out] == ["red_flag_8k"]
    assert "4.02" in out[0].description


def test_502_needs_cfo_language():
    filings = [_filing(items=["5.02"])]
    assert _find(list_filings=lambda t, **_: filings) == []
    out = _find(
        list_filings=lambda t, **_: filings,
        fetch_text=lambda f: "our Chief Financial Officer resigned",
    )
    assert [t.kind for t in out] == ["red_flag_8k"]
    assert "CFO" in out[0].description


def test_insider_sell_cluster_needs_three_distinct_non_plan_sellers():
    two = [_sale("A"), _sale("B")]
    assert _find(transactions_fetcher=lambda t, since: two) == []
    plan = [_sale("A"), _sale("B"), _sale("C", ten_b5_1=True)]
    assert _find(transactions_fetcher=lambda t, since: plan) == []
    buys = [_sale("A"), _sale("B"), _sale("C", code="P")]
    assert _find(transactions_fetcher=lambda t, since: buys) == []
    three = [_sale("A"), _sale("B"), _sale("C")]
    out = _find(transactions_fetcher=lambda t, since: three)
    assert [t.kind for t in out] == ["insider_sell_cluster"]


def test_going_concern_matches_on_cik():
    hits = [{"_id": "0002-26-000002:doc", "_source": {"ciks": ["12345"]}}]
    out = _find(full_text_search=lambda q, **_: hits)
    assert [t.kind for t in out] == ["going_concern"]
    other = [{"_id": "x", "_source": {"ciks": ["99999"]}}]
    assert _find(full_text_search=lambda q, **_: other) == []


def test_fts_failure_degrades_without_suppressing_other_flags():
    def boom(query, **_):
        raise RuntimeError("efts down")

    out = _find(
        list_filings=lambda t, **_: [_filing(items=["1.03"])],
        full_text_search=boom,
    )
    assert [t.kind for t in out] == ["red_flag_8k"]
